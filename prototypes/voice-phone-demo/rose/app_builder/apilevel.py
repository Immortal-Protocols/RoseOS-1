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
  result, an app field) may decide a branch it did not recognise, an ``SDK_INT`` value it does not follow is used, the method could not be read
  completely (a switch, a line or branch target it could not parse, too many instructions, the time budget), the
  code is unreachable, the API is on an extension SDK (its availability is not decided by ``SDK_INT``), or the
  reference is a class's own supertype (only never loading the class protects it).

It fails closed: anything it could not read completely makes that method's references ``unverified`` and the
report ``incomplete``; if the database or dexdump is missing, or anything goes wrong, the report is ``unavailable``.
It is advisory only: it never fails or changes a build, and it is not device proof. An empty list does not mean the
app is compatible with every phone it can be installed on. Reflection and any API reached without a dex reference
are not seen. A platform reference the pinned database cannot place is reported ``unverified``, never dropped; so
is an app interface method that a class implements only through an inherited platform method above minSdk.
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
INSN = re.compile(r'^[0-9a-f]{6,}: ([0-9a-f .]+)\|([0-9a-f]{4,}): (\S+)(?: (.*))?$')
PAYLOAD_UNITS = re.compile(r'\((\d+) units\)')
MEMBER = re.compile(r'L([^;\s]+);\.([^:\s]+):([^\s,]+)')  # invoke-polymorphic adds ", (proto)" after it
ACCESS = re.compile(r"  Access flags      : 0x[0-9a-f]+ \(([^)]*)\)")
UNKNOWN = 'not in the pinned API database: the analysis cannot place it, so a phone may not have it'
TYPE = re.compile(r'\[*L([^;\s]+);')
TARGET = re.compile(r'(?:^|, )([0-9a-f]{4,8}) // [+-]')
TARGET32 = re.compile(r'^#([0-9a-f]{8})$')
DESCRIPTOR = re.compile(r"  Class descriptor  : 'L([^;]+);'")
SUPERCLASS = re.compile(r"  Superclass        : 'L([^;]+);'")
SECTION = re.compile(r'  (Interfaces|Static fields|Instance fields|Direct methods|Virtual methods) ')
INTERFACE = re.compile(r"    #\d+ +: 'L([^;]+);'")
METHOD = re.compile(r'    #\d+ +: \(in L')
NAME_TYPE = re.compile(r"      (name|type)          : '(.*)'$")
SIZE = re.compile(r'      insns size    : (\d+) ')
TRY_RANGE = re.compile(r'        0x([0-9a-f]+) - 0x([0-9a-f]+)$')
HANDLER = re.compile(r'          (?:L([^;]+);|<any>) -> 0x([0-9a-f]+)$')
HEX_UNIT = re.compile(r'[0-9a-f]{4}')
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
    widths: list = field(default_factory=list)  # (code units shown, whether dexdump cut the hex column short)
    tries: list = field(default_factory=list)  # (start, end, [(type or None, handler address)])
    unparsed: int = 0  # code lines this parser could not read


@dataclass
class Klass:
    name: str
    flags: str = ''
    superclass: str | None = None
    interfaces: list = field(default_factory=list)
    methods: list = field(default_factory=list)
    fields: set = field(default_factory=set)

    @property
    def declared(self) -> set:
        """Member keys the class itself declares: methods as name+proto, fields by name."""
        return {m.name + m.proto for m in self.methods} | self.fields


