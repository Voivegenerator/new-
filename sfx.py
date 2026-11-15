"""Procedural sound effects (no external audio files, no paid API).

Every effect is synthesised with numpy. Each generator returns a float32
mono array in the range [-1, 1] at the requested sample rate.
"""
import re
import unicodedata

import numpy as np

_rng = np.random.default_rng()


# ---------------------------------------------------------------- helpers
def _t(n, sr):
    return np.arange(n) / sr


def _noise(n):
    return _rng.standard_normal(n)


def _norm(x, peak=1.0):
    m = np.max(np.abs(x)) if len(x) else 0
    return (x / m * peak).astype(np.float32) if m > 0 else x.astype(np.float32)


def _fftconv(a, b):
    n = len(a) + len(b) - 1
    size = 1 << (n - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(a, size) * np.fft.rfft(b, size), size)[:n]


def _band(x, sr, lo=0.0, hi=None, tilt=0.0):
    """Smooth FFT band-pass. tilt>0 gives a pink/brown slope."""
    hi = hi or sr / 2
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / sr)
    h = np.ones_like(f)
    if lo > 0:
        h *= 1 / (1 + (lo / np.maximum(f, 1e-3)) ** 4)
    if hi < sr / 2:
        h *= 1 / (1 + (f / hi) ** 4)
    if tilt:
        h *= (np.maximum(f, 20) / 1000.0) ** (-tilt / 2)
    return np.fft.irfft(spec * h, len(x))


def _tv_band(x, sr, centers, width=0.7, frame=2048):
    """Time-varying band-pass via overlap-add. centers: array (Hz) per sample."""
    hop = frame // 2
    win = np.hanning(frame)
    out = np.zeros(len(x) + frame)
    xp = np.concatenate([x, np.zeros(frame)])
    f = np.fft.rfftfreq(frame, 1 / sr)
    for start in range(0, len(x), hop):
        c = centers[min(start + hop, len(centers) - 1)]
        seg = xp[start:start + frame] * win
        oct_dist = np.log2(np.maximum(f, 1) / c)
        h = np.exp(-0.5 * (oct_dist / width) ** 2)
        out[start:start + frame] += np.fft.irfft(np.fft.rfft(seg) * h, frame)
    return out[:len(x)]


def _smooth_random(n, sr, step, lo=0.0, hi=1.0):
    """Slowly wandering random curve (e.g. wind gusts)."""
    pts = int(n / (sr * step)) + 3
    vals = _rng.uniform(lo, hi, pts)
    xs = np.linspace(0, n, pts)
    curve = np.interp(np.arange(n), xs, vals)
    k = max(1, int(sr * step / 2))
    for _ in range(2):  # two O(n) box blurs ~ smooth bell-shaped kernel
        c = np.cumsum(np.concatenate([np.full(k, curve[0]), curve, np.full(k, curve[-1])]))
        curve = (c[2 * k:] - c[:-2 * k])[:n] / (2 * k)
        if len(curve) < n:
            curve = np.pad(curve, (0, n - len(curve)), mode='edge')
    return curve


def _fade(x, sr, fin=0.01, fout=0.05):
    x = x.copy()
    a, b = int(fin * sr), int(fout * sr)
    if a:
        x[:a] *= np.linspace(0, 1, a)
    if b:
        x[-b:] *= np.linspace(1, 0, b)
    return x


def _reverb(x, sr, decay=1.0, mix=0.25, bright=4000):
    n = int(decay * sr)
    ir = _band(_noise(n), sr, 80, bright) * np.exp(-6.9 * _t(n, sr) / decay)
    ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
    wet = _fftconv(x, ir)
    dry = np.concatenate([x, np.zeros(len(wet) - len(x))])
    return dry * (1 - mix) + _norm(wet, np.max(np.abs(x)) or 1) * mix


def _modes(sr, dur, freqs, decays, amps=None):
    """Sum of exponentially decaying sines (struck object impulse response)."""
    n = int(dur * sr)
    t = _t(n, sr)
    amps = amps or [1.0] * len(freqs)
    y = np.zeros(n)
    for f, d, a in zip(freqs, decays, amps):
        if f < sr / 2:
            y += a * np.sin(2 * np.pi * f * t + _rng.uniform(0, 6.28)) * np.exp(-t / d)
    return y


