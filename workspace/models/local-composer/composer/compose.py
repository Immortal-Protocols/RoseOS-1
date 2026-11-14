"""One request in, one validated layout or typed failure out. No network, no files, no model text in failures."""

from __future__ import annotations

import time

from . import context as context_mod
from . import grammar as grammar_mod
from . import prompt as prompt_mod
from .contract import CONTRACT, DEFAULT_CATALOG, Refused, check_request
from .validate import validate

TOKEN_CHARS = 2      # conservative chars-per-token for alias JSON; bounds max_tokens from the grammar's max length
MAX_TOKENS_CAP = 512


def _failed(req, category: str, timing: dict | None = None) -> dict:
    rid = req.get('requestId') if isinstance(req, dict) else None
    out = {'contract': CONTRACT, 'requestId': rid if isinstance(rid, str) else None, 'status': 'failed',
           'failure': category}
    if timing:
        out['timing'] = timing
    return out


def compose(req: dict, backend, catalog: dict = DEFAULT_CATALOG, budget_chars: int = 1600,
            clock=time.perf_counter, cancelled=lambda: False) -> dict:
    """`cancelled()` is checked before and after generation; a backend may also raise Refused('cancelled')."""
    t0 = clock()
    timing = {}
    try:
        limits = check_request(req, catalog)
        snap = req['snapshot']
        ctx = context_mod.select(snap, req.get('prompt'), budget_chars=budget_chars, avoid=req.get('avoid'))
        tree = grammar_mod.build(ctx, catalog, limits)
        if tree is None:
            raise Refused('empty_context')
        gbnf = grammar_mod.to_gbnf(tree)
        system, user = prompt_mod.render(ctx, catalog, req.get('prompt'), snap.get('timeOfDay'))
        max_tokens = min(MAX_TOKENS_CAP, grammar_mod.max_chars(tree) // TOKEN_CHARS + 8)
        t1 = clock()
        timing['contextMs'] = round((t1 - t0) * 1000, 3)
        deadline_s = limits['deadlineMs'] / 1000 - (t1 - t0)
        if cancelled():
            raise Refused('cancelled')
        try:
            gen = backend.generate(system, user, gbnf, max_tokens, deadline_s)
        except Refused:
            raise
        except Exception:  # a runtime failure is a typed failure, never a crash of the page
            raise Refused('model_unavailable') from None
        t2 = clock()
        timing['generationMs'] = round((t2 - t1) * 1000, 3)
        if cancelled():
            raise Refused('cancelled')
        if (t2 - t0) * 1000 > limits['deadlineMs']:
            raise Refused('timeout')
        layout = validate(getattr(gen, 'text', None), ctx, catalog, limits, snap['snapshotVersion'])
        t3 = clock()
        timing['validationMs'] = round((t3 - t2) * 1000, 3)
    except Refused as r:
        timing['composeMs'] = round((clock() - t0) * 1000, 3)
        return _failed(req, r.category, timing)
    timing['composeMs'] = round((t3 - t0) * 1000, 3)
    return {'contract': CONTRACT, 'requestId': req['requestId'], 'status': 'ok', 'layout': layout,
            'model': dict(backend.info), 'timing': timing,
            'context': {'entries': len(ctx.aliases), 'chars': ctx.chars, 'maxTokens': max_tokens},
            'runtimeStats': gen.stats}