def parse_dexdump(text: str) -> dict:
    """Classes from ``dexdump -d`` output: supertypes, and each method's instructions and try ranges. A code line it
    cannot read is counted on its method, so that method is never analysed as if it were complete."""
    classes, cls, method, section, tries = {}, None, None, None, None
    # Lines end at '\n' only: dexdump prints some characters raw inside string constants (U+2028, U+0085, ...)
    # that str.splitlines() would also break on, letting a string forge dump lines.
    for line in text.split('\n'):
        if line.startswith('Class #'):
            cls, method, section = None, None, None
            continue
        m = DESCRIPTOR.match(line)
        if m:
            cls = classes.setdefault(m.group(1), Klass(m.group(1)))
            continue
        if cls is None:
            continue
        m = SUPERCLASS.match(line)
        if m:
            cls.superclass = m.group(1)
            continue
        m = ACCESS.match(line) if section is None else None
        if m:
            cls.flags = m.group(1)
            continue
        m = SECTION.match(line)
        if m:
            section, method = m.group(1), None
            continue
        if section == 'Interfaces':
            m = INTERFACE.match(line)
            if m:
                cls.interfaces.append(m.group(1))
            continue
        if section in ('Static fields', 'Instance fields'):
            m = NAME_TYPE.match(line)
            if m and m.group(1) == 'name':
                cls.fields.add(m.group(2))
            continue
        if section not in ('Direct methods', 'Virtual methods'):
            continue
        if METHOD.match(line):
            method = Method(cls.name, '', '')
            cls.methods.append(method)
            tries = None
            continue
        if method is None:
            continue
        m = NAME_TYPE.match(line)
        if m:
            setattr(method, 'name' if m.group(1) == 'name' else 'proto', m.group(2))
            continue
        m = SIZE.match(line)
        if m:
            method.size = int(m.group(1))
            continue
        if CODE_LINE.match(line):
            m = INSN.match(line)
            if m:
                method.insns.append((int(m.group(2), 16), m.group(3), m.group(4) or ''))
                hexes = m.group(1)
                payload = PAYLOAD_UNITS.search(m.group(4) or '') if m.group(3).endswith('-data') else None
                method.widths.append((int(payload.group(1)) if payload else len(HEX_UNIT.findall(hexes)),
                                      '...' in hexes and not payload))
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
            m = TRY_RANGE.match(line)
            if m:
                method.tries.append((int(m.group(1), 16), int(m.group(2), 16), []))
                continue
            m = HANDLER.match(line)
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
        if not self._covers():
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
        protected = {b: [] for b in range(blocks)}  # handlers a block's instructions may jump to
        for start, end, handlers in method.tries:
            lo = bisect.bisect_left(self.addresses, start)
            hi = bisect.bisect_left(self.addresses, end)
            if lo < hi:
                for b in range(self.block_at(lo), self.block_at(hi - 1) + 1):
                    protected[b] += [self.block_at(index[h]) for _, h in handlers]
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
            edges[b] += [(h, None) for h in protected[b]]  # a protected instruction may jump to its handlers
        self.edges = edges
        self.unrecognised = self._unrecognised_sdk_use()
        self.sdk_read = any(_is_sdk(op, a) for _, op, a in insns)
        self.other_branches = any(op.startswith('if-') and i not in self.recognised_ifs
                                  for i, (_, op, _) in enumerate(insns))
        # A branch this analysis did not recognise, in a method that uses the app's own code (a call, an app field):
        # an app helper or flag may decide it, through any number of moves, blocks or operations.
        self.app_value = self.other_branches and any(
            (op.startswith(('invoke', 'sget', 'iget')) and (m := MEMBER.search(a)) and m.group(1) in app_classes)
            for _, op, a in insns)
        self._dominators()

    def _covers(self) -> bool:
        """Whether the parsed instructions tile the method's code exactly: from 0, each one where the previous one
        ends (at least where a cut-short hex column allows), the last one ending at the stated size."""
        a, method = self.addresses, self.method
        if not a or a[0] != 0 or len(method.widths) != len(a):
            return False
        for i, (units, cut) in enumerate(method.widths):
            end = a[i + 1] if i + 1 < len(a) else method.size
            if units < 1 or (end - a[i] < units if cut else end - a[i] != units):
                return False
        return True

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
            if _is_sdk(o, a):
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
                if not _is_sdk(op, args):
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
        memo = self.__dict__.setdefault('_levels', {0: self.guard_of.get(0, 0)})
        chain = []
        while n not in memo:
            chain.append(n)
            n = self.idom[n]
        best = memo[n]
        for node in reversed(chain):
            best = max(best, self.guard_of.get(node, 0))
            memo[node] = best
        return memo[self.block_at(index)]


