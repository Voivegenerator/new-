import asyncio
import io
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
import contextvars
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file
import edge_tts
import numpy as np
import imageio_ffmpeg

import sfx
import music
import voice_clone
from auth import auth_bp, init_db

app = Flask(__name__)
app.json.ensure_ascii = False
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-this-in-render-env')
app.config['MAX_CONTENT_LENGTH'] = 400000
app.register_blueprint(auth_bp)
app.register_blueprint(voice_clone.clone_bp)
init_db()


@app.context_processor
def inject_user():
    from flask import session
    return {
        'logged_in': bool(session.get('user_id')),
        'current_user_name': session.get('user_name'),
    }
MAX_CHARS = 25000
MIX_SR = 24000

# Hindi has two distinct base voices; five presets per gender use pitch/rate variations.
VOICES = {
 'hi_f_1': ('hi-IN-SwaraNeural', 0, 0), 'hi_f_2': ('hi-IN-SwaraNeural', -12, -8),
 'hi_f_3': ('hi-IN-SwaraNeural', 12, 8), 'hi_f_4': ('hi-IN-SwaraNeural', -20, 5),
 'hi_f_5': ('hi-IN-SwaraNeural', 5, -12),
 'hi_m_1': ('hi-IN-MadhurNeural', 0, 0), 'hi_m_2': ('hi-IN-MadhurNeural', -14, -12),
 'hi_m_3': ('hi-IN-MadhurNeural', 12, 7), 'hi_m_4': ('hi-IN-MadhurNeural', -22, 4),
 'hi_m_5': ('hi-IN-MadhurNeural', 6, -7),
 'en': ('en-IN-NeerjaNeural', 0, 0), 'bn': ('bn-IN-TanishaaNeural', 0, 0),
 'ta': ('ta-IN-PallaviNeural', 0, 0), 'te': ('te-IN-ShrutiNeural', 0, 0),
 'mr': ('mr-IN-AarohiNeural', 0, 0), 'gu': ('gu-IN-DhwaniNeural', 0, 0),
 'kn': ('kn-IN-SapnaNeural', 0, 0), 'ml': ('ml-IN-SobhanaNeural', 0, 0),
 'pa': ('pa-IN-GurleenNeural', 0, 0), 'ur': ('ur-IN-GulNeural', 0, 0),
 # v4: new Hindi voice models (different neural voices / engines, not presets)
 'hi_new_andrew': ('en-US-AndrewMultilingualNeural', 0, 0),
 'hi_new_brian': ('en-US-BrianMultilingualNeural', 0, 0),
 'hi_new_google': ('gtts:co.in', 0, 0),
 # v5: Gemini TTS — emotional/expressive narration, replaces Ava & Emma
 'hi_new_gemini': ('gemini:Kore', 0, 0),
 # v6: Gemini TTS, male — same emotional/expressive engine, male voice
 'hi_new_gemini_m': ('gemini:Charon', 0, 0),
}
# If a new multilingual voice is unavailable, fall back to the closest Hindi voice.
FALLBACK = {
 'en-US-AndrewMultilingualNeural': 'hi-IN-MadhurNeural', 'en-US-BrianMultilingualNeural': 'hi-IN-MadhurNeural',
 'gtts:co.in': 'hi-IN-SwaraNeural',
 'gemini:Kore': 'hi-IN-SwaraNeural',
 'gemini:Charon': 'hi-IN-MadhurNeural',
}

# v7: the site owner's own cloned voice (optional, beta) — appears only
# once GEMINI_CLONED_VOICE_ID is set in the environment. That id is
# created once via POST /api/admin/clone-voice (see voice_clone.py) from
# a short reference recording + a matching consent recording.
CLONED_VOICE_ID = os.environ.get('GEMINI_CLONED_VOICE_ID')
if CLONED_VOICE_ID:
    VOICES['hi_clone_user'] = (f'geminiclone:{CLONED_VOICE_ID}', 0, 0)
    FALLBACK[f'geminiclone:{CLONED_VOICE_ID}'] = os.environ.get('GEMINI_CLONED_VOICE_FALLBACK', 'hi-IN-SwaraNeural')

