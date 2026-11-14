"""An advisory report of platform API references above the app's minSdk (docs/APP-BUILDER.md §2.3).

It reads the *final* dex (after d8, so backported methods such as ``List.of`` are no longer platform references)
with the pinned ``dexdump -d``, and looks each framework and ``java.*`` reference up in the pinned platform 35
``data/api-versions.xml``: invokes, field accesses, type uses, catch handler types, and the app's own supertypes and
interfaces (synthetic lambda classes included), resolved through the app's and the platform's inheritance.

Each reference above minSdk gets one status:

- ``guarded``: in its method, a recognised ``Build.VERSION.SDK_INT`` comparison with a constant dominates it on the
  branch that guarantees at least the API's level (branch direction respected);
- ``not_guarded_here``: its method was analysed completely and no such guard protects it there. A caller may still
  guard it (this check does not look across methods);
- ``unverified``: the analysis cannot tell: a helper may guard it (the method calls a boolean method of the app),
  an ``SDK_INT`` use was not recognised, the method has a switch (dexdump does not print switch targets), the code is
  unreachable, the API is on an extension SDK (its availability is not decided by ``SDK_INT``), or the reference is
  a class's own supertype (only never loading the class protects it).

It is advisory only: it never fails or changes a build, and it is not device proof. An empty list does not mean the
app is compatible with every phone it can be installed on. Reflection and any API reached without a dex reference
are not seen. If the database or dexdump is missing, or anything goes wrong, the report says ``unavailable``.
"""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

DATABASE = 'platforms/android-35/data/api-versions.xml'
MAX_FINDINGS = 200
NOTE = ('Advisory: references above minSdk found in the built dex. This is detection, not device compatibility '
        'proof; an empty list does not mean the app works on every supported phone.')
SDK_INT = 'Landroid/os/Build$VERSION;.SDK_INT:I'
INSN = re.compile(r'^[0-9a-f]{6}: [0-9a-f .]+\|([0-9a-f]{4}): (\S+)(?: (.*))?$')
MEMBER = re.compile(r'L([^;\s]+);\.([^:\s]+):(\S+)')
TYPE = re.compile(r'\[*L([^;\s]+);')
TARGET = re.compile(r'(?:^|, )([0-9a-f]{4,8}) // [+-]')
TYPE_OPS = ('new-instance', 'const-class', 'check-cast', 'instance-of', 'new-array', 'filled-new-array')
FIELD_OPS = ('sget', 'sput', 'iget', 'iput')
# (comparison as SDK_INT op constant) -> (minimum guaranteed on the true branch, on the false branch), relative to c
GUARANTEE = {'ge': (0, None), 'gt': (1, None), 'lt': (None, 0), 'le': (None, 1), 'eq': (0, None), 'ne': (None, 0)}
FLIP = {'lt': 'gt', 'gt': 'lt', 'le': 'ge', 'ge': 'le', 'eq': 'eq', 'ne': 'ne'}


class Database:
    """api-versions.xml: per class its level, supertypes (with the level each was added) and member levels."""

    def __init__(self, path: Path):
        self.sha256 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        self.classes = {}
        for c in ET.parse(path).getroot().iter('class'):
            since = int(c.get('since', 1))
            self.classes[c.get('name')] = (
                since, bool(c.get('sdks')),
                [(e.get('name'), int(e.get('since', 1))) for e in c if e.tag in ('extends', 'implements')],
                {m.get('name'): (int(m.get('since', since)), bool(m.get('sdks'))) for m in c
                 if m.tag in ('method', 'field')})

    def level(self, cls: str, member: str | None = None, seen=None):
        """(API level, extension SDK?) of ``cls`` or ``cls.member`` (searching supertypes); None if unknown."""
        seen = seen if seen is not None else set()
        if cls in seen or cls not in self.classes:
            return None
        seen.add(cls)
        since, ext, supers, members = self.classes[cls]
        if member is None:
            return since, ext
        if member in members:
            level, member_ext = members[member]
            return max(level, since), ext or member_ext
        for sup, added in supers:
            found = self.level(sup, member, seen)
            if found is not None:
                return max(found[0], added), found[1]
        return None


_DATABASES: dict = {}


def database(path: Path) -> Database:
    st = Path(path).stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _DATABASES:
        _DATABASES.clear()
        _DATABASES[key] = Database(path)
    return _DATABASES[key]