def _place(dst, src, at):
    at = int(at)
    if at >= len(dst):
        return
    end = min(len(dst), at + len(src))
    dst[at:end] += src[:end - at]


# ---------------------------------------------------------------- effects
def door_creak(sr, dur=None):
    dur = dur or 2.2
    n = int(dur * sr)
    t = _t(n, sr)
    # stick-slip pulse rate wanders like a slowly swinging hinge
    rate = 18 + 55 * np.sin(np.pi * t / dur) ** 1.5 + _smooth_random(n, sr, 0.12, -8, 8)
    phase = np.cumsum(rate / sr)
    pulses = np.zeros(n)
    idx = np.nonzero(np.diff(np.floor(phase)) > 0)[0]
    pulses[idx] = _rng.uniform(0.6, 1.0, len(idx))
    ir = _modes(sr, 0.05, [620, 1040, 1630, 2380, 3150], [0.012, 0.009, 0.007, 0.005, 0.004],
                [1, 0.8, 0.6, 0.35, 0.2])
    y = _fftconv(pulses, ir)[:n]
    y += 0.03 * _band(_noise(n), sr, 600, 3000)
    y *= np.sin(np.pi * t / dur) ** 0.5
    latch = _modes(sr, 0.12, [1900, 3400, 5200], [0.02, 0.015, 0.01])
    _place(y, 1.5 * latch, int(0.05 * sr))
    return _norm(_reverb(_fade(y, sr), sr, 0.9, 0.25))


def door_knock(sr, dur=None):
    gaps = [0, 0.24, 0.48, 1.25, 1.49, 1.73]
    y = np.zeros(int((gaps[-1] + 0.8) * sr))
    for g in gaps:
        k = _modes(sr, 0.35, [110, 190, 340, 520, 880], [0.06, 0.05, 0.035, 0.025, 0.012],
                   [1, 0.9, 0.6, 0.35, 0.2])
        click = _band(_noise(int(0.01 * sr)), sr, 800, 4000) * 0.4
        k[:len(click)] += click
        _place(y, k * _rng.uniform(0.8, 1.0), g * sr)
    return _norm(_reverb(y, sr, 0.8, 0.22))


def door_slam(sr, dur=None):
    n = int(2.4 * sr)
    t = _t(n, sr)
    boom = np.sin(2 * np.pi * np.cumsum(70 * np.exp(-t * 3) + 45) / sr) * np.exp(-t / 0.18)
    bang = _band(_noise(n), sr, 60, 2500) * np.exp(-t / 0.06)
    rattle = _modes(sr, 2.4, [240, 410, 730, 1210], [0.15, 0.1, 0.07, 0.05], [0.4, 0.3, 0.2, 0.1])
    y = boom * 1.0 + bang * 0.8 + rattle * 0.5
    return _norm(_reverb(_fade(y, sr, 0.001, 0.3), sr, 1.6, 0.35))


def footsteps(sr, dur=None):
    dur = dur or 4.0
    y = np.zeros(int((dur + 0.5) * sr))
    step, at = 0.55, 0.05
    while at < dur:
        n = int(0.25 * sr)
        tt = _t(n, sr)
        heel = _band(_noise(n), sr, 70, 900) * np.exp(-tt / 0.03)
        toe = _band(_noise(n), sr, 150, 2200) * np.exp(-tt / 0.02) * 0.5
        s = heel.copy()
        _place(s, toe, int(0.045 * sr))
        _place(y, s * _rng.uniform(0.65, 1.0), at * sr)
        at += step * _rng.uniform(0.92, 1.08)
    return _norm(_reverb(y, sr, 0.9, 0.3))


def glass_break(sr, dur=None):
    n = int(2.2 * sr)
    t = _t(n, sr)
    y = _band(_noise(n), sr, 2500, None) * np.exp(-t / 0.08)
    y += 0.6 * _band(_noise(n), sr, 400, 3000) * np.exp(-t / 0.03)
    for _ in range(70):
        at = abs(_rng.normal(0, 0.35))
        f = _rng.uniform(2800, 9500)
        tink = _modes(sr, 0.3, [f, f * 1.47], [_rng.uniform(0.03, 0.12), 0.03], [1, 0.4])
        _place(y, tink * _rng.uniform(0.05, 0.35) * np.exp(-at * 2), at * sr)
    return _norm(_reverb(y, sr, 1.0, 0.25, bright=8000))


