"""Scripted conversations for the end-to-end eval, and how each one is judged.

A scenario is what a fake user does (speak, wait, talk over Rose, receive a
text) followed by checks on what was recorded. Content checks read Rose's own
text events (``rose_turn``), which are exact; ``--transcribe`` in
``rose.eval.run`` spot-checks that the played audio said the same thing.
Soft checks are design targets: reported, never failing a scenario.

Steps only describe. ``rose.eval.run`` performs them in real time against the
real pipeline and hands each check a ``Context``: the scenario's recorded
events, the derived turns and timings, and the phone.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from rose.textnorm import speakable

if TYPE_CHECKING:
    from .run import Context

USER_VOICE = "Samantha"


# ------------------------------------------------------------------ steps
@dataclass
class Say:
    """Speak ``text`` (macOS ``say``) into the fake mic in real time, then trailing silence."""

    text: str
    voice: str = USER_VOICE
    trail_ms: int = 300


@dataclass
class SayWhileRoseSpeaks:
    """Wait until Rose's audio has been playing for ``after_ms``, then speak over her."""

    text: str
    after_ms: int = 2000
    voice: str = USER_VOICE
    role: str = "barge"  # "barge" (meant to interrupt) | "backchannel" (must not)
    timeout: float = 30.0
    trail_ms: int = 300


@dataclass
class Wait:
    seconds: float


@dataclass
class WaitFor:
    """Wait for a bus event of ``kind`` matching ``pred``, recorded after the anchor.

    ``since``: "say" (start of the last user utterance), "step" (this step),
    "start" (the scenario) or a mark name.
    """

    kind: str
    pred: Callable[[dict], bool] | None = None
    timeout: float = 20.0
    since: str = "say"


@dataclass
class WaitIdle:
    """Wait until Rose has played audio since the last utterance and the client is quiet again."""

    timeout: float = 30.0
    quiet_ms: int = 300


@dataclass
class Notify:
    app: str
    title: str
    text: str


@dataclass
class Mark:
    name: str


@dataclass
class Settle:
    """Wait for quiet: no user speech, no Rose audio, no running task, no events for ``quiet`` s."""

    quiet: float = 3.0
    timeout: float = 45.0


@dataclass
class Check:
    name: str
    fn: Callable[[Context], bool | tuple[bool, str]]
    soft: bool = False


@dataclass
class Expect:
    """Evaluate a check at this point of the script (for state that changes later)."""

    check: Check


Step = Say | SayWhileRoseSpeaks | Wait | WaitFor | WaitIdle | Notify | Mark | Settle | Expect


@dataclass
class Scenario:
    name: str
    about: str
    steps: list[Step]
    checks: list[Check] = field(default_factory=list)
    settle: Settle = field(default_factory=Settle)


def say(text: str, voice: str = USER_VOICE, trail_ms: int = 300) -> Say:
    return Say(text, voice, trail_ms)


def wait(seconds: float) -> Wait:
    return Wait(seconds)


def wait_for(kind: str, predicate: Callable[[dict], bool] | None = None, timeout: float = 20.0,
             since: str = "say") -> WaitFor:
    return WaitFor(kind, predicate, timeout, since)


def wait_idle(timeout: float = 30.0) -> WaitIdle:
    return WaitIdle(timeout)


def say_while_rose_speaks(text: str, after_ms: int = 2000, voice: str = USER_VOICE) -> SayWhileRoseSpeaks:
    return SayWhileRoseSpeaks(text, after_ms, voice, role="barge")


def backchannel(word: str = "Mm-hmm.", after_ms: int = 1500, voice: str = USER_VOICE) -> SayWhileRoseSpeaks:
    return SayWhileRoseSpeaks(word, after_ms, voice, role="backchannel")


def notify(app: str, title: str, text: str) -> Notify:
    return Notify(app, title, text)


def mark(name: str) -> Mark:
    return Mark(name)


def settle(quiet: float = 3.0, timeout: float = 45.0) -> Settle:
    return Settle(quiet, timeout)


def check(name: str, fn: Callable[[Context], bool | tuple[bool, str]], soft: bool = False) -> Check:
    return Check(name, fn, soft)


