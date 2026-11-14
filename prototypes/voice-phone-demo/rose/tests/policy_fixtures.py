"""Test-only gates for the screening policy (docs/APP-BUILDER.md §3.7). Nothing here is importable by a host: the
approving gate exists so tests can reach the build path, and verdicts come from recorded fixtures, never a model."""
from __future__ import annotations

import json
from pathlib import Path

from rose.app_builder import policy

FIXTURES = Path(__file__).parent / 'fixtures' / 'screening'


def load(name: str) -> list[dict]:
    """One fixture set: JSON lines of {id, kind, text, priorText, expected, category, source}."""
    rows = []
    for line in (FIXTURES / name).read_text(encoding='utf-8').splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


class RecordedGate:
    """An allow-authoritative stand-in that answers from recorded verdicts. ``default`` answers anything not
    recorded; None makes an unrecorded request raise (so the chain treats it as unavailable)."""

    def __init__(self, recorded=None, default=policy.ALLOW, name='recorded', version='test-1', can_allow=True):
        self.recorded = dict(recorded or {})
        self.default, self.name, self.version, self.can_allow = default, name, version, can_allow
        self.seen = []

    def assess(self, request):
        self.seen.append(request)
        verdict = self.recorded.get(request.text, self.default)
        if verdict is None:
            raise LookupError('no recorded verdict')
        category = 'none' if verdict == policy.ALLOW else 'recorded'
        reason = '' if verdict == policy.ALLOW else 'Recorded verdict. Nothing was started.'
        return policy.Decision(verdict, category, reason, self.name, self.version)


def approving_policy(**kwargs) -> policy.Chain:
    """The phrase gate, then a recorded gate that allows by default: for tests that exercise the build path."""
    return policy.Chain([policy.PhraseGate(), RecordedGate(**kwargs)])
