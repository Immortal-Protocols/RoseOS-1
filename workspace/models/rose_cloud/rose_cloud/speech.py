"""PR288's own Speaker, on Linux, speaking through PR288's own /ws/audio wire to a simulated phone player.

  python3 -m rose_cloud.speech --pr288 prototypes/voice-phone-demo --model-dir <phonon-31m-en dir> \
      --voice zoey=<voices/NbpkqMVS3CJeq2j8.safetensors> --text "Hello there, how are you today? I am fine." \
      [--quant q8] [--wav out.wav]

What runs unchanged from PR288 (loaded through nomlx): rose.runtime.Runtime, rose.bus.Bus, rose.speaker.Speaker and
Utterance (chunking, segment ids, played-sample accounting, Stop/duck/pause, at most ~1.5 s synthesized ahead of
playback), and rose.transport.BrowserTransport, the server side of ``/ws/audio`` (uint32 LE segment id + int16
24 kHz PCM out, JSON controls out, JSON ``played``/``stopped``/``idle`` reports in).

What is provided here: the Phonon model object (phonon.PhononProvider) and ``PhoneWire``, an in-process websocket
whose far end decodes every frame and feeds PR288's FakeTransport player, the same player semantics as the
browser worklet (reports every 100 ms of audio and at the end of each piece, a 40 ms fade on stop). It is not the
Android client's LocalRosePlayer, not a network socket and not the signed remote listener.

PR288's ``Speaker.load`` would load mlx-audio and ``_voice_file`` would look up (and possibly download) the voice;
``bind`` sets the provider as the speaker's model and points the voice lookup at the explicit local files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import struct
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any

from . import nomlx
from .phonon import VOICES, MissingInput, PhononProvider


def load_pr288(pr288_dir: str | Path) -> dict:
    """PR288's modules, imported from ``pr288_dir`` (prototypes/voice-phone-demo) with MLX stood in for."""
    nomlx.install()
    root = str(Path(pr288_dir).resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    from rose import bus, runtime, speaker, transport

    return {'bus': bus, 'runtime': runtime, 'speaker': speaker, 'transport': transport}


def bind(speaker_mod: Any, speaker: Any, provider: PhononProvider, voice: str, warm: bool = True) -> None:
    """Make ``speaker`` use ``provider`` and the explicit voice files (instead of Speaker.load)."""
    files = {VOICES[name][0]: path for name, path in provider.voice_files.items()}

    def voice_file(voice_id: str) -> str:
        if voice_id not in files:
            raise MissingInput(f'no local file given for voice id {voice_id}')
        return files[voice_id]

    speaker_mod._voice_file = voice_file
    speaker.model = provider
    speaker.set_voice(voice)
    if warm:
        speaker.runtime.run(speaker._warmup, priority=speaker_mod.SPEAK, name='tts-warmup')


class PhoneWire:
    """A websocket for BrowserTransport.serve whose far end is PR288's FakeTransport player.

    ``frames`` records every binary frame as (monotonic time, segment id, samples), ``controls`` every JSON control
    and ``reports`` every report the player sent back."""

    def __init__(self, transport_mod: Any, speed: float = 1.0):
        self.transport_mod = transport_mod
        self.player = transport_mod.FakeTransport(speed=speed)
        self.player.on_client = self._report
        self.frames: list[tuple[float, int, int]] = []
        self.controls: list[tuple[float, dict]] = []
        self.reports: list[tuple[float, dict]] = []
        self.application_state = type('State', (), {'name': 'CONNECTED'})()
        self.loop = asyncio.new_event_loop()
        self._inbox: asyncio.Queue | None = None
        self._thread = threading.Thread(target=self.loop.run_forever, name='phone-wire', daemon=True)
        self._thread.start()
        self._serving: Any = None

    def connect(self, transport: Any) -> None:
        async def start():
            self._inbox = asyncio.Queue()
            self._serving = asyncio.ensure_future(transport.serve(self))

        asyncio.run_coroutine_threadsafe(start(), self.loop).result(5)
        deadline = time.monotonic() + 5
        while not transport.connected and time.monotonic() < deadline:
            time.sleep(0.005)

    def disconnect(self) -> None:
        asyncio.run_coroutine_threadsafe(self._inbox.put({'type': 'websocket.disconnect'}), self.loop).result(5)

    def close(self) -> None:
        async def finish():
            if self._serving is not None and not self._serving.done():
                await self._inbox.put({'type': 'websocket.disconnect'})
                try:
                    await asyncio.wait_for(self._serving, 2)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass

        if self._thread.is_alive():
            asyncio.run_coroutine_threadsafe(finish(), self.loop).result(5)
        self.player.close()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(2)

    # --- the websocket interface BrowserTransport uses
    async def send_bytes(self, data: bytes) -> None:
        seg = struct.unpack('<I', data[:4])[0]
        import numpy as np

        pcm = np.frombuffer(data[4:], dtype='<i2').astype(np.float32) / 32768.0
        self.frames.append((time.monotonic(), seg, len(pcm)))
        self.player.send_audio(seg, pcm)

    async def send_text(self, text: str) -> None:
        msg = json.loads(text)
        self.controls.append((time.monotonic(), msg))
        self.player.send_control(msg)

    async def receive(self) -> dict:
        return await self._inbox.get()

    def _report(self, msg: dict) -> None:  # player thread
        self.reports.append((time.monotonic(), dict(msg)))
        asyncio.run_coroutine_threadsafe(self._inbox.put({'type': 'websocket.receive', 'text': json.dumps(msg)}),
                                         self.loop)


def speak(mods: dict, provider_args: dict, voice: str, text: str, speed: float = 1.0, timeout_s: float = 60) -> dict:
    runtime = mods['runtime'].Runtime()
    bus = mods['bus'].Bus()
    transport = mods['transport'].BrowserTransport()
    wire = PhoneWire(mods['transport'], speed=speed)
    try:
        speaker = mods['speaker'].Speaker(runtime, bus, transport)
        provider = PhononProvider(runtime=runtime, **provider_args)
        bind(mods['speaker'], speaker, provider, voice)
        wire.connect(transport)
        utt = speaker.utterance(1, None)
        utt.add(text)
        utt.finish()
        finished = utt.done.wait(timeout_s)
        return {'finished': finished, 'heard': utt.heard(), 'segments': sorted({s for _, s, _ in wire.frames}),
                'samplesSent': sum(n for _, _, n in wire.frames), 'samplesPlayed': wire.player.played_samples(),
                'controls': [m.get('type') for _, m in wire.controls], 'audio': wire.player.played_audio()}
    finally:
        wire.close()
        runtime.stop()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--pr288', required=True)
    p.add_argument('--model-dir', required=True)
    p.add_argument('--voice', required=True, help='name=path, e.g. zoey=/data/voices/NbpkqMVS3CJeq2j8.safetensors')
    p.add_argument('--quant', choices=('q8', 'q4k'), default=None)
    p.add_argument('--text', default='Hello there, how are you today? I am fine.')
    p.add_argument('--wav')
    a = p.parse_args(argv)
    name, _, path = a.voice.partition('=')
    try:
        result = speak(load_pr288(a.pr288), {'model_dir': a.model_dir, 'voice_files': {name: path},
                                             'quant': a.quant}, name, a.text)
    except MissingInput as e:
        print(json.dumps({'error': 'MissingInput', 'detail': str(e)}))
        return 2
    audio = result.pop('audio')
    if a.wav:
        import numpy as np

        with wave.open(a.wav, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype('<i2').tobytes())
    print(json.dumps(result, sort_keys=True))
    return 0 if result['finished'] else 1


if __name__ == '__main__':
    sys.exit(main())