def expect(name: str, fn: Callable[[Context], bool | tuple[bool, str]], soft: bool = False) -> Expect:
    return Expect(Check(name, fn, soft))


def called(name: str) -> Callable[[dict], bool]:
    return lambda ev: ev.get("name") == name


# ------------------------------------------------------------ text helpers
def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower().replace("’", "'").replace("-", " "))


def clip(text: str, n: int = 160) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 3] + "..."


def word_prefix(heard: str, said: str) -> bool:
    """``heard`` is the start of ``said`` word for word (the last heard word may be cut)."""

    h = words(speakable((heard or "").rstrip(" -—–")))
    s = words(speakable(said or ""))
    if not h:
        return True
    if len(h) > len(s) or h[:-1] != s[: len(h) - 1]:
        return False
    return s[len(h) - 1].startswith(h[-1])


_ONES = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}


def times_in(text: str) -> list[tuple[int, int]]:
    """Clock times mentioned in ``text`` as (hour % 12, minute): digits or spoken words."""

    t = (text or "").lower().replace("’", "'")
    out = [(int(h) % 12, int(m)) for h, m in re.findall(r"\b(\d{1,2}):(\d{2})\b", t)]
    out += [(int(h) % 12, 0) for h in re.findall(r"(?<![\d:])(\d{1,2})\s*(?:o'clock|a\.?m\b|p\.?m\b)", t)]
    w = re.findall(r"[a-z']+", t.replace("-", " "))
    for i, tok in enumerate(w):
        h = _ONES.get(tok)
        if h is None or not 1 <= h <= 12:
            continue
        nxt = w[i + 1 : i + 3]
        first = nxt[0] if nxt else ""
        second = nxt[1] if len(nxt) > 1 else ""
        if first == "o'clock":
            out.append((h % 12, 0))
        elif first == "oh" and 1 <= _ONES.get(second, 0) <= 9:
            out.append((h % 12, _ONES[second]))
        elif 10 <= _ONES.get(first, 0) <= 19:
            out.append((h % 12, _ONES[first]))
        elif first in _TENS:
            extra = _ONES.get(second, 0)
            out.append((h % 12, _TENS[first] + (extra if 1 <= extra <= 9 else 0)))
    for part, rel, hour in re.findall(r"\b(half|quarter) (past|after|to|til) (\w+)", t):
        h = _ONES.get(hour) or (int(hour) if hour.isdigit() else None)
        if h:
            m = 30 if part == "half" else 15
            out.append(((h % 12), m) if rel != "to" and rel != "til" else ((h - 1) % 12, 60 - m))
    return out


def near_time(text: str, t_unix: float, tolerance_min: int = 3) -> bool:
    lt = time.localtime(t_unix)
    ref = (lt.tm_hour % 12) * 60 + lt.tm_min
    for h, m in times_in(text):
        d = abs(h * 60 + m - ref) % 720
        if min(d, 720 - d) <= tolerance_min:
            return True
    return False


def _sent_to(data: dict, contact: str) -> list[str]:
    """Messages the user sent to a contact, oldest first."""

    out = []
    for name, msgs in data.get("threads", {}).items():
        if contact.lower() not in name.lower():
            continue
        for m in msgs:
            who, text = (m.get("from") or m.get("who"), m.get("text")) if isinstance(m, dict) else m
            if who == "me":
                out.append(str(text))
    return out


def new_sent(c: Context, contact: str) -> list[str]:
    """Messages sent to ``contact`` during the scenario."""

    return _sent_to(c.phone_data(), contact)[len(_sent_to(c.phone_start, contact)):]


# ------------------------------------------------------------------ checks
def said(pattern: str, name: str | None = None, soft: bool = False,
         since: Callable[[Context], float | None] | None = None) -> Check:
    """Rose's words (all turns, or those starting after ``since(ctx)``) match ``pattern``."""

    rx = re.compile(pattern, re.I)

    def fn(c: Context):
        t = since(c) if since else None
        if since and t is None:
            return False, "anchor event never happened"
        text = c.rose_text(since=t)
        return bool(rx.search(text)), clip(text)

    return Check(name or f"Rose said /{pattern}/", fn, soft)


