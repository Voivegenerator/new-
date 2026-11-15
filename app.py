import asyncio
import io
import os
import re
import subprocess
import tempfile
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file
import edge_tts
import numpy as np
import imageio_ffmpeg

import sfx

app = Flask(__name__)
app.json.ensure_ascii = False
app.config['MAX_CONTENT_LENGTH'] = 60000
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
 'pa': ('pa-IN-GurleenNeural', 0, 0), 'ur': ('ur-IN-GulNeural', 0, 0)
}

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

async def synthesize_piece(text, voice, rate, pitch):
    data = bytearray()
    async for item in edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).stream():
        if item['type'] == 'audio':
            data.extend(item['data'])
    if not data:
        raise RuntimeError('No audio returned')
    return bytes(data)

async def synthesize(text, voice, rate, pitch):
    chunks = split_text(text)
    audio_parts = []
    for chunk in chunks:
        audio_parts.append(await synthesize_piece(chunk, voice, rate, pitch))
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
                                 '-i', str(manifest), '-vn', '-codec:a', 'libmp3lame', '-b:a', '128k',
                                 '-y', str(output)], capture_output=True, timeout=90, cwd=folder)
        if result.returncode != 0 or not output.exists():
            raise RuntimeError('Audio merge failed')
        return output.read_bytes()


# ------------------------------------------------------------ audio helpers
def ffmpeg_exe():
    return imageio_ffmpeg.get_ffmpeg_exe()


def mp3_to_pcm(mp3_bytes, sr=MIX_SR):
    result = subprocess.run([ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-i', 'pipe:0',
                             '-f', 's16le', '-ac', '1', '-ar', str(sr), 'pipe:1'],
                            input=mp3_bytes, capture_output=True, timeout=90)
    if result.returncode != 0:
        raise RuntimeError('Audio decode failed')
    return np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def pcm_to_mp3(samples, sr):
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()
    result = subprocess.run([ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-f', 's16le',
                             '-ac', '1', '-ar', str(sr), '-i', 'pipe:0', '-codec:a', 'libmp3lame',
                             '-b:a', '160k', '-f', 'mp3', 'pipe:1'],
                            input=pcm, capture_output=True, timeout=90)
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError('MP3 encode failed')
    return result.stdout


async def synthesize_segments(texts, voice, rate, pitch):
    sem = asyncio.Semaphore(4)

    async def one(t):
        async with sem:
            return await synthesize(t, voice, rate, pitch)
    return await asyncio.gather(*(one(t) for t in texts))


ONESHOT_GAIN = 0.55
AMBIENT_GAIN = 0.20
AMBIENT_SECONDS = 12.0


def build_mix(items, voice, rate, pitch, sfx_volume=1.0):
    """items: [('text', str) | ('sfx', key)] -> mixed MP3 bytes."""
    texts = [v for k, v in items if k == 'text' and re.search(r'\w', v)]
    voice_parts = asyncio.run(synthesize_segments(texts, voice, rate, pitch)) if texts else []
    voice_pcm = iter([mp3_to_pcm(a) for a in voice_parts])

    sr = MIX_SR
    voice_track, events = [], []  # events: (start_sample, samples)
    cursor = 0
    for kind, val in items:
        if kind == 'text':
            if not re.search(r'\w', val):
                continue
            pcm = next(voice_pcm)
            voice_track.append((cursor, pcm))
            cursor += len(pcm)
        else:
            if sfx.is_ambient(val):
                fx = sfx.generate(val, sr, AMBIENT_SECONDS) * AMBIENT_GAIN
                events.append((cursor, fx))
                cursor += int(0.6 * sr)
            else:
                fx = sfx.generate(val, sr) * ONESHOT_GAIN
                events.append((cursor, fx))
                cursor += int(min(len(fx) / sr * 0.7, 2.5) * sr)
    total = max([cursor] + [s + len(x) for s, x in voice_track] + [s + len(x) for s, x in events])
    total += int(0.3 * sr)
    voice_mix = np.zeros(total, dtype=np.float32)
    fx_mix = np.zeros(total, dtype=np.float32)
    for s, x in voice_track:
        voice_mix[s:s + len(x)] += x
    for s, x in events:
        fx_mix[s:s + len(x)] += x
    vpeak = float(np.max(np.abs(voice_mix))) or 1.0
    out = voice_mix / vpeak * 0.9 + fx_mix * 0.9 * sfx_volume
    peak = float(np.max(np.abs(out)))
    if peak > 0.98:
        out = out / peak * 0.98
    return pcm_to_mp3(out, sr)


@app.get('/')
def home():
    return render_template('index.html')

SITE = 'https://hindi-ai-voice-studio.onrender.com'
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

@app.post('/api/tts')
def tts():
    payload = request.get_json(silent=True) or {}
    text = str(payload.get('text', '')).strip()
    if not 1 <= len(text) <= 5000:
        return jsonify(error='1 से 5000 अक्षर लिखें।'), 400
    selected = VOICES.get(payload.get('voice', 'hi_f_1'))
    if selected is None:
        return jsonify(error='गलत वॉइस विकल्प।'), 400
    try:
        speed = int(payload.get('speed', 0))
        pitch = int(payload.get('pitch', 0))
    except (ValueError, TypeError):
        return jsonify(error='गति या पिच गलत है।'), 400
    if not (-30 <= speed <= 30 and -30 <= pitch <= 30):
        return jsonify(error='गति या पिच सीमा से बाहर है।'), 400
    voice, preset_rate, preset_pitch = selected
    speed = max(-50, min(50, speed + preset_rate))
    pitch = max(-50, min(50, pitch + preset_pitch))
    rate_s, pitch_s = f'{speed:+d}%', f'{pitch:+d}Hz'
    use_sfx = bool(payload.get('sfx', False))
    try:
        if use_sfx:
            items = sfx.parse_script(text, auto=bool(payload.get('auto', True)))
            try:
                vol = float(payload.get('sfx_volume', 1.0))
            except (TypeError, ValueError):
                vol = 1.0
            vol = max(0.0, min(2.0, vol))
            if any(k == 'sfx' for k, _ in items):
                audio = build_mix(items, voice, rate_s, pitch_s, vol)
            else:
                audio = asyncio.run(synthesize(sfx.strip_tags(text), voice, rate_s, pitch_s))
        else:
            clean = sfx.strip_tags(text).strip()
            if not clean:
                return jsonify(error='बोलने के लिए टेक्स्ट नहीं मिला।'), 400
            audio = asyncio.run(synthesize(clean, voice, rate_s, pitch_s))
        return send_file(io.BytesIO(audio), mimetype='audio/mpeg', as_attachment=False,
                         download_name='VoiceGenrator.mp3', max_age=0)
    except Exception:
        app.logger.exception('TTS generation failed')
        return jsonify(error='आवाज़ नहीं बन सकी। वॉइस सेवा या सर्वर जाँचें।'), 502


@app.get('/api/sfx/list')
def sfx_list():
    return jsonify(effects=sfx.catalogue())


@app.post('/api/sfx/detect')
def sfx_detect():
    payload = request.get_json(silent=True) or {}
    text = str(payload.get('text', ''))[:5000]
    items = sfx.parse_script(text, auto=bool(payload.get('auto', True)))
    seen, found = set(), []
    info = {e['key']: e for e in sfx.catalogue()}
    for kind, val in items:
        if kind == 'sfx' and val not in seen:
            seen.add(val)
            found.append(info[val])
    return jsonify(effects=found)


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
