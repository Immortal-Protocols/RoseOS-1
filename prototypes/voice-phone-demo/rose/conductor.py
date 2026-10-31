"""The conductor: who speaks when, and what Rose remembers.

Everything conversational is decided here, on one conductor thread fed by a
queue (listener events, LLM tokens, client playback, task results, phone
notifications), so there are no races between those sources. Model work runs
on the runtime thread through a single *talker worker* job that executes
talker operations strictly in order; only that job touches the talker's
cache.

Rules that make Rose feel attentive rather than chatty:
- She speaks only to answer a user turn, or to report a finished task or a
  notification at a quiet moment. Never filler, never unprompted.
- When the user starts talking over her, her voice ducks at once; real speech
  stops her, while a backchannel ("mm-hmm") or a cough lets her continue.
- What she remembers saying is what the user actually heard: an interrupted
  reply is rewound in the cache and re-written up to the cut, ending in a dash.
- If the user resumes before she starts speaking, she drops her draft and
  waits, then answers the whole turn.
- Background tasks are never cancelled by interruptions.
"""

from __future__ import annotations

import collections
import json
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from .brain import GEN_FAST, Brain, Mark, Sampling, Sequence, SpeechSplitter, ToolCall, parse_tool_calls, \
    tool_segment, user_segment
from .runtime import TALK, Runtime, Sleep
from .textnorm import speakable

# Listener sounds that do not take the floor. "yes"/"no" are left out on purpose:
# they answer questions and confirm actions.
BACKCHANNELS = {
    "mm", "mmm", "mmhm", "mmhmm", "mm-hmm", "mhm", "uh-huh", "uhhuh", "uh", "um", "umm", "uhm", "er",
    "erm", "hm", "hmm", "huh", "ah", "oh", "yeah", "yep", "yup", "ya", "right", "okay", "ok",
    "alright", "sure", "cool", "nice", "wow", "gotcha", "got", "it", "i", "see", "true", "totally",
    "exactly",
}
# Words that make Rose yield at once, even alone.
# ("mm-mm" is left out: speech recognition writes "mm-hmm" that way too.)
STOP_WORDS = {"stop", "wait", "hold", "pause", "shh", "quiet", "enough", "no", "nope", "uh-uh",
              "nuh-uh", "sorry", "actually"}

TALKER_TOOLS = [
    {"type": "function", "function": {
        "name": "calculate",
        "description": ("Exact arithmetic, instantly. Use it for any calculation with more than one "
                        "step (scaling recipes, splitting bills, percentages, unit conversions)."),
        "parameters": {"type": "object", "properties": {
            "expression": {"type": "string",
                           "description": "A plain arithmetic expression, e.g. 1.5 * 7 / 4"}},
            "required": ["expression"]}}},
    {"type": "function", "function": {
        "name": "phone_task",
        "description": ("Do something on the user's phone in the background: send a text, set, "
                        "change or turn off an alarm, change a setting (Do Not Disturb, Wi-Fi, "
                        "Bluetooth, brightness), play, pause or skip music, find places nearby "
                        "(pharmacy, coffee, grocery, gas, restaurants...) with their distance and "
                        "hours, start directions, or open an app. Do not use it to read messages, "
                        "calendar, alarms or settings: you already have those. Returns at once with a "
                        "task id; the result arrives later."),
        "parameters": {"type": "object", "properties": {
            "goal": {"type": "string",
                     "description": "Exactly what to do, with every detail the user gave (names, "
                                    "message text in their words, times)."}},
            "required": ["goal"]}}},
    {"type": "function", "function": {
        "name": "think",
        "description": ("Think a hard problem through carefully in the background (multi-step math, "
                        "planning, comparing options). Do not use for easy questions."),
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string",
                         "description": ("The question in one or two sentences with every number and "
                                         "detail they gave. Do not work it out here.")}},
            "required": ["question"]}}},
    {"type": "function", "function": {
        "name": "update_task",
        "description": "Change a running task when the user changes their mind.",
        "parameters": {"type": "object", "properties": {
            "task_id": {"type": "integer"},
            "instruction": {"type": "string", "description": "What should be different."}},
            "required": ["task_id", "instruction"]}}},
    {"type": "function", "function": {
        "name": "cancel_task",
        "description": "Stop a running task.",
        "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}},
                       "required": ["task_id"]}}},
]

SYSTEM = """You are Rose, the user's voice assistant on their phone. You are talking with them live: everything you write is spoken aloud.

How you talk:
- Warm, natural and quick, like a sharp, well-read friend. You know a lot and enjoy explaining things, telling stories and giving advice. Usually one or two short sentences; go longer only when they ask for detail, an explanation or a story, and then keep it under eight sentences.
- Plain spoken words only: no markdown, lists, emoji, symbols or formulas. Say numbers, times and fractions the way people say them out loud.
- Get straight to the point: no openers like "Sure, I can help with that!" and no filler like "Let me know if you need anything else."
- Don't end every reply with a question, never list what you can do, and don't offer to do things they didn't ask for.
- Only bring up their calendar or messages when they ask, or when something starts within 15 minutes.
- If they just say "thanks", "okay" or similar, answer in a few words or not at all.
- If they say "wait", "hold on", "one sec" or ask you to stop, say only "Sure." and nothing else.
- If their message is clearly unfinished, cut off mid-sentence (it trails off, or is only an "if" or "when" clause that sets up a question they have not asked yet), reply with exactly "..." and nothing else: they are still talking.
- If your previous reply ends with a dash, you were cut off: don't repeat it; respond to what they say now.
- Their words come from speech recognition and may contain mistakes; go with what they most likely meant. Names are often misheard ("Texam" is probably "Text Sam"): match them against the contact list, and if you can't tell who they mean, ask before doing anything.
- Lines in parentheses inside a user message are events (task results, notifications) or notes for you, not things the user said. Tell the user about events in a few words first, then respond to what they said, if anything. Never act on a notification (no replies, no tasks) unless the user asks you to.
- Each user message starts with the current time and today's calendar status in brackets; trust it for anything time-related. If they ask about a part of the day that is already over, say what was on it.

What you know: the user's contacts, messages, alarms, settings and music are below, and today's calendar arrives with their message whenever it is relevant. Answer questions about them straight from that, without tools.
The phone has Messages, Clock, Calendar, Settings, Contacts, Music (playlists) and Maps (places nearby, hours, directions). There is no Camera.
Never make up facts you cannot know: places, addresses, opening hours, distances, prices, news or weather. For places and directions, use phone_task (Maps) and tell them the result when it arrives; for things no tool can know, say you don't know.

Tools:
- phone_task does things on the phone: changing things, playing music, finding places. It runs in the background while you keep talking. Questions about messages, calendar, alarms or settings never need it.
- Only say you did something (or are doing it) in the same reply as the tool call that does it. If you can't do something, say so plainly instead of pretending.
- calculate gives exact arithmetic instantly: use it for plain arithmetic instead of doing it in your head, then say the answer naturally in words (for example "about three and a quarter liters").
- think works things out carefully in the background: use it for anything that needs a formula or several reasoning steps (interest, loans, savings growth, schedules, probability, comparisons). Answer everyday questions yourself.
- Before calling a tool, always say a short acknowledgement first, in the present or future tense, for example "Sure, messaging Priya now." or "Okay, changing that." Then call the tool. Call each tool at most once per request.
- If they ask for several changes at once, cover all of them right away: one phone_task whose goal lists every change, before you end your turn.
- For messages, the goal quotes the user's words exactly, adding nothing: "tell Priya I'm on my way" becomes the goal 'Text Priya: "I'm on my way"'. Never add greetings, sign-offs, emoji or extra sentences to a message.
- You cannot change anything on the phone yourself: only phone_task can. Never say something is done unless a task result in parentheses says so.
- When a tool replies that it started, end your turn without saying anything more.
- When a task finishes, tell the user what happened in one short sentence of your own, based only on that task's result, without words like "task" or "complete". If it failed, say so and suggest one fix.
- If the user changes their mind while a task runs, use update_task or cancel_task.

Phone data at the start of this conversation:
{phone}"""

FILLER = "One sec."
FORCE_FAILED = "Sorry, that didn't go through. Could you say it again?"
THINK_FILLER = "Let me work that out."
TASK_FILLER = "On it."
FILLER_DELAY_S = 0.35  # after the end of their speech: not over someone who is still going
# Room for a long tool call; spoken length is limited by _runaway (MAX_SENTENCES).
TALK_SAMPLING = Sampling(temperature=0.6, top_p=0.8, top_k=20, presence_penalty=0.0, max_tokens=420)
# Barge-in: duck at the first speech frame, pause (silent, position kept) once
# speech is clearly there, then stop for good or resume if it was a backchannel.
# She is silent once paused, so the final stop can wait for recognised words;
# the voiced-time limits only catch speech the recognizer cannot make out.
BARGE_PAUSE_S = 0.2        # voiced time that pauses Rose
BARGE_VOICED_S = 1.0       # voiced time that stops Rose when no words are recognised yet
BARGE_BACKCHANNEL_S = 1.6  # ... when the words so far are only "mm-hmm", "yeah", ...


def _words(text: str) -> list[str]:
    # "mm-hmm" and "uh-huh" stay one word; a bare "-" is not a word.
    return re.findall(r"[a-z']+(?:-[a-z']+)*", text.lower())


def _bc_word(w: str) -> bool:
    return w in BACKCHANNELS or (w not in STOP_WORDS and all(p in BACKCHANNELS for p in w.split("-")))


def is_backchannel(text: str) -> bool:
    words = _words(text)
    if words and len(words) <= 4 and _hum_like(text):
        return True  # "mm-hmm" the recognizer wrote as "NN Hun" / "Ann Hum"
    return (bool(words) and len(words) <= 4 and not (STOP_WORDS & set(words))
            and all(_bc_word(w) for w in words))


def _bc_ish(text: str) -> bool:
    """The live transcript so far could still be an "mm-hmm" ("And n", "MM Ham Hum")."""

    words = _words(text)
    return (bool(words) and not (STOP_WORDS & set(words))
            and sum(1 for w in words if not (_bc_word(w) or _hum_like(w))) < 2)


def _hum_like(text: str) -> bool:
    letters = re.sub(r"[^a-z]", "", text.lower())
    return 0 < len(letters) <= 12 and set(letters) <= set("mnhua") and not text.strip().endswith("?")


@dataclass
class Part:
    """One generated assistant message (a reply may continue after a tool response)."""

    mark: Mark | None = None
    ids: list[int] = field(default_factory=list)
    text: str = ""
    splitter: SpeechSplitter = field(default_factory=SpeechSplitter)
    after: str = ""  # segments appended after this message (tool responses)
    after_join: str = ""
    filler: str = ""  # spoken by the conductor while the model wrote a tool call
    thinking: bool = False  # inside a <think> block the model opened itself (never spoken)
    done: bool = False
    # Set on the runtime thread (ops are ordered, so _rewrite always sees them settled):
    complete: bool = False       # the message ended with its own stop token
    call_in_cache: bool = False  # a <tool_call> was written into the cache
    executed: bool = False       # its calls actually ran (conductor thread, before any rewrite)


