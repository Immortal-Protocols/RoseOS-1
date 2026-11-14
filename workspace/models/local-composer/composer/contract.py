"""rose-layout-v1 (PROPOSAL, contract/rose-layout-v1.md): request, snapshot and catalog checks.

Standard library only. OS Product owns the real catalog and snapshot; DEFAULT_CATALOG is the proposed starting set
so the composer and its tests have something concrete to bind to.
"""

from __future__ import annotations

CONTRACT = 'rose-layout-v1'
TRIGGERS = ('button', 'direct_text', 'conversational')
TIMES_OF_DAY = ('morning', 'afternoon', 'evening', 'night')
KINDS = {'apps': 'a', 'notifications': 'n', 'content': 'c', 'actions': 'x'}  # snapshot list -> alias prefix
ACTION_KINDS = ('launch', 'shortcut', 'deeplink', 'notification_action', 'app_function', 'agent_task')
MAX_PROMPT_CHARS = 500
MAX_LABEL_CHARS = 60
MAX_RECENT_RANK = 1_000_000
LAYOUT_TITLE_CHARS, SECTION_TITLE_CHARS = 40, 30

FAILURES = ('bad_request', 'catalog_mismatch', 'stale_snapshot', 'empty_context', 'model_unavailable', 'timeout',
            'cancelled', 'invalid_output', 'unknown_ref', 'disallowed_ref', 'over_limit', 'duplicate_ref')

# component -> item kinds it may reference, min/max items, max card actions, and action kinds it may not hold as
# items. `agent_task` (a Rose task that always asks first; App Core PR782 §4.3) may be a `button` item or a `card`
# action, never a grid item. PROPOSAL: OS Product owns the real catalog (the HiFi r2 card set is apps, actions,
# carousel, list/checklist and notification); composer code reads every rule from the catalog data.
DEFAULT_CATALOG = {
    'version': 'cat-1',
    'components': {
        'grid': {'items': ('apps', 'actions'), 'min': 2, 'max': 8, 'actions': 0, 'excludeActionKinds': ('agent_task',)},
        'carousel': {'items': ('content', 'notifications', 'apps'), 'min': 2, 'max': 8, 'actions': 0},
        'list': {'items': ('notifications', 'content'), 'min': 1, 'max': 8, 'actions': 0},
        'card': {'items': ('notifications', 'content'), 'min': 1, 'max': 1, 'actions': 2},
        'button': {'items': ('actions',), 'min': 1, 'max': 1, 'actions': 0},
    },
    'limits': {'maxSections': 4, 'maxItemsPerSection': 8, 'maxTotalItems': 20, 'deadlineMs': 4000},
}


class Refused(Exception):
    """A typed failure: `category` is one of FAILURES and is all a caller may show or log."""

    def __init__(self, category: str):
        assert category in FAILURES, category
        super().__init__(category)
        self.category = category


def check_request(req: dict, catalog: dict = DEFAULT_CATALOG) -> dict:
    """The request's effective limits, or Refused('bad_request' | 'catalog_mismatch')."""
    if not isinstance(req, dict) or req.get('contract') != CONTRACT or req.get('trigger') not in TRIGGERS:
        raise Refused('bad_request')
    if not isinstance(req.get('requestId'), str) or not 0 < len(req['requestId']) <= 64:
        raise Refused('bad_request')
    prompt = req.get('prompt')
    if req['trigger'] == 'button':
        if prompt not in (None, ''):
            raise Refused('bad_request')
    elif not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
        raise Refused('bad_request')
    snap = req.get('snapshot')
    if not isinstance(snap, dict) or not isinstance(snap.get('snapshotVersion'), str):
        raise Refused('bad_request')
    if req.get('catalogVersion') != catalog['version'] or snap.get('catalogVersion') != catalog['version']:
        raise Refused('catalog_mismatch')
    limits = dict(catalog['limits'])
    asked = req.get('limits')
    if asked is not None and not isinstance(asked, dict):
        raise Refused('bad_request')
    for k, v in (asked or {}).items():
        if k not in limits or not isinstance(v, int) or isinstance(v, bool) or not 0 < v <= limits[k]:
            raise Refused('bad_request')  # a request may only tighten the catalog's bounds
        limits[k] = v
    check_snapshot(snap)
    return limits


def check_snapshot(snap: dict) -> None:
    """Every entry has a unique string id and a bounded label; actions target an id in the snapshot."""
    seen = set()
    if snap.get('timeOfDay') is not None and snap['timeOfDay'] not in TIMES_OF_DAY:
        raise Refused('bad_request')
    if not isinstance(snap.get('pinned', []), list):
        raise Refused('bad_request')
    for kind in KINDS:
        entries = snap.get(kind, [])
        if not isinstance(entries, list):
            raise Refused('bad_request')
        for e in entries:
            if not isinstance(e, dict) or not isinstance(e.get('id'), str) or not e['id'] or e['id'] in seen:
                raise Refused('bad_request')
            label = e.get('label', e.get('title'))
            if not isinstance(label, str) or not label or len(label) > MAX_LABEL_CHARS:
                raise Refused('bad_request')
            if any(e.get(f) is not None and not isinstance(e[f], str) for f in ('category', 'kind', 'appId')):
                raise Refused('bad_request')
            rank = e.get('recentRank')
            if rank is not None and (not isinstance(rank, int) or isinstance(rank, bool)
                                     or not 0 <= rank <= MAX_RECENT_RANK):
                raise Refused('bad_request')
            seen.add(e['id'])
    for a in snap.get('actions', []):
        if not isinstance(a.get('targetId'), str) or a['targetId'] not in seen or a.get('kind') not in ACTION_KINDS:
            raise Refused('bad_request')
        if a['kind'] == 'agent_task' and a.get('requiresConfirmation') is not True:
            raise Refused('bad_request')  # a Rose task always asks first
    if not all(isinstance(p, str) and p in seen for p in snap.get('pinned', [])):
        raise Refused('bad_request')
