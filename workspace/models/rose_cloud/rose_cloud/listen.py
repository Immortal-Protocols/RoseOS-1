"""PR288's own Listener on Linux: the same four selected models through their official Linux runtimes.

PR288 (rose/listener.py) turns 16 kHz mic audio into speech events and committed turns. Its endpointing (VAD per
512-sample frame, pause after 200 ms, early final transcript 64 ms into a dip, smart-turn at the pause, silence
timers by dangling words) counts audio samples, never wall time, and that file runs here unchanged (through
nomlx). It reaches the models through four objects, which ``bind`` hands it instead of ``Listener.load`` (mlx-audio):

  vad(frame) -> probability                   PR288's own SileroCPU (numpy), built from the same mlx-community
                                               weights file, read here with numpy: identical arithmetic
  turn.predict_endpoint(audio, sample_rate)    SmartTurn: pipecat-ai's official ONNX, the exact file the MLX
                                               conversion came from (smart-turn-v3.2-gpu.onnx), with the official
                                               inference preprocessing (last 8 s, left zero pad, Whisper features)
  final.generate(audio) -> .text               Parakeet: nvidia/parakeet-tdt-0.6b-v3 through transformers
                                               AutoModelForTDT (the model card's Transformers path)
  stream.create_streaming_session(language)    Nemotron: nvidia/nemotron-3.5-asr-streaming-0.6b through
                                               transformers AutoModelForRNNT cache-aware streaming (model card)

The MLX files on the Mac are conversions of these upstream checkpoints (each mlx-community card names its base);
the Linux runtimes load the upstream files, so weights match but kernels and numerics differ (except VAD).

Waiting: smart-turn and Parakeet run inside PR288's LISTEN runtime jobs; here their work runs on a worker thread
and the job waits with coop.Waiter (nothing outranks LISTEN, so like GPU work on the Mac it holds the runtime thread
for its duration; cancelling the job, as PR288 does when speech resumes in a dip, ends the wait at once and the
result is dropped). The streaming session never blocks: ``step`` hands back whatever text the decoder thread has
produced, so PR288's streaming job keeps yielding between units.

Inputs are explicit local directories, checked against the pins below before anything loads. Nothing downloads.
"""

from __future__ import annotations

import collections
import json
import os
import queue
import struct
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from .coop import Waiter
from .pump import END, Pump

SR = 16_000
LANGUAGE = 'en'  # PR288 creates every streaming session with language="en"
STREAM_ATT_CONTEXT = [56, 0]  # PR288's choice: 80 ms chunks, no right context