def replied(n: int = 1) -> Check:
    def fn(c: Context):
        turns = c.rose_turns()
        return len(turns) >= n, f"{len(turns)} Rose turn(s)"

    return Check("Rose replied" if n == 1 else f"Rose took >= {n} turns", fn)


def turn_count(n: int, name: str | None = None) -> Check:
    def fn(c: Context):
        turns = c.rose_turns()
        return len(turns) == n, f"{len(turns)}: " + " | ".join(clip(r['text'], 60) for r in turns)

    return Check(name or f"exactly {n} Rose turn(s)", fn)


def no_tool_calls(name: str | None = None, label: str | None = None, soft: bool = False) -> Check:
    def fn(c: Context):
        calls = c.tool_calls(name)
        return not calls, ", ".join(f"{x['name']}({clip(str(x.get('arguments')), 60)})" for x in calls)

    return Check(label or (f"no {name} call" if name else "no tool calls"), fn, soft)


def called_once(name: str) -> Check:
    def fn(c: Context):
        calls = c.tool_calls(name)
        return len(calls) == 1, f"{len(calls)} call(s): " + "; ".join(
            clip(str(x.get("arguments")), 80) for x in calls)

    return Check(f"{name} called exactly once", fn)


def phone_state(name: str, fn: Callable[[dict], bool | tuple[bool, str]], soft: bool = False) -> Check:
    return Check(name, lambda c: fn(c.phone_data()), soft)


def last_sent_matches(contact: str, pattern: str, name: str | None = None) -> Check:
    rx = re.compile(pattern, re.I)

    def fn(c: Context):
        new = new_sent(c, contact)
        if not new:
            return False, f"nothing new sent to {contact}"
        return bool(rx.search(new[-1])), f"last sent: {new[-1]!r}"

    return Check(name or f"{contact} thread: last sent message matches /{pattern}/", fn)


def task_finished(kind: str = "phone", status: str = "done", within_s: float | None = None,
                  soft: bool = False) -> Check:
    def fn(c: Context):
        tasks = [t for t in c.tasks() if t["kind"] == kind]
        if not tasks:
            return False, f"no {kind} task"
        t = tasks[-1]
        ok = t["status"] == status
        if within_s is not None:
            ok = ok and t.get("duration_s") is not None and t["duration_s"] <= within_s
        return ok, (f"#{t['id']} {t['status']} in {t.get('duration_s')} s, {len(t['steps'])} steps: "
                    f"{clip(t['result'], 80)}")

    label = f"{kind} task {status}" + (f" within {within_s:g} s" if within_s else "")
    return Check(label, fn, soft)


def no_unprompted() -> Check:
    def fn(c: Context):
        extra = c.unprompted()
        return not extra, "; ".join(f"#{r['id']}: {clip(r['text'], 60)}" for r in extra)

    return Check("no unprompted Rose turns", fn)


def no_repeated_calls() -> Check:
    def fn(c: Context):
        dups = c.repeated_calls()
        return not dups, "; ".join(f"{d['name']}({clip(str(d.get('arguments')), 60)})" for d in dups)

    return Check("no repeated tool calls", fn)


def no_talk_over() -> Check:
    def fn(c: Context):
        bad = c.talk_overs()
        return not bad, "; ".join(f"#{b['id']} started {b['early_ms']} ms before the user finished: "
                                  f"{clip(b['text'], 50)}" for b in bad)

    return Check("Rose never started talking while the user was speaking", fn)


def repeats(text: str, times: int = 3) -> list[str]:
    """Sentences (>= 2 words) said ``times`` or more in one reply: a degenerate loop."""

    counts: dict[str, int] = {}
    for sentence in re.split(r"[.!?\n]+", text or ""):
        key = " ".join(words(sentence))
        if len(key.split()) >= 2:
            counts[key] = counts.get(key, 0) + 1
    return [k for k, n in counts.items() if n >= times]


def no_loops() -> Check:
    def fn(c: Context):
        bad = [(r["id"], repeats(r["text"])) for r in c.rose_turns() if repeats(r["text"])]
        return not bad, "; ".join(f"#{rid} repeats {', '.join(repr(x) for x in rs[:3])}" for rid, rs in bad)

    return Check("no reply repeats a sentence 3+ times", fn)


