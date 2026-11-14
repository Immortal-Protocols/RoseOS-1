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
- ``unverified``: the analysis cannot tell, and never calls it safe: a value from the app's own code (a helper's
  result, an app field) or an ``SDK_INT`` value it does not follow decides a branch, the method could not be read
  completely (a switch, a line or branch target it could not parse, too many instructions, the time budget), the
  code is unreachable, the API is on an extension SDK (its availability is not decided by ``SDK_INT``), or the
  reference is a class's own supertype (only never loading the class protects it).

It fails closed: anything it could not read completely makes that method's references ``unverified`` and the
report ``incomplete``; if the database or dexdump is missing, or anything goes wrong, the report is ``unavailable``.
It is advisory only: it never fails or changes a build, and it is not device proof. An empty list does not mean the
app is compatible with every phone it can be installed on. Reflection and any API reached without a dex reference
are not seen.
"""
from __future__ import annotations

import bisect
import hashlib
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

DATABASE = 'platforms/android-35/data/api-versions.xml'
MAX_FINDINGS = 200
MAX_INSNS = 20000  # instructions in one method; a larger one is reported unverified, not analysed
BUDGET_S = 60.0  # analysis time for one build; past it, the rest is reported as not analysed
NOTE = ('Advisory: references above minSdk found in the built dex. This is detection, not device compatibility '
        'proof; an empty list does not mean the app works on every supported phone.')
SDK_INT = 'Landroid/os/Build$VERSION;.SDK_INT:I'
CODE_LINE = re.compile(r'^[0-9a-f]{6,}: ')
INSN = re.compile(r'^[0-9a-f]{6,}: [0-9a-f .]+\|([0-9a-f]{4,}): (\S+)(?: (.*))?$')
MEMBER = re.compile(r'L([^;\s]+);\.([^:\s]+):(\S+)')
TYPE = re.compile(r'\[*L([^;\s]+);')
TARGET = re.compile(r'(?:^|, )([0-9a-f]{4,8}) // [+-]')
TARGET32 = re.compile(r'^#([0-9a-f]{8})$')
TYPE_OPS = ('new-instance', 'const-class', 'check-cast', 'instance-of', 'new-array', 'filled-new-array')
FIELD_OPS = ('sget', 'sput', 'iget', 'iput')
SWITCHES = ('packed-switch', 'sparse-switch')
# Instructions whose first register is written (the rest, if any, are read).
WRITERS = ('move', 'const', 'sget', 'iget', 'aget', 'new-', 'neg-', 'not-', 'int-to', 'long-to', 'float-to',
           'double-to', 'add-', 'sub-', 'mul-', 'div-', 'rem-', 'and-', 'or-', 'xor-', 'shl-', 'shr-', 'ushr-',
           'rsub-', 'cmp', 'array-length', 'instance-of', 'check-cast')
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
                [(e.get('name'), int(e.get('since', since))) for e in c if e.tag in ('extends', 'implements')],
                {m.get('name'): (int(m.get('since', since)), bool(m.get('sdks'))) for m in c
                 if m.tag in ('method', 'field')})

    def level(self, cls: str, member: str | None = None, seen=None):
        """(API level, extension SDK?) of ``cls`` or ``cls.member``, None if unknown. An inherited member is available
        from the earliest level at which it is reachable through ``cls``: never before ``cls`` itself, and never
        before the supertype that provides it was added."""
        seen = seen if seen is not None else frozenset()
        if cls in seen or cls not in self.classes:
            return None
        since, ext, supers, members = self.classes[cls]
        if member is None:
            return since, ext
        if member in members:
            level, member_ext = members[member]
            return max(level, since), ext or member_ext
        best = None
        for sup, added in supers:
            found = self.level(sup, member, seen | {cls})
            if found is not None:
                candidate = (max(found[0], added, since), ext or found[1])
                if best is None or candidate[0] < best[0]:
                    best = candidate
        return best


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
    size: int = 0  # code units, as dexdump states them
    insns: list = field(default_factory=list)  # (address, op, args)
    tries: list = field(default_factory=list)  # (start, end, [(type or None, handler address)])
    unparsed: int = 0  # code lines this parser could not read


@dataclass
class Klass:
    name: str
    superclass: str | None = None
    interfaces: list = field(default_factory=list)
    methods: list = field(default_factory=list)

    @property
    def declared(self) -> set:
        return {m.name + m.proto for m in self.methods}


def parse_dexdump(text: str) -> dict:
    """Classes from ``dexdump -d`` output: supertypes, and each method's instructions and try ranges. A code line it
    cannot read is counted on its method, so that method is never analysed as if it were complete."""
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
        m = re.match(r'      insns size    : (\d+) ', line)
        if m:
            method.size = int(m.group(1))
            continue
        if CODE_LINE.match(line):
            m = INSN.match(line)
            if m:
                method.insns.append((int(m.group(1), 16), m.group(2), m.group(3) or ''))
            elif '|[' not in line:  # the method's own header line is the only other code line
                method.unparsed += 1
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
            elif line.strip():
                method.unparsed += 1
    return classes


def _target(addr: int, op: str, args: str):
    """A branch's target address; None if it cannot be read."""
    if op.startswith('goto/32'):
        m = TARGET32.match(args.strip())
        if not m:
            return None
        offset = int(m.group(1), 16)
        return addr + (offset - (1 << 32) if offset >= 1 << 31 else offset)
    found = TARGET.findall(args)
    return int(found[-1], 16) if found else None


