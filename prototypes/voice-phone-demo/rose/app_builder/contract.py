"""The API a client-server app's two halves share: contract/api.json (docs/APP-BUILDER.md §7.3). Source only and
called nowhere yet.

The writer describes the app's own types and routes once; the builder generates the server's Rust trait and the
client's Java API from it (a later slice) and checks every revision against the delivered one, so an update can
never silently break the copies of the app already installed on phones. A change that would break them is still
allowed: the writer raises `api`, and the library then tells older clients to update (minClientVersionCode).

Nothing here is a rule on what an app may do. The refusals are a malformed contract, a type that could only be
infinite, and a breaking change that does not say it breaks.

    {"schemaVersion": 1, "api": 1,
     "types": {"Note": {"fields": {"id": "int", "text": {"type": "string", "max": 10000},
                                   "tags": {"type": "list", "of": "string"},
                                   "parent": {"type": "Note", "optional": true}}},
               "NoteList": {"fields": {"notes": {"type": "list", "of": "Note"}}}},
     "routes": {"listNotes": {"method": "POST", "path": "/app/notes/list", "response": "NoteList"}}}

- A field is a primitive (bool, int, float, string, bytes), a list or map (string keys) `of` a field type, or one
  of the contract's types. `optional` fields may be absent or null. `max` bounds a string's characters, bytes'
  length or a list's or map's entries in requests (the server refuses more); it is not a promise about responses.
- A route has a method (GET and DELETE carry no request body), a path under /app/, `auth` "account" (the default:
  a signed-in user) or "public", an optional `request` type and a `response` type.
- Generated code ignores fields it does not know, in both directions, so adding fields never breaks anyone.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .appprofile import ProfileRefused

FILE = Path('contract/api.json')
MAX_FILE = 256 * 1024
MAX_TYPES, MAX_FIELDS, MAX_ROUTES, MAX_DEPTH = 128, 64, 128, 4
MAX_LIMIT = 2 ** 31 - 1
PRIMITIVES = ('bool', 'int', 'float', 'string', 'bytes')
CONTAINERS = ('list', 'map')
BOUNDED = ('string', 'bytes', 'list', 'map')
METHODS = ('GET', 'POST', 'PUT', 'DELETE')
BODYLESS = ('GET', 'DELETE')
AUTH = ('account', 'public')
TYPE_NAME = re.compile(r'[A-Z][A-Za-z0-9]{0,63}')
MEMBER_NAME = re.compile(r'[a-z][A-Za-z0-9]{0,63}')
PATH = re.compile(r'/app(?:/[a-z0-9][a-z0-9-]{0,63}){1,8}')


class ContractRefused(ProfileRefused):
    pass


@dataclass(frozen=True)
class Contract:
    data: dict  # canonical: every field spelled out, defaults filled in

    @property
    def api(self) -> int:
        return self.data['api']

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical_bytes(self.data)).hexdigest()


@dataclass(frozen=True)
class Update:
    breaking: tuple[str, ...]  # what an installed older client would trip on, for the owner to read
    client_update_required: bool  # api was raised: older clients are told to update


def canonical_bytes(data) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def _no_duplicates(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate key')
    return dict(pairs)


def _invalid(why: str) -> ContractRefused:
    return ContractRefused(f'{FILE} is not valid: {why}.', 'contract_invalid')


def _count(value) -> bool:
    return type(value) is int and 1 <= value <= MAX_LIMIT


def _field(spec, where: str, types: dict, depth: int = 0) -> dict:
    """One field type, canonical: {"type": .., "optional": bool} plus "of" for containers and "max" if bounded."""
    if depth > MAX_DEPTH:
        raise _invalid(f'{where} nests lists and maps more than {MAX_DEPTH} deep')
    if isinstance(spec, str):
        spec = {'type': spec}
    if not isinstance(spec, dict) or set(spec) - {'type', 'optional', 'of', 'max'}:
        raise _invalid(f'{where} must be a type name or an object with type, optional, of and max')
    kind = spec.get('type')
    if not isinstance(kind, str) or not (kind in PRIMITIVES or kind in CONTAINERS or kind in types):
        raise _invalid(f'{where} has an unknown type {kind!r}')
    optional = spec.get('optional', False)
    if type(optional) is not bool or depth and optional:  # an entry is never optional; saying false is fine
        raise _invalid(f'{where}: optional is true or false, and only a field itself can be optional')
    out = {'type': kind, 'optional': optional}
    if kind in CONTAINERS:
        if 'of' not in spec:
            raise _invalid(f'{where} is a {kind} and needs "of"')
        out['of'] = _field(spec['of'], f'{where} (of)', types, depth + 1)
    elif 'of' in spec:
        raise _invalid(f'{where}: "of" is only for list and map')
    if 'max' in spec:
        if kind not in BOUNDED or not _count(spec['max']):
            raise _invalid(f'{where}: max is a positive whole number, for string, bytes, list and map')
        out['max'] = spec['max']
    return out


def parse(data) -> Contract:
    """A contract from its parsed JSON, refused unless it is complete and consistent."""
    if not isinstance(data, dict) or set(data) - {'schemaVersion', 'api', 'types', 'routes'}:
        raise _invalid('only schemaVersion, api, types and routes are allowed')
    if type(data.get('schemaVersion')) is not int or data['schemaVersion'] != 1:
        raise _invalid('schemaVersion must be 1')
    if not _count(data.get('api')):
        raise _invalid('api must be a positive whole number')
    types, routes = data.get('types', {}), data.get('routes', {})
    if not isinstance(types, dict) or len(types) > MAX_TYPES:
        raise _invalid(f'types must be an object of at most {MAX_TYPES} types')
    if not isinstance(routes, dict) or not 1 <= len(routes) <= MAX_ROUTES:
        raise _invalid(f'routes must be an object of 1 to {MAX_ROUTES} routes')
    for name in types:
        if not TYPE_NAME.fullmatch(name) or name.lower() in PRIMITIVES + CONTAINERS:
            raise _invalid(f'{name!r} is not a type name (UpperCamelCase, not a built-in name)')
    out_types = {}
    for name, body in types.items():
        if not isinstance(body, dict) or set(body) != {'fields'} or not isinstance(body['fields'], dict):
            raise _invalid(f'type {name} must be {{"fields": {{...}}}}')
        if len(body['fields']) > MAX_FIELDS:
            raise _invalid(f'type {name} has more than {MAX_FIELDS} fields')
        fields = {}
        for field, spec in body['fields'].items():
            if not MEMBER_NAME.fullmatch(field):
                raise _invalid(f'{name}.{field} is not a field name (lowerCamelCase)')
            fields[field] = _field(spec, f'{name}.{field}', types)
        out_types[name] = {'fields': fields}
    out_routes, seen = {}, set()
    for name, route in routes.items():
        if not MEMBER_NAME.fullmatch(name):
            raise _invalid(f'{name!r} is not a route name (lowerCamelCase)')
        if not isinstance(route, dict) or set(route) - {'method', 'path', 'auth', 'request', 'response'}:
            raise _invalid(f'route {name} allows only method, path, auth, request and response')
        method, path, auth = route.get('method'), route.get('path'), route.get('auth', 'account')
        request, response = route.get('request'), route.get('response')
        if method not in METHODS:
            raise _invalid(f'route {name}: method is one of {", ".join(METHODS)}')
        if not isinstance(path, str) or not PATH.fullmatch(path):
            raise _invalid(f'route {name}: path is /app/ then 1 to 8 lower-case segments')
        if (method, path) in seen:
            raise _invalid(f'route {name}: another route already has {method} {path}')
        seen.add((method, path))
        if auth not in AUTH:
            raise _invalid(f'route {name}: auth is "account" or "public"')
        if request is not None and not (isinstance(request, str) and request in out_types) \
                or not (isinstance(response, str) and response in out_types):
            raise _invalid(f'route {name}: request (optional) and response must name types')
        if method in BODYLESS and request is not None:
            raise _invalid(f'route {name}: {method} carries no request body')
        out_routes[name] = {'method': method, 'path': path, 'auth': auth, 'request': request,
                            'response': response}
    _cycles(out_types)
    return Contract({'schemaVersion': 1, 'api': data['api'], 'types': out_types, 'routes': out_routes})


def _cycles(types: dict):
    """A type that contains itself through required fields only could never be written down: refused."""
    def required_refs(name):
        return [f['type'] for f in types[name]['fields'].values() if not f['optional'] and f['type'] in types]

    state = {}
    for root in types:
        if root in state:
            continue
        stack = [(root, iter(required_refs(root)))]
        state[root] = 'open'
        while stack:
            name, refs = stack[-1]
            ref = next(refs, None)
            if ref is None:
                state[name] = 'done'
                stack.pop()
            elif state.get(ref) == 'open':
                raise ContractRefused(f'{FILE}: type {ref} contains itself through required fields, so no value '
                                      'of it can exist; make one of them optional or a list.', 'type_cycle')
            elif ref not in state:
                state[ref] = 'open'
                stack.append((ref, iter(required_refs(ref))))


def load(snapshot: Path) -> Contract:
    path = Path(snapshot) / FILE
    try:
        if not path.is_file() or path.is_symlink():
            raise _invalid('it must be a plain file')
        if path.stat().st_size > MAX_FILE:
            raise _invalid(f'it is larger than {MAX_FILE // 1024} KB')
        data = json.loads(path.read_bytes().decode('utf-8'), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise _invalid('it must be UTF-8 JSON with no repeated keys') from None
    except OSError:
        raise _invalid('it could not be read') from None
    return parse(data)


# Compatibility: what a phone still running the delivered version would see.

def _compare_shapes(old_types, new_types, items, out: list):
    """Walk (old spec, new spec, direction, where) items with a queue, so a recursive type never recurses here.
    direction is 'response' (the server sends the new shape to an old client) or 'request' (an old client sends
    the old shape to the new server). Each (old type, new type, direction) pair is walked once per compare()."""
    seen = set()
    queue = deque(items)
    while queue:
        old, new, direction, where = queue.popleft()
        if old['type'] in PRIMITIVES or new['type'] in PRIMITIVES or old['type'] in CONTAINERS \
                or new['type'] in CONTAINERS:
            if old['type'] != new['type']:
                out.append(f'{where} changes type from {old["type"]} to {new["type"]}')
                continue
            if direction == 'request' and 'max' in new and new['max'] < old.get('max', MAX_LIMIT + 1):
                out.append(f'{where} now allows at most {new["max"]}, fewer than older clients may send')
            if old['type'] in CONTAINERS:
                queue.append((old['of'], new['of'], direction, f'{where} (entries)'))
            continue
        key = (old['type'], new['type'], direction)
        if key in seen:
            continue
        seen.add(key)
        old_fields, new_fields = old_types[old['type']]['fields'], new_types[new['type']]['fields']
        if direction == 'response':  # every field an old client expects is still sent, in a shape it reads
            for name, field in old_fields.items():
                if name not in new_fields:
                    if not field['optional']:
                        out.append(f'{where}.{name} is no longer sent')
                    continue
                if not field['optional'] and new_fields[name]['optional']:
                    out.append(f'{where}.{name} may now be missing')
                queue.append((field, new_fields[name], direction, f'{where}.{name}'))
        else:  # every field the new server requires, an old client still sends, in a shape the server reads
            for name, field in new_fields.items():
                if name not in old_fields:
                    if not field['optional']:
                        out.append(f'{where}.{name} is now required, and older clients do not send it')
                    continue
                if not field['optional'] and old_fields[name]['optional']:
                    out.append(f'{where}.{name} is now required, and older clients may leave it out')
                queue.append((old_fields[name], field, direction, f'{where}.{name}'))


def compare(old: Contract, new: Contract) -> list[str]:
    """What in `new` would break a client built against `old` (empty when nothing would). An installed client calls
    a method and a path, so routes are matched by those; renaming a route changes nothing."""
    out, items = [], []
    ot, nt = old.data['types'], new.data['types']
    by_call = {(r['method'], r['path']): r for r in new.data['routes'].values()}
    for name, route in old.data['routes'].items():
        moved = by_call.get((route['method'], route['path']))
        if moved is None:
            out.append(f'route {name} ({route["method"]} {route["path"]}) is removed or moved')
            continue
        if route['auth'] == 'public' and moved['auth'] == 'account':
            out.append(f'route {name} now needs a signed-in user')
        items.append(({'type': route['response']}, {'type': moved['response']}, 'response', f'{name} response'))
        if route['request'] is not None and moved['request'] is not None:
            items.append(({'type': route['request']}, {'type': moved['request']}, 'request', f'{name} request'))
        elif moved['request'] is not None:  # older clients send no body: only required fields matter
            required = [f for f, spec in nt[moved['request']]['fields'].items() if not spec['optional']]
            if required:
                out.append(f'{name} request now needs a body ({", ".join(required)}), which older clients do '
                           'not send')
    _compare_shapes(ot, nt, items, out)
    return out


def check_update(delivered, new: Contract) -> Update:
    """A revision's contract against what was delivered: one contract, or every delivered contract in order. It is
    refused if it lowers api, or if it would break a client of any version delivered since api last rose (a field
    dropped in one revision and brought back with another shape in the next still meets the phones that kept the
    first). Clients of older api levels have already been asked to update."""
    history = [delivered] if isinstance(delivered, Contract) else list(delivered)
    if not history:
        return Update((), False)
    old = history[-1]
    if new.api < old.api:
        raise ContractRefused(f'{FILE}: api cannot go down (from {old.api} to {new.api}).', 'api_lowered')
    current = [c for c in history if c.api == old.api]
    breaking = []
    for contract in (current if new.api == old.api else [old]):
        breaking.extend(b for b in compare(contract, new) if b not in breaking)
    if breaking and new.api == old.api:
        listed = '; '.join(breaking[:3]) + ('; …' if len(breaking) > 3 else '')
        raise ContractRefused(f'{FILE}: this change would break the app on phones that have not updated '
                              f'({listed}). Raise api to {old.api + 1} to make it, and older versions will be '
                              'asked to update.', 'breaking_without_major')
    return Update(tuple(breaking), new.api > old.api)