@dataclass
class Reply:
    id: int
    user_text: str
    t_user_end: float | None
    carry_on: bool = False   # it answers an "mm-hmm" that cut Rose off: go on with the cut reply
    tool_hinted: bool = False  # the conductor told the model this turn needs phone_task
    pre_mark: Mark | None = None
    parts: list[Part] = field(default_factory=list)
    utterance: Any = None
    cancelled: bool = False
    generating: bool = True
    executed_tools: bool = False
    interrupted: bool = False
    finalized: bool = False
    is_announcement: bool = False
    t_first_token: float | None = None
    t_first_speech: float | None = None
    t_start: float = field(default_factory=time.monotonic)
    metrics_sent: bool = False
    listen: dict = field(default_factory=dict)
    events: list[str] = field(default_factory=list)  # event lines consumed by this reply
    announce: bool = False
    cancelled_generation: bool = False  # generation stopped early, reply still plays
    forced: bool = False  # the conductor already forced a tool call for this reply
    speculative: bool = False  # started on a tentative transcript; speech held until confirmed
    pending_speech: str = ""
    pending_part_done: bool = False
    unknown_recipient: bool = False  # they named someone who is not a contact: ask, don't send
    script: str = ""  # a fixed reply written into the cache and spoken, no sampling
    hold_until: float = 0.0  # short turns ("Yes,"): first audio waits for this much silence
    confirmed_send: bool = False  # the user confirmed a recipient Rose asked about
    accepted_offer: bool = False  # they said yes to something Rose offered to do
    head: str = ""          # first speech, held until it is clearly not the "..." marker
    forced_answer: bool = False  # after a "..." wait: must answer now
    filler_due: float = 0.0      # say FILLER at this time if nothing else has been said
    filler_part: Any = None
    head_done: bool = False
    pending_send: str = ""
    finish_pending: bool = False
    needs_followup: bool = False     # a tool refused: let Rose speak after its response
    followups: int = 0

    @property
    def speech(self) -> str:
        return "".join((p.filler + " " if p.filler else "") + p.splitter.spoken for p in self.parts)


