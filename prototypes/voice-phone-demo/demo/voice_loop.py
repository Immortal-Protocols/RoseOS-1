"""Real-time loop: microphone -> VoiceChat -> speakers, plus tool hand-offs.

All MLX work stays on this one thread. Tool jobs (brain, phone agent) run on
worker threads and come back through ``self._results`` to be injected here.
"""

from __future__ import annotations

import collections
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from . import speedups
from .orchestrator import Orchestrator
from .tools import VOICE_TOOLS, voice_system_message
from .voice_engine import create_tool_session, parse_tool_call, tools_system_prompt

IN_SR, OUT_SR, FRAME = 16_000, 22_050, 1_280
FRAME_S = FRAME / IN_SR
TURN_GAP_S = 0.8  # Design B: user transcript quiet this long ends a turn
DECIDE_GAP_S = 0.35  # Design C: start the decider after this much transcript quiet
INTERRUPT_FRAMES = 6  # ~0.5 s of user speech during a tool run cancels it
VOICE_RMS = 0.015  # Design C: input frames louder than this count as user speech
DECIDE_TIMEOUT_S = 5.0  # Design C: release the hold if the decider is this slow
IDLE_FRAMES = 6  # Design B: agent silent this many frames before injecting
MAX_BACKLOG = 6  # frames; beyond this, drop input to stay real time

DESIGN_B_SYSTEM = (
    "You are Rose, a warm, curious voice companion. Talk naturally about anything and keep "
    "replies short. Never list what you can or cannot do. A smarter helper works alongside "
    "you: it answers questions, sees the user's messages, contacts and calendar, and does "
    "things on the phone. You never call tools yourself; the helper's results arrive as tool "
    "responses. For anything beyond small talk, say you are checking and do not make up an "
    "answer. When a tool response arrives, tell the user what it says."
)

Emit = Callable[[str, dict], None]

# A soft two-pulse tone played while the voice model waits on a tool.
_t = np.arange(int(OUT_SR * 0.09)) / OUT_SR
_pulse = (0.05 * np.sin(2 * np.pi * 660 * _t) * np.hanning(len(_t))).astype(np.float32)
WAIT_TONE = np.concatenate([_pulse, np.zeros(int(OUT_SR * 0.07), np.float32), _pulse])


class AudioOut:
    """Jitter buffer feeding a sounddevice output callback."""

    def __init__(self):
        self._buf = np.zeros(0, np.float32)
        self._lock = threading.Lock()

    def write(self, samples: np.ndarray) -> None:
        with self._lock:
            self._buf = np.concatenate([self._buf, samples.astype(np.float32)])

    def read(self, n: int) -> np.ndarray:
        with self._lock:
            out, self._buf = self._buf[:n], self._buf[n:]
        if len(out) < n:
            out = np.pad(out, (0, n - len(out)))
        return out

    def clear(self) -> None:
        with self._lock:
            self._buf = np.zeros(0, np.float32)

    @property
    def seconds(self) -> float:
        return len(self._buf) / OUT_SR


