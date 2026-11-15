"""Strict validation of one model output against one request. Everything the grammar enforces is checked again here:
a runtime without grammar support, a grammar bug or a hand-written layout must never reach the renderer unchecked.

Returns the layout with real IDs, or raises Refused with a fixed category. Model text never appears in a failure.
"""

from __future__ import annotations

import json

from .contract import CONTRACT, LAYOUT_TITLE_CHARS, SECTION_TITLE_CHARS, Refused
from .context import Context

SECTION_KEYS = {'component', 'title', 'items', 'actions'}
MAX_OUTPUT_CHARS = 8192


def _no_dupes(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise Refused('invalid_output')
    return dict(pairs)


def _no_constants(_):
    raise Refused('invalid_output')  # NaN / Infinity


def parse(text) -> dict:
    if not isinstance(text, str) or len(text) > MAX_OUTPUT_CHARS:
        raise Refused('invalid_output')
    try:
        value = json.loads(text, object_pairs_hook=_no_dupes, parse_constant=_no_constants)
    except (ValueError, RecursionError):
        raise Refused('invalid_output') from None
    if not isinstance(value, dict):
        raise Refused('invalid_output')
    return value


def _title(value, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(ch) < 0x20 for ch in value):
        raise Refused('invalid_output')
    if len(value) > limit:
        raise Refused('over_limit')
    return value


def _alias_list(value) -> list:
    if not isinstance(value, list) or not all(isinstance(a, str) for a in value):
        raise Refused('invalid_output')
    return value


def validate(text, ctx: Context, catalog: dict, limits: dict, snapshot_version: str) -> dict:
    """The layout with real IDs for `text`, or Refused."""
    raw = parse(text)
    if set(raw) != {'title', 'sections'}:
        raise Refused('invalid_output')
    title = _title(raw['title'], LAYOUT_TITLE_CHARS)
    sections = raw['sections']
    if not isinstance(sections, list) or not sections:
        raise Refused('invalid_output')
    if len(sections) > limits['maxSections']:
        raise Refused('over_limit')
    seen, total, out = set(), 0, []
    for s in sections:
        if not isinstance(s, dict) or not {'component', 'title', 'items'} <= set(s) <= SECTION_KEYS:
            raise Refused('invalid_output')
        rule = catalog['components'].get(s['component']) if isinstance(s['component'], str) else None
        if rule is None:
            raise Refused('invalid_output')
        section_title = _title(s['title'], SECTION_TITLE_CHARS)
        items, actions = _alias_list(s['items']), _alias_list(s.get('actions', []))
        if len(items) < rule['min']:
            raise Refused('invalid_output')
        if actions and not rule['actions']:
            raise Refused('disallowed_ref')
        if len(items) > min(rule['max'], limits['maxItemsPerSection']) or len(actions) > rule['actions']:
            raise Refused('over_limit')
        for a in items + actions:
            if a not in ctx.aliases:
                raise Refused('unknown_ref')
            if a in seen:
                raise Refused('duplicate_ref')
            seen.add(a)
        excluded = rule.get('excludeActionKinds', ())
        if any(ctx.kinds[a] not in rule['items'] or ctx.action_kinds.get(a) in excluded for a in items):
            raise Refused('disallowed_ref')
        for a in actions:  # a card's actions must be actions that target one of the section's own items
            if ctx.kinds[a] != 'actions' or ctx.targets.get(a) not in items:
                raise Refused('disallowed_ref')
        total += len(items)
        if total > limits['maxTotalItems']:
            raise Refused('over_limit')
        out.append({'component': s['component'], 'title': section_title,
                    'items': [ctx.aliases[a] for a in items], 'actions': [ctx.aliases[a] for a in actions]})
    return {'snapshotVersion': snapshot_version, 'catalogVersion': catalog['version'], 'title': title,
            'sections': out}


def check_fresh(result: dict, current_snapshot_version: str) -> dict:
    """For the caller just before rendering: a result composed against an older snapshot becomes stale_snapshot."""
    if result.get('status') == 'ok' and result['layout']['snapshotVersion'] != current_snapshot_version:
        return {'contract': CONTRACT, 'requestId': result.get('requestId'), 'status': 'failed',
                'failure': 'stale_snapshot'}
    return result
