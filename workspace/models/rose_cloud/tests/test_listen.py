"""PR288's own Listener and Runtime, unchanged, through rose_cloud.listen's adapters, on synthetic 16 kHz PCM.

FIXTURES, not recognition: the VAD is an energy rule, Parakeet's transcribe, smart-turn's ONNX session and features,
and Nemotron's decoder are stand-ins that return scripted text or probabilities. What runs for real: PR288's
Listener (sample-clock endpointing, dangling/preamble rules, early transcript and its cancellation, partials,
echo guard), PR288's Runtime, and this directory's adapters, worker threads and cooperative waits. Nothing here
shows what the real models hear. Needs ROSE_PR288_DIR and numpy; skipped with the reason otherwise.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

from rose_cloud import listen  # noqa: E402

PR288 = os.environ.get('ROSE_PR288_DIR')
SR, FRAME = 16000, 512

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


def tone(seconds, amp=0.3):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * 200 * t)).astype(np.float32)


def silence(seconds):
    return np.zeros(int(seconds * SR), np.float32)


class EnergyVad:
    """FIXTURE VAD: speech probability from frame RMS (loud 0.9, medium 0.55, quiet 0.05)."""

    def __call__(self, frame):
        rms = float(np.sqrt(np.mean(frame ** 2)))
        return 0.9 if rms > 0.15 else 0.55 if rms > 0.04 else 0.05


class Script:
    """FIXTURE recognizer outputs, one per call, with an optional delay (a slow recognizer)."""

    def __init__(self, texts, delay=0.0):
        self.texts, self.delay, self.calls, self.cancelled_seen = list(texts), delay, 0, 0

    def __call__(self, audio):
        self.calls += 1
        time.sleep(self.delay)
        return self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]


class Features:
    """FIXTURE Whisper feature extractor: records the audio it was given."""

    def __init__(self):
        self.seen = []

    def __call__(self, audio, **kw):
        self.seen.append((np.array(audio), kw))
        return SimpleNamespace(input_features=np.zeros((1, 80, 800), np.float32))


class Session:
    """FIXTURE ONNX session returning a fixed probability, after an optional delay."""

    def __init__(self, p=0.9, delay=0.0):
        self.p, self.delay, self.calls = p, delay, 0

    def run(self, outputs, feeds):
        self.calls += 1
        assert feeds['input_features'].shape == (1, 80, 800)
        time.sleep(self.delay)
        return [np.array([[self.p]], np.float32)]


def word_decoder(words, every_s=0.4, delay=0.0):
    """FIXTURE Nemotron decoder: one word per ``every_s`` of audio consumed, each after ``delay``."""

    def decode(language, chunks, emit, stopped):
        got, said = 0, 0
        for chunk in chunks:
            got += len(chunk)
            while said < len(words) and got >= (said + 1) * every_s * SR:
                time.sleep(delay)
                if stopped.is_set():
                    return
                emit(('' if said == 0 else ' ') + words[said])
                said += 1
    return decode


@unittest.skipUnless(PR288, 'ROSE_PR288_DIR (PR288 prototypes/voice-phone-demo checkout) not set')
@unittest.skipIf(np is None, 'numpy missing')
class ListenerOnLinux(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rose_cloud import nomlx
        nomlx.install()
        sys.path.insert(0, PR288)
        from rose import bus, listener, runtime
        cls.L, cls.R, cls.B = listener, runtime, bus

    def make(self, texts=('What time is it?',), turn_p=0.9, asr_delay=0.0, turn_delay=0.0, words=('what', 'time'),
             decode_delay=0.0):
        self.runtime = self.R.Runtime()
        self.addCleanup(self.runtime.stop)
        self.events = []
        self.pushed = 0
        self.listener = self.L.Listener(self.runtime, self.B.Bus(), on_event=self.events.append)
        self.addCleanup(self.listener.stop)
        self.asr = Script(texts, asr_delay)
        self.session, self.features = Session(turn_p, turn_delay), Features()
        self.final = listen.Parakeet('unused', runtime=self.runtime, verify=False, transcribe=self.asr)
        self.turn = listen.SmartTurn('unused', runtime=self.runtime, verify=False, session=self.session,
                                     features=self.features)
        self.stream = listen.NemotronStreaming('unused', verify=False,
                                               decoder=word_decoder(list(words), delay=decode_delay))
        listen.bind(self.listener, vad=EnergyVad(), turn=self.turn, final=self.final, stream=self.stream)
        self.listener.start()

    def feed(self, audio, chunk=1024, timeout=20):
        for i in range(0, len(audio), chunk):
            self.listener.push(audio[i:i + chunk])
        self.pushed = getattr(self, 'pushed', 0) + len(audio)
        target = self.pushed // FRAME * FRAME
        deadline = time.monotonic() + timeout
        while self.listener.heard_samples < target and time.monotonic() < deadline:
            time.sleep(0.005)
        time.sleep(0.05)  # the last frame's callbacks

    def kinds(self):
        return [e.kind for e in self.events if e.kind != 'partial']

    def decisions(self):  # what the sample clock decides; partial/tentative timing is wall-clock by design
        return [(e.kind, e.data.get('t_audio'), e.text) for e in self.events if e.kind not in ('partial', 'tentative')]

    def test_question_commits_at_the_pause_with_smart_turn(self):
        self.make()
        self.feed(np.concatenate([silence(0.5), tone(1.0), silence(1.0)]))
        kinds = self.kinds()
        self.assertEqual([k for k in kinds if k != 'tentative'], ['speech_start', 'speech_pause', 'turn_end'])
        self.assertIn('tentative', kinds)  # the early transcript lands when it lands (wall clock), before the commit
        end = self.events[-1]
        self.assertEqual((end.text, end.data['by']), ('What time is it?', 'smart_turn'))
        self.assertAlmostEqual(end.data['silence_ms'], 200, delta=40)  # committed at the 200 ms pause
        self.assertEqual((self.session.calls, self.turn.calls), (1, 1))
        audio, kw = self.features.seen[0]
        self.assertEqual(len(audio), 8 * SR)  # official preprocessing: left-padded to 8 s
        self.assertTrue(np.all(audio[:SR * 6] == 0))
        self.assertEqual((kw['max_length'], kw['padding'], kw['do_normalize']), (8 * SR, 'max_length', True))

    def test_chunk_boundaries_do_not_change_decisions(self):
        audio = np.concatenate([silence(0.3), tone(0.8), silence(0.1), tone(0.6), silence(2.5)])
        runs = []
        for chunk in (7, 333, 512, 4096, len(audio)):
            self.make(texts=('I was thinking about the',))
            self.feed(audio, chunk=chunk)
            runs.append(self.decisions())
            self.listener.stop()
            self.runtime.stop()
        self.assertTrue(all(r == runs[0] for r in runs), runs)
        self.assertEqual(runs[0][-1][0], 'turn_end')

    def test_dangling_text_waits_two_seconds_of_audio(self):
        self.make(texts=('I want to',))
        self.feed(np.concatenate([tone(1.0), silence(1.5)]))
        self.assertNotIn('turn_end', self.kinds())
        self.feed(silence(1.0))
        end = self.events[-1]
        self.assertEqual((end.kind, end.data['by']), ('turn_end', 'silence'))
        self.assertAlmostEqual(end.data['silence_ms'], 2000, delta=40)

    def test_silence_and_wordless_audio(self):
        self.make(texts=('',))
        self.feed(silence(3.0))
        self.assertEqual(self.kinds(), [])
        self.feed(np.concatenate([tone(0.4), silence(0.6)]))
        self.assertEqual(self.kinds()[-1], 'noise')
        self.assertNotIn('turn_end', self.kinds())

    def test_slow_recognizers_change_no_sample_clock_decision(self):
        audio = np.concatenate([silence(0.2), tone(1.0), silence(1.5)])
        self.make(texts=('Is it raining',))
        self.feed(audio, chunk=len(audio))
        fast = self.decisions()
        self.listener.stop()
        self.runtime.stop()
        self.make(texts=('Is it raining',), asr_delay=0.4, turn_delay=0.2)
        t = time.monotonic()
        self.feed(audio, chunk=len(audio))
        self.assertEqual(self.decisions(), fast)  # same events at the same audio times, only later in wall time
        self.assertGreater(time.monotonic() - t, 0.4)

    def test_resumed_speech_cancels_the_slow_early_transcript(self):
        self.make(asr_delay=0.6)
        self.asr.texts = None
        calls = []

        def transcribe(audio):  # FIXTURE: the dip's early run sees ~1.4 s of audio, the final one ~2 s
            calls.append(len(audio) / SR)
            time.sleep(0.6)
            return 'first dip' if len(audio) < 1.6 * SR else 'Can you open maps?'
        self.final.transcribe = transcribe
        t = time.monotonic()
        # 1 s speech, then a dip past the 64 ms early-transcript point but short of the 200 ms pause ...
        self.feed(np.concatenate([tone(1.0), silence(0.128)]), chunk=512)
        time.sleep(0.2)  # ... so the early run is in progress, on its worker, when speech resumes
        self.feed(np.concatenate([tone(0.5), silence(0.8)]), chunk=512)
        self.assertEqual(self.listener.early_asr['discarded'], 1)
        self.assertLess(calls[0], 1.6)  # the early run did start (late cancellation, not a never-run job)
        self.assertNotIn('first dip', [e.text for e in self.events])  # and its text never surfaces
        self.assertEqual((self.events[-1].kind, self.events[-1].text), ('turn_end', 'Can you open maps?'))
        # The cancelled run stopped holding the runtime at once: the decision waited for its own 0.6 s run only.
        self.assertLess(self.events[-1].data['wait_ms'], 1000)
        self.assertLess(time.monotonic() - t, 5)
        deadline = time.monotonic() + 3
        while any(th.name == 'parakeet' for th in threading.enumerate()) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(any(th.name == 'parakeet' for th in threading.enumerate()))

    def test_output_audible_raises_the_start_bar_input_mute_is_silence(self):
        self.make(texts=('Stop.',))
        self.listener.set_rose_audible(True)  # Rose is playing: echo guard
        self.feed(np.concatenate([tone(0.6, amp=0.08), silence(0.5)]))  # medium level, p 0.55 < 0.6
        self.assertEqual(self.kinds(), [])
        self.listener.set_rose_audible(False)  # her output stopped or was muted on the phone
        self.feed(np.concatenate([tone(0.6, amp=0.08), silence(0.5)]))
        self.assertEqual(self.kinds()[0], 'speech_start')
        # Input mute: the phone sends zeros, so the clock runs and a pending turn still commits on time.
        self.make(texts=('I went to the',))
        self.feed(np.concatenate([tone(1.0), silence(0.3)]))
        self.assertNotIn('turn_end', self.kinds())
        self.feed(np.zeros(int(1.9 * SR), np.float32))  # muted mic
        end = self.events[-1]
        self.assertEqual((end.kind, end.data['by']), ('turn_end', 'silence'))
        self.assertAlmostEqual(end.data['silence_ms'], 2000, delta=40)

    def test_partials_stream_without_blocking_the_runtime(self):
        self.make(words=('what', 'time', 'is', 'it'), decode_delay=0.3)
        seen = {}

        def talk():
            t = time.monotonic()
            for _ in range(40):
                yield self.R.Sleep(0.005)
            seen['talk_s'] = time.monotonic() - t

        self.runtime.submit(talk, priority=self.R.TALK, name='talk-probe')
        self.feed(np.concatenate([tone(2.0), silence(0.05)]), chunk=512)
        deadline = time.monotonic() + 5
        while 'talk_s' not in seen and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertLess(seen['talk_s'], 1.0)  # the slow decoder never held the runtime thread
        deadline = time.monotonic() + 5
        while not any(e.kind == 'partial' for e in self.events) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(any(e.kind == 'partial' and e.text.startswith('what') for e in self.events))
        self.listener.stop()
        deadline = time.monotonic() + 3
        while any(th.name == 'nemotron-stream' for th in threading.enumerate()) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(any(th.name == 'nemotron-stream' for th in threading.enumerate()))

    def test_listening_runs_while_the_talker_waits_on_vllm(self):
        from fakes import CharTokenizer, FakeVllm
        from rose_cloud.vllm_brain import VllmBrain

        self.make(texts=('Hello?',))
        tok = CharTokenizer()
        fake = FakeVllm([tok.encode('Hi<|im_end|>')], header_delay_s=3.0)
        self.addCleanup(fake.close)
        brain = VllmBrain(tok, fake.url, 'rose-brain', runtime=self.runtime)
        seen = {}

        def talk():
            seq = brain.sequence()
            yield from seq.append_text('q')
            seen['start'] = time.monotonic()
            for _ in seq.generate(SimpleNamespace(temperature=0.0, top_p=1.0, top_k=0, presence_penalty=0.0,
                                                  max_tokens=8)):
                yield None
            seen['done'] = time.monotonic()

        self.runtime.submit(talk, priority=self.R.TALK, name='talker')
        deadline = time.monotonic() + 3
        while 'start' not in seen and time.monotonic() < deadline:
            time.sleep(0.01)
        t = time.monotonic()
        self.feed(np.concatenate([tone(1.0), silence(0.5)]), chunk=len(tone(1.5)))
        self.assertEqual(self.events[-1].kind, 'turn_end')
        self.assertLess(time.monotonic() - t, 2.0)  # smart-turn and Parakeet ran during the 3 s stall
        self.assertNotIn('done', seen)


@unittest.skipIf(np is None, 'numpy missing')
class Providers(unittest.TestCase):
    def test_missing_inputs_are_named(self):
        problems = listen.check_inputs('final', '/nonexistent')
        self.assertIn('missing nvidia/parakeet-tdt-0.6b-v3@541d1f99c6b0/model.safetensors', problems)
        with self.assertRaises(listen.MissingInput) as e:
            listen.SmartTurn('/nonexistent', session=Session(), features=Features())
        self.assertIn('pipecat-ai/smart-turn-v3@f766f81d3cfd/smart-turn-v3.2-gpu.onnx', str(e.exception))
        for role in ('vad', 'stream'):
            self.assertTrue(listen.check_inputs(role, '/nonexistent'))

    def test_smart_turn_keeps_the_last_eight_seconds(self):
        f = Features()
        st = listen.SmartTurn('x', verify=False, session=Session(0.3), features=f)
        audio = np.arange(10 * SR, dtype=np.float32)
        r = st.predict_endpoint(audio, sample_rate=SR)
        self.assertEqual((round(r.probability, 3), r.prediction), (0.3, 0))
        self.assertTrue(np.array_equal(f.seen[0][0], audio[-8 * SR:]))
        with self.assertRaises(ValueError):
            st.predict_endpoint(audio, sample_rate=8000)

    def test_streaming_session_contract(self):
        session = listen.StreamingSession(word_decoder(['a', 'b'], every_s=0.1), 'en')
        self.addCleanup(session.cancel)
        self.assertEqual(session.step(), [])  # never waits
        session.feed(np.zeros(int(0.25 * SR), np.float32))
        deadline = time.monotonic() + 3
        out = []
        while len(out) < 2 and time.monotonic() < deadline:
            out += session.step()
            time.sleep(0.01)
        self.assertEqual(out, ['a', ' b'])
        with self.assertRaises(ValueError):
            session.feed(np.zeros(31 * SR, np.float32))
        self.assertTrue(session.cancel())
        session.feed(np.zeros(10, np.float32))  # ignored after cancel
        broken = listen.StreamingSession(lambda *a: (_ for _ in ()).throw(RuntimeError('model failed')), 'en')
        time.sleep(0.1)
        with self.assertRaises(RuntimeError):
            broken.step()

    def test_safetensors_reader_and_silero_shapes(self):
        rng = np.random.default_rng(0)
        shapes = {'vad_16k.stft_conv.weight': (258, 256, 1), 'vad_16k.conv1.weight': (128, 3, 129),
                  'vad_16k.conv1.bias': (128,), 'vad_16k.conv2.weight': (64, 3, 128), 'vad_16k.conv2.bias': (64,),
                  'vad_16k.conv3.weight': (64, 3, 64), 'vad_16k.conv3.bias': (64,),
                  'vad_16k.conv4.weight': (128, 3, 64), 'vad_16k.conv4.bias': (128,),
                  'vad_16k.lstm.Wx': (512, 128), 'vad_16k.lstm.Wh': (512, 128), 'vad_16k.lstm.bias': (512,),
                  'vad_16k.final_conv.weight': (1, 1, 128), 'vad_16k.final_conv.bias': (1,)}
        tensors = {k: (rng.standard_normal(s) * 0.05).astype('<f4') for k, s in shapes.items()}
        with tempfile.TemporaryDirectory() as d:
            header, blob, off = {}, b'', 0
            for k, a in tensors.items():
                header[k] = {'dtype': 'F32', 'shape': list(a.shape), 'data_offsets': [off, off + a.nbytes]}
                blob += a.tobytes()
                off += a.nbytes
            raw = json.dumps(header).encode()
            Path(d, 'model.safetensors').write_bytes(struct.pack('<Q', len(raw)) + raw + blob)
            Path(d, 'config.json').write_text(json.dumps({'branch_16k': {
                'sample_rate': 16000, 'filter_length': 256, 'hop_length': 128, 'pad': 64, 'cutoff': 129,
                'context_size': 64, 'chunk_size': 512}}))
            read = listen.read_safetensors(Path(d, 'model.safetensors'))
            self.assertTrue(all(np.array_equal(read[k], tensors[k]) for k in tensors))
            model = listen.silero_model(d, verify=False)
            if not PR288:
                self.skipTest('ROSE_PR288_DIR not set: SileroCPU is PR288 code')
            from rose_cloud import nomlx
            nomlx.install()
            sys.path.insert(0, PR288)
            from rose.listener import SileroCPU
            vad = SileroCPU(model)  # PR288's numpy VAD, unchanged, on these (random, FIXTURE) weights
            p = vad(np.zeros(FRAME, np.float32))
            self.assertTrue(0.0 <= p <= 1.0)
        self.assertEqual(listen.check_inputs('vad', '/nonexistent')[0],
                         'missing mlx-community/silero-vad@7bc17f22d3c0/config.json')


if __name__ == '__main__':
    unittest.main()