# Gemini TTS follows a natural-language style instruction placed before the text.
GEMINI_STYLE = 'भावनात्मक और कहानी सुनाने वाले अंदाज़ में, प्राकृतिक उतार-चढ़ाव के साथ बोलो'

def split_text(text, limit=1100):
    pieces = re.split(r'(?<=[।.!?\n])\s*', text)
    chunks, current = [], ''
    for piece in pieces:
        if not piece:
            continue
        while len(piece) > limit:
            if current:
                chunks.append(current.strip()); current = ''
            cut = piece.rfind(' ', 0, limit)
            if cut < limit // 2:
                cut = limit
            chunks.append(piece[:cut].strip()); piece = piece[cut:].strip()
        if len(current) + len(piece) + 1 > limit:
            if current:
                chunks.append(current.strip())
            current = piece
        else:
            current = (current + ' ' + piece).strip()
    if current:
        chunks.append(current)
    return chunks

PROGRESS = contextvars.ContextVar('progress', default=None)


def _tick(key, n=1):
    p = PROGRESS.get()
    if p is not None:
        p[key] = p.get(key, 0) + n


def gtts_piece(text, tld, rate, pitch):
    """Google Translate TTS (Hindi). Speed/pitch applied afterwards with ffmpeg."""
    from gtts import gTTS
    buf = io.BytesIO()
    gTTS(text, lang='hi', tld=tld).write_to_fp(buf)
    data = buf.getvalue()
    if not data:
        raise RuntimeError('No audio returned')
    speed, hz = int(rate.rstrip('%')), int(pitch.rstrip('Hz'))
    if speed == 0 and hz == 0:
        return data
    p = (200 + hz) / 200.0            # approx. pitch ratio for a ~200 Hz voice
    tempo = max(0.5, min(2.0, (1 + speed / 100.0) / p))
    flt = f'asetrate=24000*{p:.4f},aresample=24000,atempo={tempo:.4f}'
    result = subprocess.run([ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-i', 'pipe:0',
                             '-af', flt, '-ar', '24000', '-ac', '1', '-codec:a', 'libmp3lame', '-b:a', '64k',
                             '-f', 'mp3', 'pipe:1'], input=data, capture_output=True, timeout=120)
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError('gTTS post-process failed')
    return result.stdout


def gemini_piece(text, voice_name, rate, pitch):
    """Gemini TTS — expressive/emotional Hindi narration, guided by a style instruction."""
    from google import genai
    from google.genai import types
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        raise RuntimeError('GEMINI_API_KEY not configured')
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model='gemini-2.5-flash-preview-tts',
        contents=f'{GEMINI_STYLE}: {text}',
        config=types.GenerateContentConfig(
            response_modalities=['AUDIO'],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name))),
        ),
    )
    part = resp.candidates[0].content.parts[0]
    data = part.inline_data.data
    mime = part.inline_data.mime_type or 'audio/L16;rate=24000'
    m = re.search(r'rate=(\d+)', mime)
    sr = int(m.group(1)) if m else 24000
    if not data:
        raise RuntimeError('No audio returned from Gemini')

    speed, hz = int(rate.rstrip('%')), int(pitch.rstrip('Hz'))
    p = (200 + hz) / 200.0
    tempo = max(0.5, min(2.0, (1 + speed / 100.0) / p))
    flt = f'asetrate={sr}*{p:.4f},aresample=24000,atempo={tempo:.4f}' if (speed or hz) else 'aresample=24000'
    result = subprocess.run([ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-f', 's16le', '-ar', str(sr),
                             '-ac', '1', '-i', 'pipe:0', '-af', flt, '-ar', '24000', '-ac', '1',
                             '-codec:a', 'libmp3lame', '-b:a', '64k', '-f', 'mp3', 'pipe:1'],
                            input=data, capture_output=True, timeout=120)
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError('Gemini TTS post-process failed: ' + result.stderr.decode(errors='ignore')[-200:])
    return result.stdout


