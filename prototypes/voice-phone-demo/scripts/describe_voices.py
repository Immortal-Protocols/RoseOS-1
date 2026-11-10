#!/usr/bin/env python3
"""Measure Rose's Phonon voices from the provider's own samples (Nov 9 plan 1D).

Writes rose/voice_traits.json: for each voice in rose/speaker.py VOICES, the provider's metadata (locale and gender,
from the folder its samples are published under) and acoustic measurements of the five Phonon S (31M) samples the
provider publishes for it at a pinned revision. Every voice speaks the same five prompts, so the numbers compare
voices directly. Voice names play no part.

Measured, per voice, over all five samples:
- pitch_hz: median fundamental frequency of voiced frames (normalized autocorrelation, 60-400 Hz). Octave errors are
  folded back toward each sample's median, and a frame still more than 7 semitones from it is dropped.
- pitch_range_st: spread of that pitch, the 20th to 80th percentile in semitones (flat vs lively intonation).
- brightness_db: spectral tilt of voiced frames, energy at 1.5-5 kHz relative to 0.1-1 kHz in dB (lower reads warmer
  and darker, higher brighter and crisper).
- speech_s: total speech duration of the five prompts, leading/trailing silence trimmed (shorter is faster).

Run where huggingface.co is reachable; it needs ffmpeg and numpy only:
    python3 scripts/describe_voices.py [--cache DIR]
The output records the revision and each sample's sha256, so a rerun at the same revision must reproduce it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REPO = 'gradium/phonon-voices'
REVISION = '0ee17205f254b5abc43b266c8a0f215565c352f7'
MODEL = '31m'  # rose/speaker.py's REPO is gradium/phonon-31m-en
PROMPTS = ('exclamation_02', 'happy_01', 'question_01', 'support_01', 'support_04')
RATE = 16000
FRAME, HOP = 640, 160  # 40 ms frames every 10 ms


def voices() -> dict[str, str]:
    """rose/speaker.py's VOICES, read without importing mlx."""
    import ast
    tree = ast.parse((ROOT / 'rose/speaker.py').read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == 'VOICES' for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit('VOICES not found in rose/speaker.py')


def listing() -> list[str]:
    with urllib.request.urlopen(f'https://huggingface.co/api/models/{REPO}/revision/{REVISION}', timeout=60) as r:
        return [s['rfilename'] for s in json.load(r)['siblings']]


def fetch(path: str, cache: Path) -> Path:
    out = cache / REVISION[:12] / path
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        url = f'https://huggingface.co/{REPO}/resolve/{REVISION}/{path}'
        with urllib.request.urlopen(url, timeout=120) as r:
            out.write_bytes(r.read())
    return out


def decode(path: Path) -> np.ndarray:
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-ac', '1', '-ar', str(RATE), '-f', 'f32le', '-'],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def frames(x: np.ndarray) -> np.ndarray:
    n = 1 + max(0, (len(x) - FRAME) // HOP)
    return np.stack([x[i * HOP:i * HOP + FRAME] for i in range(n)])


def pitch(frame: np.ndarray) -> float | None:
    """F0 of one frame by normalized autocorrelation, or None when it is not clearly periodic."""
    f = frame - frame.mean()
    energy = float(np.dot(f, f))
    if energy <= 0:
        return None
    ac = np.correlate(f, f, mode='full')[len(f) - 1:] / energy
    lo, hi = int(RATE / 400), int(RATE / 60)
    lag = lo + int(np.argmax(ac[lo:hi]))
    if ac[lag] < 0.5:
        return None
    # Parabolic refinement of the peak.
    if 0 < lag < len(ac) - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        denom = a - 2 * b + c
        if denom:
            lag = lag + 0.5 * (a - c) / denom
    return RATE / lag


def octave_fixed(f0s: list[float]) -> list[float]:
    """One sample's frame pitches with halving and doubling errors folded toward its median; outliers dropped."""
    if not f0s:
        return []
    median = float(np.median(f0s))
    out = []
    for f0 in f0s:
        for candidate in (f0, f0 / 2, f0 * 2):
            if abs(12 * np.log2(candidate / median)) <= 7:
                out.append(candidate)
                break
    return out


def measure(samples: list[np.ndarray]) -> dict:
    f0s, speech = [], 0.0
    window = np.hanning(FRAME)
    freqs = np.fft.rfftfreq(FRAME, 1 / RATE)
    low, high = (freqs >= 100) & (freqs < 1000), (freqs >= 1500) & (freqs <= 5000)
    spectrum = np.zeros(len(freqs))
    for x in samples:
        sample_f0 = []
        fr = frames(x)
        rms = np.sqrt((fr ** 2).mean(axis=1))
        gate = rms > max(rms.max() * 0.05, 1e-4)
        idx = np.flatnonzero(gate)
        if len(idx):
            speech += (idx[-1] - idx[0] + 1) * HOP / RATE
        for i in idx:
            f0 = pitch(fr[i])
            if f0 is None:
                continue
            sample_f0.append(f0)
            spectrum += np.abs(np.fft.rfft(fr[i] * window)) ** 2
        f0s.extend(octave_fixed(sample_f0))
    f0s = np.array(f0s)
    st = 12 * np.log2(f0s / np.median(f0s))
    return {
        'pitch_hz': round(float(np.median(f0s)), 1),
        'pitch_range_st': round(float(np.percentile(st, 80) - np.percentile(st, 20)), 2),
        'brightness_db': round(float(10 * np.log10(spectrum[high].sum() / spectrum[low].sum())), 2),
        'speech_s': round(speech, 2),
        'voiced_frames': int(len(f0s)),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--cache', type=Path, default=Path.home() / '.cache/rose-voice-samples')
    p.add_argument('--out', type=Path, default=ROOT / 'rose/voice_traits.json')
    args = p.parse_args(argv)
    files = listing()
    out = {'source': {'repo': REPO, 'revision': REVISION, 'model': MODEL, 'prompts': list(PROMPTS),
                      'method': 'scripts/describe_voices.py'}, 'voices': {}}
    for name, voice_id in voices().items():
        folder = next((f.rsplit('/', 1)[0] for f in files
                       if f.startswith(f'samples/{MODEL}/') and f'_{voice_id}/' in f), None)
        if folder is None:
            raise SystemExit(f'no {MODEL} samples for {name} ({voice_id}) at {REVISION[:12]}')
        _, _, language, locale, gender, _ = folder.split('/')
        paths = [fetch(f'{folder}/{prompt}.ogg', args.cache) for prompt in PROMPTS]
        out['voices'][name] = {
            'voice_id': voice_id, 'language': language, 'locale': locale, 'gender': gender,
            'samples': {prompt: hashlib.sha256(path.read_bytes()).hexdigest() for prompt, path in zip(PROMPTS, paths)},
            **measure([decode(path) for path in paths]),
        }
    args.out.write_text(json.dumps(out, indent=1, sort_keys=False) + '\n')
    for name, v in out['voices'].items():
        print(f"{name:8} {v['gender']:6} {v['pitch_hz']:6.1f} Hz  range {v['pitch_range_st']:5.2f} st  "
              f"tilt {v['brightness_db']:6.2f} dB  speech {v['speech_s']:5.2f} s")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