class Conductor:
    def __init__(self, runtime: Runtime, brain: Brain, listener, speaker, tasks, phone, bus):
        self.runtime, self.brain = runtime, brain
        self.listener, self.speaker, self.tasks, self.phone, self.bus = listener, speaker, tasks, phone, bus
        self._q: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._ops: collections.deque[Callable[[], Iterator[Any]]] = collections.deque()
        self._thread = threading.Thread(target=self._loop, name="conductor", daemon=True)
        self._running = False
        self.talker: Sequence | None = None
        self.reply: Reply | None = None
        self._reply_ids = 0
        self._events: list[str] = []          # event lines for the next user message
        self._announce = False                # some pending event deserves a spoken update
        self._carry = ""                      # user text carried over when they resumed talking
        self._barge: dict | None = None       # {"t": start, "stopped": bool}
        self._last_user_end = 0.0
        self._last_rose_end = 0.0
        self._user_active = False             # an utterance is in progress (start .. turn_end)
        self._held_until = 0.0                # after "wait": more hold phrases get no reply
        self._stopped_by_utt = False          # the current utterance already stopped Rose
        self._answer_rid: int | None = None   # a "yeah" waiting for this reply's question to end
        self._pending_send = ""               # a message waiting for "did you mean ...?"
        self._unfinished_until = 0.0          # after "...": answer anyway at this time
        self._force_answer = False
        self._carry_on = False                # an abandoned carry-on draft: the next "mm-hmm" turn goes on
        self._cut: Reply | None = None        # the reply a barge-in last cut off (kept in memory)
        self._recent_tasks: list = []         # finished tasks, newest last (for corrections)
        self._status = ""
        self.history: list[dict] = []         # transcript for the dashboard / eval
        self.turn_metrics: list[dict] = []

    # ----------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._new_talker()
        self._running = True
        self.runtime.submit(self._talker_worker, priority=TALK, name="talker",
                            on_item=lambda item: self._q.put(item))
        self._thread.start()
        self._set_status("listening")

    def stop(self) -> None:
        self._running = False

    def reset(self) -> None:
        self._q.put(("reset", None))

    def _new_talker(self) -> None:
        system = SYSTEM.format(phone=_static_phone(self.phone))

        def build():
            seq = self.brain.sequence("talker")
            prefix = self.brain.system_prefix(system, TALKER_TOOLS)
            for _ in seq.append_text(prefix):
                pass
            # Warm up the turn-sized prefill and decode kernels, then forget it,
            # so the user's first turn is as fast as the rest.
            mark = seq.mark()
            for _ in seq.append_text(user_segment("Hello!") + GEN_FAST):
                pass
            for _ in seq.generate(Sampling(temperature=0.0, max_tokens=6)):
                pass
            seq.rewind(mark)
            return seq

        self.talker = self.runtime.run(build, priority=TALK, name="talker-prefix", timeout=120)

    # --------------------------------------------------------------- inputs
    def on_listen(self, ev) -> None:
        self._q.put(("listen", ev))

    def on_task_done(self, task) -> None:
        self._q.put(("task_done", task))

    def on_phone(self, what: str, info: dict) -> None:
        if what == "notification":
            self._q.put(("notification", info))

    def inject_text_turn(self, text: str) -> None:
        self._q.put(("text_turn", text))

    # ---------------------------------------------------------- talker job
    def _talker_worker(self) -> Iterator[Any]:
        """Runs on the runtime thread for the whole session; executes ops in order."""

        while self._running:
            if not self._ops:
                yield Sleep(0.004)
                continue
            op = self._ops.popleft()
            try:
                yield from op()
            except Exception as exc:  # report and keep serving
                import traceback

                traceback.print_exc()
                yield ("error", f"talker op failed: {type(exc).__name__}: {exc}")

    def _op(self, fn: Callable[[], Iterator[Any]]) -> None:
        self._ops.append(fn)

    def _join_prefix(self) -> str:
        """Text needed before a new segment so the cache stays well formed."""

        tokens = self.talker.tokens
        return "\n" if tokens and tokens[-1] == self.brain.im_end else ""

    # ------------------------------------------------------------ the loop
    def _loop(self) -> None:
        while True:
            try:
                kind, payload = self._q.get(timeout=0.02)
            except queue.Empty:
                kind, payload = None, None
            try:
                if kind is not None:
                    self._handle(kind, payload)
                self._tick()
            except Exception as exc:
                import traceback

                traceback.print_exc()
                self.bus.emit("error", message=f"conductor: {type(exc).__name__}: {exc}")

    def _handle(self, kind: str, payload: Any) -> None:
        if kind == "listen":
            self._on_listen(payload)
        elif kind == "token":
            self._on_token(*payload)
        elif kind == "part_done":
            self._on_part_done(*payload)
        elif kind == "heard":
            self._on_heard(*payload)
        elif kind == "scripted":
            rid, text = payload
            r = self.reply
            if r is not None and r.id == rid and not r.cancelled:
                r.t_first_token = time.monotonic()
                r.parts[-1].splitter.spoken = text
                self._speak(r, text)
                self._on_part_done(rid)  # normal end of reply (held if speculative)
        elif kind == "forced_call":
            r, part, text = payload
            if self.reply is r and not r.cancelled:
                if text is None:
                    self.bus.emit("warning", message="forced tool call failed or was cut short")
                    part.splitter.spoken += " " + FORCE_FAILED
                    self._speak(r, " " + FORCE_FAILED)
                    self._after_calls(r, part, [])
                    return
                part.splitter.calls_text = text
                calls = parse_tool_calls(text)
                self.bus.emit("warning", message=f"forced a tool call after an acknowledgement: {text!r}")
                self._after_calls(r, part, calls)
        elif kind == "task_done":
            self._on_task_done(payload)
        elif kind == "notification":
            self._add_event(f"Notification: new {payload.get('app', 'message')} from "
                            f"{payload.get('title', 'someone')}: \"{payload.get('text', '')}\"", announce=True)
        elif kind == "text_turn":
            self._user_turn(payload, t_user_end=time.monotonic(), listen={})
        elif kind == "reset":
            self._reset()
        elif kind == "error":
            self.bus.emit("error", message=str(payload))

    # ------------------------------------------------------ listener events
    def _on_listen(self, ev) -> None:
        r = self.reply
        if ev.kind in ("speech_start", "speech_resume", "partial"):
            self._user_active = True
        elif ev.kind in ("turn_end", "noise"):
            self._user_active = False
            self._last_user_end = time.monotonic()
        if ev.kind in ("speech_start", "speech_resume", "noise") and r is not None and r.speculative \
                and not r.finalized:
            self._drop_speculation(r, ev.kind)
            r = self.reply
        if ev.kind == "tentative":
            if self._barge is not None and not self._barge["stopped"]:
                # The final transcript beats the live one on whether it was only "mm-hmm".
                self._barge["backchannel"] = is_backchannel(ev.text)
            self._speculate(ev.text)
            return
        if ev.kind in ("speech_start", "noise"):
            self._stopped_by_utt = False
        if ev.kind == "speech_start":
            self._set_status("user_speaking")
            if r is not None and not r.finalized and self._rose_audible():
                if self._barge is not None and self._barge.get("rid") != r.id:
                    self._barge = None  # left over from an earlier reply
                if self._barge is None:
                    self._barge = {"t": time.monotonic(), "stopped": False, "voiced": 0.0,
                                   "last": time.monotonic(), "text": "", "rid": r.id}
                    self.speaker.duck()
                    self.bus.emit("barge", stage="duck", reason="user started speaking")
            elif r is not None and not r.finalized and not r.executed_tools and not self._audio_started(r):
                # They kept talking before Rose said anything: drop the draft, wait.
                self._abandon_draft(r)
        elif ev.kind == "partial":
            if self._barge is not None and not self._barge["stopped"]:
                text = ev.text
                self._barge["text"] = text
                words = _words(text)
                if not words or self._is_echo(text):
                    pass
                elif STOP_WORDS & set(words):
                    self._stop_rose("stop word", keep_turn=is_hold(text))
                    self._stopped_by_utt = True
                elif not _bc_ish(text) and not self._barge.get("backchannel"):
                    # (The live transcript writes "mm-hmm" as "And n" or "MM Ham Hum".)
                    self._stop_rose("user is talking", keep_turn=is_hold(text))
                    self._stopped_by_utt = True
        elif ev.kind in ("speech_pause", "noise"):
            # A pause inside an utterance (even inside a word) is not the end of
            # it: Rose only resumes once the utterance ends as noise or a backchannel.
            if ev.kind == "noise" and self._barge is not None and not self._barge["stopped"]:
                self._barge = None
                self._resume_rose()
                self.bus.emit("barge", stage="resume", reason="noise")
            if ev.kind == "noise" and self.reply is None and self._carry and self.listener_idle():
                # The draft was dropped for what turned out to be a cough: answer it now.
                text, self._carry = self._carry, ""
                self._user_turn(text, t_user_end=time.monotonic(), listen={})
            elif ev.kind == "noise" and self.listener_idle():
                self._set_status(self._idle_status())
        elif ev.kind == "speech_resume":
            self._set_status("user_speaking")
        elif ev.kind == "turn_end":
            text = (ev.text or "").strip()
            barging = self._barge is not None and not self._barge["stopped"]
            if barging and (r is None or r.finalized or r.id != self._barge.get("rid")):
                self._barge = None  # the reply it interrupted is over
                self._resume_rose()
                barging = False
            if barging and self._is_echo(text):
                # Rose's own words leaking into the mic: not a user turn.
                self._barge = None
                self._resume_rose()
                self.bus.emit("barge", stage="resume", reason=f"echo: {text}")
                return
            if barging and is_backchannel(text):
                self._barge = None
                self._resume_rose()
                self.bus.emit("barge", stage="resume", reason=f"backchannel: {text}")
                if self._at_final_question(r):
                    self._answer_later(r, text)
                return
            hold = is_hold(text)
            was_talking = (barging or self._stopped_by_utt
                           or (r is not None and not r.finalized and self._rose_audible()))
            # She stopped for what was only an "mm-hmm": the reply should go on.
            carry_on = self._stopped_by_utt and not barging and is_backchannel(text)
            self._stopped_by_utt = False
            if barging or (hold and was_talking):
                self._stop_rose("hold request" if hold else "user turn", keep_turn=hold)
            if hold and (was_talking or self._held_until > time.monotonic()):
                # "Wait", "hold on a sec" while she talks: stopping is the reply.
                if r is not None and r.speculative and not r.finalized:
                    self._drop_speculation(r, "hold")
                self._held_until = time.monotonic() + 4.0
                self.history.append({"role": "user", "text": text, "id": 0})
                self.bus.emit("user_turn", text=text, id=0, hold=True)
                return
            if r is not None and not r.finalized and self._rose_audible() and is_backchannel(text):
                if self._at_final_question(r):
                    self._answer_later(r, text)
                return
            listen = dict(ev.data or {})
            t_end = ev.t - listen.get("endpoint_ms", 0) / 1000.0
            if self._carry:
                text = (self._carry + " " + text).strip()
                self._carry = ""
            if r is not None and r.speculative and not r.finalized:
                if _norm(r.user_text) == _norm(text):
                    self._confirm_speculation(r, t_end, listen)
                    return
                self._drop_speculation(r, "transcript changed")
            if text and (r is None or r.finalized) and not self._force_answer and self._hold_fragment(text):
                return
            if text:
                self._user_turn(text, t_user_end=t_end, listen=listen, carry_on=carry_on)

    def _at_final_question(self, r: Reply | None) -> bool:
        """Rose is finishing a question, so a short "yeah" is an answer, not a backchannel."""

        if r is None or r.generating or r.utterance is None:
            return False
        speech = r.speech.strip()
        if not speech.endswith("?"):
            return False
        last = re.split(r"(?<=[.!?])\s+", speech)[-1]
        before = len(speakable(speech).split()) - len(speakable(last).split())
        try:
            return len(speakable(r.utterance.heard()).split()) >= before
        except Exception:
            return False

    def _answer_later(self, r: Reply, text: str) -> None:
        """Keep the answer; submit it once Rose has finished her question."""

        self._carry = (self._carry + " " + text).strip()
        self._answer_rid = r.id

    def _resume_rose(self) -> None:
        resume = getattr(self.speaker, "resume", None)
        if resume is not None:
            resume()
        else:
            self.speaker.unduck()

    def listener_idle(self) -> bool:
        return not getattr(self.listener, "in_speech", False)

    def _rose_audible(self) -> bool:
        """Rose's voice is playing, or her reply is mid-way (between chunks)."""

        if getattr(self.speaker, "audible", False):
            return True
        r = self.reply
        return (r is not None and not r.finalized and r.utterance is not None
                and r.utterance.first_audio_at is not None and not r.utterance.done.is_set())

    def _audio_started(self, r: Reply) -> bool:
        return r.utterance is not None and r.utterance.first_audio_at is not None

    def _is_echo(self, text: str) -> bool:
        """The words look like Rose's own recent speech leaking into the mic."""

        r = self.reply
        if r is None or r.utterance is None:
            return False
        try:
            played = r.utterance.heard()
        except Exception:
            return False
        recent = set(_words(played)[-14:])
        words = [w for w in _words(text) if w not in BACKCHANNELS]
        return len(words) >= 2 and sum(w in recent for w in words) / len(words) >= 0.8

    # --------------------------------------------------------- user turns
    def _user_turn(self, text: str, t_user_end: float | None, listen: dict, carry_on: bool = False) -> None:
        self._last_user_end = time.monotonic()
        r = self.reply
        if r is not None and not r.finalized:
            # A new turn supersedes whatever Rose is doing.
            if self._audio_started(r) or self._rose_audible():
                self._stop_rose("new user turn")
            elif not r.executed_tools:
                self._abandon_draft(r)
                if self._carry:
                    text = (self._carry + " " + text).strip()
                    self._carry = ""
            else:
                self._finish_reply(r, cancel=True)
        if self._carry:
            text = (self._carry + " " + text).strip()
            self._carry = ""
        self._answer_rid = None
        self._unfinished_until = 0.0
        if self._barge is not None and (self._barge["stopped"] or self.reply is not r):
            self._barge = None  # the interruption it tracked is over
        cut = self._cut
        reply_carry_on = ((carry_on or self._carry_on) and is_backchannel(text) and cut is not None
                          and not self._pending_send and not self._at_final_question(cut))
        self._carry_on = False
        self._reply_ids += 1
        reply = Reply(self._reply_ids, text, t_user_end, listen=listen, carry_on=reply_carry_on)
        reply.hold_until = _short_turn_hold(text, t_user_end)
        self.reply = reply
        self.history.append({"role": "user", "text": text, "id": reply.id})
        self.bus.emit("user_turn", text=text, id=reply.id)
        self._set_status("thinking")
        segment = self._compose_user(text, reply)
        self._start_generation(reply, segment)

    def _hold_fragment(self, text: str) -> bool:
        """A lone "If I save $250 a month." is a setup, not a question: keep
        listening (as if the model had answered "...") without asking the model."""

        if not _conditional_fragment(text):
            return False
        self._carry = text
        self._unfinished_until = time.monotonic() + UNFINISHED_WAIT_S
        self.bus.emit("barge", stage="wait", reason="a condition with no question yet")
        self._set_status("listening")
        return True

    def _speculate(self, text: str) -> None:
        """Start answering on the early transcript of a pause. Nothing is said or
        done until the listener commits the same words; if they keep talking the
        draft is dropped (rewound) at no cost to the user."""

        r = self.reply
        text = ((self._carry + " ") if self._carry else "") + (text or "").strip()
        if r is not None and r.speculative and not r.finalized and _norm(r.user_text) != _norm(text):
            self._drop_speculation(r, "newer tentative")  # they went on without a real pause
            r = self.reply
        if (not text or (r is not None and not r.finalized) or self._barge is not None
                or self._rose_audible() or is_hold(text) or is_backchannel(text)
                or _conditional_fragment(text)):
            return
        self._reply_ids += 1
        reply = Reply(self._reply_ids, text, None, speculative=True)
        self.reply = reply
        self.bus.emit("speculation", stage="start", id=reply.id, text=text)
        self._start_generation(reply, self._compose_user(text, reply))

    def _confirm_speculation(self, r: Reply, t_user_end: float, listen: dict) -> None:
        r.speculative = False
        r.hold_until = _short_turn_hold(r.user_text, t_user_end)
        r.t_user_end, r.listen = t_user_end, listen
        self._last_user_end = time.monotonic()
        self.history.append({"role": "user", "text": r.user_text, "id": r.id})
        self.bus.emit("user_turn", text=r.user_text, id=r.id)
        self.bus.emit("speculation", stage="confirm", id=r.id,
                      head_start_ms=round((time.monotonic() - r.t_start) * 1000))
        self._set_status("thinking")
        if r.pending_speech:
            speech, r.pending_speech = r.pending_speech, ""
            self._speak(r, speech)
            if r.parts and r.parts[-1].splitter.in_call and r.utterance is not None:
                flush = getattr(r.utterance, "flush", None)
                if flush is not None:
                    flush()
        elif r.parts and (r.parts[-1].splitter.in_call or r.parts[-1].splitter.calls_text):
            # The model went straight to a tool while we waited.
            self._schedule_filler(r, r.parts[-1])
        if r.t_first_token is not None and r.t_first_token < t_user_end:
            r.t_first_token = t_user_end  # tokens were ready before the turn ended
        if r.pending_part_done:
            r.pending_part_done = False
            self._on_part_done(r.id)

    def _wait_for_more(self, r: Reply) -> None:
        """Rose judged the turn unfinished ("..."): keep listening. If they stay
        quiet, she answers what they said after UNFINISHED_WAIT_S."""

        self.bus.emit("barge", stage="wait", reason="the turn sounds unfinished", id=r.id)
        self._abandon_draft(r)
        self._unfinished_until = time.monotonic() + UNFINISHED_WAIT_S
        self._set_status("listening")

    def _drop_speculation(self, r: Reply, why: str) -> None:
        r.cancelled = True
        r.finalized = True
        self._pending_send = self._pending_send or r.pending_send
        self._events = r.events + self._events
        self._announce = self._announce or r.announce
        self._carry_on = self._carry_on or r.carry_on

        def op():
            if r.pre_mark is not None:
                self.talker.rewind(r.pre_mark)
            return
            yield

        self._op(op)
        self.bus.emit("speculation", stage="drop", id=r.id, reason=why)
        if self.reply is r:
            self.reply = None

    def _compose_user(self, text: str, reply: Reply) -> str:
        stamp = time.strftime("%-I:%M %p")
        calendar, soon = _calendar_status(self.phone)
        # Not for a bare "what time is it?": at 1 AM the whole day ahead made her count the
        # hours to each event, list them, or set an alarm nobody asked for.
        if calendar and (soon or not text or (_TIMEY.search(text) and not _PLAIN_TIME_Q.match(text.strip())
                                              and not _SMALL_TALK.search(text))):
            stamp += f"; {calendar}"
        part_of_day = _part_of_day_events(self.phone, text)
        if part_of_day:
            stamp += f"; {part_of_day}"
        running = [t for t in self.tasks.running()] if self.tasks else []
        lines = [f"[{stamp}]"]
        if running:
            lines[0] = f"[{stamp}; running tasks: " + "; ".join(
                f"#{t.id} {t.kind} '{t.goal}'" for t in running) + "]"
        lines += [f"({e})" for e in self._events]
        hint = self._tool_hint(text)
        reply.tool_hinted = "phone_task" in hint
        if text and not hint and self._accepts_offer(text):
            hint = "They said yes to what you just offered: do it now with phone_task."
            reply.accepted_offer = True
        if self._force_answer:
            self._force_answer = False
            reply.forced_answer = True
            hint = (hint + " " if hint else "") + "They have finished speaking: answer now, never with '...'."
        if reply.carry_on:
            hint = (hint + " " if hint else "") + ("That was only a listening sound, and it cut you off: carry "
                                                   "on from where you stopped, without starting over.")
        if text:
            pending, self._pending_send = self._pending_send, ""
        else:
            pending = ""  # announcements leave the "Did you mean ...?" question pending
        reply.pending_send = pending  # restored if this draft is dropped
        if pending and text and not hint:
            if _DECLINE.search(text):
                hint = f"They declined the message \"{pending}\": send nothing."
            elif _BARE_NO.match(text):
                self._pending_send = pending  # still waiting for the right name
                hint = (f"They said no to your suggested recipient for \"{pending}\": ask who they mean "
                        "and send nothing yet.")
            else:
                # They are answering "Did you mean Sam?": that answer is the go-ahead.
                hint = (f"They are answering your question about the recipient of: \"{pending}\". If "
                        "they confirmed or named a contact, send it now with phone_task to that contact.")
                reply.confirmed_send = True
        if hint:
            lines.append(f"({hint})")
        if "is not in the contacts" in hint:
            reply.unknown_recipient = True
            reply.script = _ask_recipient(hint)
            self._pending_send = text
        reply.events, reply.announce = list(self._events), self._announce
        self._events.clear()
        self._announce = False
        if text:
            lines.append(text)
        return user_segment("\n".join(lines))

    def _tool_hint(self, text: str) -> str:
        """A nudge for requests that need a tool, so the model never fakes it."""

        if not text:
            return ""
        target = self._correction_target(text)
        if target is not None:
            task, running = target
            if running:
                return (f"This changes running task #{task.id} ('{task.goal}'): call update_task "
                        f"with task_id {task.id}.")
            return (f"Task #{task.id} ('{task.goal}') already finished with: {task.result} "
                    "To change it, call phone_task to fix it (for example send a short correction).")
        if _NEEDS_THINKING.search(text) and not _wants_phone_action(text):
            return "This needs a formula: use think, and tell them you're working it out."
        who, glued = _recipient_hint(text, self._contacts())
        if "is not in the contacts" in who:
            return who if (_wants_phone_action(text) or glued) else ""
        if _wants_phone_action(text) or glued:
            return "This needs phone_task." + (f" {who}" if who else "")
        return ""

    def _contacts(self) -> list[str]:
        try:
            return list(self.phone.data["contacts"])
        except (AttributeError, KeyError, TypeError):
            return []

    def _correction_target(self, text: str):
        """(task, running) when the user is changing a recent phone request."""

        t = text.strip()
        if not _CORRECTION.search(t) or t.endswith("?") or _QUESTION_START.match(t) or _DECLINE.search(t):
            return None
        running = [t for t in self.tasks.running() if t.kind == "phone"] if self.tasks else []
        if running:
            return running[-1], True
        now = time.monotonic()
        recent = [t for t, at in self._recent_tasks if now - at < 30 and t.kind == "phone"]
        return (recent[-1], False) if recent else None

    def _start_generation(self, reply: Reply, segment: str, continuation: bool = False) -> None:
        rid = reply.id
        part = Part()

        def op():
            if reply.cancelled:
                return
            join = self._join_prefix()
            if not continuation:
                reply.pre_mark = self.talker.mark()
            else:
                part.after_join = join
            yield from self.talker.append_text(join + segment + GEN_FAST)
            part.mark = self.talker.mark()
            if reply.script and not continuation:  # a fixed reply: no sampling
                yield from self.talker.append_text(reply.script + "<|im_end|>")
                part.complete = True
                yield ("scripted", (rid, reply.script))
                return
            for tok in self.talker.generate(
                    TALK_SAMPLING, cancel=lambda: reply.cancelled or reply.cancelled_generation):
                yield ("token", (rid, tok))
            gen = self.talker.tokens[part.mark.tokens:]
            part.complete = bool(gen) and gen[-1] == self.brain.im_end
            part.call_in_cache = self.brain.tool_call_start in gen
            if self.talker.tokens and self.talker.tokens[-1] != self.brain.im_end:
                # Cut short (cancelled or out of budget): close the message.
                yield from self.talker.prefill([self.brain.im_end])
            yield ("part_done", (rid,))

        reply.parts.append(part)
        reply.generating = True
        self._op(op)

    def _on_token(self, rid: int, tok: int) -> None:
        r = self.reply
        if r is None or r.id != rid or r.cancelled or r.cancelled_generation:
            return
        if r.t_first_token is None:
            r.t_first_token = time.monotonic()
        part = r.parts[-1]
        part.ids.append(tok)
        text = self.brain.decode(part.ids)
        if text.endswith("�"):
            return
        new, part.text = text[len(part.text):], text
        new = new.replace("<|im_end|>", "").replace("<|endoftext|>", "")
        new = _hide_thinking(part, new)
        was_in_call = part.splitter.in_call
        old = part.splitter.spoken
        speech = part.splitter.feed(new)
        cut = _runaway(part.splitter.spoken)
        if cut is not None:
            # A repeated sentence or a very long reply means the model is
            # rambling. End the reply here and take back what was not yet spoken.
            keep = len(part.splitter.spoken) - cut
            speech = speech[: max(0, len(speech) - keep)]
            sent_tail = old[cut:]
            self.bus.emit("warning", message=f"cut a rambling reply before {part.splitter.spoken[cut:cut + 80]!r}")
            if r.utterance is None and r.pending_speech.endswith(sent_tail):  # still held
                r.pending_speech = r.pending_speech[: len(r.pending_speech) - len(sent_tail)]
                part.splitter.spoken = part.splitter.spoken[:cut]
            elif r.utterance is not None and sent_tail.strip() and not r.utterance.retract(sent_tail):
                part.splitter.spoken = old[:cut] + sent_tail  # already on its way to the speaker
            else:
                part.splitter.spoken = part.splitter.spoken[:cut]
            r.cancelled_generation = True
            self._speak(r, speech)
            self._end_generation(r)
            return
        self._speak(r, speech)
        if part.splitter.in_call and not was_in_call:
            if r.utterance is not None:
                # A tool call is starting: say the acknowledgement now, don't wait for more text.
                flush = getattr(r.utterance, "flush", None)
                if flush is not None:
                    flush()
            elif not r.speech.strip() and not r.is_announcement and not r.speculative:
                # The model went straight to a tool: if the answer is not ready
                # soon, fill the gap (but never while they may still be talking).
                self._schedule_filler(r, part)

    def _schedule_filler(self, r: Reply, part: Part) -> None:
        base = r.t_user_end if r.t_user_end is not None else time.monotonic()
        r.filler_due = max(time.monotonic(), base + FILLER_DELAY_S)
        r.filler_part = part

    def _say_filler(self, r: Reply) -> None:
        part, r.filler_due = r.filler_part, 0.0
        if part is None or r.utterance is not None or r.speech.strip():
            return  # the real answer is already on its way
        calls = part.splitter.calls_text
        part.filler = (THINK_FILLER if "<function=think>" in calls
                       else TASK_FILLER if "<function=phone_task>" in calls else FILLER)
        self._speak(r, part.filler)
        flush = getattr(r.utterance, "flush", None)
        if flush is not None:
            flush()

    def _rose_asked(self) -> bool:
        """Rose's last turn offered something or asked a question (the user may be answering)."""

        last = next((h for h in reversed(self.history) if h["role"] == "rose"), None)
        return last is not None and (last["text"].rstrip().endswith("?") or bool(_OFFER.search(last["text"])))

    def _accepts_offer(self, text: str) -> bool:
        """'Great, do that.' after Rose offered to do something ('Want me to open it?')."""

        last = next((h for h in reversed(self.history) if h["role"] == "rose"), None)
        if last is None or not _OFFER.search(last["text"]):
            return False
        return bool(_AFFIRM.match(text.strip())) and not _declines(text)

    def _should_force_call(self, r: Reply) -> bool:
        """Rose acknowledged a phone action ("Sure, setting an alarm...") but the
        model ended without calling the tool: make it call."""

        speech = r.speech.strip()
        target = self._correction_target(r.user_text)
        wanted = (_wants_phone_action(r.user_text) or (target is not None and target[1])
                  or (r.confirmed_send and not _declines(r.user_text)) or r.accepted_offer)
        return (not r.is_announcement and not r.executed_tools and not r.forced and bool(speech)
                and not speech.endswith("?") and wanted and _is_ack(speech)
                and not re.search(r"\b(can't|cannot|unable|not able|don't have)\b", speech, re.I))

    def _force_call(self, r: Reply, part: Part) -> None:
        r.forced = True
        said = part.splitter.spoken.rstrip()
        prefix = "<tool_call>\n<function=phone_task>\n<parameter=goal>\n"
        target = self._correction_target(r.user_text)
        if target is not None and target[1]:
            prefix = (f"<tool_call>\n<function=update_task>\n<parameter=task_id>\n{target[0].id}\n"
                      "</parameter>\n<parameter=instruction>\n")
        stop = {self.brain.encode("</tool_call>")[0]}
        budget = max(80, 48 + 2 * len(self.brain.encode(r.user_text)))  # the goal quotes their words

        def op():
            if part.mark is None or r.cancelled:
                return
            self.talker.rewind(part.mark)
            yield from self.talker.append_text(said + "\n\n" + prefix)
            part.call_in_cache = True
            ids = []
            for tok in self.talker.generate(Sampling(temperature=0.0, max_tokens=budget), stop=stop,
                                            cancel=lambda: r.cancelled):
                ids.append(tok)
                yield None
            full = prefix + self.brain.decode(ids)
            if r.cancelled or not full.rstrip().endswith("</tool_call>") or not parse_tool_calls(full):
                # Cut short or malformed: never half-close it (it could send a truncated
                # message). Take the call back out and own up to it.
                self.talker.rewind(part.mark)
                yield from self.talker.append_text(said + " " + FORCE_FAILED + "<|im_end|>")
                part.call_in_cache, part.complete = False, True
                yield ("forced_call", (r, part, None))
                return
            yield from self.talker.append_text("<|im_end|>")
            part.complete = True
            yield ("forced_call", (r, part, full))
        self._op(op)

    def _end_generation(self, r: Reply) -> None:
        """Stop generating but keep what was said: record the cut-short message
        in the cache the way the user will hear it."""

        part = r.parts[-1]
        said = part.splitter.spoken

        def op():
            # Ordered after the generation op, which has stopped at the flag.
            if part.mark is None:
                return
            self.talker.rewind(part.mark)
            yield from self.talker.append_text(said.rstrip() + "<|im_end|>")
            part.complete, part.call_in_cache = True, False
        self._op(op)

    def _speak(self, r: Reply, speech: str) -> None:
        if not r.head_done:
            # "..." means "they are not done": never say it, and wait for more.
            r.head += speech
            h = r.head.strip()
            if not h or set(h) <= set(".…"):
                return
            r.head_done = True
            speech, r.head = r.head, ""
        if r.speculative or (r.utterance is None and (time.monotonic() < r.hold_until or self._user_active
                                                       or not self.listener_idle())):
            r.pending_speech += speech  # held until the turn is confirmed / they stay quiet
            return
        if r.pending_speech and r.utterance is None:
            speech, r.pending_speech = r.pending_speech + speech, ""
        if not (speech.strip() or (speech and r.utterance is not None)):
            return
        if r.utterance is None:
            r.utterance = self.speaker.utterance(r.id, r.t_user_end)
            r.t_first_speech = time.monotonic()
        r.utterance.add(speech)
        self.bus.emit("rose_partial", id=r.id, text=r.speech)

    def _on_part_done(self, rid: int) -> None:
        r = self.reply
        if r is None or r.id != rid:
            return
        if r.speculative:
            r.pending_part_done = True  # tools wait for the confirmed turn
            return
        part = r.parts[-1]
        part.done = True
        tail = "" if r.cancelled_generation else part.splitter.feed("")
        if part.splitter.buffer and not part.splitter.in_call and not r.cancelled_generation:
            tail += part.splitter.buffer
            part.splitter.spoken += part.splitter.buffer
            part.splitter.buffer = ""
        if not r.cancelled:
            self._speak(r, tail)
        if r.cancelled:
            r.generating = False
            return
        if (not r.head_done and r.head.strip() and set(r.head.strip()) <= set(".…")
                and not part.splitter.calls_text and not r.is_announcement and len(r.parts) == 1):
            if not r.forced_answer:
                self._wait_for_more(r)
                return
            # They trailed off and stayed quiet: invite them to go on.
            r.head, r.head_done = "", True
            part.splitter.spoken = GO_ON

            def op():
                if part.mark is not None:
                    self.talker.rewind(part.mark)
                    yield from self.talker.append_text(GO_ON + "<|im_end|>")
            self._op(op)
            self._speak(r, GO_ON)
        calls = parse_tool_calls(part.splitter.calls_text)
        if part.splitter.calls_text.strip() and not calls:
            self.bus.emit("warning", message=f"unparsed tool call: {part.splitter.calls_text[-300:]!r}")
            if "<function=think>" in part.splitter.calls_text and r.user_text:
                # A think call cut off mid-argument: think about what they asked, and
                # replace the broken call in the cache with a well-formed one.
                calls = [ToolCall("think", {"question": r.user_text})]
                fixed = ("<tool_call>\n<function=think>\n<parameter=question>\n" + r.user_text.strip()
                         + "\n</parameter>\n</function>\n</tool_call>")
                part.splitter.calls_text = fixed
                said = part.splitter.spoken.rstrip()

                def op():
                    if part.mark is not None:
                        self.talker.rewind(part.mark)
                        yield from self.talker.append_text((said + "\n\n" if said else "") + fixed
                                                           + "<|im_end|>")
                        part.complete = True
                self._op(op)
        if not calls and self._should_force_call(r):
            flush = getattr(r.utterance, "flush", None)
            if flush is not None:
                flush()  # say the acknowledgement now, while the call is forced
            self._force_call(r, part)
            return
        self._after_calls(r, part, calls)

    def _after_calls(self, r: Reply, part: Part, calls: list) -> None:
        if calls and part.filler:
            # Make Rose's memory match what she said: filler, then the call.
            filler, calls_text = part.filler, part.splitter.calls_text

            def op():
                if part.mark is None:
                    return
                self.talker.rewind(part.mark)
                yield from self.talker.append_text(filler + "\n\n" + calls_text + "<|im_end|>")
            self._op(op)
        if calls:
            results, seen = [], set()
            for c in calls:
                key = (c.name, json.dumps(c.arguments, sort_keys=True))
                if key in seen:  # the same call twice in one message runs once
                    self.bus.emit("warning", message=f"skipped a repeated {c.name} call")
                    results.append("Same as the call above: not run again.")
                else:
                    results.append(self._run_tool(r, c))
                seen.add(key)
            r.executed_tools = part.executed = True
            segment = tool_segment(results)
            synchronous = (any(c.name == "calculate" for c in calls) and not _PLAIN_TIME_Q.match(r.user_text.strip())
                           or (r.needs_followup and r.followups < 1))
            if synchronous and r.needs_followup:
                r.followups += 1
            r.needs_followup = False
            if r.speech.strip() and not synchronous:
                # Rose already acknowledged out loud: record the tool response and
                # an empty assistant turn instead of letting her add more words.
                part.after = "\n" + segment + GEN_FAST + "<|im_end|>"
                after = part.after

                def op():
                    yield from self.talker.append_text(after)
                self._op(op)
            else:
                part.after = "\n" + segment
                self._start_generation(r, segment, continuation=True)
                return
        r.generating = False
        if r.utterance is not None:
            r.utterance.finish()
        elif r.pending_speech.strip():
            r.finish_pending = True  # held speech; finished once released
        else:
            self._finish_reply(r)

    # ---------------------------------------------------------------- tools
    def _run_tool(self, r: Reply, call) -> str:
        name, args = call.name, call.arguments
        if r.is_announcement and name != "calculate" and not any(e.startswith(("Task", "Not told"))
                                                                  for e in r.events):
            # News from outside (a notification) never starts actions by itself.
            self.bus.emit("warning", message=f"refused {name} during an announcement (not asked by the user)")
            return "Not done: only act when the user asks. Just tell them the news."
        self.bus.emit("tool_call", id=r.id, name=name, arguments=args)
        try:
            if name == "calculate":
                if _PLAIN_TIME_Q.match(r.user_text.strip()):
                    # "What time is it?" once came back as calculate("23 + 59/60") and a wrong time.
                    self.bus.emit("warning", message="refused calculate for a plain time question")
                    if not _SAYS_TIME.search("".join(p.splitter.spoken for p in r.parts)):
                        r.needs_followup = True  # only a filler or an ack so far: let her say it
                    return "Not needed: the current time is in the stamp. Just say it."
                return calculate(str(args.get("expression", "")))
            if name == "phone_task":
                goal = str(args.get("goal", "")).strip()
                if _is_info_question(r.user_text) and not r.confirmed_send:
                    if _claims_action(r.speech):
                        r.needs_followup = True  # she said she would: let her correct herself
                    return ("Not done: they asked a question, not for a change. Answer it, and offer to "
                            "do it if it would help.")
                if (_unrelated_question(r.user_text) and not (r.confirmed_send or r.accepted_offer or r.tool_hinted)
                        and not self._rose_asked()):
                    # "What time is it?" once got an alarm set "since you have a standup at nine".
                    self.bus.emit("warning", message=f"refused an unrequested phone_task: {goal!r}")
                    if r.speech.strip():
                        r.needs_followup = True  # she may have claimed it: let her take it back
                    return ("Not done: they did not ask for this. Never say you did it; offer it in a few "
                            "words if it would really help.")
                if r.unknown_recipient:
                    r.needs_followup = True
                    return ("Not sent: the person they named is not in the contacts. Ask which contact "
                            "they mean (suggest the closest name) and send nothing yet.")
                if not _changes_phone(goal):
                    if r.speech.strip():
                        r.needs_followup = True  # she already acked: let her answer from the data
                    return ("Not started: that only reads information you already have in the phone "
                            "data. Answer from it directly.")
                dup = next((t for t in self.tasks.running()
                            if t.kind == "phone" and _same_goal(t.goal, goal)), None)
                if dup is not None:
                    return f"Task #{dup.id} is already doing that."
                task = self.tasks.start_phone(goal)
                return f"Task #{task.id} started in the background."
            if name == "think":
                question = str(args.get("question", "")).strip()
                task = self.tasks.start_think(question, self._recent_context())
                return f"Task #{task.id} started in the background."
            if name == "update_task":
                ok = self.tasks.update(int(args.get("task_id", 0)), str(args.get("instruction", "")))
                return "Updated." if ok else "That task is not running any more."
            if name == "cancel_task":
                ok = self.tasks.cancel(int(args.get("task_id", 0)))
                return "Cancelled." if ok else "That task is not running any more."
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"
        return f"Unknown tool {name}."

    def _recent_context(self) -> str:
        lines = [f"{'User' if h['role'] == 'user' else 'Rose'}: {h['text']}" for h in self.history[-8:]]
        return "Phone data:\n" + self.phone.summary() + "\n\nRecent conversation:\n" + "\n".join(lines)

    def _on_task_done(self, task) -> None:
        if task.status == "cancelled":
            return
        self._recent_tasks = (self._recent_tasks + [(task, time.monotonic())])[-5:]
        verb = "finished" if task.status == "done" else "failed"
        what = "thinking about" if task.kind == "think" else "phone task"
        self._add_event(f"Task #{task.id} ({what} '{task.goal}') {verb}. Result: {task.result}",
                        announce=True)

    def _add_event(self, line: str, announce: bool) -> None:
        self._events.append(line)
        self._announce = self._announce or announce
        self.bus.emit("event_queued", line=line)

    # ---------------------------------------------------- interruptions
    def _stop_rose(self, reason: str, keep_turn: bool = False) -> None:
        r = self.reply
        rid = r.id if r is not None else None
        if self._barge is not None and self._barge.get("rid", rid) != rid:
            self._barge = None  # left over from an earlier reply
        if self._barge is None:
            self._barge = {"t": time.monotonic(), "stopped": False, "voiced": 0.0,
                           "last": time.monotonic(), "text": "", "rid": rid}
        if self._barge["stopped"]:
            return
        self._barge["stopped"] = True
        self.speaker.stop()
        self.bus.emit("barge", stage="stop", reason=reason,
                      ms=round((time.monotonic() - self._barge["t"]) * 1000))
        if r is None or r.finalized:
            return
        started = r.utterance.first_audio_at if r.utterance is not None else None
        early = started is not None and self._barge["t"] - started < 1.0
        if early and not keep_turn and not r.executed_tools and not r.is_announcement:
            # They were still mid-thought when Rose began: treat it as one turn.
            self._abandon_draft(r)
            return
        r.interrupted = True
        self._cut = r
        self._finish_reply(r, cancel=True)

    def _abandon_draft(self, r: Reply) -> None:
        """User resumed before Rose spoke: forget the draft and the partial user turn."""

        if r.user_text:
            self._carry = (self._carry + " " + r.user_text).strip()
        r.cancelled = True
        r.finalized = True
        if r.utterance is not None:
            r.utterance.cancel()
        if self.history and self.history[-1].get("id") == r.id and self.history[-1]["role"] == "user":
            self.history.pop()
        self._events = r.events + self._events
        self._announce = self._announce or r.announce
        self._pending_send = self._pending_send or r.pending_send
        self._carry_on = self._carry_on or r.carry_on

        def op():
            # Ordered after the generation op, which set pre_mark if it ran at all.
            if r.pre_mark is not None:
                self.talker.rewind(r.pre_mark)
            return
            yield

        self._op(op)
        self.bus.emit("barge", stage="abandon", reason="user kept talking")
        self.reply = None

    def _finish_reply(self, r: Reply, cancel: bool = False) -> None:
        if r.finalized:
            return
        r.finalized = True
        if not cancel and self._cut is not None and self._cut is not r:
            self._cut = None  # a newer reply was heard through: nothing left to carry on
        if cancel:
            r.cancelled = True
            if r.utterance is not None:
                r.utterance.cancel()
        self._last_rose_end = time.monotonic()
        def op():
            # Runs after the generation op finished (ops are ordered).
            heard = ""
            if r.utterance is not None:
                for _ in range(100):  # wait for the client's stop report (<= 0.5 s)
                    if r.utterance.done.is_set():
                        break
                    yield Sleep(0.005)
                heard = r.utterance.heard()
            yield ("heard", (r, heard))
            if r.interrupted or (cancel and r.parts):
                yield from self._rewrite(r, heard)
        self._op(op)

    def _on_heard(self, r: Reply, heard: str) -> None:
        rid = r.id
        interrupted = r.interrupted
        text = heard if interrupted else (r.speech or heard)
        if r.is_announcement and text.strip().endswith("?"):
            self._pending_send = ""  # a "yes" now answers the announcement, not the old question
        if r.announce and (interrupted or (not r.cancelled and not r.is_announcement)):
            # News that came with this reply but was skipped, or cut off before it
            # was said, is told at the next quiet moment (once).
            ends = [text.rfind(p) for p in ".!?"]
            told = text[: max(ends) + 1] if interrupted else text
            for e in r.events:
                if not e.startswith("Not told") and not _mentions(told, e):
                    self._add_event(f"Not told to the user yet: {e}", announce=True)
        self.bus.emit("rose_heard", id=rid, text=text, interrupted=interrupted)
        if text.strip():
            text = text.strip()
            self.history.append({"role": "rose", "text": text, "id": rid, "interrupted": interrupted})
            self.bus.emit("rose_turn", id=rid, text=text, interrupted=interrupted)

    def _rewrite(self, r: Reply, heard: str) -> Iterator[Any]:
        """Rewind the talker to the first part the user did not fully hear and
        re-append only what they heard, so Rose's memory matches reality."""

        heard_n = len(speakable(heard).split())
        for part in r.parts:
            words = speakable(((part.filler + " ") if part.filler else "") + part.splitter.spoken).split()
            # A part stays as generated only if it was heard in full, ended on its
            # own, and any call it wrote actually ran (else the cache would hold a
            # cut-off or never-answered call).
            if heard_n >= len(words) and part.complete and (part.executed or not part.call_in_cache):
                heard_n -= len(words)
                continue
            if part.mark is None:
                return
            full = heard_n >= len(words)
            said = " ".join(words[:heard_n])
            tail = said if full else ((said + "—") if said else "—")
            calls = ("\n\n" + part.splitter.calls_text) if part.executed and part.splitter.calls_text else ""
            self.talker.rewind(part.mark)
            yield from self.talker.append_text(tail + calls + "<|im_end|>" + part.after)
            return

    # ---------------------------------------------------------------- tick
    def _tick(self) -> None:
        now = time.monotonic()
        r = self.reply
        # Barge-in: sustained speech stops Rose even without a transcript yet.
        if self._barge is not None and not self._barge["stopped"]:
            b = self._barge
            dt, b["last"] = now - b["last"], now
            if getattr(self.listener, "in_speech", False) and getattr(self.listener, "vad_p", 1.0) >= 0.5:
                b["voiced"] += dt
            bc = b.get("backchannel") or is_backchannel(b["text"]) or (b["text"] and _bc_ish(b["text"]))
            limit = BARGE_BACKCHANNEL_S if bc else BARGE_VOICED_S
            if b["voiced"] >= limit and not self._is_echo(b["text"]):
                self._stop_rose(f"{round(b['voiced'] * 1000)} ms of speech", keep_turn=is_hold(b["text"]))
                self._stopped_by_utt = True
            elif b["voiced"] >= BARGE_PAUSE_S and not b.get("paused"):
                b["paused"] = True
                pause = getattr(self.speaker, "pause", None)
                if pause is not None:
                    pause()
                self.bus.emit("barge", stage="pause", reason="speech continues",
                              ms=round((now - b["t"]) * 1000))
            elif not self._user_active and now - b["t"] >= 1.5:
                self._barge = None
                self._resume_rose()
                self.bus.emit("barge", stage="resume", reason="no speech")
        if self._barge is not None and self._barge["stopped"] and now - self._barge["t"] > 1.0 \
                and not self._rose_audible():
            self._barge = None
        # They stopped mid-thought and stayed quiet: answer what they said.
        if self._unfinished_until and now >= self._unfinished_until:
            self._unfinished_until = 0.0
            if self.reply is None and self._carry and not self._user_active:
                text, self._carry = self._carry, ""
                self._force_answer = True
                self._user_turn(text, t_user_end=now, listen={})
        # A tool is taking a while and nothing has been said yet: say "One sec."
        if r is not None and r.filler_due and now >= r.filler_due and not r.finalized \
                and not r.speculative and not self._user_active:
            self._say_filler(r)
        # Release a short turn's held reply once they have stayed quiet.
        if r is not None and not r.finalized and not r.speculative and r.pending_speech \
                and r.utterance is None and now >= r.hold_until and not self._user_active \
                and self.listener_idle():
            speech, r.pending_speech = r.pending_speech, ""
            self._speak(r, speech)
            if r.finish_pending and r.utterance is not None:
                r.finish_pending = False
                r.utterance.finish()
        # Reply finished playing.
        if r is not None and not r.finalized and not r.generating and r.utterance is not None \
                and r.utterance.done.is_set():
            self._finish_reply(r)
            if self._answer_rid == r.id and self._carry and not self._user_active:
                text, self._carry, self._answer_rid = self._carry, "", None
                self._user_turn(text, t_user_end=time.monotonic(), listen={})
        if r is not None and r.utterance is not None and not r.metrics_sent \
                and r.utterance.first_audio_at is not None:
            self._emit_metrics(r)
        # Announce finished tasks / notifications at a quiet moment.
        if self._announce and self._quiet(now):
            self._announcement()
        self._set_status(self._idle_status() if self.listener_idle() else "user_speaking")

    def _quiet(self, now: float) -> bool:
        r = self.reply
        busy = r is not None and not r.finalized
        return (not busy and not self._user_active and now >= self._held_until
                and self.listener_idle() and not self._rose_audible()
                and now - self._last_user_end > 0.8 and now - self._last_rose_end > 0.5
                and not self._carry)

    def _announcement(self) -> None:
        self._reply_ids += 1
        reply = Reply(self._reply_ids, "", None, is_announcement=True)
        self.reply = reply
        results = [e.split("Result:", 1)[1].strip() for e in self._events
                   if e.startswith(("Task #", "Not told")) and "Result:" in e]
        segment = self._compose_user("", reply)
        if len(results) == 1 and len(reply.events) == 1 and len(results[0].split()) >= 3:
            # One finished task: its result is already written to be spoken.
            reply.script = results[0]
        self._start_generation(reply, segment)

    def _idle_status(self) -> str:
        r = self.reply
        if r is not None and not r.finalized and not r.speculative:
            return "speaking" if self._rose_audible() or self._audio_started(r) else "thinking"
        return "listening"

    def _set_status(self, state: str) -> None:
        if state != self._status:
            self._status = state
            self.bus.emit("status", state=state)

    def _emit_metrics(self, r: Reply) -> None:
        r.metrics_sent = True
        u = r.utterance
        base = r.t_user_end
        m = {"id": r.id, "announcement": r.is_announcement}
        if base is not None:
            m["voice_to_voice_ms"] = round((u.first_audio_at - base) * 1000)
            if r.t_first_token:
                m["llm_first_token_ms"] = round((r.t_first_token - base) * 1000)
                m["llm_ttft_ms"] = round((r.t_first_token - r.t_start) * 1000)
            if r.t_first_speech:
                m["first_speech_text_ms"] = round((r.t_first_speech - base) * 1000)
        m.update({k: r.listen[k] for k in ("endpoint_ms", "asr_ms", "smart_turn_p") if k in r.listen})
        self.turn_metrics.append(m)
        self.bus.emit("metrics", turn=m)

    # ---------------------------------------------------------------- misc
    def _reset(self) -> None:
        self._recent_tasks.clear()
        self._pending_send = ""
        self._unfinished_until, self._force_answer = 0.0, False
        if self.reply is not None:
            self.reply.events, self.reply.announce, self.reply.pending_send = [], False, ""
        self._answer_rid = None
        self._held_until = 0.0
        r = self.reply
        if r is not None and not r.finalized:
            self._stop_rose("reset")
        for t in list(self.tasks.running()):
            self.tasks.cancel(t.id)
        self._events.clear()
        self._announce = False
        self._carry = ""
        self._carry_on, self._cut = False, None
        self.history.clear()
        self.reply = None
        system = SYSTEM.format(phone=_static_phone(self.phone))

        def op():
            seq = self.brain.sequence("talker")
            yield from seq.append_text(self.brain.system_prefix(system, TALKER_TOOLS))
            self.talker = seq
        self._op(op)
        self.bus.emit("reset")