@dataclass
class Method:
    cls: str
    name: str
    proto: str
    insns: list = field(default_factory=list)  # (address, op, args)
    tries: list = field(default_factory=list)  # (start, end, [(type or None, handler address)])


@dataclass
class Klass:
    name: str
    superclass: str | None = None
    interfaces: list = field(default_factory=list)
    methods: list = field(default_factory=list)


def parse_dexdump(text: str) -> dict:
    """Classes from ``dexdump -d`` output: supertypes, and each method's instructions and try ranges."""
    classes, cls, method, section, tries = {}, None, None, None, None
    for line in text.splitlines():
        if line.startswith('Class #'):
            cls, method, section = None, None, None
            continue
        m = re.match(r"  Class descriptor  : 'L([^;]+);'", line)
        if m:
            cls = classes.setdefault(m.group(1), Klass(m.group(1)))
            continue
        if cls is None:
            continue
        m = re.match(r"  Superclass        : 'L([^;]+);'", line)
        if m:
            cls.superclass = m.group(1)
            continue
        m = re.match(r'  (Interfaces|Static fields|Instance fields|Direct methods|Virtual methods) ', line)
        if m:
            section, method = m.group(1), None
            continue
        if section == 'Interfaces':
            m = re.match(r"    #\d+ +: 'L([^;]+);'", line)
            if m:
                cls.interfaces.append(m.group(1))
            continue
        if section not in ('Direct methods', 'Virtual methods'):
            continue
        if re.match(r'    #\d+ +: \(in L', line):
            method = Method(cls.name, '', '')
            cls.methods.append(method)
            tries = None
            continue
        if method is None:
            continue
        m = re.match(r"      (name|type)          : '(.*)'$", line)
        if m:
            setattr(method, 'name' if m.group(1) == 'name' else 'proto', m.group(2))
            continue
        m = INSN.match(line)
        if m:
            method.insns.append((int(m.group(1), 16), m.group(2), m.group(3) or ''))
            continue
        if line.startswith('      catches       :'):
            tries = []
            continue
        if line.startswith('      ') and not line.startswith('        ') and tries is not None:
            tries = None  # the next method field (positions, locals) ends the catches
            continue
        if tries is not None:
            m = re.match(r'        0x([0-9a-f]+) - 0x([0-9a-f]+)$', line)
            if m:
                method.tries.append((int(m.group(1), 16), int(m.group(2), 16), []))
                continue
            m = re.match(r'          (?:L([^;]+);|<any>) -> 0x([0-9a-f]+)$', line)
            if m and method.tries:
                method.tries[-1][2].append((m.group(1), int(m.group(2), 16)))
    return classes


def _targets(op: str, args: str) -> list:
    if op.startswith(('if-', 'goto')):
        return [int(t, 16) for t in TARGET.findall(args)][-1:]
    return []