async def _edge_piece(text, voice, rate, pitch):
    data = bytearray()
    async for item in edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).stream():
        if item['type'] == 'audio':
            data.extend(item['data'])
    if not data:
        raise RuntimeError('No audio returned')
    return bytes(data)


async def synthesize_piece(text, voice, rate, pitch):
    last = None
    for attempt in range(2):
        try:
            if voice.startswith('gtts:'):
                return await asyncio.to_thread(gtts_piece, text, voice.split(':', 1)[1], rate, pitch)
            if voice.startswith('gemini:'):
                return await asyncio.to_thread(gemini_piece, text, voice.split(':', 1)[1], rate, pitch)
            if voice.startswith('geminiclone:'):
                return await asyncio.to_thread(
                    voice_clone.gemini_cloned_piece, text, voice.split(':', 1)[1], rate, pitch, GEMINI_STYLE)
            return await _edge_piece(text, voice, rate, pitch)
        except Exception as exc:  # retry once, then fall back to a Hindi voice
            last = exc
            await asyncio.sleep(0.8)
    fb = FALLBACK.get(voice)
    if fb:
        app.logger.warning('Voice %s failed (%s); falling back to %s', voice, last, fb)
        return await _edge_piece(text, fb, rate, pitch)
    raise last


async def synthesize(text, voice, rate, pitch):
    chunks = split_text(text)
    _tick('total', len(chunks))
    sem = asyncio.Semaphore(5)

    async def one(chunk):
        async with sem:
            data = await synthesize_piece(chunk, voice, rate, pitch)
            _tick('done')
            return data
    audio_parts = await asyncio.gather(*(one(c) for c in chunks))
    if len(audio_parts) == 1:
        return audio_parts[0]
    # Re-encode to one valid MP3 rather than concatenating MP3 byte streams.
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory() as folder:
        manifest = Path(folder) / 'files.txt'
        entries = []
        for i, audio in enumerate(audio_parts):
            path = Path(folder) / f'part_{i:02d}.mp3'
            path.write_bytes(audio)
            entries.append(f"file '{path.name}'")
        manifest.write_text('\n'.join(entries), encoding='utf-8')
        output = Path(folder) / 'final.mp3'
        result = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'concat', '-safe', '0',
                                 '-i', str(manifest), '-vn', '-c', 'copy',
                                 '-y', str(output)], capture_output=True, timeout=240, cwd=folder)
        if result.returncode != 0 or not output.exists() or output.stat().st_size < 1000:
            result = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'concat', '-safe', '0',
                                     '-i', str(manifest), '-vn', '-codec:a', 'libmp3lame', '-b:a', '96k',
                                     '-y', str(output)], capture_output=True, timeout=400, cwd=folder)
        if result.returncode != 0 or not output.exists():
            raise RuntimeError('Audio merge failed')
        return output.read_bytes()


# ------------------------------------------------------------ audio helpers
def ffmpeg_exe():
    return imageio_ffmpeg.get_ffmpeg_exe()


def mp3_to_pcm(mp3_bytes, sr=MIX_SR):
    result = subprocess.run([ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-i', 'pipe:0',
                             '-f', 's16le', '-ac', '1', '-ar', str(sr), 'pipe:1'],
                            input=mp3_bytes, capture_output=True, timeout=240)
    if result.returncode != 0:
        raise RuntimeError('Audio decode failed')
    return np.frombuffer(result.stdout, dtype=np.int16)  # int16 keeps memory low for long scripts