def no_user_turns() -> Check:
    def fn(c: Context):
        turns = c.events("user_turn")
        return not turns, "; ".join(clip(e.get("text", ""), 60) for e in turns)

    return Check("no user turns detected", fn)


def latency_under(ms: float = 900, soft: bool = True) -> Check:
    def fn(c: Context):
        rows = [t for t in c.turns() if t["role"] == "turn"]
        vals = [t.get("voice_to_voice_ms") for t in rows]
        if not rows or any(v is None for v in vals):
            return False, f"missing: {vals}"
        return max(vals) <= ms, f"{[round(v) for v in vals]} ms"

    return Check(f"voice-to-voice <= {ms:g} ms", fn, soft)


def _barge(c: Context, role: str = "barge") -> dict | None:
    rows = [b for b in c.barges() if b["role"] == role]
    return rows[-1] if rows else None


def barge_stop_within(ms: float, soft: bool = False) -> Check:
    def fn(c: Context):
        b = _barge(c)
        if b is None:
            return False, "no barge-in utterance"
        v = b.get("stop_ms")
        return v is not None and v <= ms, f"stopped {v} ms after the interruption began (duck {b.get('duck_ms')} ms)"

    return Check(f"Rose stopped within {ms:g} ms", fn, soft)


def barge_silent_within(ms: float, soft: bool = False) -> Check:
    """Rose is inaudible (paused with her place kept, or stopped) within ``ms``."""

    def fn(c: Context):
        b = _barge(c)
        if b is None:
            return False, "no barge-in utterance"
        v = b.get("silent_ms")
        return v is not None and v <= ms, (f"silent {v} ms after the interruption began "
                                           f"(duck {b.get('duck_ms')}, pause {b.get('pause_ms')}, "
                                           f"stop {b.get('stop_ms')} ms)")

    return Check(f"Rose went silent within {ms:g} ms", fn, soft)


def barge_duck_within(ms: float, soft: bool = True) -> Check:
    def fn(c: Context):
        b = _barge(c)
        v = b.get("duck_ms") if b else None
        return v is not None and v <= ms, f"ducked {v} ms after the interruption began"

    return Check(f"Rose ducked within {ms:g} ms", fn, soft)


def heard_is_prefix() -> Check:
    def fn(c: Context):
        b = _barge(c)
        if b is None or b.get("reply_id") is None:
            return False, "no interrupted reply"
        heard = b.get("heard")
        if not heard or not heard.strip():
            return False, "nothing recorded as heard"
        ok = word_prefix(heard, b.get("said", "")) or word_prefix(heard, b.get("generated", ""))
        return ok, f"heard {clip(heard, 80)!r} of {clip(b.get('said', ''), 80)!r}"

    return Check("heard text is a prefix of what Rose said", fn)


def reply_interrupted() -> Check:
    def fn(c: Context):
        b = _barge(c)
        if b is None or b.get("reply_id") is None:
            return False, "no reply was playing when the user cut in"
        return bool(b.get("interrupted")), f"reply #{b['reply_id']} interrupted={b.get('interrupted')}"

    return Check("interrupted reply is marked interrupted", fn)


def answer_after_barge(pattern_fn: Callable[[str, float], bool], name: str) -> Check:
    def fn(c: Context):
        b = _barge(c)
        if b is None:
            return False, "no barge-in utterance"
        turns = [r for r in c.rose_turns() if r["start"] > b["t_voice_start"] and r["id"] != b.get("reply_id")]
        if not turns:
            return False, "no reply after the interruption"
        r = turns[0]
        return pattern_fn(r["text"], r["t"]), clip(r["text"])

    return Check(name, fn)


def backchannel_kept_talking() -> Check:
    def fn(c: Context):
        b = _barge(c, "backchannel")
        if b is None:
            return False, "no backchannel utterance"
        if not b["rose_playing"]:
            return False, "invalid run: Rose was not speaking when the backchannel began"
        if b.get("stop_ms") is not None:
            return False, f"stopped {b['stop_ms']} ms after the backchannel"
        return not b.get("interrupted"), (f"duck {b.get('duck_ms')} ms, resumed={b.get('resumed')}, "
                                          f"interrupted={b.get('interrupted')}")

    return Check("backchannel did not stop Rose", fn)