# The upstream checkpoints of PR288's MLX models, with sizes and hashes at these revisions (Hub metadata).
MODELS = {
    'vad': {'repo': 'mlx-community/silero-vad', 'revision': '7bc17f22d3c0451bd3a6cd71e759b009271ff49a',
            'mac': 'mlx-community/silero-vad (the same file)', 'files': [
                ['config.json', 549, 'gitsha1', 'af83a8c743562a75af3edad522a6c94d1f885115'],
                ['model.safetensors', 2179454, 'sha256',
                 '185e0bc3ee2c48ce425a37209fe917a1aca22ab6b85799430dd1b4894087a8b8']]},
    'turn': {'repo': 'pipecat-ai/smart-turn-v3', 'revision': 'f766f81d3cfdf7737ac64aad813d91bbfd56bf93',
             'mac': 'mlx-community/smart-turn-v3 (config: source_onnx smart-turn-v3.2-gpu.onnx)', 'files': [
                 ['smart-turn-v3.2-gpu.onnx', 32411198, 'sha256',
                  'ab8dc64b88713f90b571c15b714bd1330e6c883cad8763dacf65c9376dc539be']]},
    'final': {'repo': 'nvidia/parakeet-tdt-0.6b-v3', 'revision': '541d1f99c6b0c3cd0b11a95167540bb8edefd82b',
              'mac': 'mlx-community/parakeet-tdt-0.6b-v3 (base_model)', 'files': [
                  ['config.json', 1153, 'gitsha1', '969c65410a736e4a3922f968b3481b2418aba786'],
                  ['generation_config.json', 289, 'gitsha1', 'e368b292e2ad5232aae2a3e45f4761097e5fc216'],
                  ['model.safetensors', 2508311120, 'sha256',
                   '3a2026366188c8c68598edbbff92f8d11590a08e0ae2e6775544e7b07d6a5e11'],
                  ['processor_config.json', 392, 'gitsha1', '7acffca89b7dd12e0ebcd085c7181c91cae6575c'],
                  ['tokenizer.json', 1159960, 'gitsha1', 'a10a554cf61e38108756650018d5781ae9675c1d'],
                  ['tokenizer_config.json', 290, 'gitsha1', '8a31c60ee8ce7b81b999061d3a233845cf8bd033']]},
    'stream': {'repo': 'nvidia/nemotron-3.5-asr-streaming-0.6b',
               'revision': 'ea30d66debe3740a08b573244286791d423d6b3e',
               'mac': 'mlx-community/nemotron-3.5-asr-streaming-0.6b (base_model)', 'files': [
                   ['config.json', 1376, 'gitsha1', '3ba627e56b655f0600b652b273c6d1e05fbc78e4'],
                   ['generation_config.json', 193, 'gitsha1', 'b7ca70126139d1bfe866146bb414f372903effdb'],
                   ['model.safetensors', 2552062944, 'sha256',
                    '9eebdd6590289cb3030f310858f3df93256600a800a3e8200c5993d5f967e174'],
                   ['processor_config.json', 2519, 'gitsha1', 'd430fe73aa4a2a97b2a0ea606f768b5e71765c3d'],
                   ['tokenizer.json', 752051, 'gitsha1', '425c659bba34d7116233c6e102d2948c26e114f7'],
                   ['tokenizer_config.json', 881, 'gitsha1', '33e468c971b257265e23a56ca113d2e86b363b30']]},
}
# Libraries. transformers >= 5.13.0 for Nemotron (model card); AutoModelForTDT and AutoModelForRNNT are both present in
# the released 5.22.1 (checked locally); onnxruntime 1.31.1 is PyPI's current release (Python >= 3.11).
LIBRARIES = {'transformers': '5.22.1', 'onnxruntime': '1.31.1', 'torch': 'any build matching the host (CUDA for GPU)',
             'numpy': '>=1.26'}


class MissingInput(FileNotFoundError):
    pass


class Cancelled(RuntimeError):
    """The runtime job waiting for a recognizer was cancelled (PR288 cancels the early transcript on resumed speech)."""


def check_inputs(role: str, model_dir: str | os.PathLike, hash_limit: int = 64 << 20) -> list[str]:
    """Missing or mismatched pinned files for ``role`` (empty when ready). Files up to ``hash_limit`` are hashed."""
    from .phonon import file_digest

    spec, base, problems = MODELS[role], Path(model_dir), []
    for name, size, kind, digest in spec['files']:
        path = base / name
        if not path.is_file():
            problems.append(f"missing {spec['repo']}@{spec['revision'][:12]}/{name}")
        elif path.stat().st_size != size or (size <= hash_limit and file_digest(path, kind) != digest):
            problems.append(f'not the pinned file: {name}')
    return problems


def _require(role: str, model_dir, verify: bool) -> Path:
    if verify:
        problems = check_inputs(role, model_dir)
        if problems:
            raise MissingInput(f'{role} inputs not ready: ' + '; '.join(problems))
    return Path(model_dir)


# ------------------------------------------------------------------ VAD
_DTYPES = {'F32': ('<f4', 4), 'F16': ('<f2', 2)}


def read_safetensors(path: str | os.PathLike) -> dict:
    """name -> numpy array, from a safetensors file (8-byte header length, JSON header, raw little-endian data)."""
    import numpy as np

    with open(path, 'rb') as f:
        (n,) = struct.unpack('<Q', f.read(8))
        if n > 16 << 20:
            raise ValueError('safetensors header too large')
        header = json.loads(f.read(n))
        data = f.read()
    out = {}
    for name, meta in header.items():
        if name == '__metadata__':
            continue
        if meta['dtype'] not in _DTYPES:
            raise ValueError(f'unsupported dtype {meta["dtype"]}')
        dtype, width = _DTYPES[meta['dtype']]
        start, end = meta['data_offsets']
        count = (end - start) // width
        out[name] = np.frombuffer(data, dtype=dtype, count=count, offset=start).reshape(meta['shape']).astype('<f4')
    return out


