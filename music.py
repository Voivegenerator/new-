"""Procedural background music: 500 original, royalty-free loops.

Every tune is composed and synthesised by this file with numpy (no audio
files, no samples, no third-party music). The same tune id always produces
the same audio, so the library is 10 moods x 50 variations = 500 tunes.
Because the music is generated from scratch by this code, it carries no
third-party copyright; VoiceGenrator releases it as CC0 (free for any use).
"""
import hashlib
from functools import lru_cache

import numpy as np

SR = 22050

# Indian-flavoured scales (semitones from the root) — "thaat"/raag names.
SCALES = {
    'bilawal': ('बिलावल', [0, 2, 4, 5, 7, 9, 11]),
    'yaman': ('यमन', [0, 2, 4, 6, 7, 9, 11]),
    'khamaj': ('खमाज', [0, 2, 4, 5, 7, 9, 10]),
    'kafi': ('काफ़ी', [0, 2, 3, 5, 7, 9, 10]),
    'asavari': ('आसावरी', [0, 2, 3, 5, 7, 8, 10]),
    'bhairavi': ('भैरवी', [0, 1, 3, 5, 7, 8, 10]),
    'bhairav': ('भैरव', [0, 1, 4, 5, 7, 8, 11]),
    'todi': ('तोड़ी', [0, 1, 3, 6, 7, 8, 11]),
    'marwa': ('मारवा', [0, 1, 4, 6, 7, 9, 11]),
    'purvi': ('पूर्वी', [0, 1, 4, 6, 7, 8, 11]),
    'bhupali': ('भूपाली', [0, 2, 4, 7, 9, 12, 14]),
    'malkauns': ('मालकौंस', [0, 3, 5, 8, 10, 12, 15]),
}

# key, Hindi name, emoji, bpm range, scales, instrument flags
MOODS = [
    ('horror', 'डरावना', '👻', (52, 70), ['bhairavi', 'todi', 'marwa', 'purvi', 'malkauns'],
     dict(drone='dark', pad='dark', bells='random', swell=True, heart=True, verb=2.2)),
    ('suspense', 'सस्पेंस', '🕵️', (84, 104), ['asavari', 'todi', 'kafi', 'bhairavi', 'marwa'],
     dict(pad='dark', ostinato=True, tick=True, swell=True, verb=1.4)),
    ('sad', 'भावुक / दुखी', '😢', (60, 74), ['asavari', 'bhairavi', 'kafi', 'malkauns', 'todi'],
     dict(pad='soft', flute=True, arp='slow', verb=1.8)),
    ('romantic', 'रोमांटिक', '💞', (70, 88), ['yaman', 'khamaj', 'bilawal', 'kafi', 'bhupali'],
     dict(pad='soft', flute=True, arp='mid', bass=True, softkick=True, verb=1.6)),
    ('happy', 'खुशनुमा', '😊', (104, 124), ['bilawal', 'khamaj', 'bhupali', 'yaman', 'kafi'],
     dict(pad='bright', lead='pluck', arp='fast', bass=True, drums='pop', verb=0.9)),
    ('devotional', 'भक्ति', '🙏', (66, 84), ['bhairav', 'yaman', 'bhupali', 'kafi', 'purvi'],
     dict(drone='tanpura', flute=True, bells='temple', tabla='slow', verb=1.7)),
    ('calm', 'शांत / ध्यान', '🧘', (50, 64), ['bhupali', 'yaman', 'bilawal', 'kafi', 'malkauns'],
     dict(drone='tanpura', pad='soft', bells='soft', verb=2.4)),
    ('epic', 'एपिक / साहसिक', '⚔️', (86, 108), ['asavari', 'kafi', 'bhairavi', 'khamaj', 'purvi'],
     dict(pad='big', brass=True, ostinato=True, taiko=True, bass=True, verb=1.5)),
    ('mystery', 'रहस्यमय', '🔮', (70, 88), ['marwa', 'todi', 'yaman', 'purvi', 'malkauns'],
     dict(pad='dark', arp='mid', bells='random', tick=True, verb=2.0)),
    ('village', 'गाँव / लोक', '🌾', (96, 118), ['khamaj', 'bilawal', 'kafi', 'bhupali', 'bhairav'],
     dict(drone='tanpura', lead='sitar', flute=True, tabla='fast', verb=1.0)),
]
MOOD_INFO = {m[0]: m for m in MOODS}
PER_MOOD = 50