def pcm_to_mp3(samples, sr):
    """Encode float audio to MP3, streaming through a temp file in blocks (low memory)."""
    with tempfile.TemporaryDirectory() as folder:
        raw, out = Path(folder) / 'in.raw', Path(folder) / 'out.mp3'
        block = 1 << 20
        with open(raw, 'wb') as fh:
            for i in range(0, len(samples), block):
                part = samples[i:i + block]
                if part.dtype != np.int16:
                    part = (np.clip(part, -1, 1) * 32767).astype('<i2')
                fh.write(part.tobytes())
        result = subprocess.run([ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-f', 's16le',
                                 '-ac', '1', '-ar', str(sr), '-i', str(raw), '-codec:a', 'libmp3lame',
                                 '-b:a', '128k', '-y', str(out)], capture_output=True, timeout=300)
        if result.returncode != 0 or not out.exists():
            raise RuntimeError('MP3 encode failed')
        return out.read_bytes()


async def synthesize_segments(texts, voice, rate, pitch):
    sem = asyncio.Semaphore(2)

    async def one(t):
        async with sem:
            return await synthesize(t, voice, rate, pitch)
    return await asyncio.gather(*(one(t) for t in texts))


ONESHOT_GAIN = 0.55
AMBIENT_GAIN = 0.20
AMBIENT_SECONDS = 12.0


def build_mix(items, voice, rate, pitch, sfx_volume=1.0, raw=False):
    """items: [('text', str) | ('sfx', key)] -> mixed MP3 bytes."""
    texts = [v for k, v in items if k == 'text' and re.search(r'\w', v)]
    voice_parts = asyncio.run(synthesize_segments(texts, voice, rate, pitch)) if texts else []
    voice_pcm = iter([mp3_to_pcm(a) for a in voice_parts])
    del voice_parts

    sr = MIX_SR
    voice_track, events = [], []  # events: (start_sample, key, samples or None)
    oneshots = {}
    cursor = 0
    for kind, val in items:
        if kind == 'text':
            if not re.search(r'\w', val):
                continue
            pcm = next(voice_pcm)
            voice_track.append((cursor, pcm))
            cursor += len(pcm)
        else:
            if sfx.is_ambient(val):  # long ambience: generated later, at mix time
                events.append((cursor, val, None))
                cursor += int(0.6 * sr)
            else:
                if val not in oneshots:  # reuse per key: long scripts may repeat an effect 100s of times
                    oneshots[val] = (sfx.generate(val, sr) * ONESHOT_GAIN).astype(np.float32)
                fx = oneshots[val]
                events.append((cursor, val, fx))
                cursor += int(min(len(fx) / sr * 0.7, 2.5) * sr)
    total = max([cursor] + [s + len(x) for s, x in voice_track]) + int(AMBIENT_SECONDS * sr) + int(0.3 * sr)
    # Mix straight into one int16 buffer (a 25,000-char script is ~30 min of audio,
    # so float buffers would not fit in a 512 MB server).
    B = 1 << 19  # process big arrays in blocks to keep temporaries small
    vpeak = max([int(np.max(np.abs(x[i:i + B].astype(np.int32))))
                 for _, x in voice_track for i in range(0, len(x), B)] + [1])
    vgain = np.float32(0.9 * 32767 / vpeak)
    out = np.zeros(total, dtype=np.int16)
    while voice_track:
        s, x = voice_track.pop(0)
        for i in range(0, len(x), B):
            blk = x[i:i + B]
            out[s + i:s + i + len(blk)] = np.clip(blk.astype(np.float32) * vgain, -32767, 32767).astype(np.int16)
        del x
    end = 0
    fgain = np.float32(0.9 * sfx_volume * 32767)
    for s, key, fx in events:
        if fx is None:
            fx = sfx.generate(key, sr, AMBIENT_SECONDS) * AMBIENT_GAIN
        fx = fx[:total - s]
        seg = out[s:s + len(fx)].astype(np.float32) + fx.astype(np.float32) * fgain
        out[s:s + len(fx)] = np.clip(seg, -32767, 32767).astype(np.int16)
        end = max(end, s + len(fx))
    out = out[:max(end, cursor) + int(0.3 * sr)]
    if raw:
        return out
    return pcm_to_mp3(out, sr)


