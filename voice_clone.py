"""Voice Replication (Gemini) — one-time flow to clone the site owner's
own voice, using Google's newer Voices API.

This is a *different* Gemini feature/model from the existing
"hi_new_gemini" / "hi_new_gemini_m" prebuilt voices in app.py:
  - Prebuilt voices (Kore, Charon) use model `gemini-2.5-flash-preview-tts`
    via `client.models.generate_content` (see app.py:gemini_piece). That
    path is untouched by this file.
  - A replicated (cloned) voice uses model `gemini-3.8-flash-tts` via the
    newer `client.interactions.create`. It is kept in this separate module
    and function (gemini_cloned_piece) so that if Google's exact request
    shape differs slightly from what's coded here, it can only ever fail
    and fall back to a normal Hindi voice (app.py's synthesize_piece
    already retries once then falls back via FALLBACK) — it can never
    break the existing, working prebuilt-voice feature.

Beta feature, subject to change. Reference:
https://ai.google.dev/gemini-api/docs/voice-replication
"""
import base64
import os
import re
import subprocess

import imageio_ffmpeg
from flask import Blueprint, jsonify, render_template, request

clone_bp = Blueprint('clone', __name__)


@clone_bp.get('/api/admin/clone-voice-ui')
def clone_voice_ui():
    """A small page to submit the reference + consent recordings from a
    browser (simpler than curl/Postman for a one-time admin task). The
    page itself needs no auth to view; the actual POST below still
    requires the correct ADMIN_CLONE_TOKEN."""
    return render_template('pages/admin_clone.html')

# Google requires the voice's owner to read this exact sentence as proof of
# consent (hi-IN locale). Before recording, re-check it word-for-word
# against Google's own page in case this copy ever drifts:
# https://ai.google.dev/gemini-api/docs/voice-replication
CONSENT_TEXT_HI = (
    'मैं इस आवाज का मालिक हूं और मैं सिंथेटिक आवाज मॉडल बनाने के लिए Google को '
    'इस आवाज का उपयोग करने की सहमति देता हूं'
)


def _ffmpeg_exe():
    return imageio_ffmpeg.get_ffmpeg_exe()


def _to_replication_wav(raw: bytes) -> bytes:
    """Convert any uploaded audio to the 24kHz mono 16-bit WAV Google asks for."""
    result = subprocess.run(
        [_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-i', 'pipe:0',
         '-ar', '24000', '-ac', '1', '-sample_fmt', 's16', '-f', 'wav', 'pipe:1'],
        input=raw, capture_output=True, timeout=60,
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError('Could not read that audio file: ' + result.stderr.decode(errors='ignore')[-200:])
    return result.stdout


@clone_bp.post('/api/admin/clone-voice')
def clone_voice_endpoint():
    """One-time: create a replicated voice from a reference + consent recording.

    Protected by a shared secret (ADMIN_CLONE_TOKEN) because it spends the
    account's Gemini quota and creates a voice tied to a real person's
    identity — it must never be publicly callable. Disabled entirely
    (403) until ADMIN_CLONE_TOKEN is set in the environment.
    """
    token = os.environ.get('ADMIN_CLONE_TOKEN')
    if not token or request.headers.get('X-Admin-Token') != token:
        return jsonify(error='Not authorized.'), 403

    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        return jsonify(error='GEMINI_API_KEY not configured.'), 500

    source = request.files.get('source_audio')
    consent = request.files.get('consent_audio')
    if not source or not consent:
        return jsonify(error='Upload both source_audio and consent_audio files.'), 400

    try:
        source_wav = _to_replication_wav(source.read())
        consent_wav = _to_replication_wav(consent.read())
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 400

    from google import genai
    client = genai.Client(api_key=api_key)
    try:
        voice = client.voices.create(
            store=True,
            voice={
                'model': 'gemini-3.8-flash-tts',
                'type': 'replicated',
                'display_name': request.form.get('display_name', 'VoiceGenrator owner voice'),
                'replicated': {
                    'source_audio': {'mime_type': 'audio/wav', 'data': base64.b64encode(source_wav).decode()},
                    'consent_audio': {'mime_type': 'audio/wav', 'data': base64.b64encode(consent_wav).decode()},
                },
            },
        )
    except Exception as exc:  # noqa: BLE001 -- show Google's own error text to the admin caller
        return jsonify(error=f'Google rejected the clone request: {exc}'), 502

    return jsonify(
        voice_id=voice.id,
        note='Set this value as GEMINI_CLONED_VOICE_ID in the Render environment, then redeploy.',
    )


def gemini_cloned_piece(text, voice_id, rate, pitch, style):
    """Speak `text` with a replicated (cloned) voice via Gemini's Interactions API."""
    from google import genai
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        raise RuntimeError('GEMINI_API_KEY not configured')
    client = genai.Client(api_key=api_key)
    interaction = client.interactions.create(
        model='gemini-3.8-flash-tts',
        contents=[{'role': 'user', 'parts': [{'text': f'{style}: {text}'}]}],
        response_format={'type': 'audio'},
        generation_config={'speech_config': [{'voice': voice_id}]},
    )
    audio = interaction.output_audio
    data = base64.b64decode(audio.data)
    if not data:
        raise RuntimeError('No audio returned from Gemini (cloned voice)')
    mime = getattr(audio, 'mime_type', None) or 'audio/L16;rate=24000'
    m = re.search(r'rate=(\d+)', mime)
    sr = int(m.group(1)) if m else 24000

    speed, hz = int(rate.rstrip('%')), int(pitch.rstrip('Hz'))
    p = (200 + hz) / 200.0
    tempo = max(0.5, min(2.0, (1 + speed / 100.0) / p))
    flt = f'asetrate={sr}*{p:.4f},aresample=24000,atempo={tempo:.4f}' if (speed or hz) else 'aresample=24000'
    result = subprocess.run(
        [_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-f', 's16le', '-ar', str(sr),
         '-ac', '1', '-i', 'pipe:0', '-af', flt, '-ar', '24000', '-ac', '1',
         '-codec:a', 'libmp3lame', '-b:a', '64k', '-f', 'mp3', 'pipe:1'],
        input=data, capture_output=True, timeout=120,
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError('Cloned-voice post-process failed: ' + result.stderr.decode(errors='ignore')[-200:])
    return result.stdout