def silero_model(model_dir: str | os.PathLike, verify: bool = True) -> SimpleNamespace:
    """The object PR288's SileroCPU reads (``model.vad_16k``: config and weights), with numpy weights."""
    base = _require('vad', model_dir, verify)
    cfg = json.loads((base / 'config.json').read_text())['branch_16k']
    w = read_safetensors(base / 'model.safetensors')
    ns = SimpleNamespace

    def layer(name, bias=True):
        return ns(weight=w[f'vad_16k.{name}.weight'], **({'bias': w[f'vad_16k.{name}.bias']} if bias else {}))

    branch = ns(config=ns(**cfg), stft_conv=layer('stft_conv', bias=False),
                conv1=layer('conv1'), conv2=layer('conv2'), conv3=layer('conv3'), conv4=layer('conv4'),
                lstm=ns(Wx=w['vad_16k.lstm.Wx'], Wh=w['vad_16k.lstm.Wh'], bias=w['vad_16k.lstm.bias']),
                final_conv=layer('final_conv'))
    return ns(vad_16k=branch)


# ------------------------------------------------------------------ waiting on a worker inside a runtime job
def _run_off_thread(waiter: Waiter, fn: Callable[[], Any], name: str) -> Any:
    def produce(pump: Pump) -> None:
        pump.put(fn())

    pump = Pump(waiter, produce, maxsize=1, name=name)
    try:
        rt = waiter.runtime
        me = rt.current if rt is not None and rt.on_thread() else None
        ok, value = pump.get()
        if not ok or (me is not None and me.cancelled):
            raise Cancelled(name)
        if value is END:
            raise RuntimeError(f'{name} produced nothing')
        return value
    finally:
        pump.close(join_s=0.1)  # a model call cannot be interrupted; its result is simply dropped


# ------------------------------------------------------------------ smart-turn
class SmartTurn:
    """``predict_endpoint`` as PR288 calls it, on pipecat-ai's ONNX with the official preprocessing (inference.py,
    audio_utils.py of github.com/pipecat-ai/smart-turn): keep the last 8 s or zero-pad on the left, Whisper features
    (chunk_length 8, max_length 8 * 16000, normalized), one ONNX run, the output is already a probability."""

    def __init__(self, model_dir, *, runtime: Any = None, verify: bool = True, session: Any = None,
                 features: Any = None):
        base = _require('turn', model_dir, verify)
        if session is None:
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            so.inter_op_num_threads = 1
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            session = ort.InferenceSession(str(base / MODELS['turn']['files'][0][0]), sess_options=so)
        if features is None:
            from transformers import WhisperFeatureExtractor
            features = WhisperFeatureExtractor(chunk_length=8)
        self.session, self.features, self.waiter = session, features, Waiter(runtime)
        self.calls = 0

    def _predict(self, audio):
        import numpy as np

        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        n = 8 * SR
        audio = audio[-n:] if len(audio) > n else np.pad(audio, (n - len(audio), 0))
        inputs = self.features(audio, sampling_rate=SR, return_tensors='np', padding='max_length', max_length=n,
                               truncation=True, do_normalize=True)
        x = np.expand_dims(inputs.input_features.squeeze(0).astype(np.float32), 0)
        p = float(self.session.run(None, {'input_features': x})[0][0].item())
        return SimpleNamespace(probability=p, prediction=1 if p > 0.5 else 0)

    def predict_endpoint(self, audio, sample_rate: int = SR):
        if sample_rate != SR:
            raise ValueError('smart-turn takes 16 kHz audio')
        self.calls += 1
        return _run_off_thread(self.waiter, lambda: self._predict(audio), 'smart-turn')


# ------------------------------------------------------------------ Parakeet
class Parakeet:
    """``generate(audio).text`` as PR288 calls it, on transformers AutoModelForTDT (model card usage)."""

    def __init__(self, model_dir, *, runtime: Any = None, verify: bool = True, device: str = 'cuda',
                 transcribe: Callable[[Any], str] | None = None):
        base = _require('final', model_dir, verify)
        if transcribe is None:
            from transformers import AutoModelForTDT, AutoProcessor

            processor = AutoProcessor.from_pretrained(str(base), local_files_only=True)
            model = AutoModelForTDT.from_pretrained(str(base), local_files_only=True, dtype='auto',
                                                    device_map=device)

            def transcribe(audio):
                inputs = processor([audio], sampling_rate=SR)
                inputs.to(model.device, dtype=model.dtype)
                out = model.generate(**inputs, return_dict_in_generate=True)
                return processor.decode(out.sequences, skip_special_tokens=True)[0]
        self.transcribe, self.waiter = transcribe, Waiter(runtime)
        self.calls = 0

    def generate(self, audio):
        import numpy as np

        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        self.calls += 1
        return SimpleNamespace(text=_run_off_thread(self.waiter, lambda: self.transcribe(audio), 'parakeet'))