def mp3_duration(path):
    r = subprocess.run([ffmpeg_exe(), '-hide_banner', '-i', str(path)], capture_output=True, text=True)
    m = re.search(r'Duration: (\d+):(\d+):(\d+\.?\d*)', r.stderr)
    if not m:
        raise RuntimeError('Could not read duration')
    h, mi, se = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(se)


def add_music(voice, tune_id, level=0.35):
    """Mix a looped background tune under the voice (music ducks while voice speaks).
    voice: MP3 bytes, or an int16 numpy array at MIX_SR (skips one decode/encode)."""
    level = max(0.0, min(1.0, level))
    with tempfile.TemporaryDirectory() as folder:
        mpath, opath = Path(folder) / 'm.wav', Path(folder) / 'o.mp3'
        if isinstance(voice, np.ndarray):
            vpath = Path(folder) / 'v.raw'
            with open(vpath, 'wb') as fh:
                for i in range(0, len(voice), 1 << 20):
                    fh.write(voice[i:i + (1 << 20)].astype('<i2').tobytes())
            vin = ['-f', 's16le', '-ar', str(MIX_SR), '-ac', '1', '-i', str(vpath)]
            dur = len(voice) / MIX_SR
        else:
            vpath = Path(folder) / 'v.mp3'
            vpath.write_bytes(voice)
            vin = ['-i', str(vpath)]
            dur = mp3_duration(vpath)
        mpath.write_bytes(music.wav_bytes(music.loop(tune_id)))
        lead, tail = 1.2, 2.5
        total = dur + lead + tail
        vol = 0.9 * level
        flt = (f'[0:a]aresample=24000,aformat=channel_layouts=mono,adelay={int(lead * 1000)},apad[vp];'
               f'[vp]atrim=0:{total:.2f},asplit=2[v1][v2];'
               f'[1:a]aresample=24000,aformat=channel_layouts=mono,atrim=0:{total:.2f},volume={vol:.3f},'
               f'afade=t=in:d=1.5,afade=t=out:st={max(0.0, total - 3):.2f}:d=3[m];'
               f'[m][v2]sidechaincompress=threshold=0.03:ratio=3:attack=40:release=600:makeup=1[md];'
               f'[v1][md]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[out]')
        result = subprocess.run([ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', *vin,
                                 '-stream_loop', '-1', '-i', str(mpath), '-filter_complex', flt,
                                 '-map', '[out]', '-t', f'{total:.2f}', '-ac', '1', '-codec:a', 'libmp3lame',
                                 '-b:a', '128k', '-y', str(opath)], capture_output=True, timeout=600)
        if result.returncode != 0 or not opath.exists():
            raise RuntimeError('Music mix failed: ' + result.stderr.decode(errors='ignore')[-300:])
        return opath.read_bytes()


@app.get('/')
def home():
    return render_template('index.html', has_cloned_voice=bool(CLONED_VOICE_ID))

