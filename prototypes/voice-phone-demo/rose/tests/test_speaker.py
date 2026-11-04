"""Speaker and transports with the real runtime, Phonon and a fake client.

    .venv/bin/python -m rose.tests.test_speaker
The fake client plays Rose's audio in real time exactly like the browser
player, and Parakeet transcribes what it played, so these check what a user
would hear: streamed text starts speaking fast and says the right words, an
interrupted reply knows how far it got and plays nothing after the stop,
ducking changes the gain, and times are read as words.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import struct
import threading
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import mlx.core as mx
import numpy as np
from scipy.signal import resample_poly

from rose.bus import Bus
from rose.runtime import LISTEN, Runtime
from rose.speaker import Chunker, Speaker
from rose.textnorm import speakable
from rose.transport import PLAY_RATE, BrowserTransport, FakeTransport

RESULTS: dict[str, dict] = {}


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", speakable(text).lower().replace("-", " "))


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
    return d[len(h)] / max(1, len(r))


def stream(utt, text: str, delay: float = 0.0) -> float:
    """add() word by word like the talker; returns the time of the first add()."""

    t0 = time.monotonic()
    for piece in re.findall(r"\S+\s*", text):
        utt.add(piece)
        if delay:
            time.sleep(delay)
    return t0


def silences_ms(audio: np.ndarray, threshold: float = 0.02) -> list[float]:
    """Lengths of near-silent stretches (10 ms resolution), in order."""

    win = PLAY_RATE // 100
    quiet = [np.abs(audio[i : i + win]).max() < threshold for i in range(0, len(audio) - win + 1, win)]
    runs, n = [], 0
    for q in quiet + [False]:
        if q:
            n += 1
        elif n:
            runs.append(n * 10.0)
            n = 0
    return runs


def wait(cond, timeout: float, step: float = 0.005) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


# ------------------------------------------------------------ no GPU
def test_chunker() -> None:
    cases = [
        ("Okay, I found two messages from Sam. He says dinner moved to Friday.",
         ["Okay, I found two messages from Sam.", "He says dinner moved to Friday."]),
        ("Sure, I can do that for you, no problem at all.",
         ["Sure, I can do that for you,", "no problem at all."]),
        ("Dinner is at 6:30 PM. It costs $3.50 each, e.g. the salad.",
         ["Dinner is at 6:30 PM.", "It costs $3.50 each, e.g. the salad."]),
        ("one two three four five six seven eight nine ten eleven twelve thirteen fourteen",
         ["one two three four five six seven eight nine ten eleven twelve", "thirteen fourteen"]),
        ("Dr. Smith called you twice today. Sure. Okay. Sam says hi to you.",
         ["Dr. Smith called you twice today.", "Sure. Okay. Sam says hi to you."]),
        ("Here is the plan for today — first the dentist, then lunch.",
         ["Here is the plan for today —", "first the dentist, then lunch."]),
    ]
    for text, expected in cases:
        c = Chunker()
        got = []
        for piece in re.findall(r"\S+\s*", text):
            got += c.feed(piece)
        got += c.flush()
        assert got == expected, (text, got)
    # Eager first boundary: a clause ending the buffer is cut without waiting for the next token.
    c = Chunker()
    assert c.feed("Okay, I found it.") == ["Okay, I found it."]
    assert Chunker().feed("It is 6:") == [] and Chunker().feed("Meet Mr.") == []
    # A long sentence arriving in one add() is capped like a streamed one.
    c = Chunker()
    got = c.feed("Okay, here is what I found. Your sister wrote that she is landing at the airport around "
                 "noon on Saturday, and she would love it if you could pick her up from terminal two before "
                 "heading over to the restaurant downtown for the birthday lunch with everyone. ") + c.flush()
    assert [len(x.split()) for x in got] == [6, 14, 27], got
    RESULTS["chunker"] = {"cases": len(cases) + 3}


def test_money() -> None:
    # The space after an amount was dropped ("two hundred fifty dollarsa month").
    for text, want in [("Save $250 a month.", "Save two hundred fifty dollars a month."),
                       ("$38,800 after ten years", "thirty-eight thousand eight hundred dollars after ten years"),
                       ("A $5k bonus", "A five thousand dollars bonus"), ("$2.50 each", "two dollars and fifty cents each")]:
        assert speakable(text) == want, (text, speakable(text))


def test_fake_transport() -> None:
    tr = FakeTransport(speed=4.0)
    reports = []
    tr.on_client = reports.append
    tone = (0.5 * np.sin(np.arange(PLAY_RATE // 2) * 0.05)).astype(np.float32)  # 0.5 s
    tr.send_audio(7, tone[:6000])
    tr.send_audio(7, tone[6000:])
    assert wait(lambda: reports and reports[-1].get("type") == "idle", 2.0), reports
    played = tr.played_audio()
    assert len(played) == len(tone) and np.abs(played - tone).max() < 1e-3
    kinds = [(r["type"], r.get("done")) for r in reports]
    assert kinds.count(("played", True)) == 2 and kinds.count(("played", False)) == 5, kinds
    # stop: 40 ms fade, then 'stopped' with the position inside the head piece, queue cleared
    reports.clear()
    tr.clear()
    tr.send_audio(8, np.tile(tone, 4))
    time.sleep(0.1)
    tr.send_control({"type": "stop"})
    assert wait(lambda: any(r["type"] == "stopped" for r in reports), 1.0)
    stop = next(r for r in reports if r["type"] == "stopped")
    time.sleep(0.1)
    assert stop["seg"] == 8 and stop["samples"] == tr.played_samples() < 4 * len(tone)
    assert tr.queued_samples() == 0
    frames = []
    tr.on_mic = frames.append
    tr.mic(np.zeros(1300, np.float32))
    assert [len(f) for f in frames] == [512, 512]
    tr.close()
    RESULTS["fake_transport"] = {"stop_after_samples": stop["samples"]}


class _FakeWebSocket:
    """Enough of Starlette's WebSocket for BrowserTransport.serve()."""

    def __init__(self, incoming: list[dict]):
        self.incoming = incoming
        self.sent: list = []
        self.closed = asyncio.Event()

    async def send_bytes(self, data: bytes) -> None:
        await asyncio.sleep(0)  # yield, as a real socket write may
        self.sent.append(data)

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def receive(self) -> dict:
        if self.incoming:
            return self.incoming.pop(0)
        await self.closed.wait()
        return {"type": "websocket.disconnect"}


