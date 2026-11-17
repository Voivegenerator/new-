# VoiceGenrator — AI Voice Studio

Original premium design inspired by modern AI voice studio patterns, not a copy of third-party assets.

# VoiceGenrator v2

## Features
- 25,000 Unicode characters per request (v4; was 5,000); longer input is split internally and merged into a single MP3 with ffmpeg.
- 5 female + 5 male Hindi **tone presets**, derived from **two distinct Hindi neural voices** (Swara and Madhur). These are not ten separate Hindi voice models.
- 10 additional language voices: English, Bengali, Tamil, Telugu, Marathi, Gujarati, Kannada, Malayalam, Punjabi, Urdu.
- MP3 playback and download; adjustable speed and pitch.
- **Five new Hindi voice models (v4): Ava, Emma (female) and Andrew, Brian (male) — Microsoft multilingual neural voices that auto-detect Hindi text — plus Google Hindi (gTTS). If one is unavailable the request falls back to Swara/Madhur automatically.
Background music (v4): 500 original tunes (10 moods x 50) composed and synthesised in code by music.py (numpy only, no audio files). Code-generated, released as CC0 — royalty-free and copyright-free. The tune is looped under the voiceover and ducks (gets quieter) while the voice speaks. API: /api/music/list, /api/music/<id>?dur=30.
Sound effects (v3):** 18 built-in effects synthesised in code (`sfx.py`, numpy only, no paid API, no audio files):
  door creak, knock, slam, footsteps, glass break, temple bell, gunshot, thunder, whoosh, owl,
  and background ambiences: wind, rain, night crickets, heartbeat, clock, fire, water, horror drone.
  - Put tags in the script, e.g. `[दरवाज़ा]`, `[हवा]`, `[दस्तक]`, `[बारिश]` — the effect plays at that point.
  - Optional auto-detect: sentences like “दरवाज़ा खुल गया” or “तेज़ हवा चल रही थी” get the effect added after them.
  - Effects are mixed under the voiceover into one MP3, or each effect can be previewed/downloaded separately
    from the Sound Effects library (`/api/sfx/<key>`).

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
4. Start: `gunicorn --bind 0.0.0.0:$PORT --timeout 300 app:app`
5. Choose an available plan and deploy. Free plan eligibility and limits can change.

## Limitations
Uses unofficial `edge-tts` online access; service availability, licensing, rate limits, and long-form generation are not guaranteed. A 25,000-character request may take time or fail when the remote service is unavailable. Language voice identifiers are configured but have not been live-tested here. Text is sent to an external TTS service. Public deployment should add rate limiting, abuse prevention, and usage controls.