def temple_bell(sr, dur=None):
    f0 = 520
    ratios = [0.5, 1, 1.183, 1.506, 2, 2.514, 2.662, 3.011, 4.166]
    decays = [3.5, 3.0, 2.5, 2.0, 1.6, 1.2, 1.0, 0.8, 0.5]
    amps = [0.5, 1, 0.6, 0.5, 0.4, 0.3, 0.25, 0.2, 0.12]
    y = _modes(sr, 4.5, [f0 * r for r in ratios], decays, amps)
    y[:int(0.004 * sr)] += _noise(int(0.004 * sr)) * 0.5
    return _norm(_reverb(_fade(y, sr, 0.001, 0.5), sr, 1.5, 0.2))


def gunshot(sr, dur=None):
    n = int(3.0 * sr)
    t = _t(n, sr)
    crack = _noise(n) * np.exp(-t / 0.012)
    body = _band(_noise(n), sr, 40, 1500) * np.exp(-t / 0.09)
    boom = np.sin(2 * np.pi * 48 * t) * np.exp(-t / 0.25)
    y = crack * 0.9 + body + boom * 0.8
    return _norm(_reverb(y, sr, 2.4, 0.4, bright=2500))


def thunder(sr, dur=None):
    dur = dur or 6.0
    n = int(dur * sr)
    t = _t(n, sr)
    crack = _band(_noise(n), sr, 300, None) * np.exp(-t / 0.05)
    rumble = _band(_noise(n), sr, 25, 220, tilt=1.0)
    env = _smooth_random(n, sr, 0.25, 0.2, 1.0) ** 2 * np.exp(-t / (dur * 0.35))
    env *= np.minimum(1, t / 0.15)
    y = crack * 0.7 + _norm(rumble) * env * 1.4
    return _norm(_reverb(_fade(y, sr, 0.001, 1.0), sr, 2.0, 0.3, bright=1500))


def whoosh(sr, dur=None):
    dur = dur or 1.4
    n = int(dur * sr)
    t = _t(n, sr)
    centers = 250 * (16 ** (np.sin(np.pi * t / dur) ** 2))
    y = _tv_band(_noise(n), sr, centers, 0.6)
    y *= np.sin(np.pi * t / dur) ** 2
    return _norm(y)


def owl(sr, dur=None):
    y = np.zeros(int(3.2 * sr))
    for at, ln in [(0.1, 0.35), (0.65, 0.25), (0.95, 0.9)]:
        n = int(ln * sr)
        t = _t(n, sr)
        f = 390 - 25 * t / ln + 4 * np.sin(2 * np.pi * 6 * t)
        ph = 2 * np.pi * np.cumsum(f) / sr
        env = np.sin(np.pi * t / ln) ** 0.8
        hoot = (np.sin(ph) + 0.15 * np.sin(2 * ph)) * env
        hoot += 0.05 * _band(_noise(n), sr, 300, 1500) * env
        _place(y, hoot, at * sr)
    return _norm(_reverb(y, sr, 2.0, 0.4, bright=2000))


# ---- ambient (loop-like, length = dur)
def wind(sr, dur=None):
    dur = dur or 10.0
    n = int(dur * sr)
    gust = _smooth_random(n, sr, 0.9, 0.0, 1.0)
    bed = _band(_noise(n), sr, 80, 1200, tilt=1.0)
    whistle = _tv_band(_noise(n), sr, 350 + 900 * gust, 0.25)
    y = _norm(bed) * (0.35 + 0.65 * gust) + _norm(whistle) * (0.15 + 0.85 * gust ** 2) * 0.7
    return _norm(_fade(y, sr, 1.0, 1.5))