PROGRESSIONS = [[0, 5, 3, 4], [0, 3, 4, 0], [0, 4, 5, 3], [0, 5, 2, 4],
                [0, 6, 5, 4], [0, 3, 5, 4], [0, 2, 3, 4], [5, 3, 0, 4]]


# ------------------------------------------------------------------ catalogue
def tune_meta(mood, idx, scale_key, bpm):
    m = MOOD_INFO[mood]
    return {'id': f'{mood}-{idx + 1:02d}', 'mood': mood, 'mood_name': m[1], 'emoji': m[2],
            'name': f'{m[1]} {idx + 1:02d}', 'raag': SCALES[scale_key][0], 'bpm': bpm}


def _params(tune_id):
    mood, num = tune_id.rsplit('-', 1)
    idx = int(num) - 1
    if mood not in MOOD_INFO or not 0 <= idx < PER_MOOD:
        raise KeyError(tune_id)
    m = MOOD_INFO[mood]
    scales = m[4]
    scale_key = scales[idx % len(scales)]
    lo, hi = m[3]
    bpm = int(round(lo + (hi - lo) * ((idx * 7) % 10) / 9))
    seed = int(hashlib.md5(tune_id.encode()).hexdigest()[:8], 16)
    return mood, idx, scale_key, bpm, seed


@lru_cache(maxsize=1)
def catalogue():
    out = []
    for m in MOODS:
        for i in range(PER_MOOD):
            _, _, sk, bpm, _ = _params(f'{m[0]}-{i + 1:02d}')
            out.append(tune_meta(m[0], i, sk, bpm))
    return out


def exists(tune_id):
    try:
        _params(tune_id)
        return True
    except (KeyError, ValueError):
        return False


def moods():
    return [{'key': m[0], 'name': m[1], 'emoji': m[2]} for m in MOODS]


# ------------------------------------------------------------------ synthesis
def _hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def _env(n, a, r, sr=SR):
    e = np.ones(n)
    na, nr = min(n, max(1, int(a * sr))), min(n, max(1, int(r * sr)))
    e[:na] = np.linspace(0, 1, na)
    e[n - nr:] *= np.linspace(1, 0, nr)
    return e


def _place(buf, start, sig):
    """Add sig into circular buffer (wraps around => seamless loop)."""
    n = len(buf)
    start %= n
    end = start + len(sig)
    if end <= n:
        buf[start:end] += sig
    else:
        k = n - start
        buf[start:] += sig[:k]
        rest = sig[k:]
        while len(rest):
            m = min(n, len(rest))
            buf[:m] += rest[:m]
            rest = rest[m:]


def _additive(freq, dur, partials, decay=None, vib=0.0, rng=None):
    n = int(dur * SR)
    t = np.arange(n) / SR
    phase_mod = vib * np.sin(2 * np.pi * 5.2 * t) if vib else 0.0
    out = np.zeros(n)
    for k, amp in partials:
        f = freq * k
        if f > SR / 2.2:
            continue
        p = np.sin(2 * np.pi * f * t + k * phase_mod + (rng.uniform(0, 6.28) if rng is not None else 0))
        if decay:
            p *= np.exp(-t * decay * (0.6 + 0.4 * k))
        out += amp * p
    return out


def pluck(freq, dur, bright=1.0):
    parts = [(k, bright ** (k - 1) / k) for k in range(1, 9)]
    return _additive(freq, dur, parts, decay=3.2) * _env(int(dur * SR), 0.003, 0.05)


