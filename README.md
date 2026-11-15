# VoiceGenrator — AI Voice Studio

Original premium design inspired by modern AI voice studio patterns, not a copy of third-party assets.

# VoiceGenrator v2

## Features
- 5,000 Unicode characters per request; longer input is split internally and merged into a single MP3 with ffmpeg.
- 5 female + 5 male Hindi **tone presets**, derived from **two distinct Hindi neural voices** (Swara and Madhur). These are not ten separate Hindi voice models.
- 10 additional language voices: English, Bengali, Tamil, Telugu, Marathi, Gujarati, Kannada, Malayalam, Punjabi, Urdu.
- MP3 playback and download; adjustable speed and pitch.

## Local setup
Install Python 3.10+ then:
```
pip install -r requirements.txt
python app.py
```
Open http://localhost:5000

## Render deployment
1. Upload this project to a GitHub repository.
2. Render > New > Web Service > connect repository.
3. Build: `pip install -r requirements.txt`
4. Start: `gunicorn --bind 0.0.0.0:$PORT --timeout 180 app:app`
5. Choose an available plan and deploy. Free plan eligibility and limits can change.

## Limitations
Uses unofficial `edge-tts` online access; service availability, licensing, rate limits, and long-form generation are not guaranteed. A 5,000-character request may take time or fail when the remote service is unavailable. Language voice identifiers are configured but have not been live-tested here. Text is sent to an external TTS service. Public deployment should add rate limiting, abuse prevention, and usage controls.