MAX_SENTENCES = 14  # about a minute of speech; stories fit, loops do not


_NOT_NAMES = {"i", "it", "its", "you", "your", "this", "that", "these", "those", "there", "here",
              "he", "she", "they", "we", "my", "yes", "no", "sure", "okay", "the", "task", "result",
              "done", "do", "not", "disturb", "am", "pm"}
_MENTION_STOP = {"there", "their", "about", "would", "could", "that", "this", "with", "have", "will",
                 "your", "what", "when", "from", "been", "they", "them", "then", "than", "just", "into",
                 "also", "were"}


def _mentions(reply: str, event: str) -> bool:
    """Whether a reply plausibly told the user about an event: it names someone
    in the result, or shares a content word with it."""

    body = event.split("Result:", 1)[-1]
    stem = lambda w: w.split("'")[0]  # alarm's -> alarm  # noqa: E731
    words = {stem(w) for w in _words(reply)}
    names = {w.lower() for w in re.findall(r"\b[A-Z][a-z]+\b", body)} - _NOT_NAMES
    if names & words:
        return True
    keys = {w for w in {stem(w) for w in _words(body)} if len(w) >= 4} - _MENTION_STOP
    return bool(keys & words)


SHORT_TURN_SILENCE_S = 0.5
UNFINISHED_WAIT_S = 2.0
GO_ON = "Go on?"
_NEEDS_THINKING = re.compile(
    r"\b(compound(ed|ing)?|interest rate|apr|mortgage|loan|amortiz\w*|annuity|invest(ed|ing|ment)?|"
    r"retire(ment)?|probability|odds of|break[- ]even|depreciat\w*)\b", re.I)