def sitar(freq, dur):
    n = int(dur * SR)
    t = np.arange(n) / SR
    bend = freq * (1 + 0.012 * np.exp(-t * 9))
    ph = 2 * np.pi * np.cumsum(bend) / SR
    x = sum(np.sin(k * ph) * (0.85 ** k) * np.exp(-t * (1.6 + 0.5 * k)) for k in range(1, 12) if freq * k < SR / 2.2)
    x = np.tanh(2.2 * x)  # jawari-like buzz
    return x * _env(n, 0.002, 0.08) * 0.6


def flute(freq, dur, rng):
    n = int(dur * SR)
    t = np.arange(n) / SR
    vib = 1 + 0.006 * np.sin(2 * np.pi * 5.0 * t) * np.clip(t / 0.4, 0, 1)
    ph = 2 * np.pi * np.cumsum(freq * vib) / SR
    x = np.sin(ph) + 0.18 * np.sin(2 * ph) + 0.06 * np.sin(3 * ph)
    breath = rng.standard_normal(n) * 0.04
    breath = np.convolve(breath, np.ones(6) / 6, 'same')
    return (x + breath) * _env(n, 0.09, min(0.25, dur * 0.4))


def bell(freq, dur):
    parts = [(1, 1), (2.0, .5), (2.76, .45), (5.4, .25), (8.93, .12)]
    return _additive(freq, dur, parts, decay=1.6) * _env(int(dur * SR), 0.002, 0.2)


def pad(freqs, dur, kind, rng):
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    nharm = {'dark': 4, 'soft': 5, 'bright': 8, 'big': 10}.get(kind, 5)
    for f in freqs:
        for det in (-0.004, 0.0, 0.005):
            ff = f * (1 + det)
            for k in range(1, nharm + 1):
                if ff * k > SR / 2.5:
                    break
                out += np.sin(2 * np.pi * ff * k * t + rng.uniform(0, 6.28)) * (0.55 ** (k - 1)) / k
    lfo = 0.85 + 0.15 * np.sin(2 * np.pi * 0.17 * t + rng.uniform(0, 6))
    return out * lfo * _env(n, min(0.8, dur * 0.3), min(1.0, dur * 0.35)) / (len(freqs) * 3)


def brass(freqs, dur):
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for f in freqs:
        out += sum(np.sin(2 * np.pi * f * k * t) / k ** 1.1 for k in range(1, 14) if f * k < SR / 2.5)
    swell = np.clip(t / 0.25, 0, 1)
    return np.tanh(out * 0.5) * swell * _env(n, 0.05, 0.3) / len(freqs)


def bass_note(freq, dur):
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(4 * np.pi * freq * t)
    return x * np.exp(-t * 2.2) * _env(n, 0.005, 0.08)


def kick(amp=1.0, dur=0.45):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 45 + 110 * np.exp(-t * 28)
    return amp * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7)


def heartbeat():
    a = kick(0.9, 0.3)
    b = kick(0.6, 0.35)
    out = np.zeros(int(0.65 * SR))
    out[:len(a)] += a
    out[int(0.22 * SR):int(0.22 * SR) + len(b)] += b
    return out


def noise_hit(rng, dur, decay, hp=True, amp=0.3):
    n = int(dur * SR)
    x = rng.standard_normal(n)
    if hp:
        x = np.diff(np.concatenate([[0], x]))
    return amp * x * np.exp(-np.arange(n) / SR * decay)


def tabla(kind, rng):
    n = int(0.5 * SR)
    t = np.arange(n) / SR
    if kind == 'ge':  # bayan, low with pitch glide
        f = 95 + 60 * np.exp(-t * 6)
        x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 5)
    elif kind == 'na':  # dayan, ringing
        x = sum(np.sin(2 * np.pi * 380 * k * t) * a for k, a in [(1, 1), (2, .5), (3, .3), (4.1, .2)])
        x = x * np.exp(-t * 14) + noise_hit(rng, 0.5, 60, amp=0.15)
    else:  # 'ta' / 'tin' short
        x = np.sin(2 * np.pi * 520 * t) * np.exp(-t * 30) + noise_hit(rng, 0.5, 80, amp=0.2)
    return x * 0.6