class Flow:
    """One method's control flow: basic blocks, edges (exception edges included), the guard each SDK_INT comparison
    gives its branches, and dominators."""

    def __init__(self, method: Method):
        self.method, insns = method, method.insns
        self.supported = not any(op in ('packed-switch', 'sparse-switch') for _, op, _ in insns)
        self.addresses = [a for a, _, _ in insns]
        leaders = {self.addresses[0]} if insns else set()
        for i, (addr, op, args) in enumerate(insns):
            leaders.update(_targets(op, args))
            if (op.startswith(('if-', 'goto', 'return', 'throw')) or op in ('packed-switch', 'sparse-switch')) \
                    and i + 1 < len(insns):
                leaders.add(insns[i + 1][0])
        for start, end, handlers in method.tries:
            leaders.update([start, end] + [h for _, h in handlers])
        self.starts = sorted(a for a in leaders if a in set(self.addresses))
        self.block_of = {}
        block = -1
        for addr in self.addresses:
            if block + 1 < len(self.starts) and addr == self.starts[block + 1]:
                block += 1
            self.block_of[addr] = block
        self.blocks = [[i for i in insns if self.block_of[i[0]] == b] for b in range(len(self.starts))]
        self.edges = {b: [] for b in range(len(self.blocks))}  # successor -> guard level given on that edge
        self.unrecognised = False
        for b, body in enumerate(self.blocks):
            if not body:
                continue
            addr, op, args = body[-1]
            nxt = b + 1 if b + 1 < len(self.blocks) else None
            if op.startswith('if-'):
                true_guard, false_guard = self._guard(body)
                for t in _targets(op, args):
                    if t in self.block_of:
                        self.edges[b].append((self.block_of[t], true_guard))
                if nxt is not None:
                    self.edges[b].append((nxt, false_guard))
            elif op.startswith('goto'):
                for t in _targets(op, args):
                    if t in self.block_of:
                        self.edges[b].append((self.block_of[t], None))
            elif not op.startswith(('return', 'throw')) and op not in ('packed-switch', 'sparse-switch') \
                    and nxt is not None:
                self.edges[b].append((nxt, None))
            for start, end, handlers in method.tries:  # a protected instruction may jump to its handlers
                if any(start <= a < end for a, _, _ in body):
                    for _, h in handlers:
                        if h in self.block_of:
                            self.edges[b].append((self.block_of[h], None))
        reads = sum(1 for _, op, args in insns if op.startswith('sget') and SDK_INT in args)
        if reads > self.recognised:
            self.unrecognised = True
        self._dominators()

    recognised = 0

    def _guard(self, body):
        """(guard on the true branch, on the false branch) of the comparison ending this block, or (None, None)."""
        addr, op, args = body[-1]
        cmp = op[3:].split('/')[0]
        regs = re.findall(r'\bv(\d+)\b', args.split('//')[0])
        defs = {}
        for _, o, a in body[:-1]:
            dest = re.match(r'v(\d+)', a)
            if not dest:
                continue
            if o.startswith('sget') and SDK_INT in a:
                defs[dest.group(1)] = 'sdk'
            elif o.startswith('const') and not o.startswith(('const-string', 'const-class')):
                value = re.search(r'#int (-?\d+)', a)
                defs[dest.group(1)] = int(value.group(1)) if value else None
            else:
                defs.pop(dest.group(1), None)
        if cmp.endswith('z') or len(regs) < 2 or cmp not in GUARANTEE:
            return None, None
        left, right = defs.get(regs[0]), defs.get(regs[1])
        if left == 'sdk' and isinstance(right, int):
            const, cmp_ = right, cmp
        elif right == 'sdk' and isinstance(left, int):
            const, cmp_ = left, FLIP[cmp]
        else:
            return None, None
        self.recognised += 1
        true_off, false_off = GUARANTEE[cmp_]
        return (None if true_off is None else const + true_off), (None if false_off is None else const + false_off)

    def _dominators(self):
        """Dominators over blocks plus one node per guarding edge, so a branch's guard covers what it dominates."""
        nodes, succ = list(range(len(self.blocks))), {b: [] for b in range(len(self.blocks))}
        self.guard_of = {}
        for b, outs in self.edges.items():
            for t, g in outs:
                if g is None:
                    succ[b].append(t)
                else:
                    e = ('edge', b, t, g)
                    nodes.append(e)
                    self.guard_of[e] = g
                    succ[b].append(e)
                    succ[e] = [t]
        preds = {n: [] for n in nodes}
        for n, outs in succ.items():
            for t in outs:
                preds[t].append(n)
        reach, stack = set(), [0] if self.blocks else []
        while stack:
            n = stack.pop()
            if n not in reach:
                reach.add(n)
                stack.extend(succ.get(n, []))
        self.reachable = reach
        dom = {n: set(reach) for n in reach}
        if 0 in dom:
            dom[0] = {0}
        changed = True
        while changed:
            changed = False
            for n in reach:
                if n == 0:
                    continue
                ps = [dom[p] for p in preds[n] if p in reach]
                new = ({n} | set.intersection(*ps)) if ps else {n}
                if new != dom[n]:
                    dom[n], changed = new, True
        self.dom = dom

    def guard_level(self, address: int) -> int | None:
        block = self.block_of.get(address)
        if block not in self.reachable:
            return None
        levels = [self.guard_of[n] for n in self.dom[block] if n in self.guard_of]
        return max(levels) if levels else 0


def _reference(op: str, args: str):
    """(class, member key or None, kind) a single instruction refers to, if any."""
    if op.startswith('invoke'):
        m = MEMBER.search(args)
        return (m.group(1), m.group(2) + m.group(3), 'call') if m else None
    if op.startswith(FIELD_OPS):
        m = MEMBER.search(args)
        return (m.group(1), m.group(2), 'field') if m else None
    if op.startswith(TYPE_OPS):
        m = TYPE.search(args)
        return (m.group(1), None, 'type') if m else None
    return None


