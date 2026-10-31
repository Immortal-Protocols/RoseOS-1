"""The brain: Qwen3.6-35B-A3B behind an OpenAI-style /v1/chat/completions.

Three jobs:
* ``ask``        - answer a question in one or two speakable sentences.
* ``decide``     - Design B: classify a user turn as chat / think / act.
* ``run_phone``  - drive the fake phone (screenshot + UI tree in, one tool call
                   out, repeat) until the request is done.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from .phone import Phone

BRAIN_URL = os.environ.get("BRAIN_URL", "http://127.0.0.1:8081/v1")
BRAIN_MODEL = os.environ.get("BRAIN_MODEL", "brain")
NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}


def _client() -> httpx.Client:
    return httpx.Client(base_url=BRAIN_URL, timeout=120)


def chat(messages: list[dict], **kwargs) -> dict:
    body = {"model": BRAIN_MODEL, "messages": messages, "temperature": 0.2, **NO_THINK, **kwargs}
    with _client() as client:
        response = client.post("/chat/completions", json=body)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]


def healthy() -> bool:
    try:
        with httpx.Client(timeout=2) as client:
            return client.get(BRAIN_URL.removesuffix("/v1") + "/health").status_code == 200
    except httpx.HTTPError:
        return False


# ----- ask -------------------------------------------------------------
ASK_SYSTEM = (
    "You answer questions for a voice assistant. Your answer will be read aloud, so reply "
    "in one or two short plain sentences: no lists, no markdown, no symbols, spell out "
    "units. Today is {today}. When the question is about the user's own life, use the "
    "phone data below; do not invent people, plans or messages that are not in it.\n\n"
    "{phone}"
)


ASK_THINKING = os.environ.get("ASK_THINKING", "1") != "0"


def ask(question: str, thinking: bool | None = None, phone_context: str = "") -> str:
    """Reasoning on is slower (5-15 s) but gets math and multi-step facts right."""

    thinking = ASK_THINKING if thinking is None else thinking
    today = time.strftime("%A %B %-d %Y, %-I:%M %p")
    msg = chat(
        [{"role": "system", "content": ASK_SYSTEM.format(
            today=today, phone=phone_context or "(no phone data)")},
         {"role": "user", "content": question}],
        max_tokens=2000 if thinking else 200,
        chat_template_kwargs={"enable_thinking": thinking},
    )
    return (msg.get("content") or "").strip()


# ----- decide (Design B) -----------------------------------------------
DECIDE_SYSTEM = """You route turns for a voice assistant on a phone. A fast voice model is \
already replying to the user. Decide if a slower helper must do extra work for this turn:

- "chat": greetings, small talk, feelings, jokes, and requests the phone cannot do at all \
(the voice model can say so itself).
- "think": anything else that is a question: facts, advice, ideas, plans, math, and questions \
about the user's own life, people, messages or schedule (the helper can see the phone's \
messages, contacts, calendar and alarms). When unsure between chat and think, choose think.
- "act": the user wants something changed or opened on the phone (send a text, set an \
alarm, change a setting, open or show an app).

Return JSON: {"mode": "chat"|"think"|"act", "task": "<self-contained task or question, empty \
for chat>"}. Use the recent conversation to resolve references like "him" or "that"."""

DECIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["chat", "think", "act"]},
        "task": {"type": "string"},
    },
    "required": ["mode", "task"],
}


def decide(user_turn: str, recent: list[str] | None = None) -> dict:
    context = "\n".join(recent or [])
    prompt = f"Recent conversation:\n{context or '(none)'}\n\nUser just said: {user_turn}"
    msg = chat(
        [{"role": "system", "content": DECIDE_SYSTEM}, {"role": "user", "content": prompt}],
        max_tokens=120,
        temperature=0.0,
        response_format={"type": "json_object", "schema": DECIDE_SCHEMA},
    )
    try:
        out = json.loads(msg.get("content") or "{}")
    except json.JSONDecodeError:
        return {"mode": "chat", "task": ""}
    if out.get("mode") not in ("chat", "think", "act"):
        out["mode"] = "chat"
    return out


# ----- phone agent -----------------------------------------------------
PHONE_SYSTEM = """You operate a phone for the user. Each turn you get a screenshot (red \
numbered marks on everything you can tap) and the screen's UI tree with the same numbers. \
Call exactly one tool per turn. Open apps with open_app. To type, use type_text on a field, \
then tap the button that submits (Send, Save, Start conversation). Check the screen after \
each action. To read something, open the app, look, and finish with what you read. When \
the request is complete, impossible, or unclear, call finish right away with a one-sentence \
summary for the user (for an unclear request, say what you need to know). Never repeat an \
action that did not help. Never invent information."""