def rain(sr, dur=None):
    dur = dur or 10.0
    n = int(dur * sr)
    y = _norm(_band(_noise(n), sr, 400, 9000, tilt=0.6)) * 0.5
    drops = np.zeros(n)
    k = int(dur * 350)
    drops[_rng.integers(0, n, k)] = _rng.uniform(0.2, 1, k)
    ir = _modes(sr, 0.01, [3200, 5200], [0.0015, 0.001])
    y += _norm(_fftconv(drops, ir)[:n]) * 0.6
    return _norm(_fade(y, sr, 1.0, 1.5))


def crickets(sr, dur=None):
    dur = dur or 10.0
    n = int(dur * sr)
    t = _t(n, sr)
    y = _norm(_band(_noise(n), sr, 100, 1500, tilt=1.0)) * 0.08
    for freq in (4100, 4700):
        if freq >= sr / 2:
            freq = sr / 2 * 0.8
        gate = np.zeros(n)
        at = _rng.uniform(0, 0.4)
        while at < dur:
            for p in range(_rng.integers(3, 5)):
                s = int((at + p * 0.035) * sr)
                ln = int(0.022 * sr)
                if s + ln < n:
                    gate[s:s + ln] = np.hanning(ln)
            at += _rng.uniform(0.45, 0.8)
        y += np.sin(2 * np.pi * freq * t) * gate * 0.35
    return _norm(_fade(y, sr, 1.0, 1.5))


def heartbeat(sr, dur=None):
    dur = dur or 8.0
    y = np.zeros(int(dur * sr))
    at = 0.1
    while at < dur - 0.4:
        for off, f, ln, a in [(0, 52, 0.13, 1.0), (0.27, 62, 0.1, 0.7)]:
            n = int(ln * sr)
            t = _t(n, sr)
            beat = np.sin(2 * np.pi * f * t) * np.sin(np.pi * t / ln) ** 2
            _place(y, beat * a, (at + off) * sr)
        at += 0.82
    return _norm(_band(y, sr, 20, 200))


def clock_tick(sr, dur=None):
    dur = dur or 8.0
    y = np.zeros(int(dur * sr))
    at, tick = 0.05, True
    while at < dur - 0.1:
        f = [2600, 3900] if tick else [2100, 3300]
        c = _modes(sr, 0.05, f, [0.008, 0.005])
        _place(y, c * (1.0 if tick else 0.85), at * sr)
        at += 0.5
        tick = not tick
    return _norm(_reverb(y, sr, 0.4, 0.15))


def fire(sr, dur=None):
    dur = dur or 10.0
    n = int(dur * sr)
    roar = _norm(_band(_noise(n), sr, 40, 500, tilt=1.0)) * (0.5 + 0.3 * _smooth_random(n, sr, 0.5))
    crack = np.zeros(n)
    k = int(dur * 14)
    pos = _rng.integers(0, n, k)
    crack[pos] = _rng.uniform(0.1, 1, k) ** 3
    for p in pos[: k // 3]:
        burst = _rng.integers(2, 6)
        for j in range(burst):
            q = p + int(_rng.uniform(0.002, 0.03) * sr) * (j + 1)
            if q < n:
                crack[q] = _rng.uniform(0.1, 0.6)
    crack = _band(crack, sr, 1200, None)
    y = roar * 0.6 + _norm(crack) * 0.8
    return _norm(_fade(y, sr, 1.0, 1.5))


def water(sr, dur=None):
    dur = dur or 10.0
    n = int(dur * sr)
    y = _norm(_band(_noise(n), sr, 200, 3500, tilt=0.8)) * 0.4
    for _ in range(int(dur * 45)):
        ln = _rng.uniform(0.02, 0.06)
        m = int(ln * sr)
        t = _t(m, sr)
        f0 = _rng.uniform(350, 1400)
        ph = 2 * np.pi * np.cumsum(f0 * (1 + 1.5 * t / ln)) / sr
        _place(y, np.sin(ph) * np.exp(-t / (ln / 3)) * _rng.uniform(0.05, 0.3), _rng.integers(0, n))
    return _norm(_fade(y, sr, 1.0, 1.5))


def horror_drone(sr, dur=None):
    dur = dur or 10.0
    n = int(dur * sr)
    t = _t(n, sr)
    y = np.zeros(n)
    for f, a in [(55, 1), (55.4, 0.8), (77.8, 0.6), (82.9, 0.4), (110.6, 0.3)]:
        y += a * np.sin(2 * np.pi * f * t + _rng.uniform(0, 6)) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.07 * t + a))
    breath = _band(_noise(n), sr, 200, 700) * _smooth_random(n, sr, 1.5, 0.0, 1.0) ** 2
    y = _norm(y) + _norm(breath) * 0.3
    return _norm(_fade(y, sr, 2.0, 2.0))