SITE = 'https://aivoicestudio.onrender.com'
POSTS = [
    {'slug': 'hindi-ai-voiceover-kaise-banaye', 'title': 'हिंदी में AI वॉइसओवर कैसे बनाएँ — पूरी गाइड',
     'summary': 'स्क्रिप्ट तैयार करने से लेकर आवाज़ चुनने और MP3 डाउनलोड करने तक, कदम-दर-कदम।'},
    {'slug': 'horror-kahani-sound-effects', 'title': 'हॉरर कहानी में साउंड इफ़ेक्ट कैसे लगाएँ',
     'summary': 'दरवाज़ा, हवा, दस्तक जैसे इफ़ेक्ट सही जगह पर लगाने के तरीके और साउंड डिज़ाइन के नियम।'},
    {'slug': 'voice-speed-pitch-settings', 'title': 'वॉइसओवर की गति और पिच: सही सेटिंग कैसे चुनें',
     'summary': 'कहानी, न्यूज़, शॉर्ट्स और बच्चों की कहानियों के लिए शुरुआती सेटिंग्स।'},
    {'slug': 'voiceover-script-likhne-ke-tips', 'title': 'अच्छी वॉइसओवर स्क्रिप्ट लिखने के 10 टिप्स',
     'summary': 'सुनने के लिए लिखना पढ़ने के लिए लिखने से अलग है — ये टिप्स आपकी स्क्रिप्ट को असरदार बनाएँगे।'},
    {'slug': 'ai-voice-youtube-niyam', 'title': 'YouTube पर AI आवाज़ इस्तेमाल करते समय किन बातों का ध्यान रखें',
     'summary': 'मौलिक कंटेंट, कॉपीराइट, नकल से बचाव और दर्शकों का भरोसा।'},
]
POST_SLUGS = {p['slug'] for p in POSTS}
STATIC_PAGES = ['app', 'about', 'privacy', 'terms', 'contact']


@app.get('/blog')
def blog_index():
    return render_template('pages/blog.html', posts=POSTS)


@app.get('/blog/<slug>')
def blog_post(slug):
    if slug not in POST_SLUGS:
        return render_template('pages/blog.html', posts=POSTS), 404
    return render_template(f'blog/{slug}.html')


@app.get('/<any(app, about, privacy, terms, contact):page>')
def static_page(page):
    return render_template(f'pages/{page}.html')


@app.get('/robots.txt')
def robots_txt():
    return app.response_class(f'User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap.xml\n', mimetype='text/plain')


@app.get('/sitemap.xml')
def sitemap_xml():
    urls = ['/', '/blog'] + [f'/{p}' for p in STATIC_PAGES] + [f"/blog/{p['slug']}" for p in POSTS]
    body = ''.join(f'<url><loc>{SITE}{u}</loc></url>' for u in urls)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'
    return app.response_class(xml, mimetype='application/xml')


@app.get('/google0d3851bdc18b670c.html')
def google_verify():
    return app.response_class('google-site-verification: google0d3851bdc18b670c.html', mimetype='text/html')


@app.get('/favicon.ico')
def favicon():
    return app.send_static_file('favicon.svg')


@app.get('/ads.txt')
def ads_txt():
    return app.response_class('google.com, pub-4167370296441487, DIRECT, f08c47fec0942fa0\n',
                              mimetype='text/plain')


@app.get('/health')
def health():
    return jsonify(status='ok')

def parse_request(payload):
    """Validate a TTS request. Returns (job dict, None) or (None, (error, status))."""
    text = str(payload.get('text', '')).strip()
    if not 1 <= len(text) <= MAX_CHARS:
        return None, (f'1 से {MAX_CHARS:,} अक्षर लिखें।', 400)
    selected = VOICES.get(payload.get('voice', 'hi_f_1'))
    if selected is None:
        return None, ('गलत वॉइस विकल्प।', 400)
    try:
        speed = int(payload.get('speed', 0))
        pitch = int(payload.get('pitch', 0))
    except (ValueError, TypeError):
        return None, ('गति या पिच गलत है।', 400)
    if not (-30 <= speed <= 30 and -30 <= pitch <= 30):
        return None, ('गति या पिच सीमा से बाहर है।', 400)
    voice, preset_rate, preset_pitch = selected
    speed = max(-50, min(50, speed + preset_rate))
    pitch = max(-50, min(50, pitch + preset_pitch))
    tune = str(payload.get('music') or '').strip()
    if tune and not music.exists(tune):
        return None, ('बैकग्राउंड म्यूज़िक नहीं मिला।', 400)

    def num(key, default, lo, hi):
        try:
            return max(lo, min(hi, float(payload.get(key, default))))
        except (TypeError, ValueError):
            return default
    use_sfx = bool(payload.get('sfx', False))
    if not use_sfx and not sfx.strip_tags(text).strip():
        return None, ('बोलने के लिए टेक्स्ट नहीं मिला।', 400)
    return dict(text=text, voice=voice, rate=f'{speed:+d}%', pitch=f'{pitch:+d}Hz', sfx=use_sfx,
                auto=bool(payload.get('auto', True)), sfx_volume=num('sfx_volume', 1.0, 0.0, 2.0),
                music=tune, music_volume=num('music_volume', 0.35, 0.0, 1.0)), None