# ------------------------------------------------------------------ Nemotron streaming
class NemotronStreaming:
    """``create_streaming_session(language=...)`` as PR288 calls it. ``default_att_context_size`` is PR288's knob
    ([56, 0]); its right-context value is passed to the processor's ``set_num_lookahead_tokens`` (model card:
    att_context_size [left, right] in 80 ms frames; the Transformers example sets the look-ahead the same way)."""

    def __init__(self, model_dir, *, verify: bool = True, device: str = 'cuda',
                 decoder: Callable[..., Any] | None = None):
        self.base = _require('stream', model_dir, verify)
        self.device = device
        self.default_att_context_size = list(STREAM_ATT_CONTEXT)
        self._decoder = decoder  # tests inject a stand-in with the same contract as _transformers_decoder
        self.sessions = 0

    def create_streaming_session(self, language: str = LANGUAGE) -> 'StreamingSession':
        self.sessions += 1
        decoder = self._decoder or _transformers_decoder(self.base, self.device, self.default_att_context_size[1])
        return StreamingSession(decoder, language)


def _transformers_decoder(base: Path, device: str, lookahead: int):
    """decode(language, chunks, emit, stopped): runs the model card's streaming loop on ``chunks`` (an iterator of
    16 kHz float32 arrays that blocks until audio arrives and ends when the session is cancelled), calling ``emit``
    with each new piece of text. Loaded once per process."""
    cached = getattr(_transformers_decoder, 'cache', {})
    key = (str(base), device, lookahead)
    if key not in cached:
        from transformers import AutoModelForRNNT, AutoProcessor
        processor = AutoProcessor.from_pretrained(str(base), local_files_only=True)
        model = AutoModelForRNNT.from_pretrained(str(base), local_files_only=True, device_map=device)
        processor.set_num_lookahead_tokens(lookahead)
        cached[key] = (processor, model)
        _transformers_decoder.cache = cached
    processor, model = cached[key]

    def decode(language, chunks, emit, stopped):
        import numpy as np
        from transformers import TextIteratorStreamer

        audio = np.zeros(0, np.float32)
        first_n = processor.num_samples_first_audio_chunk
        for chunk in chunks:
            audio = np.concatenate([audio, chunk])
            if len(audio) >= first_n:
                break
        if len(audio) < first_n:
            return
        first = processor(audio[:first_n], sampling_rate=SR, is_streaming=True, is_first_audio_chunk=True,
                          language=language, return_tensors='pt').to(model.device, dtype=model.dtype)

        def features():
            nonlocal audio
            yield first.input_features[:, :processor.num_mel_frames_first_audio_chunk, :]
            frame = processor.num_mel_frames_first_audio_chunk
            hop, n_fft = processor.feature_extractor.hop_length, processor.feature_extractor.n_fft
            start = frame * hop - n_fft // 2
            while not stopped.is_set():
                end = start + processor.num_samples_per_audio_chunk
                while len(audio) < end and not stopped.is_set():
                    more = next(chunks, None)
                    if more is None:
                        return
                    audio = np.concatenate([audio, more])
                if stopped.is_set():
                    return
                yield processor(audio[start:end], sampling_rate=SR, is_streaming=True, is_first_audio_chunk=False,
                                language=language, return_tensors='pt').to(model.device,
                                                                           dtype=model.dtype).input_features
                frame += processor.num_mel_frames_per_audio_chunk
                start = frame * hop - n_fft // 2

        streamer = TextIteratorStreamer(processor.tokenizer, skip_special_tokens=True)
        worker = threading.Thread(target=model.generate, daemon=True,
                                  kwargs={**first, 'input_features': features(), 'streamer': streamer})
        worker.start()
        for piece in streamer:
            if stopped.is_set():
                break
            emit(piece)
        worker.join(5)

    return decode


