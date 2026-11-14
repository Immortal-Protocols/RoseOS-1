"""Context selection: a bounded, compact view of the snapshot for one request.

The model never sees raw IDs. Each selected entry gets a short alias (a1, n2, c3, x4); the composer maps aliases
back to real IDs after validation. Selection is deterministic: pinned entries first, then entries ranked by overlap
with the prompt (or, for a button press, by recency and time of day), with a per-app cap for variety, until the
character budget is spent. Characters stand in for tokens here; a model's own tokenizer replaces the estimate when
a runtime is bound. Only `label`/`title`, `category` and `kind` reach the model: an action's `ask` or `goal`
text, and any other field, never does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .contract import KINDS, Refused

WORD = re.compile(r'[a-z0-9]+')
TIME_HINTS = {  # weak defaults for a best-guess button press; only categories, never app names
    'morning': {'news', 'weather', 'calendar', 'commute', 'music'},
    'afternoon': {'work', 'tools', 'calendar', 'messages'},
    'evening': {'cooking', 'food', 'media', 'music', 'messages'},
    'night': {'media', 'music', 'reading', 'alarm'},
}


@dataclass
class Context:
    aliases: dict = field(default_factory=dict)   # alias -> real id
    kinds: dict = field(default_factory=dict)     # alias -> snapshot kind ('apps', ...)
    targets: dict = field(default_factory=dict)   # action alias -> alias of its target (if selected)
    action_kinds: dict = field(default_factory=dict)  # action alias -> action kind ('agent_task', ...)
    lines: list = field(default_factory=list)     # the compact text the model sees
    chars: int = 0


def words(text) -> set:
    return set(WORD.findall((text or '').lower()))


def _label(e) -> str:
    return e.get('label') or e.get('title') or ''


def _score(kind: str, e: dict, prompt_words: set, hints: set, pinned: set) -> tuple:
    text = words(_label(e)) | words(e.get('category')) | words(e.get('kind'))
    overlap = len(text & prompt_words)
    hinted = 1 if (words(e.get('category')) & hints) else 0
    rank = e.get('recentRank')
    recency = -rank if isinstance(rank, int) and not isinstance(rank, bool) else -10 ** 6
    return (e['id'] in pinned, overlap, hinted, recency, kind == 'actions')


def select(snapshot: dict, prompt: str | None, budget_chars: int = 1600, per_app: int = 3) -> Context:
    """The selected entries, within `budget_chars` of compact lines; Refused('empty_context') if none fit."""
    pinned = set(snapshot.get('pinned', []))
    prompt_words = words(prompt)
    hints = set() if prompt_words else TIME_HINTS.get(snapshot.get('timeOfDay'), set())
    candidates = []
    for kind in KINDS:
        for e in snapshot.get(kind, []):
            candidates.append((_score(kind, e, prompt_words, hints, pinned), kind, e))
    candidates.sort(key=lambda c: c[0], reverse=True)
    ctx, counts, per_app_count = Context(), {k: 0 for k in KINDS}, {}
    chosen_ids, raw_targets = {}, []
    for _, kind, e in candidates:
        app = e.get('appId') or (e['id'] if kind == 'apps' else None)
        if app is not None and e['id'] not in pinned and per_app_count.get(app, 0) >= per_app:
            continue
        counts[kind] += 1
        alias = f'{KINDS[kind]}{counts[kind]}'
        extra = e.get('category') or e.get('kind') or ''
        line = f'{alias} {_label(e)}' + (f' [{extra}]' if extra else '')
        if kind == 'actions':
            line += f' -> {e["targetId"]}'  # rewritten to the target's alias below, or dropped
        if ctx.chars + len(line) + 1 > budget_chars:
            counts[kind] -= 1
            continue
        ctx.aliases[alias], ctx.kinds[alias] = e['id'], kind
        if kind == 'actions':
            ctx.action_kinds[alias] = e.get('kind')
        chosen_ids[e['id']] = alias
        ctx.lines.append(line)
        raw_targets.append(e['targetId'] if kind == 'actions' else None)
        ctx.chars += len(line) + 1
        if app is not None:
            per_app_count[app] = per_app_count.get(app, 0) + 1
    # actions point at the alias of their target; an action whose target was not selected still stands alone
    final = []
    for line, target in zip(ctx.lines, raw_targets):
        alias = line.split(' ', 1)[0]
        if ctx.kinds[alias] == 'actions':
            head = line[:len(line) - len(f' -> {target}')]
            if target in chosen_ids:
                ctx.targets[alias] = chosen_ids[target]
                line = f'{head} -> {chosen_ids[target]}'
            else:
                line = head
        final.append(line)
    ctx.lines = final
    ctx.chars = sum(len(line) + 1 for line in final)
    if not ctx.aliases:
        raise Refused('empty_context')
    return ctx