def render_audio(job, progress=None):
    """Produce the final MP3 bytes for a validated request."""
    def stage(name):
        if progress is not None:
            progress['stage'] = name
    stage('voice')
    if job['sfx']:
        items = sfx.parse_script(job['text'], auto=job['auto'])
        if any(k == 'sfx' for k, _ in items):
            audio = build_mix(items, job['voice'], job['rate'], job['pitch'], job['sfx_volume'],
                              raw=bool(job['music']))
            stage('mix')
        else:
            audio = asyncio.run(synthesize(sfx.strip_tags(job['text']), job['voice'], job['rate'], job['pitch']))
    else:
        audio = asyncio.run(synthesize(sfx.strip_tags(job['text']).strip(), job['voice'], job['rate'], job['pitch']))
    if job['music']:
        stage('music')
        audio = add_music(audio, job['music'], job['music_volume'])
    return audio


@app.post('/api/tts')
def tts():
    """Synchronous API (short scripts / Android app). Long scripts should use /api/tts/start."""
    job, err = parse_request(request.get_json(silent=True) or {})
    if err:
        return jsonify(error=err[0]), err[1]
    try:
        audio = render_audio(job)
        return send_file(io.BytesIO(audio), mimetype='audio/mpeg', as_attachment=False,
                         download_name='VoiceGenrator.mp3', max_age=0)
    except Exception:
        app.logger.exception('TTS generation failed')
        return jsonify(error='आवाज़ नहीं बन सकी। वॉइस सेवा या सर्वर जाँचें।'), 502


# ------------------------------------------------------------ background jobs
# Long scripts (up to 25,000 chars ~ 30 min audio) take minutes on a small server,
# longer than the HTTP worker timeout, so they run in a background thread and the
# page polls for progress. One heavy job at a time keeps memory under 512 MB.
JOBS = {}
JOBS_LOCK = threading.Lock()
HEAVY = threading.Semaphore(1)
JOB_DIR = Path(tempfile.gettempdir()) / 'vg_jobs'
JOB_DIR.mkdir(exist_ok=True)
JOB_TTL = 30 * 60


def _cleanup_jobs():
    now = time.time()
    with JOBS_LOCK:
        for jid in [j for j, v in JOBS.items() if now - v['created'] > JOB_TTL]:
            try:
                (JOB_DIR / f'{jid}.mp3').unlink(missing_ok=True)
            except OSError:
                pass
            JOBS.pop(jid, None)


def _run_job(jid, job):
    state = JOBS[jid]
    heavy = len(job['text']) > 4000 or bool(job['music'])
    try:
        if heavy:
            state['stage'] = 'queue'
            HEAVY.acquire()
        try:
            state['stage'] = 'voice'
            ctx = contextvars.copy_context()
            ctx.run(PROGRESS.set, state)
            audio = ctx.run(render_audio, job, state)
        finally:
            if heavy:
                HEAVY.release()
        (JOB_DIR / f'{jid}.mp3').write_bytes(audio)
        state['size'] = len(audio)
        state['stage'] = 'ready'
    except Exception:
        app.logger.exception('TTS job failed')
        state['stage'] = 'error'
        state['error'] = 'आवाज़ नहीं बन सकी। वॉइस सेवा या सर्वर जाँचें।'