class VoiceLoop(threading.Thread):
    def __init__(
        self,
        orchestrator: Orchestrator,
        emit: Emit,
        *,
        design: str = "A",
        model_id: str = "mlx-community/NemotronLabs-VoiceChat-11B-8bit",
        mic: bool = True,
        speaker: bool = True,
        input_device=None,
        output_device=None,
    ):
        super().__init__(daemon=True, name="voice-loop")
        self.orch = orchestrator
        self.emit = emit
        self.design = design
        self.model_id = model_id
        self.use_mic, self.use_speaker = mic, speaker
        self.input_device, self.output_device = input_device, output_device
        self._in: queue.Queue[np.ndarray] = queue.Queue()
        self._sim = np.zeros(0, np.float32)  # synthesized "user" speech to mix in
        self._sim_lock = threading.Lock()
        self._commands: queue.Queue[tuple] = queue.Queue()
        self._results: queue.Queue[tuple] = queue.Queue()
        self.out = AudioOut()
        self.waiting_tone = True
        self.status = "loading"
        self.stats: dict = {}

    # ----- control (any thread) -----------------------------------------
    def set_design(self, design: str) -> None:
        self._commands.put(("design", design))

    def new_conversation(self) -> None:
        self._commands.put(("reset",))

    def say(self, text: str) -> None:
        """Speak ``text`` with macOS `say` into the model as if from the mic."""

        def work():
            with tempfile.TemporaryDirectory() as tmp:
                aiff, wav = Path(tmp) / "u.aiff", Path(tmp) / "u.wav"
                subprocess.run(["say", "-o", str(aiff), text], check=True)
                subprocess.run(
                    ["afconvert", "-f", "WAVE", "-d", f"LEI16@{IN_SR}", "-c", "1", str(aiff), str(wav)],
                    check=True,
                )
                import soundfile as sf

                audio, _ = sf.read(wav, dtype="float32")
            with self._sim_lock:
                self._sim = np.concatenate([self._sim, audio])
            self.emit("simulated_speech", {"text": text})

        threading.Thread(target=work, daemon=True).start()

    # ----- loop ---------------------------------------------------------
    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:
            self.status = "error"
            self.emit("status", {"status": "error", "error": f"{type(exc).__name__}: {exc}"})
            raise

    def _set_status(self, status: str, **extra) -> None:
        self.status = status
        self.emit("status", {"status": status, "design": self.design, **extra})

    def _run(self) -> None:
        from mlx_vlm import load

        self._set_status("loading")
        speedups.apply()
        model, processor = load(self.model_id)
        speedups.quantize_speech_path(model, perception=True)
        self.voicechat = model.create_session(processor)
        self._new_session()
        self._start_audio()

        frame_times: collections.deque[float] = collections.deque(maxlen=50)
        last_stats = time.time()
        while True:
            self._handle_commands()
            if self.session.pending_call is not None:
                self._wait_for_tool()
                continue
            self._maybe_inject_b()
            self._update_hold_c()
            try:
                frame = self._in.get(timeout=0.2)
            except queue.Empty:
                continue
            dropped = 0
            while self._in.qsize() > MAX_BACKLOG:
                self._in.get_nowait()
                dropped += 1
            if dropped:
                self.emit("warning", {"message": f"voice fell behind; dropped {dropped * 80} ms of input"})
            frame = self._mix_sim(frame)
            if float(np.sqrt(np.mean(frame * frame))) > VOICE_RMS:
                self._last_voice = time.time()
                self._speech_run += 1
                if self._speech_run >= 3:  # ~240 ms of speech: the user took a turn
                    self.session.floor_open = self.session.call_open = True
            else:
                self._speech_run = 0
            start = time.perf_counter()
            events = self.session.push_audio(frame, sample_rate=IN_SR)
            frame_times.append(time.perf_counter() - start)
            self._handle_events(events)
            if self._trace_frames > 0 and self.session._text_tokens:
                self._trace_frames -= 1
                ss = self.session
                self.emit("frame_trace", {"text": ss._text_tokens[-1], "fn": ss._function_tokens[-1],
                                          "hold": ss.hold_speech, "paused": ss.paused,
                                          "await": ss._await_speech, "floor": ss.floor_open,
                                          "quiet": ss.quiet_frames, "nudges": ss.nudges,
                                          "suppressed": ss.suppressed})
            self._maybe_end_turn_b()
            self._maybe_decide_c()
            if time.time() - last_stats > 1.0:
                last_stats = time.time()
                mean = sum(frame_times) / max(1, len(frame_times))
                self.stats = {
                    "frame_ms": round(mean * 1000, 1),
                    "realtime_factor": round(mean / FRAME_S, 2),
                    "backlog_frames": self._in.qsize(),
                    "out_buffer_ms": round(self.out.seconds * 1000),
                    "conversation_s": round(self.session.frame_index * FRAME_S),
                }
                self.emit("stats", self.stats)

    def _new_session(self) -> None:
        self._set_status("warming", detail="prefilling the system prompt")
        start = time.perf_counter()
        if self.design in ("A", "C"):
            prompt = tools_system_prompt(voice_system_message(), VOICE_TOOLS)
        else:
            prompt = tools_system_prompt(DESIGN_B_SYSTEM, VOICE_TOOLS)
        self.session = create_tool_session(
            self.voicechat, system_prompt=prompt, seed=0, mute_function_channel=self.design == "B"
        )
        self._decided_upto = 0  # Design C: user transcript chars already ruled on
        self._pending_decision: tuple[int, float] | None = None  # (transcript end, started)
        self._decisions: queue.Queue[tuple[int, str, dict]] = queue.Queue()
        self._ready_decisions: dict[int, dict] = {}
        self._last_voice = 0.0
        self._last_tone = 0.0
        self._tool_job: threading.Thread | None = None
        self._tool_cancel: threading.Event | None = None
        self._tool_id = 0
        self._user_len_at_tool = -1  # transcript length when the last tool started
        self._wait_frames: collections.deque[np.ndarray] = collections.deque(maxlen=20)
        self._voiced_run = 0
        self._speech_run = 0
        # VOICE_TRACE=1: per-frame session state for 40 frames after each tool result
        self._trace_frames = 0
        self._user_text = ""
        self._turn_start = 0
        self._last_user_change = 0.0
        self._agent_text = ""
        self._agent_turn_start = 0
        self._idle_frames = 0
        self._recent: collections.deque[str] = collections.deque(maxlen=8)
        while not self._results.empty():
            self._results.get_nowait()
        self.out.clear()
        with self._in.mutex:
            self._in.queue.clear()
        self._set_status("listening", prefill_s=round(time.perf_counter() - start, 1))

    def _handle_commands(self) -> None:
        # Collapse queued clicks: only the last design matters, and one
        # session rebuild covers any number of resets.
        rebuild, design = False, self.design
        while not self._commands.empty():
            cmd = self._commands.get_nowait()
            if cmd[0] == "design" and cmd[1] in ("A", "B", "C"):
                design = cmd[1]
            elif cmd[0] == "reset":
                rebuild = True
        if design != self.design or rebuild:
            self.design = design
            self._new_session()

    # ----- audio I/O ------------------------------------------------------
    def _start_audio(self) -> None:
        if self.use_mic:
            import sounddevice as sd

            def on_input(indata, frames, t, status):
                self._in.put(indata[:, 0].copy())

            self._mic = sd.InputStream(
                samplerate=IN_SR, channels=1, dtype="float32", blocksize=FRAME,
                device=self.input_device, callback=on_input,
            )
            self._mic.start()
        else:
            def clock():
                next_t = time.perf_counter()
                while True:
                    next_t += FRAME_S
                    self._in.put(np.zeros(FRAME, np.float32))
                    time.sleep(max(0.0, next_t - time.perf_counter()))

            threading.Thread(target=clock, daemon=True, name="silence-clock").start()
        if self.use_speaker:
            import sounddevice as sd

            def on_output(outdata, frames, t, status):
                outdata[:, 0] = self.out.read(frames)

            self._spk = sd.OutputStream(
                samplerate=OUT_SR, channels=1, dtype="float32", blocksize=0,
                device=self.output_device, callback=on_output,
            )
            self._spk.start()

    def _mix_sim(self, frame: np.ndarray) -> np.ndarray:
        with self._sim_lock:
            if not len(self._sim):
                return frame
            chunk, self._sim = self._sim[:FRAME], self._sim[FRAME:]
        chunk = np.pad(chunk, (0, FRAME - len(chunk)))
        return frame + chunk

    # ----- events ---------------------------------------------------------
    def _handle_events(self, events) -> None:
        pad = self.session.config.pad_token_id
        silence = self.session.config.silence_token_id
        for ev in events:
            if ev.kind == "user_transcript_delta":
                if (ev.text or "") != self._user_text:
                    self.session.floor_open = self.session.call_open = True
                self._user_text = ev.text or ""
                self._last_user_change = time.time()
                self.emit("user_partial", {"text": self._user_text[self._turn_start:].strip()})
            elif ev.kind == "assistant_text_delta":
                self._agent_text = ev.text or ""
                self.emit("agent_partial", {"text": self._agent_text[self._agent_turn_start:].strip()})
            elif ev.kind == "audio":
                if self.use_speaker:
                    self.out.write(np.asarray(ev.samples, dtype=np.float32))
            elif ev.kind == "function_delta" and ev.delta is None:
                self._close_agent_turn()
                calls = parse_tool_call(ev.text)
                self.emit("tool_call", {"raw": ev.text, "calls": [
                    {"name": c.name, "arguments": c.arguments, "error": c.error} for c in calls]})
                first = calls[0]
                if self.design == "C":
                    self._decided_upto = len(self._user_text)
                    self._pending_decision = None
                    self.session.hold_speech = False
                    self.emit("hold", {"held": False, "reason": "voice model called a tool itself"})
                if len(self._user_text) == self._user_len_at_tool:
                    # The user has not spoken since the last tool: the model is
                    # retrying on its own. Refuse instead of looping.
                    self.emit("warning", {"message": "voice model retried a tool without new user input; refused"})
                    self._tool_id += 1
                    self._results.put(("A", first.name, first.arguments, {
                        "status": "not run",
                        "summary": "Already tried. Tell the user it did not work and ask what they want."},
                        self._tool_id))
                else:
                    # One tool at a time: NVIDIA documents parallel calls as unreliable.
                    self._start_tool("A", first.name, first.arguments)
        last = self.session._text_tokens[-1] if self.session._text_tokens else pad
        self._idle_frames = self._idle_frames + 1 if last in (pad, silence) else 0
        if self._idle_frames == IDLE_FRAMES * 2:
            self._close_agent_turn()

    def _close_agent_turn(self) -> None:
        said = self._agent_text[self._agent_turn_start:].strip()
        if said:
            self._recent.append(f"Rose: {said}")
            self.emit("agent_turn", {"text": said})
        self._agent_turn_start = len(self._agent_text)

    def _start_tool(self, origin: str, name: str, args: dict) -> None:
        self._tool_id += 1
        job, cancel = self._tool_id, threading.Event()
        self._tool_cancel = cancel
        self._user_len_at_tool = len(self._user_text)
        self._wait_frames.clear()
        self._voiced_run = 0
        self._tool_job = threading.Thread(
            target=lambda: self._results.put(
                (origin, name, args, self.orch.run_tool(name, args, cancel=cancel), job)),
            daemon=True,
        )
        self._tool_job.start()

    def _wait_for_tool(self) -> None:
        """Timeline frozen while a tool runs. Mic audio is not fed to the model,
        but if the user talks for ~0.5 s the tool is cancelled and the model
        hears what they said."""

        if self.status != "tool":
            self._set_status("tool", detail="voice model is waiting on a tool")
        if self.use_speaker and self.waiting_tone and time.time() - self._last_tone > 1.6:
            self._last_tone = time.time()
            self.out.write(WAIT_TONE)
        while not self._in.empty():
            frame = self._mix_sim(self._in.get_nowait())
            self._wait_frames.append(frame)
            voiced = float(np.sqrt(np.mean(frame * frame))) > VOICE_RMS
            self._voiced_run = self._voiced_run + 1 if voiced else max(0, self._voiced_run - 1)
        if self._voiced_run >= INTERRUPT_FRAMES:
            self._interrupt_tool()
            return
        try:
            item = self._results.get(timeout=0.1)
        except queue.Empty:
            return
        _, name, _, result = item[:4]
        if len(item) > 4 and item[4] != self._tool_id:
            return  # result of a cancelled tool
        start = time.perf_counter()
        self.session.inject_tool_response([result])
        self._drain_input()
        if self.design == "C":
            # Speech heard before the result belongs to the turn just answered.
            self._decided_upto = len(self._user_text)
            self._pending_decision = None
            self._ready_decisions.clear()
        if os.environ.get("VOICE_TRACE"):
            self._trace_frames = 40
        self.emit("injected", {"name": name, "ms": round((time.perf_counter() - start) * 1000)})
        self._set_status("listening")

    def _interrupt_tool(self) -> None:
        if self._tool_cancel is not None:
            self._tool_cancel.set()
        self._tool_id += 1  # its eventual result is ignored
        self.emit("warning", {"message": "user interrupted; tool cancelled"})
        self.session.inject_tool_response(
            [{"status": "cancelled", "summary": "Stopped because the user started talking. Listen to them."}])
        self._drain_input()
        heard = list(self._wait_frames)
        self._wait_frames.clear()
        self._voiced_run = 0
        self._set_status("listening")
        if self.design == "C":
            # Earlier speech is settled; the interruption is a new turn to decide.
            self._decided_upto = len(self._user_text)
            self._pending_decision = None
            self._ready_decisions.clear()
        if heard:
            # Let the model hear the interruption it would otherwise have missed.
            self._handle_events(self.session.push_audio(np.concatenate(heard), sample_rate=IN_SR))

    def _drain_input(self) -> None:
        # Audio that arrived during an injection belongs to the frozen part
        # of the timeline; drop it rather than reporting a backlog.
        while not self._in.empty():
            self._in.get_nowait()

    # ----- Design B -------------------------------------------------------
    def _maybe_end_turn_b(self) -> None:
        if self.design != "B":
            return
        text = self._user_text[self._turn_start:].strip()
        if not text or time.time() - self._last_user_change < TURN_GAP_S:
            return
        self._turn_start = len(self._user_text)
        self._recent.append(f"User: {text}")
        self.emit("user_turn", {"text": text})
        recent = list(self._recent)[:-1]

        def job():
            decision = self.orch.decide(text, recent)
            outcome = self.orch.run_decision(decision)
            if outcome is not None:
                self._results.put(("B", *outcome))

        threading.Thread(target=job, daemon=True).start()

    def _maybe_inject_b(self) -> None:
        if self.design in ("A", "C"):
            if self.status != "tool":
                self._emit_user_turn_a()
            return
        if self._results.empty() or self._idle_frames < IDLE_FRAMES:
            return
        _, name, args, result = self._results.get_nowait()
        self._close_agent_turn()
        start = time.perf_counter()
        self.session.inject_tool_exchange(name, args, [result])
        self._drain_input()
        self.emit("injected", {"name": name, "ms": round((time.perf_counter() - start) * 1000)})

    def _emit_user_turn_a(self) -> None:
        # For the transcript panel only: close a user turn after a pause.
        text = self._user_text[self._turn_start:].strip()
        if text and time.time() - self._last_user_change >= TURN_GAP_S:
            self._turn_start = len(self._user_text)
            self._recent.append(f"User: {text}")
            self.emit("user_turn", {"text": text})

    # ----- Design C (hybrid) ----------------------------------------------
    # The voice model keeps its own tool calls. The decider starts on a short
    # pause in the user's speech. If the voice model tries to start talking
    # before the decider has ruled, that one frame is muted and the whole
    # timeline pauses (as during a tool call), so when it resumes the model is
    # still at the moment it chose to speak. Muting it for many frames instead
    # makes it give up the turn.

    def _undecided(self) -> str:
        return self._user_text[self._decided_upto:].strip()

    def _quiet_for(self) -> float:
        # Acoustic, not transcript: transcript words arrive in bursts, so a
        # gap between updates is not a pause in the user's speech.
        return time.time() - self._last_voice

    def _collect_decisions(self) -> None:
        while not self._decisions.empty():
            end, _, decision = self._decisions.get_nowait()
            self._ready_decisions[end] = decision
            if self._pending_decision and self._pending_decision[0] == end:
                self._pending_decision = None

    def _start_decider(self) -> None:
        text, end = self._undecided(), len(self._user_text)
        if not text or self._pending_decision or end in self._ready_decisions:
            return  # one decider at a time; a stale result triggers a re-run
        self._pending_decision = (end, time.time())
        recent = list(self._recent)

        def job():
            self._decisions.put((end, text, self.orch.decide(text, recent)))

        threading.Thread(target=job, daemon=True).start()

    def _update_hold_c(self) -> None:
        if self.design != "C":
            return
        self._collect_decisions()
        end = len(self._user_text)
        if self.session.paused:
            self._drain_input()
            decision = self._ready_decisions.pop(end, None)
            if decision is not None:
                self._apply_decision(end, decision)
            elif self._pending_decision is None:
                self._start_decider()  # the finished one was for older text
            elif time.time() - self._pending_decision[1] > DECIDE_TIMEOUT_S:
                self._decided_upto, self._pending_decision = end, None
                self.session.paused = False
                self.emit("warning", {"message": "decider timed out; letting the voice model answer"})
            else:
                time.sleep(0.02)
            return
        if self._undecided() and self._quiet_for() >= TURN_GAP_S:
            # Long pause and the voice model did not try to talk: still apply
            # a think/act decision so the work gets done.
            decision = self._ready_decisions.pop(end, None)
            if decision is not None:
                self._apply_decision(end, decision)
                return
        held = bool(self._undecided())
        if held != self.session.hold_speech:
            self.session.hold_speech = held

    def _apply_decision(self, end: int, decision: dict) -> None:
        self._decided_upto = end
        self._pending_decision = None
        self._ready_decisions.clear()
        self.session.wants_to_speak = False
        self.session.hold_speech = False
        self.session.paused = False
        outcome = self.orch.tool_for(decision)
        if outcome is None:
            self.emit("hold", {"held": False, "reason": "decider: chat"})
            return
        name, args = outcome
        call = self.session.begin_tool_call(name, args)
        self.emit("tool_call", {"raw": call, "by": "decider",
                                "calls": [{"name": name, "arguments": args}]})
        self._close_agent_turn()
        self._start_tool("C", name, args)

    def _maybe_decide_c(self) -> None:
        if self.design != "C":
            return
        if not self._undecided():
            self.session.wants_to_speak = False
            return
        if self._quiet_for() < DECIDE_GAP_S:
            # Mid-utterance attempts to talk are backchannels; stay muted.
            if self.session.wants_to_speak:
                self.emit("muted", {"quiet_s": round(self._quiet_for(), 2)})
            self.session.wants_to_speak = False
            return
        self._start_decider()
        if self.session.wants_to_speak and not self.session.paused:
            self.session.paused = True
            self.emit("hold", {"held": True, "reason": "voice model wants to answer; waiting for the decider"})