_CONDITION = re.compile(r"^(?:so,? |and,? |okay,? |ok,? )?(if|when|whenever|suppose|supposing|assuming|"
                        r"say|given that|let's say)\b", re.I)


def _conditional_fragment(text: str) -> bool:
    """'If I save $250 a month at 5%.' sets up a question that has not come yet."""

    t = text.strip()
    if not _CONDITION.match(t) or t.endswith("?"):
        return False
    # A main clause after a comma ("If it rains, remind me...") makes it complete.
    return not re.search(r",\s*(then\s+)?(\w+)", t[t.find(" ") + 1:])


_INFO_QUESTION = re.compile(
    r"^(?:(?:hey |ok |okay )?rose,? )?(should|is|are|do|does|did|what|how|why|when|where|who|which|"
    r"would it|will it|was|were|has|have|am|isn't|aren't|doesn't|don't)\b", re.I)


_POLITE_REQUEST = re.compile(
    r"^(?:(?:hey |ok |okay )?rose,? )?(?:do you mind|would you mind|is it possible (?:for you )?to|"
    r"would it be possible (?:for you )?to|do you think you (?:could|can)|could you possibly|"
    r"why don't you|why not just|how about you|is it ok(?:ay)? if you|would it be ok(?:ay)? if you)\b", re.I)