def quiet_after_barge(max_words: int = 2) -> Check:
    def fn(c: Context):
        b = _barge(c)
        if b is None:
            return False, "no interrupting utterance"
        after = [r for r in c.rose_turns() if r["start"] > b["t_voice_start"] and r["id"] != b.get("reply_id")]
        n = sum(len(words(r["text"])) for r in after)
        return len(after) <= 1 and n <= max_words, (
            f"{len(after)} turn(s), {n} words: " + " | ".join(clip(r["text"], 60) for r in after))

    return Check(f"after the interruption Rose is quiet or says <= {max_words} words", fn)


def stopped() -> Check:
    def fn(c: Context):
        b = _barge(c)
        if b is None:
            return False, "no interrupting utterance"
        if not b["rose_playing"]:
            return False, "invalid run: Rose was not speaking when the user cut in"
        return b.get("stop_ms") is not None, f"stop {b.get('stop_ms')} ms, duck {b.get('duck_ms')} ms"

    return Check("Rose stopped talking", fn)


def _task_done_at(c: Context) -> float | None:
    done = [t["t_finished"] for t in c.tasks() if t.get("t_finished") is not None]
    return min(done) if done else None


def _notified_at(c: Context) -> float | None:
    ev = c.events("eval.notify")
    return ev[0]["mono"] if ev else None


def _duck_undone(c: Context) -> tuple[bool, str]:
    b = _barge(c, "backchannel") or {}
    ok = bool(b.get("resumed")) or b.get("duck_ms") is None
    return ok, f"duck {b.get('duck_ms')} ms, resumed={b.get('resumed')}, stop {b.get('stop_ms')} ms"


def _short_after_barge(c: Context, max_words: int) -> tuple[bool, str]:
    b = _barge(c)
    after = [r for r in c.rose_turns() if b and r["start"] > b["t_voice_start"] and r["id"] != b.get("reply_id")]
    if not after:
        return False, "no reply after the interruption"
    n = len(words(after[0]["text"]))
    return n <= max_words, f"{n} words"


def _answered(c: Context, utterance: int) -> tuple[bool, str]:
    row = next((t for t in c.turns() if t["utterance"] == utterance), None)
    if row is None:
        return False, "utterance missing"
    return bool(row.get("reply_text", "").strip()), clip(row.get("reply_text") or row.get("error", ""))


def _answered_during_task(c: Context, utterance: int) -> tuple[bool, str]:
    row = next((t for t in c.turns() if t["utterance"] == utterance), None)
    done = _task_done_at(c)
    if row is None or row.get("first_played") is None or done is None:
        return False, "reply or task finish missing"
    return row["first_played"] < done, f"reply audio {round((done - row['first_played']) * 1000)} ms before task end"


MATH = (r"\b2\s*(?:and\s*)?5\s*/\s*8\b|\b2\.625\b|\b2\.63\b|2⅝|\b21\s*/\s*8\b|"
        r"two and (?:a )?five[\s-]eighths?|twenty[\s-]one eighths|two point six two five")


# --------------------------------------------------------------- scenarios
def _base(turns: bool = True) -> list[Check]:
    out = [no_unprompted(), no_repeated_calls(), no_talk_over(), no_loops()]
    if turns:
        out.append(latency_under(900))
    return out