def test_browser_transport() -> None:
    tr = BrowserTransport()
    mic, client = [], []
    tr.on_mic, tr.on_client = mic.append, client.append
    pcm = (np.arange(512) - 256).astype("<i2").tobytes()
    ws = _FakeWebSocket([{"type": "websocket.receive", "bytes": pcm},
                         {"type": "websocket.receive", "text": json.dumps({"type": "played", "seg": 3,
                                                                           "samples": 10, "done": False})}])
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    served = asyncio.run_coroutine_threadsafe(tr.serve(ws), loop)
    assert wait(lambda: tr.connected and len(client) == 1, 2.0)
    senders = [threading.Thread(target=lambda k=k: [tr.send_audio(100 * k + i, np.full(240, 0.5, np.float32))
                                                     for i in range(50)]) for k in range(3)]
    for s in senders:
        s.start()
    for s in senders:
        s.join()
    tr.send_control({"type": "duck", "gain": 0.25})
    assert wait(lambda: len(ws.sent) == 151, 2.0), len(ws.sent)
    segs = [struct.unpack("<I", b[:4])[0] for b in ws.sent if isinstance(b, bytes)]
    for k in range(3):  # each thread's pieces arrive in the order it sent them
        mine = [s for s in segs if s // 100 == k]
        assert mine == sorted(mine) and len(mine) == 50
    first = np.frombuffer(ws.sent[0][4:], dtype="<i2")
    assert len(first) == 240 and abs(int(first[0]) - 16383) <= 1
    assert len(mic) == 1 and mic[0].dtype == np.float32 and abs(mic[0][0] + 256 / 32768) < 1e-6
    loop.call_soon_threadsafe(ws.closed.set)
    served.result(2.0)
    assert not tr.connected and client[-1]["type"] == "stopped"
    client.clear()
    tr.send_audio(9, np.zeros(480, np.float32))  # detached: reported played at once
    assert client and client[-1]["type"] == "played" and client[-1]["dropped"]
    loop.call_soon_threadsafe(loop.stop)
    RESULTS["browser_transport"] = {"pieces_in_order": 150}


# ---------------------------------------------------------------- GPU
class Rig:
    def __init__(self):
        self.rt = Runtime()
        self.bus = Bus()
        self.events: list[dict] = []
        self.bus.subscribe(self.events.append)
        self.tr = FakeTransport()
        self.sp = Speaker(self.rt, self.bus, self.tr)
        t = time.perf_counter()
        self.sp.load("zoey")
        self.tts_load_s = time.perf_counter() - t

        def load_asr():
            from mlx_audio.stt.utils import load_model

            model = load_model("mlx-community/parakeet-tdt-0.6b-v3")
            model.generate(mx.zeros(16000))
            return model

        self.asr = self.rt.run(load_asr, priority=LISTEN, name="asr-load")

    def transcribe(self, audio24: np.ndarray) -> str:
        x16 = resample_poly(audio24.astype(np.float64), 2, 3).astype(np.float32)
        return self.rt.run(lambda: self.asr.generate(mx.array(x16)).text, priority=LISTEN, name="asr")

    def reset(self) -> None:
        self.tr.clear()
        self.events.clear()


def test_stream(rig: Rig) -> None:
    sp, tr = rig.sp, rig.tr
    text = ("Okay, I found two messages from Sam. "
            "He says dinner moved to Friday, and he will bring the wine.")
    rig.reset()
    audible = []
    sp.on_audible = audible.append
    utt = sp.utterance(1, t_user_end=time.monotonic())
    t0 = stream(utt, text)
    utt.finish()
    assert wait(lambda: utt.first_audio_at is not None, 2.0)
    first_ms = (utt.first_audio_at - t0) * 1000
    chunk_ms = (utt.first_audio_at - utt.first_chunk_at) * 1000
    audible_early = sp.audible
    sp.on_client({"type": "idle"})  # a late idle must not mark queued audio as played
    audible_after_idle = sp.audible
    assert utt.done.wait(30.0), "utterance never finished"
    t_done = time.monotonic()
    played = tr.played_audio()
    sent = sum(n for _, _, n in tr.sends)
    dur = len(played) / PLAY_RATE
    heard = utt.heard()
    asr = rig.transcribe(played)
    err = wer(text, asr)
    says = [e for e in rig.events if e["kind"] == "rose_say"]
    start = [e for e in rig.events if e["kind"] == "rose_audio_start"]
    pieces = [n for _, _, n in tr.sends]
    lead_ms = int(np.argmax(np.abs(played) > 0.02)) / PLAY_RATE * 1000
    idle_at = max((t for t, m in tr.reports if m["type"] == "idle"), default=t_done)
    quiet = silences_ms(played)
    sp.on_audible = None
    RESULTS["stream"] = {
        "first_audio_ms_from_first_add": round(first_ms, 1),
        "first_audio_ms_from_chunk_ready": round(chunk_ms, 1),
        "rose_audio_start": {k: start[0].get(k) for k in ("latency_ms", "tts_ms")} if start else None,
        "leading_silence_ms": round(lead_ms, 1),
        "played_s": round(dur, 2), "sent_s": round(sent / PLAY_RATE, 2),
        "s_per_word": round(dur / len(text.split()), 3),
        "chunks": [e["text"] for e in says], "seg_ids": [e["seg"] for e in says],
        "pieces_ms_min_max": [round(min(pieces[:-1]) / 24, 1), round(max(pieces) / 24, 1)],
        "pieces": len(pieces),
        "silences_ms": quiet, "audible_changes": audible,
        "done_after_idle_ms": round((t_done - idle_at) * 1000, 1),
        "asr": asr, "wer": round(err, 3), "heard_is_full": heard == text,
    }
    assert first_ms < 60, first_ms
    assert audible_early and audible_after_idle and not sp.audible
    assert len(played) == sent and 0.25 < dur / len(text.split()) < 0.6, dur
    assert lead_ms < 40, lead_ms
    assert max(pieces) <= 3840 and min(pieces[:-1]) >= 1920, pieces
    assert max(quiet) < 450 and quiet[-1] < 260, quiet  # sentence gap and final tail are trimmed
    assert audible == [True, False], audible
    assert err <= 0.15, (asr, err)
    assert heard == text, heard
    assert len(says) == 2 and says[0]["seg"] < says[1]["seg"]
    assert start and start[0]["latency_ms"] is not None
    assert not utt.interrupted


def test_interrupt(rig: Rig, stop_s: float = 1.2, key: str = "interrupt") -> None:
    sp, tr = rig.sp, rig.tr
    text = ("First, you have a dentist appointment at nine in the morning, right before work. "
            "After that, you are having lunch with Priya near the office. "
            "Then the team review runs from two until four in the afternoon.")
    rig.reset()
    utt = sp.utterance(2, t_user_end=None)
    stream(utt, text, delay=0.012)  # ~talker speed, still streaming while speech starts
    utt.finish()
    assert wait(lambda: tr.played_samples() >= int(stop_s * PLAY_RATE), 15.0)
    at_stop = tr.played_samples()
    buffered = sp.buffered_s()
    t_stop = time.monotonic()
    sp.stop()
    utt.cancel()
    assert utt.done.wait(1.0), "stop never settled"
    settle_ms = (time.monotonic() - t_stop) * 1000
    heard = utt.heard()
    n_after_stop = tr.played_samples()
    time.sleep(0.6)
    played = tr.played_audio()
    asr = rig.transcribe(played)
    stopped = [m for _, m in tr.reports if m["type"] == "stopped"]
    unplayed_text = text[len(heard):]
    says = [e["text"] for e in rig.events if e["kind"] == "rose_say"]
    RESULTS[key] = {
        "chunks_started": says,
        "played_at_stop_s": round(at_stop / PLAY_RATE, 3),
        "played_total_s": round(len(played) / PLAY_RATE, 3),
        "fade_ms": round((len(played) - at_stop) / 24, 1),
        "buffered_ahead_at_stop_s": round(buffered, 2),
        "stop_to_done_ms": round(settle_ms, 1),
        "heard": heard, "asr_of_played": asr,
        "heard_words": len(words(heard)), "asr_words": len(words(asr)),
        "tail_rms_last_10ms": round(float(np.sqrt(np.mean(played[-240:] ** 2))), 5),
        "interrupted": utt.interrupted,
    }
    assert len(played) == n_after_stop, "audio played after the stop settled"
    assert len(played) - at_stop < int(0.08 * PLAY_RATE), "fade took too long"
    assert stopped and buffered <= 1.7
    assert text.startswith(heard) and heard and unplayed_text.strip()
    assert abs(len(words(heard)) - len(words(asr))) <= 2, (heard, asr)
    assert utt.interrupted and not sp.audible


def test_latency(rig: Rig, trials: int = 8) -> None:
    """First audio after the first chunk is ready, over several replies (each
    stopped right after its first audio, which also exercises early stops)."""

    sp = rig.sp
    replies = ["Sure, I can check that for you right now.", "Okay, texting Sam that you are running late.",
               "Your next meeting is with Alex at four.", "Done. I set an alarm for seven tomorrow.",
               "Hmm, I could not find a contact named Jordan.", "It looks sunny all afternoon, so no umbrella.",
               "You have three unread messages from Mom.", "Alright, calling the dentist now."]
    from_add, from_chunk = [], []
    for i in range(trials):
        utt = sp.utterance(100 + i, t_user_end=None)
        t0 = stream(utt, replies[i % len(replies)])
        utt.finish()
        assert wait(lambda: utt.first_audio_at is not None, 2.0)
        from_add.append((utt.first_audio_at - t0) * 1000)
        from_chunk.append((utt.first_audio_at - utt.first_chunk_at) * 1000)
        time.sleep(0.15)
        sp.stop()
        assert utt.done.wait(1.0) and utt.heard() != replies[i % len(replies)]
    RESULTS["latency"] = {"first_audio_ms_from_first_add": {"p50": round(float(np.median(from_add)), 1),
                                                          "max": round(max(from_add), 1)},
                          "first_audio_ms_from_chunk_ready": {"p50": round(float(np.median(from_chunk)), 1),
                                                              "max": round(max(from_chunk), 1)},
                          "trials": trials}
    assert np.median(from_add) < 60, from_add


def test_interrupt_later(rig: Rig) -> None:
    test_interrupt(rig, stop_s=4.2, key="interrupt_later")


def test_overlap(rig: Rig) -> None:
    """A second utterance (an announcement) while the first is still speaking
    waits for the shared TTS state and plays after it; both finish."""

    sp, tr = rig.sp, rig.tr
    a_text = "Your package from the bookstore arrives on Thursday afternoon, so someone should be home."
    b_text = "Also, Sam just texted you back."
    rig.reset()
    a = sp.utterance(5, t_user_end=None)
    stream(a, a_text)
    a.finish()
    assert wait(lambda: a.first_audio_at is not None, 2.0)
    b = sp.utterance(6, t_user_end=None)
    stream(b, b_text)
    b.finish()
    assert a.done.wait(20.0) and b.done.wait(20.0)
    asr = rig.transcribe(tr.played_audio())
    segs = [seg for _, seg, _ in tr.sends]
    RESULTS["overlap"] = {"asr": asr, "wer": round(wer(a_text + " " + b_text, asr), 3),
                          "seg_order": sorted(set(segs), key=segs.index)}
    assert a.heard() == a_text and b.heard() == b_text
    assert all(x <= y for x, y in zip(segs, segs[1:])), "utterances interleaved"
    assert wer(a_text + " " + b_text, asr) <= 0.15, asr


def test_duck(rig: Rig) -> None:
    sp, tr = rig.sp, rig.tr
    text = "I added the dentist appointment to your calendar for Tuesday morning at nine, and set a reminder."
    rig.reset()
    utt = sp.utterance(3, t_user_end=None)
    stream(utt, text)
    utt.finish()
    assert wait(lambda: tr.played_samples() >= int(0.6 * PLAY_RATE), 10.0)
    i_duck = tr.played_samples()
    sp.duck()
    time.sleep(0.5)
    i_unduck = tr.played_samples()
    sp.unduck()
    assert utt.done.wait(20.0)
    out, raw = tr.played_audio(), tr.played_audio(raw=True)
    mask = np.abs(raw) > 0.02
    gain = np.full(len(raw), np.nan, np.float32)
    gain[mask] = out[mask] / raw[mask]
    before = float(np.nanmedian(gain[:i_duck]))
    during = float(np.nanmedian(gain[i_duck + 2400 : i_unduck]))
    after = float(np.nanmedian(gain[i_unduck + 2400 :]))
    reached = np.flatnonzero(gain[i_duck:] <= 0.2501)
    duck_ms = float(reached[0]) / 24 if reached.size else None
    RESULTS["duck"] = {"gain_before": round(before, 3), "gain_ducked": round(during, 3),
                       "gain_after": round(after, 3), "duck_ramp_ms": duck_ms,
                       "heard_is_full": utt.heard() == text}
    assert abs(before - 1) < 0.01 and abs(during - 0.25) < 0.01 and abs(after - 1) < 0.01
    assert not sp.ducked and utt.heard() == text


def test_time(rig: Rig) -> None:
    sp, tr = rig.sp, rig.tr
    rig.reset()
    utt = sp.utterance(4, t_user_end=None)
    stream(utt, "Dinner is at 6:30 PM.")
    utt.finish()
    assert utt.done.wait(15.0)
    asr = rig.transcribe(tr.played_audio())
    say = next(e for e in rig.events if e["kind"] == "rose_say")
    RESULTS["time"] = {"spoken": say["spoken"], "asr": asr, "asr_normalized": speakable(asr)}
    assert "six thirty" in say["spoken"]
    assert "six thirty" in " ".join(words(asr)), asr


def main() -> None:
    test_chunker()
    test_money()
    test_fake_transport()
    test_browser_transport()
    rig = Rig()
    RESULTS["load"] = {"tts_load_s": round(rig.tts_load_s, 2)}
    try:
        for test in (test_stream, test_latency, test_interrupt, test_interrupt_later, test_overlap,
                     test_duck, test_time):
            test(rig)
            print(test.__name__, json.dumps(RESULTS[test.__name__[5:]]), flush=True)
        RESULTS["runtime"] = rig.rt.stats()
    finally:
        rig.tr.close()
        rig.rt.stop()
    print(json.dumps(RESULTS, indent=1))
    print("OK")


if __name__ == "__main__":
    main()