def _is_info_question(text: str) -> bool:
    """'Should I turn off Bluetooth?' asks for advice; 'Can you turn it off?' asks for action."""

    t = text.strip()
    if _PLACE_QUESTION.search(t):
        return False  # "Where's the nearest pharmacy?" needs Maps
    if _POLITE_REQUEST.match(t) and _PHONE_REQUEST.search(t):
        return False
    if _requests_change(t):
        return False  # "Should I turn off Bluetooth? Actually, just turn it off."
    return any(s.endswith("?") and _INFO_QUESTION.match(s) for s in _sentences(t))


def _short_turn_hold(text: str, t_user_end: float | None) -> float:
    """One- or two-word turns ("Yes,", "No, ...") often continue: Rose's first
    audio waits until they have been quiet this long."""

    if t_user_end is None or len(_words(text)) > 2 or is_hold(text):
        return 0.0
    return t_user_end + SHORT_TURN_SILENCE_S


def _hide_thinking(part: Part, new: str) -> str:
    """Drop any <think>...</think> the model writes on its own: reasoning is never spoken."""

    out = ""
    while new:
        if part.thinking:
            end = new.find("</think>")
            if end < 0:
                return out
            new, part.thinking = new[end + len("</think>"):].lstrip("\n"), False
            continue
        start = new.find("<think>")
        stray = new.find("</think>")  # a close with no open ("It's 1:08 AM.\n</think>")
        if 0 <= stray and (start < 0 or stray < start):
            out += new[:stray]
            new = new[stray + len("</think>"):].lstrip("\n")
            continue
        if start < 0:
            return out + new
        out += new[:start]
        new, part.thinking = new[start + len("<think>"):], True
    return out


