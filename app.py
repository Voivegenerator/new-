import asyncio
import io
import os
import re
import subprocess
import tempfile
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file
import edge_tts

app = Flask(__name__)
app.json.ensure_ascii = False
app.config['MAX_CONTENT_LENGTH'] = 40000

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

@app.get('/')
def home():
    return render_template('index.html')

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
    try:
        audio = asyncio.run(synthesize(text, voice, f'{speed:+d}%', f'{pitch:+d}Hz'))
        return send_file(io.BytesIO(audio), mimetype='audio/mpeg', as_attachment=False,
                         download_name='VoiceGenrator.mp3', max_age=0)
    except Exception:
        app.logger.exception('TTS generation failed')
        return jsonify(error='आवाज़ नहीं बन सकी। वॉइस सेवा या सर्वर जाँचें।'), 502

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')))
