"""PR288's own Speaker and /ws/audio transport, unchanged, speaking through PhononProvider on Linux.

Needs ROSE_PR288_DIR and PR288's numpy; skipped, with the reason, without them. FIXTURE: the Phonon engine is
tests/fake_ptts.py (a tone per word), because no Phonon runtime, weights or voice file is present here; the player
at the far end of the wire is PR288's FakeTransport (browser-worklet semantics), not the Android player. What runs
for real: PR288's Runtime, Speaker/Utterance, Chunker, BrowserTransport and its wire encoding, and this
directory's provider, pump and cooperative wait.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

PR288 = os.environ.get('ROSE_PR288_DIR')
TEXT = 'Hello there, how are you today? I am fine, thank you for asking me that.'


@unittest.skipUnless(PR288, 'ROSE_PR288_DIR (PR288 prototypes/voice-phone-demo checkout) not set')
class SpeechWire(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import numpy  # noqa: F401
        except ImportError as e:
            raise unittest.SkipTest(f'PR288 dependency missing: {e.name}')
        from rose_cloud import speech
        cls.speech = speech
        cls.mods = speech.load_pr288(PR288)

    def setUp(self):
        from fake_ptts import FakeTTS
        from rose_cloud.phonon import PhononProvider

        self.voice = HERE / 'fake_ptts.py'  # any existing file stands in for the voice (verify=False)
        self.runtime = self.mods['runtime'].Runtime()
        self.addCleanup(self.runtime.stop)
        self.bus = self.mods['bus'].Bus()
        self.transport = self.mods['transport'].BrowserTransport()
        self.speaker = self.mods['speaker'].Speaker(self.runtime, self.bus, self.transport)
        self.make_provider = lambda **kw: PhononProvider(
            'unused', {'zoey': self.voice}, runtime=self.runtime, verify=False,
            tts_factory=lambda *_: self.tts, **kw)
        self.FakeTTS = FakeTTS

    def start(self, speed=4.0, **tts):
        self.tts = self.FakeTTS(**tts)
        self.provider = self.make_provider()
        self.speech.bind(self.mods['speaker'], self.speaker, self.provider, 'zoey')
        self.wire = self.speech.PhoneWire(self.mods['transport'], speed=speed)
        self.addCleanup(self.wire.close)
        self.wire.connect(self.transport)
        self.assertTrue(self.transport.connected)

    def wait_for(self, cond, timeout=15):
        deadline = time.monotonic() + timeout
        while not cond() and time.monotonic() < deadline:
            time.sleep(0.005)
        return cond()

    def segs(self, since=0.0):
        return [s for t, s, _ in self.wire.frames if t >= since]

    def test_two_segments_with_acks_end_and_bounded_lead(self):
        self.start(speed=4.0)
        lead = []
        stop = threading.Event()

        def watch():
            while not stop.is_set():
                lead.append(self.speaker.buffered_s())
                time.sleep(0.01)
        threading.Thread(target=watch, daemon=True).start()
        utt = self.speaker.utterance(1, None)
        utt.add(TEXT)
        utt.finish()
        self.assertTrue(utt.done.wait(30))
        stop.set()
        ids = self.segs()
        self.assertEqual(sorted(set(ids)), [1, 2])  # two chunks, two segment ids, in order on the wire
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(self.tts.texts, ['Hi there.',  # PR288's own warm-up in Speaker._warmup
                                          'Hello there, how are you today?',
                                          'I am fine, thank you for asking me that.'])
        self.assertEqual([m['type'] for _, m in self.wire.controls], ['end'])
        self.assertEqual(self.wire.controls[-1][1]['seg'], 2)
        played = [m for _, m in self.wire.reports if m['type'] == 'played']
        self.assertTrue(any(m['seg'] == 1 and m.get('done') for m in played))
        self.assertTrue(any(m['seg'] == 2 and m.get('done') for m in played))
        self.assertEqual(utt.heard(), TEXT)
        self.assertFalse(utt.interrupted)
        sent = sum(n for _, _, n in self.wire.frames)
        self.assertEqual(self.wire.player.played_samples(), sent)  # everything sent was played and acknowledged
        self.assertLess(max(lead), 1.5 + 0.2)  # PR288's MAX_AHEAD_S plus one piece in flight
        self.assertEqual((self.tts.opened, self.tts.closed, self.provider.workers_left), (3, 3, 0))  # + warm-up

    def test_stop_mid_reply_no_late_audio_then_restart(self):
        self.start(speed=1.0, chunk_delay_s=0.01)
        utt = self.speaker.utterance(1, None)
        utt.add(TEXT)
        utt.finish()
        self.assertTrue(self.wait_for(lambda: self.wire.player.played_samples() > 24000 * 0.3))
        self.speaker.stop()
        t_stop = time.monotonic()
        self.assertTrue(utt.done.wait(5))
        self.assertTrue(utt.interrupted)
        heard = utt.heard()
        self.assertTrue(TEXT.startswith(heard.rstrip('.')) or heard in TEXT, heard)
        self.assertLess(len(heard), len(TEXT))
        self.assertIn('stop', [m['type'] for _, m in self.wire.controls])
        self.assertTrue(any(m['type'] == 'stopped' for _, m in self.wire.reports))
        time.sleep(0.5)
        self.assertEqual(self.segs(since=t_stop + 0.05), [])  # nothing for the stopped reply after the stop
        self.assertTrue(self.wait_for(lambda: self.tts.opened == self.tts.closed))
        self.assertEqual(self.provider.workers_left, 0)
        # The next reply speaks normally, with new segment ids.
        old = max(self.segs())
        again = self.speaker.utterance(2, None)
        again.add('Okay, starting over now.')
        again.finish()
        self.assertTrue(again.done.wait(15))
        self.assertEqual(again.heard(), 'Okay, starting over now.')
        self.assertGreater(min(self.segs(since=t_stop + 0.6)), old)

    def test_disconnect_mid_reply_does_not_hang(self):
        self.start(speed=1.0)
        utt = self.speaker.utterance(1, None)
        utt.add(TEXT)
        utt.finish()
        self.assertTrue(self.wait_for(lambda: self.wire.player.played_samples() > 24000 * 0.2))
        self.wire.disconnect()
        self.assertTrue(self.wait_for(lambda: not self.transport.connected, 5))
        self.assertTrue(utt.done.wait(15))  # later audio is reported dropped; the reply settles
        self.assertTrue(self.wait_for(lambda: self.tts.opened == self.tts.closed))
        self.assertEqual(self.provider.workers_left, 0)

    def test_slow_synthesis_does_not_block_listening(self):
        self.start(speed=4.0)
        self.tts.first_delay_s = 1.0  # after warm-up: every chunk's first audio takes 1 s
        utt = self.speaker.utterance(1, None)
        utt.add('Hello there, how are you today?')
        utt.finish()
        time.sleep(0.2)  # the speaker job is waiting for its first audio
        seen = {}
        submitted = time.monotonic()

        def listen():
            seen['listen'] = time.monotonic() - submitted
            return None
            yield  # pragma: no cover
        self.runtime.submit(listen, priority=self.mods['runtime'].LISTEN, name='listen-probe')
        self.assertTrue(self.wait_for(lambda: 'listen' in seen, 2))
        self.assertLess(seen['listen'], 0.1)
        self.assertGreater(self.provider.waiter.nested_units, 0)
        self.assertTrue(utt.done.wait(15))

    def test_cancelled_utterance_stops_a_stalled_stream(self):
        self.start(speed=4.0)
        self.tts.first_delay_s = 3.0
        utt = self.speaker.utterance(1, None)
        utt.add('Hello there, how are you today?')
        utt.finish()
        time.sleep(0.2)
        t = time.monotonic()
        utt.cancel()
        self.assertTrue(utt.done.wait(2))
        self.assertTrue(self.wait_for(lambda: not [th for th in threading.enumerate() if th.name == 'phonon-stream'],
                                      5))
        self.assertEqual(self.segs(), [])  # no audio from the cancelled reply, ever
        time.sleep(3.2 - (time.monotonic() - t))  # past the stalled chunk's arrival
        self.assertEqual(self.segs(), [])


class ProviderInputs(unittest.TestCase):
    """Runs without PR288: explicit local inputs, pinned identities, no implicit download."""

    def test_missing_inputs_are_named_exactly(self):
        from rose_cloud import phonon

        with self.assertRaises(phonon.MissingInput) as e:
            phonon.PhononProvider('/nonexistent', {'zoey': '/nonexistent/zoey.safetensors'},
                                  tts_factory=lambda *_: None)
        msg = str(e.exception)
        for name in ('gradium/phonon-31m-en@92741c5b07d0/config.json', 'tokenizer.json', 'model.safetensors',
                     'gradium/phonon-voices@0ee17205f254/voices/NbpkqMVS3CJeq2j8.safetensors'):
            self.assertIn(name, msg)
        problems = phonon.check_inputs('/nonexistent', {}, quant='q8')
        self.assertIn('gradium/phonon-31m-en@92741c5b07d0/model.q8.gguf', problems['missing'])

    def test_wrong_files_are_refused(self):
        import tempfile
        from rose_cloud import phonon

        with tempfile.TemporaryDirectory() as d:
            for name in ('config.json', 'tokenizer.json', 'model.safetensors'):
                Path(d, name).write_bytes(b'not the pinned file')
            Path(d, 'voice.safetensors').write_bytes(b'x')
            problems = phonon.check_inputs(d, {'zoey': Path(d, 'voice.safetensors')})
            self.assertEqual(problems['missing'], [])
            self.assertEqual(problems['mismatched'], ['config.json', 'tokenizer.json', 'model.safetensors',
                                                      'voice zoey'])
        with self.assertRaises(ValueError):
            phonon.check_inputs('/nonexistent', {'Morgan Freeman': '/x'})

    def test_git_blob_digest_matches_git(self):
        import subprocess
        import tempfile
        from rose_cloud import phonon

        with tempfile.NamedTemporaryFile('wb', delete=False) as f:
            f.write(b'{"x": 1}\n')
        self.addCleanup(os.unlink, f.name)
        try:
            want = subprocess.run(['git', 'hash-object', f.name], capture_output=True, text=True,
                                  check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest('git not available')
        self.assertEqual(phonon.file_digest(Path(f.name), 'gitsha1'), want)

    def test_sample_rate_and_voice_are_enforced(self):
        from fake_ptts import FakeTTS
        from rose_cloud import phonon

        with self.assertRaises(ValueError):
            phonon.PhononProvider('x', {'zoey': __file__}, verify=False, tts_factory=lambda *_: FakeTTS(rate=22050))
        p = phonon.PhononProvider('x', {'zoey': __file__}, verify=False, tts_factory=lambda *_: FakeTTS())
        with self.assertRaises(phonon.MissingInput):
            list(p.generate_audio_stream(phonon.VoiceState('zoey'), 'hi'))  # not added through the speaker path
        state = p.get_state_for_audio_prompt(p._resolve_audio_prompt(__file__, None, False))
        chunks = list(p.generate_audio_stream(state, 'one two'))
        self.assertEqual(sum(len(c) for c in chunks), 2400 + 12000 + 4800)
        self.assertEqual(p.tts.voices, {'zoey': __file__})


if __name__ == '__main__':
    unittest.main()