def _norm(text: str) -> str:
    # Digits count: "set an alarm for 7" must not match "... for 7:30".
    return " ".join(re.findall(r"[\w']+", text.lower()))


def _runaway(spoken: str) -> int | None:
    """Index where a rambling reply should be cut, or None: before a sentence
    that repeats an earlier one, or after MAX_SENTENCES sentences."""

    seen: set[str] = set()
    for n, m in enumerate(re.finditer(r"\S.*?[.!?](?=\s)", spoken, re.S)):
        key = " ".join(re.findall(r"[a-z0-9']+", m.group(0).lower()))
        if (len(key) > 12 and key in seen) or n >= MAX_SENTENCES:
            return m.start()
        seen.add(key)
    return None


_DAY_PARTS = {"this morning": (5 * 60, 12 * 60), "this afternoon": (12 * 60, 17 * 60),
              "this evening": (17 * 60, 24 * 60), "tonight": (17 * 60, 24 * 60), "today": (0, 24 * 60)}


def _part_of_day_events(phone, text: str) -> str:
    """'this afternoon: Design review with Alex 4:00 PM to 5:00 PM (already over)' so a
    question about a part of the day gets every event in it, past ones included."""

    t = text.lower()
    part = next((p for p in _DAY_PARTS if p in t), None)
    if part is None:
        return ""
    lo, hi = _DAY_PARTS[part]
    try:
        events = phone.data["calendar"]
    except (AttributeError, KeyError, TypeError):
        return ""
    now = time.localtime()
    now_min = now.tm_hour * 60 + now.tm_min
    items = []
    for e in events:
        start, end = _minutes(e["time"]), _minutes(e.get("end", e["time"]))
        if start is None or not lo <= start < hi:
            continue
        status = "already over" if end is not None and end <= now_min else (
            "happening now" if start <= now_min else "still to come")
        items.append(f"{e['title']} {e['time']} to {e.get('end', '?')} ({status})")
    return f"{part}: " + ("; ".join(items) if items else "nothing")


def _static_phone(phone) -> str:
    """Phone data for the system prompt, without the calendar: event status changes
    with the clock, so the calendar arrives with each relevant user message instead."""

    out, skipping = [], False
    for line in phone.summary().splitlines():
        if line.startswith("Calendar"):
            skipping = True
            continue
        if skipping and line.startswith("  "):
            continue
        skipping = False
        out.append(line)
    return "\n".join(out)


def _minutes(clock: str) -> int | None:
    m = re.match(r"(\d+):(\d+)\s*([AP])M", clock)
    if not m:
        return None
    return (int(m.group(1)) % 12 + (12 if m.group(3) == "P" else 0)) * 60 + int(m.group(2))


def _calendar_status(phone) -> str:
    """'calendar: earlier today Standup, Lunch; now Design review; next Dinner with Sam at 6:30 PM
    (in 57 minutes)' so the talker never has to do clock arithmetic."""

    try:
        events = phone.data["calendar"]
    except (AttributeError, KeyError, TypeError):
        return "", False
    now = time.localtime()
    now_min = now.tm_hour * 60 + now.tm_min
    done, current, upcoming = [], [], []
    soon = False
    for e in events:
        start, end = _minutes(e["time"]), _minutes(e.get("end", e["time"]))
        if start is None:
            continue
        if end is not None and end <= now_min:
            done.append(f"{e['title']} ({e['time']} to {e.get('end', '?')}, already over)")
        elif start <= now_min:
            current.append(f"{e['title']} (until {e.get('end', '?')})")
        else:
            delta = start - now_min
            soon = soon or delta <= 15
            when = f"in {delta} minutes" if delta < 90 else f"in about {round(delta / 60)} hours"
            where = f" at {e['where']}" if e.get("where") else ""
            upcoming.append(f"{e['title']}{where}, {e['time']} to {e.get('end', '?')} ({when})")
    parts = []
    if done:
        parts.append("earlier today: " + ", ".join(done))
    if current:
        parts.append("happening now: " + ", ".join(current))
    parts.append("next: " + (upcoming[0] if upcoming else "nothing else today"))
    if len(upcoming) > 1:
        parts.append("later: " + ", ".join(upcoming[1:]))
    return "calendar " + "; ".join(parts), soon


_TIMEY = re.compile(
    r"\b(time|today|tonight|tomorrow|morning|afternoon|evening|calendar|schedule|meeting|"
    r"dinner|lunch|breakfast|event|review|plans?|free|busy|soon|late|early|when|how long|"
    r"next|later|o'clock|minutes?|hours?|appointment|standup|stand-up|going on|agenda|day|week|"
    r"weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday|tartine|nopa|priya|alex|"
    r"sam|lunch|zoom|room|where|remind)\b", re.I)


