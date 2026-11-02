"""Phonon TTS on Linux through its official local runtime (``ptts``), shaped for PR288's Speaker.

PR288 (rose/speaker.py) speaks with ``gradium/phonon-31m-en`` through mlx-audio and drives the model object with
five calls: ``_resolve_audio_prompt(path, None, False)`` and ``get_state_for_audio_prompt(...)`` (a voice),
``_get_flow_cache_num_frames(state)`` and ``_slice_flow_cache(state, base)`` (cut the cache back to the voice prompt
before every chunk), and ``generate_audio_stream(state, text)`` (24 kHz audio frames, evaluated with ``mx.eval``).
``PhononProvider`` answers those calls with the documented ``ptts`` Python API (README of ``ptts-pyo3``):

- ``ptts.TTS(<local model dir>, lang=..., quant=...)``: a local directory downloads nothing;
- ``tts.add_voice(name, <voice .safetensors>)``: only voices added this way are used, so a catalog lookup (which
  would download) never happens;
- ``with tts.stream(text, voice=name) as audio: for chunk in audio``: float32 chunks while generating; leaving the
  block stops the stream and waits for its workers; its buffers are bounded.

Each chunk is a fresh ``stream`` call, so text cannot leak between chunks and ``_slice_flow_cache`` has nothing to
do. The stream runs on a worker thread (pump.Pump); the speaker's runtime job waits for each chunk with
coop.Waiter, so listening keeps running while synthesis is pending, and closing the frames (a stop, a cancelled
utterance) stops the stream before anything else is handed out.

Inputs are explicit local files, checked against the pins below before ``ptts`` sees them. ``lang`` defaults to
``none`` (text as written): PR288's speaker already normalizes each chunk with ``textnorm.speakable``. Not verified
here: the ``ptts`` package itself (0.6.0 pinned in manifest.json, not installed in this environment), its chunk
size and timing, and that its audio matches mlx-audio's for the same weights and voice. The quant choice is resolved
from ptts 0.6.0's source: ``f32`` (its default) loads ``model.safetensors``.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from .coop import Waiter
from .pump import END, Pump

RATE = 24000
REPO = ('gradium/phonon-31m-en', '92741c5b07d085ff840e26fea8ce43110b87852f')
VOICE_REPO = ('gradium/phonon-voices', '0ee17205f254b5abc43b266c8a0f215565c352f7')
# sha256 of the LFS weights, and git blob sha1 of the small text files, at REPO's revision (Hub metadata).
MODEL_FILES = {
    'model.safetensors': ('sha256', '8a04cf833cfe69b67c2ec968b2d31244655b9accc84ade9c0ebcc176f2074607', 102823920),
    'model.q8.gguf': ('sha256', 'a7d9aad2cd6dc66473f24a08bc6d2939030ef4d751ef82349361fbef3272dbae', 64512352),
    'model.q4k.gguf': ('sha256', 'b89e867e995b45835cbe30564fe360178fb39f1fe457c13e1e559c2e4260e4f3', 55075168),
    'config.json': ('gitsha1', '11cfeb2513febe31358a8ff942093e0b2c28710a', 4634),
    'tokenizer.json': ('gitsha1', '70d39980cb2154bd364dbed8424b1343923a03e2', 277967),
}
# ptts quant names (ptts 0.6.0 available_quants) and the weights file each loads first (checkpoint.rs
# weight_candidates). f32 is the default (quant=None is Quant::F32) and the full-precision file mlx-audio uses.
WEIGHTS = {'f32': 'model.safetensors', 'q8_0': 'model.q8.gguf', 'q4k': 'model.q4k.gguf'}
PTTS = ('ptts', '0.6.0')  # PyPI; wheel pinned in manifest.json
# PR288 rose/speaker.py VOICES, with each voice file's sha256 at VOICE_REPO's revision.
VOICES = {
    'zoey': ('NbpkqMVS3CJeq2j8', '97c7f8c253413574406ab80dd23f49c3ebb2517163b00a9bf86b9c4cf43e7e23'),
    'harper': ('4SZHfMpw-p46Ywgs', '6b3da6e2d796a20c51af892c482bd6509fb88267a221aff83c870843dee2a123'),
    'everly': ('vjFyp2BcCyTlUmYb', '958ea94da14f46dd48c7f64adac11ed2ad2aa45f7ccd165dfebab5272664402e'),
    'juniper': ('NIWIOv5vTi5eRMj8', '444378eb06b7c492b86e8fc7d13e4db1a3c0cf3e68891a960bf6f0a41a13ef35'),
    'hadley': ('bXwrkHhURL1VFJdG', 'a879938aa9a2df0a8220bdadef88137929f71e887bd91e7db205bd5fa11661d2'),
    'delia': ('nGzMHMPAWGaJFHOo', 'a0c3dcb91b2247eca56536be9ae8e8becc1c807b1136c5fe101552d6a155b24a'),
    'easton': ('LMDIYE0Oa377Qh6Z', '45c582673cb20edb04ddcca2b5a8865ddfb0a82599efadb269021e87d20fbdd4'),
    'micah': ('40QrMVgAxFiUnP9f', '0de1098d1051686a138d068e83ff09deb5053328bc74ec9541a1fdb90bfc24da'),
    'miles': ('d9XCyhjo4JhkHF1d', 'eab20e4f159b48cfbc01b1885804e24f681f80e7a64f909182763a4e3361bee8'),
}


class MissingInput(FileNotFoundError):
    """A required local file is absent or is not the pinned one. The message names the file, never its contents."""


def file_digest(path: Path, kind: str) -> str:
    h = hashlib.sha256() if kind == 'sha256' else hashlib.sha1()
    if kind == 'gitsha1':
        h.update(b'blob %d\0' % path.stat().st_size)
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def check_inputs(model_dir: str | os.PathLike, voice_files: dict[str, str | os.PathLike],
                 quant: str = 'f32') -> dict:
    """Every missing or mismatched input, by name: {'missing': [...], 'mismatched': [...]} (empty lists if ready)."""
    if quant not in WEIGHTS:
        raise ValueError(f'unsupported quant {quant!r}; pinned: {", ".join(WEIGHTS)}')
    model_dir = Path(model_dir)
    missing, mismatched = [], []
    needed = ['config.json', 'tokenizer.json', WEIGHTS[quant]]
    for name in needed:
        path = model_dir / name
        kind, digest, size = MODEL_FILES[name]
        if not path.is_file():
            missing.append(f'{REPO[0]}@{REPO[1][:12]}/{name}')
        elif path.stat().st_size != size or file_digest(path, kind) != digest:
            mismatched.append(name)
    for voice, path in voice_files.items():
        if voice not in VOICES:
            raise ValueError(f'unknown voice {voice!r}; PR288 voices: {", ".join(VOICES)}')
        voice_id, digest = VOICES[voice]
        path = Path(path)
        if not path.is_file():
            missing.append(f'{VOICE_REPO[0]}@{VOICE_REPO[1][:12]}/voices/{voice_id}.safetensors')
        elif file_digest(path, 'sha256') != digest:
            mismatched.append(f'voice {voice}')
    return {'missing': missing, 'mismatched': mismatched}


@dataclass
class VoiceState:
    name: str


class PhononProvider:
    """The model object PR288's Speaker expects, backed by ``ptts`` on local files.

    ``tts_factory`` builds the ptts engine; the default imports ``ptts``. Tests pass a stand-in engine with the same
    three calls (``add_voice``, ``stream``, ``sample_rate``) and say so."""

    def __init__(self, model_dir: str | os.PathLike, voice_files: dict[str, str | os.PathLike], *,
                 runtime: Any = None, lang: str = 'none', quant: str = 'f32',
                 tts_factory: Callable[..., Any] | None = None, verify: bool = True, max_chunks: int = 32):
        if verify:
            problems = check_inputs(model_dir, voice_files, quant)
            if problems['missing'] or problems['mismatched']:
                raise MissingInput('Phonon inputs not ready: ' + '; '.join(
                    [f'missing {m}' for m in problems['missing']] +
                    [f'not the pinned file: {m}' for m in problems['mismatched']]))
        if tts_factory is None:
            import ptts  # noqa: F401  (the official runtime; not installed here)

            def tts_factory(model, lang, quant):
                return ptts.TTS(str(model), device='cpu', quant=quant, lang=lang)
        self.tts = tts_factory(Path(model_dir), lang, quant)
        rate = getattr(self.tts, 'sample_rate', None)
        if rate != RATE:
            raise ValueError(f'Phonon sample rate is {rate}, not {RATE}')
        self.voice_files = {name: str(path) for name, path in voice_files.items()}
        self._added: set[str] = set()
        self.waiter = Waiter(runtime)
        self.max_chunks = max_chunks
        self.streams_started = 0
        self.workers_left = 0  # worker threads that did not finish within the join bound

    # ------------------------------------------------ PR288 Speaker's model calls
    def _resolve_audio_prompt(self, path: str, *_: Any) -> str:
        name = next((n for n, p in self.voice_files.items() if os.path.samefile(p, path)), None)
        if name is None:
            raise MissingInput('voice file was not given to PhononProvider')
        return name

    def get_state_for_audio_prompt(self, name: str) -> VoiceState:
        if name not in self._added:
            self.tts.add_voice(name, self.voice_files[name])
            self._added.add(name)
        return VoiceState(name)

    def _get_flow_cache_num_frames(self, state: VoiceState) -> int:
        return 0

    def _slice_flow_cache(self, state: VoiceState, base: int) -> None:
        return None  # every chunk is its own stream

    def generate_audio_stream(self, state: VoiceState, text: str) -> Iterator[Any]:
        if state.name not in self._added:
            raise MissingInput(f'voice {state.name!r} was not added')

        def produce(pump: Pump) -> None:
            with self.tts.stream(text, voice=state.name) as audio:
                for chunk in audio:
                    if pump.stopped.is_set() or not pump.put(chunk):
                        return  # leaving the block stops the stream and waits for its workers

        import numpy as np

        self.streams_started += 1
        pump = Pump(self.waiter, produce, maxsize=self.max_chunks, name='phonon-stream')
        try:
            while True:
                ok, chunk = pump.get()
                if not ok or chunk is END:
                    return
                yield np.asarray(chunk, dtype=np.float32).reshape(-1)
        finally:
            if not pump.close():
                self.workers_left += 1


def voice_path(voice_files: dict[str, str], voice: str) -> str:
    """The explicit file for ``voice``: PR288's ``_voice_file`` would look up, and possibly download, its own."""
    if voice not in voice_files:
        raise MissingInput(f'no local file given for voice {voice!r}')
    return str(voice_files[voice])