# ---------------------------------------------------------------- catalogue
# key: (Hindi name, emoji, generator, kind, tag aliases, auto-detect patterns)
# Patterns are matched against nukta-free, NFD-normalised text.
EFFECTS = {
    'door_creak': ('दरवाज़ा खुलना (चरमराहट)', '🚪', door_creak, 'oneshot',
                   ['दरवाज़ा', 'दरवाज़ा खुलना', 'चरमराहट', 'door', 'creak'],
                   [r'(दरवाज|किवाड|फाटक|door).{0,25}(खुल|चरमरा|चर्र|open|creak)']),
    'door_knock': ('दरवाज़े पर दस्तक', '✊', door_knock, 'oneshot',
                   ['दस्तक', 'खटखट', 'knock'],
                   [r'दस्तक|खटखट|खट-खट|knock']),
    'door_slam': ('दरवाज़ा ज़ोर से बंद', '💥', door_slam, 'oneshot',
                  ['धड़ाम', 'दरवाज़ा बंद', 'slam'],
                  [r'(दरवाज|किवाड|door).{0,25}(बंद|पटक|slam|shut)', r'धडाम']),
    'footsteps': ('कदमों की आहट', '👣', footsteps, 'oneshot',
                  ['कदम', 'आहट', 'footsteps', 'steps'],
                  [r'कदमों|आहट|footstep']),
    'glass_break': ('काँच टूटना', '🪟', glass_break, 'oneshot',
                    ['काँच', 'शीशा', 'glass'],
                    [r'(कांच|काँच|शीश|glass).{0,25}(टूट|चकनाचूर|break|shatter)']),
    'temple_bell': ('मंदिर की घंटी', '🔔', temple_bell, 'oneshot',
                    ['घंटी', 'घंटा', 'bell'],
                    [r'(?<![ऀ-ॿ])घंटि?(ी|यां|यों)(?![ऀ-ॿ])|bell']),
    'gunshot': ('गोली / धमाका', '🔫', gunshot, 'oneshot',
                ['गोली', 'धमाका', 'gunshot', 'blast'],
                [r'गोली चल|बंदूक|धमाक|gunshot|blast']),
    'thunder': ('बिजली की गरज', '⛈️', thunder, 'oneshot',
                ['बिजली', 'गरज', 'thunder'],
                [r'बिजली (कडक|चमक|गिर)|बादल.{0,15}गरज|गडगडाहट|thunder']),
    'whoosh': ('तेज़ झोंका (Whoosh)', '💨', whoosh, 'oneshot',
               ['झोंका', 'whoosh', 'swoosh'],
               [r'झोंका|सर्र|whoosh|swoosh']),
    'owl': ('उल्लू की आवाज़', '🦉', owl, 'oneshot',
            ['उल्लू', 'owl'],
            [r'उल्लू|owl']),
    'wind': ('हवा की सरसराहट', '🌬️', wind, 'ambient',
             ['हवा', 'सरसराहट', 'आँधी', 'wind'],
             [r'(?<![ऀ-ॿ])हवा(एं|ओं|यें)?(?![ऀ-ॿ])|सरसरा|सनसन|आंधी|wind|breeze']),
    'rain': ('बारिश', '🌧️', rain, 'ambient',
             ['बारिश', 'बरसात', 'rain'],
             [r'बारिश|बरसात|बूंदें|rain']),
    'crickets': ('रात का सन्नाटा (झींगुर)', '🌙', crickets, 'ambient',
                 ['रात', 'झींगुर', 'सन्नाटा', 'night', 'crickets'],
                 [r'झींगुर|सन्नाटा|cricket']),
    'heartbeat': ('दिल की धड़कन', '💓', heartbeat, 'ambient',
                  ['धड़कन', 'heartbeat'],
                  [r'धडकन|धक-धक|धकधक|heartbeat']),
    'clock_tick': ('घड़ी की टिक-टिक', '🕰️', clock_tick, 'ambient',
                   ['घड़ी', 'टिकटिक', 'clock'],
                   [r'घडी की (टिक|सुई)|टिक-टिक|टिकटिक|clock']),
    'fire': ('आग की चटचटाहट', '🔥', fire, 'ambient',
             ['आग', 'अलाव', 'fire'],
             [r'(?<![ऀ-ॿ])(आग|अलाव|लपटें?|चिता)(?![ऀ-ॿ])|fire']),
    'water': ('बहता पानी / नदी', '🌊', water, 'ambient',
              ['पानी', 'नदी', 'झरना', 'water', 'river'],
              [r'नदी|झरना|बहता पानी|पानी बह|लहर|river|stream']),
    'horror_drone': ('डरावना माहौल', '👻', horror_drone, 'ambient',
                     ['डर', 'डरावना', 'भूत', 'horror'],
                     [r'भूत|आत्मा|चुडैल|साया|डरावन|horror|ghost']),
}


