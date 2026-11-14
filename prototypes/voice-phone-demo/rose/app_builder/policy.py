"""Fail-closed screening policy for build requests (docs/APP-BUILDER.md §3.7).

A policy is an ordered chain of gates. Each gate judges one request and returns a ``Decision``: ``allow``,
``review_required`` or ``deny``. The chain combines them so that nothing is accepted unless it is affirmatively
allowed:

1. ``allow`` only when every gate allows and at least one of them is allow-authoritative (``can_allow``). An empty
   chain is ``deny``.
2. Any ``deny`` gives ``deny``; otherwise any ``review_required`` gives ``review_required``.
3. A gate that raises, times out, or returns anything but a well-formed ``Decision`` from itself counts as
   ``review_required`` with category ``unavailable``: never ``allow``.
4. A gate that is not allow-authoritative (the phrase screen) can only object. Its ``allow`` means "no objection",
   so a chain without an allow-authoritative gate never allows: it answers ``review_required``/``unavailable``.

There is no override: nothing here turns ``deny`` or ``review_required`` into ``allow``. ``default_policy()`` has
no allow-authoritative gate, so a Builder built with it accepts no request. No gate here calls a model or a
network service; choosing an allow-authoritative gate is a separate decision (§3.7, decisions for Nathan).
"""
from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field

from . import screening

ALLOW, REVIEW, DENY = 'allow', 'review_required', 'deny'
VERDICTS = (ALLOW, REVIEW, DENY)
UNAVAILABLE = 'unavailable'
UNAVAILABLE_REASON = ('This builder can\'t check requests right now, so it isn\'t accepting them. '
                      'Nothing was started.')
NO_POLICY_REASON = 'This builder has no screening policy, so it isn\'t accepting requests. Nothing was started.'
# One shared pool for every chain. A gate that hangs keeps its worker, and once every worker is held, later gates
# time out as unavailable: the builder then stops accepting requests, never accepts one unchecked.
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix='screen')


@dataclass(frozen=True)
class ScreenRequest:
    kind: str  # create | revise
    text: str
    prior_text: tuple = ()  # the project's accepted description and earlier requests, oldest first
    locale: str = 'en'


@dataclass(frozen=True)
class Decision:
    verdict: str
    category: str
    reason: str  # owner-facing; never quotes the request
    gate: str
    gate_version: str
    notes: tuple = field(default=())

    def record(self) -> dict:
        return {'verdict': self.verdict, 'category': self.category, 'gate': self.gate,
                'gateVersion': self.gate_version}


class PhraseGate:
    """The §3.6 phrase screen as a deny-only gate: ``deny``, or ``allow`` meaning no objection, with its notes."""
    name, version, can_allow = 'phrase', '1', False

    def assess(self, request: ScreenRequest) -> Decision:
        result = screening.screen(request.text)
        if not result['ok']:
            return Decision(DENY, result['category'], result['message'], self.name, self.version)
        return Decision(ALLOW, 'none', '', self.name, self.version, tuple(result['notes']))


def default_policy() -> 'Chain':
    """The policy a Builder gets unless a host configures another: no allow-authoritative gate, so nothing is
    accepted. Enabling the builder needs an authorized second gate (§3.7)."""
    return Chain([PhraseGate()])


def _unavailable(gate) -> Decision:
    return Decision(REVIEW, UNAVAILABLE, UNAVAILABLE_REASON, str(getattr(gate, 'name', '?')),
                    str(getattr(gate, 'version', '?')))


class Chain:
    def __init__(self, gates, timeout_s: float = 10.0):
        self.gates = tuple(gates)
        self.timeout_s = timeout_s

    @property
    def can_allow(self) -> bool:
        return any(getattr(g, 'can_allow', False) is True for g in self.gates)

    def _one(self, gate, request: ScreenRequest) -> Decision:
        """One gate's decision, or ``unavailable`` for anything that is not a well-formed answer from that gate."""
        try:
            decision = _POOL.submit(gate.assess, request).result(timeout=self.timeout_s)
        except Exception:  # raised, timed out (the thread is left to finish on its own), or could not start
            return _unavailable(gate)
        if (not isinstance(decision, Decision) or decision.verdict not in VERDICTS
                or decision.gate != getattr(gate, 'name', None) or decision.gate_version != getattr(gate, 'version', None)
                or not isinstance(decision.category, str) or not isinstance(decision.reason, str)
                or not isinstance(decision.notes, tuple) or not all(isinstance(n, str) for n in decision.notes)
                or decision.verdict != ALLOW and not decision.reason.strip()):
            return _unavailable(gate)
        return decision

    def assess(self, request: ScreenRequest) -> tuple:
        """(the chain's Decision, every gate's Decision in order). Stops at the first ``deny``."""
        if not self.gates:
            return Decision(DENY, 'no_policy', NO_POLICY_REASON, 'chain', '1'), ()
        decisions = []
        for gate in self.gates:
            decision = self._one(gate, request)
            decisions.append(decision)
            if decision.verdict == DENY:
                return decision, tuple(decisions)
        review = next((d for d in decisions if d.verdict == REVIEW), None)
        if review is not None:
            return review, tuple(decisions)
        authoritative = [d for g, d in zip(self.gates, decisions) if getattr(g, 'can_allow', False) is True]
        if not authoritative:  # only "no objection" from deny-only gates
            return Decision(REVIEW, UNAVAILABLE, UNAVAILABLE_REASON, 'chain', '1'), tuple(decisions)
        notes = tuple(n for d in decisions for n in d.notes)
        last = authoritative[-1]
        return Decision(ALLOW, last.category, '', last.gate, last.gate_version, notes), tuple(decisions)