def analyse(dumps: list[str], db: Database, package: str, min_sdk: int) -> dict:
    classes = {}
    for text in dumps:
        classes.update(parse_dexdump(text))
    if not classes:
        raise ValueError('no classes in the dex')
    own = package.replace('.', '/') + '/'

    def resolve(cls, member):
        """The platform answer for a reference, walking the app's own (and its libraries') classes up first."""
        seen = set()
        todo = [cls]
        while todo:
            c = todo.pop(0)
            if c in seen:
                continue
            seen.add(c)
            if c in classes:
                if member is None:
                    return None  # a type of the app's own: its supertypes are checked where it is defined
                todo += [x for x in [classes[c].superclass, *classes[c].interfaces] if x]
                continue
            found = db.level(c, member)
            if found is not None:
                return c, found
        return None

    findings, incomplete = [], []
    for k in classes.values():
        origin = 'app' if k.name.startswith(own) else 'library'
        for sup in [k.superclass, *k.interfaces]:
            if sup and sup not in classes:
                found = db.level(sup)
                if found and found[0] > min_sdk:
                    findings.append({'level': found[0], 'api': sup, 'kind': 'supertype', 'where': k.name,
                                     'origin': origin, 'status': 'unverified',
                                     'reason': 'the class itself needs this API: only never loading it below that '
                                               'level protects it'})
        for method in k.methods:
            if not method.insns:
                continue
            flow = Flow(method)
            if not flow.supported:
                incomplete.append(f'{k.name}.{method.name}')
            helper = any(op.startswith('invoke') and (m := MEMBER.search(a)) and m.group(1) in classes
                         and m.group(3).endswith(')Z') for _, op, a in method.insns)
            refs = [(addr, *r) for addr, op, args in method.insns if (r := _reference(op, args))]
            refs += [(start, t, None, 'catch') for start, end, handlers in method.tries for t, _ in handlers if t]
            for addr, cls, member, kind in refs:
                found = resolve(cls, member)
                if found is None:
                    continue
                platform, (level, extension) = found
                if level <= min_sdk:
                    continue
                guard = flow.guard_level(addr) if flow.supported else None
                if extension:
                    status, reason = 'unverified', 'an extension SDK API: SDK_INT alone does not decide it'
                elif guard is not None and guard >= level:
                    status, reason = 'guarded', f'dominated by SDK_INT >= {guard} in this method'
                elif not flow.supported:
                    status, reason = 'unverified', 'the method has a switch, which this analysis does not follow'
                elif guard is None:
                    status, reason = 'unverified', 'unreachable code'
                elif flow.unrecognised:
                    status, reason = 'unverified', 'the method uses SDK_INT in a way this analysis does not read'
                elif helper:
                    status, reason = 'unverified', 'a boolean method the app calls here may be its guard'
                else:
                    status, reason = 'not_guarded_here', 'no SDK_INT guard protects it in this method; a caller may'
                findings.append({'level': level, 'api': f'{platform}.{member}' if member else platform, 'kind': kind,
                                 'where': f'{k.name}.{method.name}{method.proto}', 'origin': origin,
                                 'status': status, 'reason': reason})
    findings.sort(key=lambda f: ({'not_guarded_here': 0, 'unverified': 1, 'guarded': 2}[f['status']], -f['level'],
                                 f['where'], f['api']))
    counts = {s: sum(1 for f in findings if f['status'] == s) for s in ('not_guarded_here', 'unverified', 'guarded')}
    return {'schemaVersion': 1, 'advisory': True, 'minSdk': min_sdk,
            'status': 'incomplete' if incomplete else 'complete',
            **({'reason': f'{len(incomplete)} method(s) with a switch were not fully analysed'} if incomplete else {}),
            'database': {'file': Path(DATABASE).name, 'sha256': db.sha256}, 'counts': counts,
            'findings': findings[:MAX_FINDINGS], 'truncated': max(0, len(findings) - MAX_FINDINGS), 'note': NOTE}


def unavailable(reason: str, min_sdk: int) -> dict:
    return {'schemaVersion': 1, 'advisory': True, 'minSdk': min_sdk, 'status': 'unavailable', 'reason': reason,
            'findings': [], 'note': NOTE}


def summary(report: dict) -> str:
    """One line for the job log."""
    if report['status'] == 'unavailable':
        return f'API level report (advisory): unavailable, {report["reason"]}. No compatibility claim is made.'
    c = report['counts']
    return (f'API level report (advisory, not device proof): {c["not_guarded_here"]} reference(s) above API '
            f'{report["minSdk"]} not guarded in their method, {c["unverified"]} unverified, {c["guarded"]} guarded'
            f'; analysis {report["status"]}.')