def _key(s):
    s = unicodedata.normalize('NFD', s.lower()).replace('़', '').replace('ँ', 'ं')
    return re.sub(r'\s+', ' ', s).strip()


_ALIASES = {}
for _k, (_name, _e, _g, _kind, _aliases, _pats) in EFFECTS.items():
    for _a in _aliases + [_k, _name]:
        _ALIASES[_key(_a)] = _k
_PATTERNS = [(k, [re.compile(_key(p)) for p in v[5]]) for k, v in EFFECTS.items()]

TAG_RE = re.compile(r'\[([^\[\]]{1,40})\]')
MAX_EFFECTS = 25


def catalogue():
    return [{'key': k, 'name': v[0], 'emoji': v[1], 'kind': v[3], 'tag': f'[{v[4][0]}]'}
            for k, v in EFFECTS.items()]


def lookup_tag(label):
    return _ALIASES.get(_key(label))


def detect_in_sentence(sentence):
    s = _key(sentence)
    for k, pats in _PATTERNS:
        if any(p.search(s) for p in pats):
            return k
    return None


def generate(key, sr=44100, dur=None):
    gen = EFFECTS[key][2]
    y = gen(sr, dur)
    y = np.nan_to_num(y)
    return _norm(y, 0.95)


def is_ambient(key):
    return EFFECTS[key][3] == 'ambient'


def parse_script(text, auto=True):
    """Split a script into [('text', str) | ('sfx', key)] items.

    Explicit tags like [हवा] are always used. With auto=True, a sentence that
    mentions a known sound gets that effect right after it (unless the sentence
    already has an explicit tag).
    """
    items, count = [], 0
    pos = 0
    pieces = []
    for m in TAG_RE.finditer(text):
        pieces.append(('text', text[pos:m.start()]))
        pieces.append(('tag', m.group(1)))
        pos = m.end()
    pieces.append(('text', text[pos:]))

    tagged_after = {}
    for i, (kind, val) in enumerate(pieces):
        if kind == 'tag' and lookup_tag(val):
            tagged_after[i - 1] = lookup_tag(val)

    for i, (kind, val) in enumerate(pieces):
        if kind == 'tag':
            key = lookup_tag(val)
            if key and count < MAX_EFFECTS:
                items.append(('sfx', key)); count += 1
            continue
        if not auto:
            if val.strip():
                items.append(('text', val))
            continue
        sentences = [x for x in re.split(r'(?<=[।.!?\n])', val) if x.strip()]
        for j, sent in enumerate(sentences):
            items.append(('text', sent))
            key = detect_in_sentence(sent)
            if key and j == len(sentences) - 1 and tagged_after.get(i) == key:
                continue  # the same effect is already tagged right after this sentence
            if key and count < MAX_EFFECTS:
                items.append(('sfx', key)); count += 1
    # merge consecutive text items
    merged = []
    for kind, val in items:
        if kind == 'text' and merged and merged[-1][0] == 'text':
            merged[-1] = ('text', merged[-1][1] + val)
        else:
            merged.append((kind, val))
    return merged


def strip_tags(text):
    return TAG_RE.sub(' ', text)
