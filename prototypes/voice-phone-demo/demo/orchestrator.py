"""Local stand-in for the on-phone orchestrator.

Design A: the voice model calls ``ask_brain`` / ``do_on_phone``; the
orchestrator runs them and returns a tool response the voice model speaks.

Design B: after each user turn the orchestrator asks the brain to classify it
(chat / think / act). For think/act it runs the job in the background while
the voice model keeps talking, then hands the result to the voice model.
"""

from __future__ import annotations

import time
from typing import Callable

from . import brain
from .phone import Phone

Emit = Callable[[str, dict], None]


class Orchestrator:
    def __init__(self, phone: Phone, emit: Emit):
        self.phone = phone
        self.emit = emit

    def run_tool(self, name: str, arguments: dict, cancel=None) -> dict:
        """Execute one voice-model tool call; returns the tool response payload."""

        start = time.perf_counter()
        self.emit("tool_start", {"name": name, "arguments": arguments})
        try:
            if name == "ask_brain":
                question = str(arguments.get("question") or arguments.get("text") or "")
                result = {"answer": brain.ask(question, phone_context=self.phone.summary())}
            elif name == "do_on_phone":
                request = str(arguments.get("request") or arguments.get("text") or "")
                run = brain.run_phone(
                    request,
                    self.phone,
                    cancel=cancel,
                    on_step=lambda s: self.emit(
                        "phone_step",
                        {"action": s.action, "args": s.args, "result": s.result, "ms": round(s.ms)},
                    ),
                )
                result = {"status": "done" if run.success else "failed", "summary": run.summary}
            else:
                result = {"error": f"unknown tool {name}"}
        except Exception as exc:  # report, never crash the voice loop
            result = {"error": f"{type(exc).__name__}: {exc}"}
        self.emit(
            "tool_done",
            {"name": name, "result": result, "ms": round((time.perf_counter() - start) * 1000)},
        )
        return result

    def decide(self, user_turn: str, recent: list[str]) -> dict:
        start = time.perf_counter()
        try:
            decision = brain.decide(user_turn, recent)
        except Exception as exc:
            decision = {"mode": "chat", "task": "", "error": str(exc)}
        decision["ms"] = round((time.perf_counter() - start) * 1000)
        self.emit("decision", {"turn": user_turn, **decision})
        return decision

    @staticmethod
    def tool_for(decision: dict) -> tuple[str, dict] | None:
        """Map a think/act decision to the voice tool that does it."""

        task = decision.get("task") or ""
        if decision.get("mode") == "think":
            return "ask_brain", {"question": task}
        if decision.get("mode") == "act":
            return "do_on_phone", {"request": task}
        return None

    def run_decision(self, decision: dict) -> tuple[str, dict, dict] | None:
        """Design B: turn a think/act decision into (tool name, args, response)."""

        outcome = self.tool_for(decision)
        if outcome is None:
            return None
        name, args = outcome
        return name, args, self.run_tool(name, args)