def _is_sdk(op: str, args: str) -> bool:
    """Whether an instruction reads the platform's Build.VERSION.SDK_INT (and not a look-alike in the app)."""
    m = MEMBER.search(args) if op.startswith('sget') else None
    return bool(m) and m.groups() == ('android/os/Build$VERSION', 'SDK_INT', 'I')


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
    declared = {name: k.declared for name, k in classes.items()}

    def resolve(cls, member):
        """The platform answer for a reference, walking the app's own (and its libraries') classes up first: a
        member the app's own class declares is the app's, not the platform's. (platform class, None) when it leads to
        a platform class the pinned database cannot place: never dropped, reported unverified."""
        seen, todo, unplaced = set(), [cls], None
        while todo:
            c = todo.pop(0)
            if c in seen:
                continue
            seen.add(c)
            if c in classes:
                if member is None or member in declared[c]:
                    return None
                todo += [x for x in [classes[c].superclass, *classes[c].interfaces] if x]
                continue
            found = db.level(c, member)
            if found is not None:
                return c, found
            unplaced = unplaced or c
        return (unplaced, None) if unplaced else None

    def interface_gaps(k: Klass):
        """Methods of the app's own interfaces that a concrete class implements only through an inherited platform
        method: (interface, member, platform class, level)."""
        if 'INTERFACE' in k.flags or 'ABSTRACT' in k.flags:
            return []
        chain, c = [], k.name
        while c in classes and c not in chain:
            chain.append(c)
            c = classes[c].superclass
        platform = c
        own_methods = set().union(*(declared[x] for x in chain))
        interfaces, todo = set(), [i for x in chain for i in classes[x].interfaces]
        while todo:
            i = todo.pop()
            if i in classes and i not in interfaces:
                interfaces.add(i)
                todo += classes[i].interfaces
        gaps = []
        for i in sorted(interfaces):
            for m in classes[i].methods:
                key = m.name + m.proto
                if m.insns or m.size or key in own_methods or m.name.startswith('<'):
                    continue  # a default method, or the class implements it itself
                found = db.level(platform, key) if platform else None
                if found and found[0] > min_sdk:
                    gaps.append((i, key, platform, found[0]))
        return gaps

    findings, problems, skipped = [], [], 0
    for k in classes.values():
        if time.monotonic() > deadline:
            skipped += 1 + len(k.methods)
            continue
        origin = 'app' if k.name.startswith(own) else 'library'
        for sup in [k.superclass, *k.interfaces]:
            if sup and sup not in classes:
                found = db.level(sup)
                if found is None or found[0] > min_sdk:
                    findings.append({'level': found and found[0], 'api': sup, 'kind': 'supertype', 'where': k.name,
                                     'origin': origin, 'status': 'unverified',
                                     'reason': UNKNOWN if found is None else 'the class itself needs this API: only '
                                               'never loading it below that level protects it'})
        for iface, key, platform, level in interface_gaps(k):
            findings.append({'level': level, 'api': f'{platform}.{key}', 'kind': 'supertype', 'where': k.name,
                             'origin': origin, 'status': 'unverified',
                             'reason': f'implements {iface}.{key} through the platform method: calls through the '
                                       'interface fail below that level'})
        for method in k.methods:
            if not method.insns and not method.size and not method.unparsed:
                continue  # abstract or native: no code
            if time.monotonic() > deadline:
                skipped += 1
                continue
            flow = Flow(method, classes)
            if flow.problem:
                problems.append(f'{k.name}.{method.name}: {flow.problem}')
            refs = [((i,), *r) for i, (addr, op, args) in enumerate(method.insns) if (r := _reference(op, args))]
            # A catch type matters wherever its try range can throw: it is guarded only if every instruction is.
            addresses = [a for a, _, _ in method.insns]
            refs += [(tuple(range(bisect.bisect_left(addresses, start), bisect.bisect_left(addresses, end))),
                      t, None, 'catch') for start, end, handlers in method.tries for t, _ in handlers if t]
            for where, cls, member, kind in refs:
                found = resolve(cls, member)
                if found is None:
                    continue
                if found[1] is None:  # a platform reference the pinned database cannot place
                    known = db.level(found[0])
                    findings.append({'level': known and known[0], 'kind': kind, 'origin': origin,
                                     'api': f'{found[0]}.{member}' if member else found[0],
                                     'where': f'{k.name}.{method.name}{method.proto}', 'status': 'unverified',
                                     'reason': UNKNOWN})
                    continue
                platform, (level, extension) = found
                if level <= min_sdk:
                    continue
                levels = [flow.guard_level(i) for i in where] if not flow.problem and where else [None]
                guard = None if None in levels else min(levels)
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
    findings.sort(key=lambda f: ({'not_guarded_here': 0, 'unverified': 1, 'guarded': 2}[f['status']], -(f['level'] or 0),
                                 f['where'], f['api']))
    counts = {s: sum(1 for f in findings if f['status'] == s) for s in ('not_guarded_here', 'unverified', 'guarded')}
    reasons = []
    if problems:
        reasons.append(f'{len(problems)} method(s) not fully analysed (for example {problems[0]})')
    if skipped:
        reasons.append(f'{skipped} method(s) not analysed: the {budget_s:g} s analysis budget ran out')
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