class StreamingSession:
    """mlx-audio's streaming-session contract as PR288's ``_asr_loop`` uses it: ``feed`` 16 kHz audio, ``step``
    returns the text pieces decoded since the last step (never waits), ``cancel`` ends it.

    PR288's loop yields "ready again" (not a sleep) while ``_pending(session)`` is true, i.e. while ``_queued`` > 0
    or ``_encoded`` is non-empty: on the Mac a step then has GPU work to do. Here the decoding happens on this
    session's own thread, so a step only collects text: ``_encoded`` is true only when text is waiting and
    ``_queued`` is 0, otherwise the LISTEN job would spin and starve lower priorities. The real backlog is
    ``queued_samples``; ``feed`` refuses more than 30 s of it, as mlx-audio's session does."""

    MAX_QUEUED = 30 * SR

    def __init__(self, decode: Callable[..., Any], language: str):
        self._audio: collections.deque = collections.deque()
        self.queued_samples = 0
        self.decoding = False
        self._queued = 0  # see the class docstring: always 0
        self._pieces: queue.SimpleQueue = queue.SimpleQueue()
        self._cond = threading.Condition()
        self._stopped = threading.Event()
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, args=(decode, language), name='nemotron-stream',
                                        daemon=True)
        self._thread.start()

    def feed(self, samples) -> None:
        import numpy as np

        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        with self._cond:
            if self._stopped.is_set():
                return
            if self.queued_samples + len(samples) > self.MAX_QUEUED:
                raise ValueError('streaming session refuses more than 30 s of queued audio')
            self._audio.append(samples)
            self.queued_samples += len(samples)
            self._cond.notify_all()

    def step(self, max_decode_tokens: int = 8) -> list[str]:
        if self.error is not None:
            err, self.error = self.error, None
            raise err
        out = []
        while len(out) < max_decode_tokens:
            try:
                out.append(self._pieces.get_nowait())
            except queue.Empty:
                break
        return out

    def cancel(self, join_s: float = 2.0) -> bool:
        self._stopped.set()
        with self._cond:
            self._cond.notify_all()
        self._thread.join(join_s)
        return not self._thread.is_alive()

    def _chunks(self):
        while True:
            with self._cond:
                while not self._audio and not self._stopped.is_set():
                    self.decoding = False
                    self._cond.wait(0.05)
                if self._stopped.is_set():
                    return
                chunk = self._audio.popleft()
                self.queued_samples -= len(chunk)
                self.decoding = True
            yield chunk

    def _run(self, decode, language) -> None:
        try:
            decode(language, self._chunks(), lambda piece: None if self._stopped.is_set() else self._pieces.put(piece),
                   self._stopped)
        except BaseException as exc:  # noqa: BLE001 - surfaced by the next step(), as mlx-audio's would raise
            if not self._stopped.is_set():
                self.error = exc
        finally:
            self.decoding = False

    @property
    def _encoded(self) -> bool:
        return not self._pieces.empty()


# ------------------------------------------------------------------ PR288 binding
def bind(listener: Any, *, vad: Any, turn: Any, final: Any, stream: Any) -> None:
    """Give PR288's Listener its four models (what Listener.load does with mlx-audio) without loading MLX.
    ``vad`` is a frame -> probability callable (PR288's SileroCPU over silero_model(...), or a fixture)."""
    stream.default_att_context_size = list(STREAM_ATT_CONTEXT)
    listener._vad, listener._turn, listener._final, listener._stream = vad, turn, final, stream


def main(argv=None) -> int:
    """python3 -m rose_cloud.listen --vad DIR --turn DIR --final DIR --stream DIR: which pinned inputs are ready.
    Reads the files only (sizes, and hashes of files up to 64 MiB); loads and downloads nothing."""
    import argparse
    import sys

    p = argparse.ArgumentParser(description=main.__doc__)
    for role in MODELS:
        p.add_argument(f'--{role}', required=True, help=f"local directory with {MODELS[role]['repo']}")
    a = p.parse_args(argv)
    report = {role: check_inputs(role, getattr(a, role)) for role in MODELS}
    print(json.dumps({'ready': not any(report.values()), 'problems': report}, sort_keys=True))
    return 0 if not any(report.values()) else 2


if __name__ == '__main__':
    raise SystemExit(main())