def _regs(args: str) -> list:
    """Register numbers an instruction names (a range like {v3 .. v6} is expanded; strings are not read)."""
    operands = re.sub(r'"(?:[^"\\]|\\.)*"', '', args.split(' // ')[0])
    m = re.search(r'\{v(\d+) \.\. v(\d+)\}', operands)
    if m:
        return list(range(int(m.group(1)), int(m.group(2)) + 1))
    return [int(r) for r in re.findall(r'\bv(\d+)\b', operands)]


def _writes(op: str, regs: list):
    if regs and op.startswith(WRITERS):
        return regs[0]
    return None


def _reads(op: str, regs: list) -> list:
    if _writes(op, regs) is None:
        return regs
    return regs if ('/2addr' in op or op.startswith('check-cast')) else regs[1:]


class Flow:
    """One method's control flow: basic blocks, edges (exception edges included), the guard each recognised
    SDK_INT comparison gives its branches, and immediate dominators. ``problem`` says why it cannot be relied on."""

    def __init__(self, method: Method, app_classes: dict):
        self.method, insns = method, method.insns
        self.problem = None
        if method.unparsed:
            self.problem = 'dexdump lines this analysis could not read'
        elif method.size and not insns:
            self.problem = 'code this analysis could not read'
        elif len(insns) > MAX_INSNS:
            self.problem = f'more than {MAX_INSNS} instructions'
        elif any(op in SWITCHES for _, op, _ in insns):
            self.problem = 'a switch, whose targets dexdump does not print'
        if self.problem or not insns:
            return
        self.addresses = [a for a, _, _ in insns]
        if self.addresses != sorted(set(self.addresses)) or (method.size and self.addresses[-1] >= method.size):
            self.problem = 'code this analysis could not read'
            return
        index = {a: i for i, a in enumerate(self.addresses)}
        leaders = {0}
        branch_target = {}
        for i, (addr, op, args) in enumerate(insns):
            if op.startswith(('if-', 'goto')):
                target = _target(addr, op, args)
                if target not in index:
                    self.problem = 'a branch target this analysis could not read'
                    return
                branch_target[i] = index[target]
                leaders.add(index[target])
            if op.startswith(('if-', 'goto', 'return', 'throw')) and i + 1 < len(insns):
                leaders.add(i + 1)
        for start, end, handlers in method.tries:
            for h in [h for _, h in handlers]:
                if h not in index:
                    self.problem = 'an exception handler this analysis could not read'
                    return
                leaders.add(index[h])
            for a in (start, end):
                if a in index:
                    leaders.add(index[a])
        self.starts = sorted(leaders)
        blocks = len(self.starts)
        self.block_at = lambda i: bisect.bisect_right(self.starts, i) - 1
        self.bodies = [list(range(s, (self.starts[b + 1] if b + 1 < blocks else len(insns))))
                       for b, s in enumerate(self.starts)]
        self.recognised_ifs = set()  # instruction indexes of recognised SDK_INT comparisons
        edges = {b: [] for b in range(blocks)}  # (successor, guard level given on that edge)
        for b, body in enumerate(self.bodies):
            last = body[-1]
            addr, op, args = insns[last]
            nxt = b + 1 if b + 1 < blocks else None
            if op.startswith('if-'):
                true_guard, false_guard = self._guard(body)
                edges[b].append((self.block_at(branch_target[last]), true_guard))
                if nxt is not None:
                    edges[b].append((nxt, false_guard))
            elif op.startswith('goto'):
                edges[b].append((self.block_at(branch_target[last]), None))
            elif not op.startswith(('return', 'throw')) and nxt is not None:
                edges[b].append((nxt, None))
            first, end_addr = self.addresses[body[0]], self.addresses[body[-1]]
            for start, end, handlers in method.tries:  # a protected instruction may jump to its handlers
                if start <= end_addr and first < end:
                    for _, h in handlers:
                        edges[b].append((self.block_at(index[h]), None))
        self.edges = edges
        self.unrecognised = self._unrecognised_sdk_use()
        self.sdk_read = any(op.startswith('sget') and SDK_INT in a for _, op, a in insns)
        self.other_branches = any(op.startswith('if-') and i not in self.recognised_ifs
                                  for i, (_, op, _) in enumerate(insns))
        self.app_value = self._app_value_branches(app_classes)
        self._dominators()

    def _guard(self, body):
        """(guard on the true branch, on the false branch) of the comparison ending this block, or (None, None)."""
        insns = self.method.insns
        addr, op, args = insns[body[-1]]
        cmp = op[3:].split('/')[0]
        regs = _regs(args)
        defs = {}
        for i in body[:-1]:
            _, o, a = insns[i]
            dest = _writes(o, _regs(a))
            if dest is None:
                continue
            if o.startswith('sget') and SDK_INT in a:
                defs[dest] = 'sdk'
            elif o.startswith('const') and not o.startswith(('const-string', 'const-class', 'const-wide')):
                value = re.search(r'#int (-?\d+)', a)
                defs[dest] = int(value.group(1)) if value else None
            else:
                defs.pop(dest, None)
        if cmp.endswith('z') or len(regs) < 2 or cmp not in GUARANTEE:
            return None, None
        left, right = defs.get(regs[0]), defs.get(regs[1])
        if left == 'sdk' and isinstance(right, int):
            const, cmp_ = right, cmp
        elif right == 'sdk' and isinstance(left, int):
            const, cmp_ = left, FLIP[cmp]
        else:
            return None, None
        self.recognised_ifs.add(body[-1])
        true_off, false_off = GUARANTEE[cmp_]
        return (None if true_off is None else const + true_off), (None if false_off is None else const + false_off)

    def _unrecognised_sdk_use(self) -> bool:
        """Whether an SDK_INT value is read anywhere but by the recognised comparison ending its own block
        (for example kept in a variable and compared later, stored, or passed on)."""
        insns = self.method.insns
        for b, body in enumerate(self.bodies):
            for pos, i in enumerate(body):
                _, op, args = insns[i]
                if not (op.startswith('sget') and SDK_INT in args):
                    continue
                reg = _writes(op, _regs(args))
                live = True
                for j in body[pos + 1:]:
                    _, o, a = insns[j]
                    r = _regs(a)
                    if reg in _reads(o, r) and not (j == body[-1] and j in self.recognised_ifs):
                        return True
                    if _writes(o, r) == reg:
                        live = False
                        break
                if live and self._read_later(b, reg):
                    return True
        return False

    def _read_later(self, block: int, reg: int) -> bool:
        insns, seen, todo = self.method.insns, set(), [t for t, _ in self.edges[block]]
        while todo:
            b = todo.pop()
            if b in seen:
                continue
            seen.add(b)
            for j in self.bodies[b]:
                _, o, a = insns[j]
                r = _regs(a)
                if reg in _reads(o, r):
                    return True
                if _writes(o, r) == reg:
                    break
            else:
                todo += [t for t, _ in self.edges[b]]
        return False

    def _app_value_branches(self, app_classes: dict) -> bool:
        """Whether a branch reads a value from the app's own code: a method's result or a field of an app class
        (a helper guard may hide there)."""
        insns = self.method.insns
        for body in self.bodies:
            app_regs, pending = set(), False
            for i in body:
                _, op, args = insns[i]
                r = _regs(args)
                m = MEMBER.search(args)
                if op.startswith('invoke'):
                    pending = bool(m and m.group(1) in app_classes)
                    continue
                if op.startswith('move-result') and r:
                    (app_regs.add if pending else app_regs.discard)(r[0])
                elif op.startswith(('sget', 'iget')) and m and m.group(1) in app_classes and r:
                    app_regs.add(r[0])
                elif op.startswith('if-') and any(x in app_regs for x in r):
                    return True
                elif _writes(op, r) is not None:
                    app_regs.discard(r[0])
                pending = False
        return False

    def _dominators(self):
        """Immediate dominators (Cooper, Harvey and Kennedy) over the blocks plus one node per guarding edge, so a
        branch's guard covers exactly what that branch dominates."""
        blocks = len(self.bodies)
        succ = {b: [] for b in range(blocks)}
        self.guard_of = {}
        node = blocks
        for b, outs in self.edges.items():
            for t, g in outs:
                if g is None:
                    succ[b].append(t)
                else:
                    succ[b].append(node)
                    succ[node] = [t]
                    self.guard_of[node] = g
                    node += 1
        order, seen, stack = [], {0}, [(0, iter(succ[0]))]
        while stack:  # postorder, iteratively
            n, it = stack[-1]
            for t in it:
                if t not in seen:
                    seen.add(t)
                    stack.append((t, iter(succ[t])))
                    break
            else:
                order.append(n)
                stack.pop()
        post = {n: k for k, n in enumerate(order)}
        preds = {n: [] for n in post}
        for n in post:
            for t in succ[n]:
                preds[t].append(n)
        idom = {0: 0}
        changed = True
        while changed:
            changed = False
            for n in reversed(order):
                if n == 0:
                    continue
                done = [p for p in preds[n] if p in idom]
                new = done[0]
                for p in done[1:]:
                    a, c = p, new
                    while a != c:
                        while post[a] < post[c]:
                            a = idom[a]
                        while post[c] < post[a]:
                            c = idom[c]
                    new = a
                if idom.get(n) != new:
                    idom[n], changed = new, True
        self.idom = idom

    def guard_level(self, index: int) -> int | None:
        """The highest guarantee dominating this instruction; None if unreachable."""
        n = self.block_at(index)
        if n not in self.idom:
            return None
        best = 0
        while True:
            best = max(best, self.guard_of.get(n, 0))
            if n == 0:
                return best
            n = self.idom[n]


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