def calculate(expression: str) -> str:
    """Evaluate plain arithmetic safely (numbers, + - * / ** % and parentheses)."""

    import ast
    import operator as op

    ops = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
           ast.Pow: op.pow, ast.Mod: op.mod, ast.FloorDiv: op.floordiv, ast.USub: op.neg,
           ast.UAdd: op.pos}

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow):
                if isinstance(left, int) and isinstance(right, int) and abs(right) > 1000:
                    raise ValueError("exponent too large")
                left = float(left) if abs(right) > 64 else left  # floats overflow instead of growing
            return ops[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops:
            return ops[type(node.op)](ev(node.operand))
        raise ValueError("only plain arithmetic is supported")

    try:
        expr = expression.replace("×", "*").replace("÷", "/").replace("^", "**").replace(",", "")
        value = ev(ast.parse(expr, mode="eval"))
    except OverflowError:
        return "Error: the result is too large"
    except Exception as exc:
        return f"Error: {exc}"
    if isinstance(value, float):
        from fractions import Fraction

        frac = Fraction(value).limit_denominator(16)
        exact = abs(float(frac) - value) < 1e-9 and frac.denominator > 1
        text = f"{round(value, 6):g}"
        return f"{expression} = {text}" + (f" (= {frac})" if exact else "")
    return f"{expression} = {value}"


_ACTION_WORDS = {
    "text", "send", "message", "reply", "tell", "set", "alarm", "turn", "switch", "toggle", "enable",
    "disable", "open", "change", "add", "create", "delete", "remove", "cancel", "brightness", "mute",
    "unmute", "call", "schedule", "book", "move", "rename", "dim", "raise", "lower", "increase",
    "decrease", "wifi", "wi-fi", "bluetooth", "airplane", "disturb", "snooze", "launch", "show",
}


_APP_ONLY = {"map", "maps", "directions", "navigate", "nearest", "nearby", "closest", "near", "pharmacy",
             "coffee", "grocery", "gas", "restaurant", "place", "places", "music", "song", "songs",
             "playlist", "play", "pause", "skip", "hours"}


def _changes_phone(goal: str) -> bool:
    """Phone tasks must change, open or look up something only an app knows (Maps,
    Music); messages, calendar, alarms and settings are answered from context."""

    words = set(_words(goal))
    if words & _APP_ONLY:
        return True
    if {"remind", "what", "read", "check", "find"} & words and not words & (
            _ACTION_WORDS - {"tell", "show", "message"}):
        return False
    return bool(words & _ACTION_WORDS)


_PHONE_REQUEST = re.compile(
    r"\b(text|tex|txt|message|send|reply to|set (an |my |the )?alarm|texting|messaging|sending|"
    r"setting (an |my |the )?alarm|turning (on|off)|switching (on|off)|play|playing|pause|skip|"
    r"next song|music|playlist|song|directions|navigate|navigation|nearest|nearby|closest|find (a|an|the|me)|"
    r"pharmacy|coffee|grocery|gas station|restaurant|wake me|alarm|turn (on|off)|switch (on|off)|"
    r"turn (it|them|that) (on|off)|switch (it|them|that) (on|off)|"
    r"enable|disable|open (the |my )?\w+ app|open (the |my )?(messages|clock|calendar|settings|contacts|maps|camera|music)|"
    r"mute|unmute|do not disturb|wi-?fi|bluetooth|airplane mode|brightness|tell (?!me\b)\w+ (that|i)|"
    r"let (?!me\b)\w+ know|add (an? )?(event|meeting|appointment))\b", re.I)


_QUESTION_START = re.compile(
    r"(actually,? |wait,? |so,? |and,? )?(what|when|where|who|how|why|is|are|do|does|did|can you tell)\b", re.I)
_CORRECTION = re.compile(
    r"\b(make (that|it)|change (that|it)|scratch that|i meant|correction|not (tonight|tomorrow|today)|"
    r"(actually|instead|rather|no),? (make|change|say|send|set|it's|tell|text|switch|turn|move))\b", re.I)
_HOLD_WORDS = {"wait", "hold", "on", "hang", "one", "sec", "second", "just", "a", "give", "me",
               "minute", "moment", "up", "stop", "shh", "shhh", "pause", "please", "rose", "oh", "ok",
               "okay", "uh", "um"}
_HOLD_KEYS = {"wait", "hold", "hang", "sec", "second", "stop", "shh", "shhh", "pause", "moment", "minute"}


def is_hold(text: str) -> bool:
    """'Wait', 'hold on a second', 'one sec': the user wants Rose to stop and wait."""

    words = _words(text)
    return (0 < len(words) <= 8 and set(words) <= _HOLD_WORDS and bool(set(words) & _HOLD_KEYS)
            and not text.strip().endswith("?"))


_ACK = re.compile(
    r"^(?:(?:sure|okay|ok|alright|all right|got it|yes|yep|of course|great|perfect)[,.!]?\s*)?"
    r"(?:(?:i'll|i will|let me|i'm going to|i can)\s+(?:go ahead and\s+)?"
    r"(?:open|play|start|set|send|text|turn|switch|find|search|look|pause|skip|get|put on|pull up|"
    r"check|message|tell|add|change|update|mute)\b|(?:i'm |i am )?(?:on it|will do|texting|messaging|"
    r"sending|replying|telling|letting|setting|turning|switching|changing|updating|opening|adding|getting|"
    r"grabbing|bringing up|navigating|routing|loading|"
    r"muting|unmuting|enabling|disabling|moving|canceling|cancelling|fixing|correcting|one sec|playing|"
    r"pausing|skipping|starting|finding|searching|looking|pulling up|putting on)\b)", re.I)
_IMPERATIVE = re.compile(
    r"^(?:(?:right|okay|ok|yeah|yes|so|and|great|alright|cool|sure|well|um|uh|oh|actually|now)[,.!]?\s+)*"
    r"(?:(?:hey |ok |okay )?rose,? )?(?:please |go ahead and |can you |could you |would you |will you |"
    r"i want you to |i need you to |i'd like you to )?(?:please |just )?"
    r"(text|tex|txt|message|send|reply|tell (?!me\b)|let (?!me\b)\w+ know|set|wake|turn|switch|enable|disable|"
    r"play|pause|stop the music|skip|resume|find|search|navigate|show|take me|get me directions|"
    r"directions|"
    r"open|mute|unmute|add|put|change|remind|schedule|silence|dim|raise|lower)\b", re.I)
_DECLINE = re.compile(r"\b(don't|do not(?! disturb)|never mind|nevermind|no need|not now|won't|forget it)\b",
                      re.I)


_BARE_NO = re.compile(r"^\s*(no|nope|nah|no thanks|no thank you)[.!]?\s*$", re.I)


def _declines(text: str) -> bool:
    return bool(_DECLINE.search(text) or _BARE_NO.match(text))


def _is_ack(speech: str) -> bool:
    """A short action acknowledgement at the start ("Sure, texting Sam now.")."""

    sentences = _sentences(speech)
    while len(sentences) > 1 and _INTERJECTION.match(sentences[0]):
        sentences = sentences[1:]  # "Sure! Turning on Do Not Disturb now."
    s = (sentences or [""])[0]  # "Sure, I'll get you directions now. Let me check."
    return len(_words(s)) <= 14 and bool(_ACK.match(s))


_INTERJECTION = re.compile(r"^(sure|okay|ok|alright|all right|got it|yes|yep|of course|great|perfect|"
                           r"absolutely|no problem|you got it)[.!,]?$", re.I)


def _claims_action(speech: str) -> bool:
    """Some sentence says she is doing it ("..., sure. I'll turn it off for you now.")."""

    return any(len(_words(s)) <= 14 and _ACK.match(s) for s in _sentences(speech))


def _unrelated_question(text: str) -> bool:
    """A question that asks for nothing on the phone ("What time is it?"), unlike
    "Can you text Sam?", "Is the coffee shop open?" or "What's playing?"."""

    t = text.strip()
    sentences = _sentences(t)
    return (t.endswith("?") and bool(sentences) and bool(_INFO_START.match(sentences[-1]))
            and not any(_IMPERATIVE.match(s) for s in sentences)
            and not _PHONE_REQUEST.search(t) and not _POLITE_REQUEST.match(t) and not _PLACE_WORDS.search(t)
            and not _PLACE_QUESTION.search(t))


# An information question: "What time is it?", "Is it raining?"; not "Can you ...?",
# "Yes, could you?", "What about Priya?" or "Did you send it?".
_INFO_START = re.compile(
    r"^(?:(?:actually|so|and|but|oh|okay|ok|hey|um|uh|wait|rose|hmm)[,.!]?\s+)*"
    r"(?:what|what's|whats|when|when's|who|who's|why|how|how's|where|which|is|are|was|were|do|does|am)\b"
    r"(?!\s+about\b)", re.I)


# Questions only Maps can answer ("How far is the bakery?", "Is it open now?").
_PLACE_WORDS = re.compile(r"\b(how far|open|closed?|closing|hours|address|where's|where is|where are|"
                          r"located|miles|walk|walking|drive|driving|route|near)\b", re.I)


_ALIASES = {"mum": "mom", "mother": "mom", "mommy": "mom", "mummy": "mom", "mama": "mom", "ma": "mom",
            "father": "dad", "daddy": "dad", "papa": "dad", "pa": "dad"}
_MESSAGE_VERB = re.compile(r"\b(text|message|tell|send|reply|let|tex|texted|txt)\b", re.I)
_NOT_A_NAME = {"that", "him", "her", "them", "me", "it", "a", "an", "the", "to", "i", "i'm", "know", "back",
               "saying", "say", "and", "my", "message", "text", "please", "rose", "now", "quickly", "you",
               "us", "this", "these", "those", "if", "what", "whether", "when", "how", "which", "someone",
               "something", "everyone", "everybody", "anyone"}
_LEADS = {"text", "message", "tell", "txt", "tex", "to", "send"}
_CONTRACTIONS = {"let's", "it's", "that's", "he's", "she's", "what's", "there's", "here's", "who's",
                 "where's", "how's"}


def _ask_recipient(hint: str) -> str:
    m = re.match(r"'([\w' ]+)' is not in the contacts(?:; the closest is ([^.]+)\.)?", hint)
    if not m:
        return "Who should I send that to?"
    name, closest = m.group(1)[:1].upper() + m.group(1)[1:], m.group(2)
    if closest:
        return f"I don't see {name} in your contacts. Did you mean {closest.split()[0]}?"
    return f"I don't see {name} in your contacts. Who should I send it to?"


def _recipient_hint(text: str, contacts: list[str]) -> tuple[str, bool]:
    """Resolve who a message is for against the contacts, so a misheard name
    ("Texam", "Tex Mom", "Pam") becomes the right person or a question.
    Returns (hint, glued) where glued means the verb itself was misheard
    ("texam" = "text Sam"), i.e. this is a messaging request after all."""

    import difflib

    if not contacts:
        return "", False
    firsts = {c.split()[0].lower(): c for c in contacts}
    for c in contacts:  # surnames too ("Text Lee ..."), never overriding a first name
        for part in c.lower().split()[1:]:
            firsts.setdefault(part, c)
    for alias, name in _ALIASES.items():  # "mum", "mother" -> the Mom contact
        if name in firsts and alias not in firsts:
            firsts[alias] = firsts[name]
    words = _words(text)
    for w in words[:3]:  # "texam" = "text sam", at the start of the request
        for verb in ("text", "tex", "txt"):
            rest = w[len(verb):]
            if w.startswith(verb) and len(rest) >= 2 and w not in ("texts", "texting", "texted"):
                best = difflib.get_close_matches(rest, firsts, n=1, cutoff=0.6)
                if best:
                    return (f"'{w}' is probably '{verb} {best[0]}': the recipient is the contact "
                            f"{firsts[best[0]]}."), True
    if not _MESSAGE_VERB.search(text):
        return "", False
    m = re.search(r"\b(?:text|message|tell|txt|tex|reply to|send)\s+(\w+)", text, re.I)
    name = m.group(1).lower() if m else ""
    # Only the part before the message body names the recipient ("Let Sam know
    # ...", "Send this to Sam"), never "... that Sam is late".
    body = re.compile(r"\b(?:that|saying|say|says)\b|:", re.I).search(text, m.end() if m else 0)
    head = text[: body.start()] if body else text
    hw = _words(head)

    def other_person(i: int) -> bool:  # "Sam's mom", "to Alex's wife": not Sam, not the user's Mom
        return (i + 1 < len(hw) and hw[i].endswith("'s") and hw[i] not in _CONTRACTIONS
                and (i == 0 or hw[i - 1] in _LEADS) and hw[i + 1] not in _NOT_A_NAME)

    def unknown(i: int) -> tuple[str, bool]:
        return f"'{hw[i]} {hw[i + 1]}' is not in the contacts. Ask who they mean before sending anything.", False

    slot = len(_words(text[: m.start(1)])) if m else -1  # the word right after the verb
    if 0 <= slot < len(hw) and other_person(slot):
        return unknown(slot)
    if name in firsts:
        return f"The recipient is the contact {firsts[name]}.", False
    for i, w in enumerate(hw):  # first one wins, left to right
        if w in firsts:
            return f"The recipient is the contact {firsts[w]}.", False
        if other_person(i):
            return unknown(i)
    if name and name not in _NOT_A_NAME:
        best = difflib.get_close_matches(name, firsts, n=1, cutoff=0.5)
        if best:
            return (f"'{name}' is not in the contacts; the closest is {firsts[best[0]]}. Ask whether they "
                    f"mean {firsts[best[0]]} before sending anything."), False
        return f"'{name}' is not in the contacts. Ask who they mean before sending anything.", False
    return "", False


_OFFER = re.compile(r"\b(want me to|would you like me to|shall i|should i|i can [^.?!]* if you('d| would) like|"
                    r"do you want me to|like me to)\b", re.I)
_AFFIRM = re.compile(r"^(?:(?:great|perfect|okay|ok|yes|yeah|yep|sure|please|absolutely|definitely|"
                     r"go ahead|do it|do that|that'd be great|sounds good|why not|yes please)[,.! ]*)+"
                     r"(?:do (it|that)|go ahead|please|thanks|thank you)?[.!]*$", re.I)
_PLACE_QUESTION = re.compile(
    r"\b(nearest|closest|near me|nearby|around here|directions to|how (do i|to) get to)\b", re.I)


def _wants_phone_action(text: str) -> bool:
    """The user asked Rose to change something on the phone: an imperative request,
    not a question about it, a decline or advice. Place questions ("where's the
    nearest pharmacy?") count: only Maps can answer them."""

    t = text.strip()
    if _PLACE_QUESTION.search(t) and not _DECLINE.search(t):
        return True
    if _POLITE_REQUEST.match(t) and _PHONE_REQUEST.search(t):
        return True  # "Why don't you turn on ..." asks for it, despite the "don't"
    if _DECLINE.search(t):
        return False
    return _requests_change(t)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _requests_change(text: str) -> bool:
    """Some sentence is itself a phone request: "Ride. Play my chill mix." (a misheard
    "Right,") is; "My boss sent a message. Remind me when it starts?" is not."""

    return any(_IMPERATIVE.match(s) and _PHONE_REQUEST.search(s) for s in _sentences(text))


# "What time is it?" needs no arithmetic: the time is in every turn's stamp.
_PLAIN_TIME_Q = re.compile(
    r"^(?:(?:actually|wait|hey|hi|ok|okay|so|um|uh|oh|and|sorry|rose|hmm|quick question|excuse me)[,.!]?\s+){0,3}"
    r"(?:what time is it|what's the time|what is the time|"
    r"do you know what time it is|do you have the time|got the time)(?:\s+(?:now|right now))?\s*[?.!]?$", re.I)
# "How are you doing today?", "How's your day going?": small talk, not a calendar question.
_SMALL_TALK = re.compile(r"\bhow('s| is| are| was)? (you|your day|it going|things|everything|life)\b", re.I)
_SAYS_TIME = re.compile(
    r"\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|noon|midnight|o'clock)\b", re.I)


def _same_goal(a: str, b: str) -> bool:
    wa, wb = set(_words(a)), set(_words(b))
    return bool(wa and wb) and len(wa & wb) / max(len(wa | wb), 1) >= 0.8