@app.post('/api/tts/start')
def tts_start():
    _cleanup_jobs()
    job, err = parse_request(request.get_json(silent=True) or {})
    if err:
        return jsonify(error=err[0]), err[1]
    with JOBS_LOCK:
        running = sum(1 for v in JOBS.values() if v['stage'] not in ('ready', 'error'))
        if running >= 6:
            return jsonify(error='सर्वर अभी व्यस्त है, एक मिनट बाद फिर कोशिश करें।'), 429
        jid = uuid.uuid4().hex
        JOBS[jid] = {'stage': 'queue', 'done': 0, 'total': 0, 'created': time.time(),
                     'chars': len(job['text'])}
    threading.Thread(target=_run_job, args=(jid, job), daemon=True).start()
    return jsonify(id=jid)


@app.get('/api/tts/status/<jid>')
def tts_status(jid):
    st = JOBS.get(jid)
    if not st:
        return jsonify(error='यह अनुरोध नहीं मिला (शायद पुराना हो गया)।'), 404
    return jsonify(stage=st['stage'], done=st.get('done', 0), total=st.get('total', 0),
                   error=st.get('error'), elapsed=int(time.time() - st['created']))


@app.get('/api/tts/result/<jid>')
def tts_result(jid):
    st = JOBS.get(jid)
    path = JOB_DIR / f'{jid}.mp3'
    if not st or st['stage'] != 'ready' or not path.exists():
        return jsonify(error='MP3 अभी तैयार नहीं है।'), 404
    return send_file(path, mimetype='audio/mpeg', as_attachment=False,
                     download_name='VoiceGenrator.mp3', max_age=0)


@app.get('/api/sfx/list')
def sfx_list():
    return jsonify(effects=sfx.catalogue())


@app.post('/api/sfx/detect')
def sfx_detect():
    payload = request.get_json(silent=True) or {}
    text = str(payload.get('text', ''))[:MAX_CHARS]
    items = sfx.parse_script(text, auto=bool(payload.get('auto', True)))
    seen, found = set(), []
    info = {e['key']: e for e in sfx.catalogue()}
    for kind, val in items:
        if kind == 'sfx' and val not in seen:
            seen.add(val)
            found.append(info[val])
    return jsonify(effects=found)


@app.get('/api/music/list')
def music_list():
    return jsonify(moods=music.moods(), tunes=music.catalogue(), license='CC0 — code-generated, royalty-free')


@app.get('/api/music/<tune_id>')
def music_one(tune_id):
    if not music.exists(tune_id):
        return jsonify(error='यह म्यूज़िक उपलब्ध नहीं है।'), 404
    try:
        secs = request.args.get('dur', default=30.0, type=float)
        secs = max(10.0, min(180.0, secs))
        audio = pcm_to_mp3(music.preview(tune_id, secs), music.SR)
        return send_file(io.BytesIO(audio), mimetype='audio/mpeg', as_attachment=False,
                         download_name=f'VoiceGenrator-music-{tune_id}.mp3', max_age=86400)
    except Exception:
        app.logger.exception('Music generation failed')
        return jsonify(error='म्यूज़िक नहीं बन सका।'), 500


@app.get('/api/sfx/<key>')
def sfx_one(key):
    if key not in sfx.EFFECTS:
        return jsonify(error='यह साउंड इफ़ेक्ट उपलब्ध नहीं है।'), 404
    try:
        dur = request.args.get('dur', type=float)
        if dur is not None:
            dur = max(2.0, min(60.0, dur))
        data = sfx.generate(key, 44100, dur if sfx.is_ambient(key) else None)
        audio = pcm_to_mp3(data, 44100)
        return send_file(io.BytesIO(audio), mimetype='audio/mpeg', as_attachment=False,
                         download_name=f'{key}.mp3', max_age=0)
    except Exception:
        app.logger.exception('SFX generation failed')
        return jsonify(error='साउंड इफ़ेक्ट नहीं बन सका।'), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')))