def taiko(rng):
    n = int(0.8 * SR)
    t = np.arange(n) / SR
    f = 70 + 40 * np.exp(-t * 15)
    return (np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 4.5) + noise_hit(rng, 0.8, 25, False, 0.08))


def _reverb(x, seconds, rng, wet=0.3):
    if seconds <= 0:
        return x
    n_ir = int(seconds * SR)
    ir = rng.standard_normal(n_ir) * np.exp(-np.arange(n_ir) / SR * (6.9 / seconds))
    ir = np.convolve(ir, np.ones(8) / 8, 'same')  # darken
    ir /= np.sqrt(np.sum(ir ** 2))
    n = len(x)
    if n_ir > n:
        ir = ir[:n]
    # circular convolution -> reverb tail wraps into loop start (seamless loop)
    w = np.fft.irfft(np.fft.rfft(x) * np.fft.rfft(ir, n), n)
    return x * (1 - wet) + w * wet * 1.4


# ------------------------------------------------------------------ composer
def _compose(tune_id):
    mood, idx, scale_key, bpm, seed = _params(tune_id)
    rng = np.random.default_rng(seed)
    flags = MOOD_INFO[mood][5]
    steps = SCALES[scale_key][1]
    root = 48 + (idx * 5) % 7 - 3   # C3 +/- a few semitones
    beat = 60.0 / bpm
    bars = 8
    n = int(bars * 4 * beat * SR)
    mix = {k: np.zeros(n) for k in ('pad', 'lead', 'perc', 'low', 'fx')}

    def deg(d, octave=0):
        o, i = divmod(d, len(steps))
        return root + steps[i] + 12 * (o + octave)

    prog = PROGRESSIONS[rng.integers(len(PROGRESSIONS))]
    if mood in ('horror', 'calm'):
        prog = [0, 0, prog[1], 0]
    chords = [prog[b % 4] for b in range(bars)]
    bar_len = 4 * beat

    # --- pads / brass / bass / drone
    for b, c in enumerate(chords):
        start = int(b * bar_len * SR)
        tones = [deg(c, 1), deg(c + 2, 1), deg(c + 4, 1)]
        if mood == 'horror':
            tones = [deg(c, 0), deg(c, 0) + 6, deg(c + 1, 1)]  # tritone + semitone tension
        if flags.get('pad'):
            _place(mix['pad'], start, pad([_hz(t) for t in tones], bar_len * 1.25, flags['pad'], rng) * 0.9)
        if flags.get('brass') and b % 2 == 0:
            _place(mix['pad'], start, brass([_hz(t - 12) for t in tones[:2]], bar_len * 1.8) * 0.35)
        if flags.get('bass'):
            for q in range(4 if mood != 'romantic' else 2):
                if q % 2 and rng.random() < 0.4:
                    continue
                f = _hz(deg(c, -1) if q != 2 else deg(c + 4, -1))
                _place(mix['low'], start + int(q * beat * (4 / (4 if mood != 'romantic' else 2)) * SR),
                       bass_note(f, beat * 1.2) * 0.55)

    drone = flags.get('drone')
    if drone == 'tanpura':
        pattern = [deg(4, -1), deg(0, 0), deg(0, 0), deg(0, -1)]
        if steps[4] != 7:
            pattern[0] = root - 12 + 5  # use Ma when Pa is absent
        t_step = max(beat, 0.9)
        k = 0
        pos = 0.0
        while pos < n / SR:
            _place(mix['low'], int(pos * SR), pluck(_hz(pattern[k % 4]), 3.5, bright=0.92) * 0.22)
            pos += t_step
            k += 1
    elif drone == 'dark':
        t = np.arange(n) / SR
        f0 = _hz(root - 12)
        d = (np.sin(2 * np.pi * f0 * t) + 0.5 * np.sin(2 * np.pi * f0 * 1.5 * t + 1)
             + 0.35 * np.sin(2 * np.pi * f0 * 1.414 * t)) * (0.7 + 0.3 * np.sin(2 * np.pi * t / (n / SR) * 2))
        mix['low'] += d * 0.18

    # --- melody (phrase A A' B A over 8 bars)
    def phrase(length_beats, density):
        notes, pos, d = [], 0.0, int(rng.integers(0, 5))
        while pos < length_beats:
            durs = [0.5, 1, 1, 1.5, 2] if density == 'fast' else [1, 1, 2, 2, 3] if density == 'slow' else [0.5, 1, 1, 2]
            du = float(rng.choice(durs))
            du = min(du, length_beats - pos)
            if rng.random() > 0.12:
                notes.append((pos, du, d))
            d = int(np.clip(d + rng.choice([-2, -1, -1, 0, 1, 1, 2]), -2, 9))
            pos += du
        return notes

    lead = flags.get('lead') or ('flute' if flags.get('flute') else None)
    if lead:
        dens = 'fast' if mood in ('happy', 'village') else 'slow' if mood in ('sad', 'devotional', 'calm') else 'mid'
        A = phrase(8, dens)
        B = phrase(8, dens)
        A2 = [(p, d, x + (1 if i == len(A) - 1 else 0)) for i, (p, d, x) in enumerate(A)]
        for sec, ph in enumerate([A, A2, B, A]):
            for p, du, d in ph:
                start = int((sec * 8 + p) * beat * SR)
                f = _hz(deg(d, 2 if lead != 'sitar' else 1))
                if lead == 'pluck':
                    sig = pluck(f, du * beat + 0.4, bright=0.8) * 0.32
                elif lead == 'sitar':
                    sig = sitar(f, du * beat + 0.6) * 0.42
                else:
                    sig = flute(f, du * beat * 0.95, rng) * 0.3
                _place(mix['lead'], start, sig)
        if flags.get('flute') and lead != 'flute':  # answering flute in second half
            for p, du, d in phrase(8, 'slow'):
                _place(mix['lead'], int((16 + p) * beat * SR), flute(_hz(deg(d, 2)), du * beat * 0.95, rng) * 0.22)

    arp = flags.get('arp')
    if arp:
        sub = {'slow': 1.0, 'mid': 0.5, 'fast': 0.5}[arp]
        pat = [0, 2, 4, 2, 0, 4, 2, 4] if arp != 'fast' else [0, 2, 4, 7, 4, 2, 4, 2]
        k = 0
        for b, c in enumerate(chords):
            pos = 0.0
            while pos < 4:
                f = _hz(deg(c + pat[k % len(pat)], 1))
                _place(mix['lead'], int((b * 4 + pos) * beat * SR), pluck(f, 1.2, bright=0.6) * 0.18)
                pos += sub
                k += 1

    if flags.get('ostinato'):
        for b, c in enumerate(chords):
            for e in range(8):
                f = _hz(deg(c if e % 4 != 3 else c + 1, 0))
                sig = pluck(f, beat * 0.6, bright=0.5) * (0.28 if e % 2 == 0 else 0.18)
                _place(mix['low'], int((b * 4 + e * 0.5) * beat * SR), sig)

    # --- bells / fx
    bells = flags.get('bells')
    if bells:
        count = {'random': 5, 'soft': 4, 'temple': 4}[bells]
        for _ in range(count):
            pos = rng.uniform(0, bars * 4)
            if bells == 'temple':
                pos = float(int(pos / 4) * 4)
            f = _hz(deg(int(rng.integers(0, 7)), 3 if bells != 'temple' else 2))
            if bells == 'random' and mood == 'horror':
                f *= 1.03  # slightly detuned, eerie
            _place(mix['fx'], int(pos * beat * SR), bell(f, 4.0) * (0.12 if bells != 'temple' else 0.2))
    if flags.get('swell'):
        for _ in range(2):
            L = int(rng.uniform(3, 6) * SR)
            x = rng.standard_normal(L)
            x = np.convolve(x, np.ones(20) / 20, 'same')
            env = np.sin(np.linspace(0, np.pi, L)) ** 2
            _place(mix['fx'], int(rng.uniform(0, n)), x * env * 0.5)
    if flags.get('heart'):
        for b in range(0, bars, 2):
            _place(mix['perc'], int(b * bar_len * SR), heartbeat() * 0.5)

    # --- drums
    drums, tab = flags.get('drums'), flags.get('tabla')
    for b in range(bars):
        for s in range(16):  # 16th grid
            at = int((b * 4 + s / 4) * beat * SR)
            if drums == 'pop':
                if s in (0, 8) or (s == 10 and rng.random() < 0.5):
                    _place(mix['perc'], at, kick(0.8))
                if s in (4, 12):
                    _place(mix['perc'], at, noise_hit(rng, 0.25, 22, amp=0.28) + np.pad(kick(0.15, 0.1), (0, int(0.15 * SR))))
                if s % 2 == 0:
                    _place(mix['perc'], at, noise_hit(rng, 0.06, 90, amp=0.08 if s % 4 else 0.12))
            if flags.get('softkick') and s in (0, 8):
                _place(mix['perc'], at, kick(0.45))
            if flags.get('tick') and s % 4 == 0:
                _place(mix['perc'], at, noise_hit(rng, 0.04, 140, amp=0.1 if s % 8 else 0.14))
            if flags.get('taiko') and (s in (0, 6, 8) or (b % 2 == 1 and s in (12, 14))):
                _place(mix['perc'], at, taiko(rng) * 0.7)
            if tab:
                # simple keherwa-like (8 beats over 2 bars at slow, 1 bar at fast)
                grid = 4 if tab == 'slow' else 2
                if s % grid == 0:
                    pos8 = (s // grid) % 8
                    bol = ['ge', 'na', 'ta', 'na', 'ta', 'na', 'ge', 'na'][pos8]
                    _place(mix['perc'], at, tabla(bol, rng) * (0.9 if bol == 'ge' else 0.7))

    # --- mixdown
    levels = {'pad': 0.9, 'lead': 1.0, 'perc': 0.85, 'low': 0.9, 'fx': 0.9}
    out = np.zeros(n)
    for k, v in mix.items():
        out += v * levels[k]
    dry_perc = mix['perc'] * levels['perc']
    out = _reverb(out - dry_perc, flags.get('verb', 1.2), rng, wet=0.32) + _reverb(dry_perc, 0.6, rng, wet=0.12)
    # gentle low-cut + loudness normalise
    spec = np.fft.rfft(out)
    f = np.fft.rfftfreq(n, 1 / SR)
    spec *= 1 / (1 + (35 / np.maximum(f, 1)) ** 4)
    out = np.fft.irfft(spec, n)
    rms = np.sqrt(np.mean(out ** 2)) or 1.0
    out = out / rms * 0.22
    out = np.tanh(out * 1.6) / 1.6
    return out.astype(np.float32)


@lru_cache(maxsize=6)
def loop(tune_id):
    """One seamless loop (float32 mono, SR) of the given tune."""
    return _compose(tune_id)


def preview(tune_id, seconds=30.0):
    """Loop repeated to `seconds`, with fade-in/out, for listening/downloading."""
    x = loop(tune_id)
    reps = int(np.ceil(seconds * SR / len(x)))
    y = np.tile(x, reps)[:int(seconds * SR)].copy()
    fi, fo = int(0.8 * SR), int(2.5 * SR)
    y[:fi] *= np.linspace(0, 1, fi)
    y[-fo:] *= np.linspace(1, 0, fo)
    return y


def wav_bytes(samples, sr=SR):
    import io
    import wave
    pcm = (np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes()
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return buf.getvalue()