SCENARIOS: list[Scenario] = [
    Scenario(
        "greeting", "A hello gets a short, natural reply and no tool call.",
        [say("Hi Rose, how are you doing today?")],
        [replied(), turn_count(1), no_tool_calls(),
         check("reply is short (<= 30 words)",
               lambda c: (sum(len(words(r["text"])) for r in c.rose_turns()) <= 30,
                          clip(c.rose_text())), soft=True),
         *_base()]),
    Scenario(
        "knowledge", "A fact question is answered directly: Canberra.",
        [say("What's the capital of Australia?")],
        [said(r"\bcanberra\b", "answer mentions Canberra"), no_tool_calls("phone_task"), *_base()]),
    Scenario(
        "math", "Recipe scaling: 1.5 cups for 4 people -> 2 5/8 cups for 7.",
        [say("If a recipe for four people needs one and a half cups of flour, "
             "how much flour do I need for seven people?")],
        [said(MATH, "answer is 2 5/8 cups (2.625)"), no_tool_calls("phone_task"), *_base()],
        settle=Settle(quiet=4.0, timeout=90.0)),
    Scenario(
        "calendar", "The afternoon calendar is answered from context: design review with Alex at four.",
        [say("What's on my calendar this afternoon?")],
        [said(r"\balex\b", "mentions Alex"), said(r"\bfour\b|(?<![\d:])4(?![\d])", "mentions four"),
         no_tool_calls("phone_task", "answered from context (no phone_task)"), *_base()]),
    Scenario(
        "task_while_chatting",
        "A text to Sam runs in the background while the user makes small talk; Rose then reports it.",
        [say("Text Sam that I'm running ten minutes late."),
         wait_for("tool_call", called("phone_task"), timeout=20),
         wait_idle(timeout=20),
         say("How's your day going?")],
        [called_once("phone_task"),
         last_sent_matches("Sam", r"\blate\b"),
         last_sent_matches("Sam", r"\bten\b|\b10\b", "Sam thread: message says ten minutes"),
         task_finished("phone"),
         task_finished("phone", within_s=4.0, soft=True),
         check("small talk was answered", lambda c: _answered(c, 1)),
         check("small talk answered while the task ran", lambda c: _answered_during_task(c, 1), soft=True),
         said(r"\bsam\b|\bsent\b|\btext", "completion announced after the task finished",
              since=_task_done_at),
         *_base()],
        settle=Settle(quiet=4.0, timeout=60.0)),
    Scenario(
        "alarm", "Set an alarm for 6:30 AM through the phone agent.",
        [say("Set an alarm for six thirty tomorrow morning.")],
        [called_once("phone_task"),
         phone_state("alarm 6:30 AM exists and is on", lambda d: (
             any(re.fullmatch(r"0?6:30\s*AM", str(a.get("time", "")), re.I) and a.get("on")
                 for a in d.get("alarms", [])),
             ", ".join(f"{a.get('time')} {'on' if a.get('on') else 'off'}" for a in d.get("alarms", [])))),
         task_finished("phone"), task_finished("phone", within_s=4.0, soft=True),
         said(r"6:30|six thirty|alarm", "Rose confirmed after the task finished", since=_task_done_at),
         *_base()],
        settle=Settle(quiet=4.0, timeout=60.0)),
    Scenario(
        "do_not_disturb", "Turn on Do Not Disturb through the phone agent.",
        [say("Turn on do not disturb.")],
        [called_once("phone_task"),
         phone_state("Do Not Disturb is on", lambda d: (
             bool(d.get("settings", {}).get("Do Not Disturb")), str(d.get("settings")))),
         task_finished("phone"), task_finished("phone", within_s=4.0, soft=True),
         said(r"disturb|\bon\b|done", "Rose confirmed after the task finished", since=_task_done_at),
         *_base()],
        settle=Settle(quiet=4.0, timeout=60.0)),
    Scenario(
        "barge_in",
        "Interrupt a long answer after 2 s: Rose stops within 600 ms, remembers only what was heard, "
        "and answers the new question (the time).",
        [say("Tell me a bit about the history of Rome."),
         say_while_rose_speaks("Actually, what time is it?", after_ms=2000)],
        [stopped(), barge_silent_within(500), barge_silent_within(400, soft=True), barge_stop_within(1200, soft=True),
         barge_duck_within(150),
         reply_interrupted(), heard_is_prefix(),
         answer_after_barge(lambda text, t: near_time(text, t), "answers with the current time"),
         check("the time answer is short (<= 30 words)", lambda c: _short_after_barge(c, 30), soft=True),
         *_base(turns=False)],
        settle=Settle(quiet=3.0, timeout=60.0)),
    Scenario(
        "backchannel", "A 'mm-hmm' during a long answer must not stop Rose or get a reply.",
        [say("Can you explain in some detail how volcanoes form?"),
         backchannel("Mm-hmm.", after_ms=1500)],
        [backchannel_kept_talking(), turn_count(1, "no reply to the backchannel (one Rose turn)"),
         check("backchannel duck was undone", _duck_undone, soft=True),
         *_base(turns=False)],
        settle=Settle(quiet=3.0, timeout=90.0)),
    Scenario(
        "hold_on", "'Wait, hold on a second' during a long answer: Rose stops and stays quiet (<= 2 words).",
        [say("Tell me a story about a lighthouse keeper."),
         say_while_rose_speaks("Wait, hold on a second.", after_ms=2000)],
        [stopped(), barge_silent_within(500, soft=True), quiet_after_barge(2), *_base(turns=False)],
        settle=Settle(quiet=5.0, timeout=60.0)),
    Scenario(
        "amend_task", "Change a running text: 'tonight' becomes 'tomorrow' before (or right after) it is sent.",
        [say("Text Mom that I'll call her tonight."),
         wait_for("tool_call", called("phone_task"), timeout=20),
         say("Actually, make that tomorrow.")],
        [last_sent_matches("Mom", r"\btomorrow\b", "Mom thread: last sent message says tomorrow"),
         check("no message saying 'tonight' was sent (amended in flight)", lambda c: (
             not any(re.search(r"\btonight\b", m, re.I) for m in new_sent(c, "Mom")),
             str(new_sent(c, "Mom"))), soft=True),
         check("task amended (update_task) rather than redone",
               lambda c: (bool(c.tool_calls("update_task")),
                          ", ".join(x["name"] for x in c.tool_calls())), soft=True),
         said(r"tomorrow", "Rose confirmed 'tomorrow'", soft=True),
         *_base(turns=False)],
        settle=Settle(quiet=4.0, timeout=60.0)),
    Scenario(
        "notification", "An incoming text while idle: Rose mentions it once, unprompted by the user.",
        [wait(2.0), notify("Messages", "Jordan Park", "Are we still on for soccer Saturday?")],
        [turn_count(1), said(r"jordan|soccer", "mentions Jordan or soccer", since=_notified_at),
         check("mentioned exactly once", lambda c: (
             sum(bool(re.search(r"jordan|soccer", r["text"], re.I)) for r in c.rose_turns()) == 1,
             " | ".join(clip(r["text"], 60) for r in c.rose_turns()))),
         check("spoke within 3 s of the notification", lambda c: (
             bool(c.notify_latencies()) and c.notify_latencies()[0] <= 3000, f"{c.notify_latencies()} ms"),
             soft=True),
         no_tool_calls(), no_unprompted(), no_repeated_calls(), no_loops()],
        settle=Settle(quiet=5.0, timeout=30.0)),
    Scenario(
        "silence", "30 s of room silence: Rose says nothing and nothing is mistaken for speech.",
        [wait(30.0)],
        [turn_count(0, "zero Rose turns"), no_tool_calls(), no_user_turns(),
         check("no speech detected", lambda c: (not c.events("speech_start"),
                                                f"{len(c.events('speech_start'))} speech_start"), soft=True)],
        settle=Settle(quiet=2.0, timeout=20.0)),
    # Added after the conductor review: things that must NOT trigger actions,
    # doubled backchannels, multi-part requests and context-dependent follow-ups.
    Scenario(
        "advice_no_action", "An advice question about a setting is answered, not acted on.",
        [say("Should I turn off Bluetooth to save battery?")],
        [replied(), no_tool_calls("phone_task", "no phone_task for an advice question"),
         phone_state("Bluetooth still on", lambda d: (bool(d.get("settings", {}).get("Bluetooth")),
                                                      str(d.get("settings")))),
         *_base()]),
    Scenario(
        "double_backchannel", "'Mm-hmm, mm-hmm' during a long answer must not stop Rose.",
        [say("Can you explain in some detail how hurricanes form?"),
         backchannel("Mm-hmm, mm-hmm.", after_ms=1500)],
        [backchannel_kept_talking(), turn_count(1, "no reply to the backchannel (one Rose turn)"),
         *_base(turns=False)],
        settle=Settle(quiet=3.0, timeout=90.0)),
    Scenario(
        "two_requests", "Two phone changes in one sentence both happen.",
        [say("Turn on do not disturb and set an alarm for seven fifteen tomorrow morning.")],
        [phone_state("Do Not Disturb is on", lambda d: (
            bool(d.get("settings", {}).get("Do Not Disturb")), str(d.get("settings")))),
         phone_state("alarm 7:15 AM exists and is on", lambda d: (
             any(re.fullmatch(r"0?7:15\s*AM", str(a.get("time", "")), re.I) and a.get("on")
                 for a in d.get("alarms", [])),
             ", ".join(f"{a.get('time')} {'on' if a.get('on') else 'off'}" for a in d.get("alarms", [])))),
         *_base()],
        settle=Settle(quiet=4.0, timeout=60.0)),
    Scenario(
        "context_followup", "Answer from the messages, then act on a follow-up that depends on it.",
        [say("What did Alex ask me?"),
         wait_idle(timeout=20),
         say("Reply that I'll send it tonight.")],
        [said(r"\bdeck\b", "says Alex asked about the deck"),
         called_once("phone_task"),
         last_sent_matches("Alex Chen", r"\btonight\b", "Alex thread: reply says tonight"),
         task_finished("phone"),
         *_base()],
        settle=Settle(quiet=4.0, timeout=60.0)),
    Scenario(
        "unknown_contact", "A name that is not a contact gets a question, not a guess; the answer sends it.",
        [say("Text Pam that I'll be there soon."),
         wait_idle(timeout=20),
         say("Yes, Sam.")],
        [said(r"\bpam\b", "asks about Pam"),
         said(r"\bsam\b.*\?|did you mean", "suggests Sam as a question"),
         called_once("phone_task"),
         last_sent_matches("Sam Lee", r"\bsoon\b", "Sam thread: 'be there soon' sent after confirming"),
         *_base()],
        settle=Settle(quiet=4.0, timeout=60.0)),
    Scenario(
        "hard_question", "A multi-step money question is worked out exactly (calculate or think), about 38,800.",
        [say("If I save two hundred fifty dollars a month at five percent a year, compounded monthly, "
             "about how much will I have after ten years?")],
        [said(r"\b38[,.]?[78]\d\d|\bthirty[- ]eight thousand|\b38\.8|\b39,?000|\bthirty[- ]nine thousand|"
              r"\b38,?\d{3}", "answer is about $38,800"),
         no_tool_calls("phone_task"), no_unprompted(), no_repeated_calls(), no_talk_over(), no_loops()],
        settle=Settle(quiet=5.0, timeout=90.0)),
    # Added after the first live trial: Music and Maps, and conversational phrasing.
    Scenario(
        "play_music", "'Right, play my chill mix.' actually plays it (no claim without a tool call).",
        [say("Right, play my chill mix.")],
        [called_once("phone_task"),
         phone_state("Chill Mix is playing", lambda d: (
             bool(d.get("music", {}).get("playing")) and (d["music"].get("now") or {}).get("playlist") == "Chill Mix",
             str(d.get("music", {}).get("now")))),
         task_finished("phone"), *_base()],
        settle=Settle(quiet=4.0, timeout=60.0)),
    Scenario(
        "nearest_pharmacy", "'Where's the nearest pharmacy?' is looked up in Maps, never guessed.",
        [say("Where's the nearest pharmacy?")],
        [called_once("phone_task"), task_finished("phone"),
         said(r"valencia", "names the pharmacy Maps found", since=_task_done_at),
         check("no pharmacy named before Maps answered",
               lambda c: (not re.search(r"valencia|market street|walgreens|cvs|rite aid",
                                        c.rose_text(until=_task_done_at(c)) if _task_done_at(c) else "", re.I),
                          clip(c.rose_text()))),
         *_base()],
        settle=Settle(quiet=4.0, timeout=60.0)),
    Scenario(
        "directions", "Walking directions to the nearest coffee shop are started in Maps.",
        [say("Get me walking directions to the nearest coffee shop.")],
        [called_once("phone_task"),
         phone_state("walking directions to Sunrise Coffee Roasters are on", lambda d: (
             (d.get("navigation") or {}).get("to") == "Sunrise Coffee Roasters"
             and (d.get("navigation") or {}).get("mode") == "walking", str(d.get("navigation")))),
         task_finished("phone"), *_base()],
        settle=Settle(quiet=4.0, timeout=60.0)),
]


BY_NAME = {s.name: s for s in SCENARIOS}