def _tool(name: str, description: str, **props) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {k: {"type": t, "description": d} for k, (t, d) in props.items()},
                "required": list(props),
            },
        },
    }


PHONE_TOOLS = [
    _tool("open_app", "Open an app from anywhere.", app=("string", "App name, e.g. Messages")),
    _tool("tap", "Tap an element by its mark number.", mark=("integer", "The red mark number")),
    _tool("type_text", "Replace the text in a field.", mark=("integer", "Field mark number"),
          text=("string", "Text to type")),
    _tool("back", "Go back one screen."),
    _tool("home", "Go to the home screen."),
    _tool("finish", "End the task.", summary=("string", "One sentence for the user"),
          success=("boolean", "Whether the request was done")),
]


@dataclass
class PhoneStep:
    action: str
    args: dict
    result: str
    ms: float


@dataclass
class PhoneRun:
    request: str
    summary: str = ""
    success: bool = False
    steps: list[PhoneStep] = field(default_factory=list)


def _screen_message(phone: Phone, note: str) -> dict:
    png = base64.b64encode(phone.screenshot_png()).decode()
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": f"{note}\n\n{phone.ui_tree()}"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}},
        ],
    }


def run_phone(
    request: str,
    phone: Phone,
    on_step: Callable[[PhoneStep], None] | None = None,
    max_steps: int = 8,
    cancel: threading.Event | None = None,
) -> PhoneRun:
    run = PhoneRun(request)
    messages = [
        {"role": "system", "content": PHONE_SYSTEM},
        _screen_message(phone, f"Request: {request}\nCurrent screen:"),
    ]
    seen: list[tuple] = []
    for _ in range(max_steps):
        if cancel is not None and cancel.is_set():
            run.summary = "Stopped because the user interrupted."
            return run
        start = time.perf_counter()
        msg = chat(messages, tools=PHONE_TOOLS, tool_choice="required", max_tokens=300)
        calls = msg.get("tool_calls") or []
        if not calls:
            messages.append({"role": "assistant", "content": msg.get("content") or ""})
            messages.append({"role": "user", "content": "Call one of the tools."})
            continue
        call = calls[0]
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        if name == "finish":
            run.summary = args.get("summary", "")
            run.success = bool(args.get("success", True))
            step = PhoneStep(name, args, run.summary, (time.perf_counter() - start) * 1000)
            run.steps.append(step)
            if on_step:
                on_step(step)
            return run
        before = phone.route
        result = _act(phone, name, args)
        step = PhoneStep(name, args, result, (time.perf_counter() - start) * 1000)
        state = (before, name, json.dumps(args, sort_keys=True), phone.route)
        if seen.count(state) >= 1:
            # Same action from the same screen to the same screen again: it is
            # going in circles. Stop rather than burn the user's time.
            run.steps.append(step)
            if on_step:
                on_step(step)
            run.summary = f"I could not work out how to do that on the phone: {request}"
            return run
        seen.append(state)
        run.steps.append(step)
        if on_step:
            on_step(step)
        # Keep only the newest screenshot in context: older ones become text.
        for m in messages:
            if m["role"] == "user" and isinstance(m["content"], list):
                m["content"] = m["content"][0]["text"]
        messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
        messages.append({"role": "tool", "tool_call_id": call.get("id", "0"), "content": result})
        messages.append(_screen_message(phone, "Screen now:"))
    run.summary = "I could not finish that on the phone."
    return run


def _act(phone: Phone, name: str, args: dict) -> str:
    try:
        if name == "open_app":
            return phone.open_app(str(args.get("app", ""))).message
        if name == "tap":
            return phone.tap(args.get("mark")).message
        if name == "type_text":
            return phone.type_text(args.get("mark"), str(args.get("text", ""))).message
        if name == "back":
            return phone.back().message
        if name == "home":
            return phone.home().message
    except Exception as exc:  # a bad argument should not end the run
        return f"Error: {exc}"
    return f"Unknown tool {name}."