def analyse(dumps: list[str], db: Database, package: str, min_sdk: int, budget_s: float = BUDGET_S) -> dict:
    classes = {}
    for text in dumps:
        classes.update(parse_dexdump(text))
    if not classes:
        raise ValueError('no classes in the dex')
    own = package.replace('.', '/') + '/'
    deadline = time.monotonic() + budget_s

    def resolve(cls, member):
        """The platform answer for a reference, walking the app's own (and its libraries') classes up first. A member
        the app's own class declares is the app's, not the platform's."""
        seen, todo = set(), [cls]
        while todo:
            c = todo.pop(0)
            if c in seen:
                continue
            seen.add(c)
            if c in classes:
                if member is None or member in classes[c].declared:
                    return None
                todo += [x for x in [classes[c].superclass, *classes[c].interfaces] if x]
                continue
            found = db.level(c, member)
            if found is not None:
                return c, found
        return None

    findings, problems, skipped = [], [], 0
    for k in classes.values():
        if time.monotonic() > deadline:
            skipped += 1
            continue
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
            if not method.insns and not method.size and not method.unparsed:
                continue  # abstract or native: no code
            flow = Flow(method, classes)
            if flow.problem:
                problems.append(f'{k.name}.{method.name}: {flow.problem}')
            refs = [(i, *r) for i, (addr, op, args) in enumerate(method.insns) if (r := _reference(op, args))]
            address = {a: i for i, (a, _, _) in enumerate(method.insns)}
            refs += [(address.get(start), t, None, 'catch') for start, end, handlers in method.tries
                     for t, _ in handlers if t]
            for i, cls, member, kind in refs:
                found = resolve(cls, member)
                if found is None:
                    continue
                platform, (level, extension) = found
                if level <= min_sdk:
                    continue
                guard = flow.guard_level(i) if not flow.problem and i is not None else None
                if extension:
                    status, reason = 'unverified', 'an extension SDK API: SDK_INT alone does not decide it'
                elif flow.problem:
                    status, reason = 'unverified', f'not analysed: {flow.problem}'
                elif guard is None:
                    status, reason = 'unverified', 'unreachable code'
                elif guard >= level:
                    status, reason = 'guarded', f'dominated by SDK_INT >= {guard} in this method'
                elif flow.unrecognised:
                    status, reason = 'unverified', 'an SDK_INT value this analysis does not follow is used here'
                elif flow.app_value:
                    status, reason = 'unverified', 'a value from the app\'s own code decides a branch here'
                elif flow.sdk_read and flow.other_branches:
                    status, reason = 'unverified', 'an SDK_INT result may reach a branch through a variable'
                else:
                    status, reason = 'not_guarded_here', 'no SDK_INT guard protects it in this method; a caller may'
                findings.append({'level': level, 'api': f'{platform}.{member}' if member else platform, 'kind': kind,
                                 'where': f'{k.name}.{method.name}{method.proto}', 'origin': origin,
                                 'status': status, 'reason': reason})
    findings.sort(key=lambda f: ({'not_guarded_here': 0, 'unverified': 1, 'guarded': 2}[f['status']], -f['level'],
                                 f['where'], f['api']))
    counts = {s: sum(1 for f in findings if f['status'] == s) for s in ('not_guarded_here', 'unverified', 'guarded')}
    reasons = []
    if problems:
        reasons.append(f'{len(problems)} method(s) not fully analysed (for example {problems[0]})')
    if skipped:
        reasons.append(f'{skipped} class(es) not analysed: the {budget_s:g} s analysis budget ran out')
    return {'schemaVersion': 1, 'advisory': True, 'minSdk': min_sdk,
            'status': 'incomplete' if reasons else 'complete', **({'reason': '; '.join(reasons)} if reasons else {}),
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
