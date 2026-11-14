"""Per-request output grammar: only this request's aliases, only this catalog's components, bounded counts.

The grammar is built as a small, non-recursive tree and rendered two ways from that one tree: GBNF for llama.cpp's
grammar-constrained sampling, and a Python regular expression used by the tests (and usable as a cheap pre-check)
to prove that the grammar accepts exactly the layouts it should. Cross-references a grammar cannot express (a
card's actions must target its item, no duplicates, the total item cap) are left to validate.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .contract import LAYOUT_TITLE_CHARS, SECTION_TITLE_CHARS
from .context import Context

TITLE_CHAR_GBNF = r'[^"\\\x00-\x1f]'
TITLE_CHAR_RE = r'[^"\\\x00-\x1f]'


@dataclass(frozen=True)
class Lit:
    text: str


@dataclass(frozen=True)
class Alt:
    options: tuple


@dataclass(frozen=True)
class Seq:
    parts: tuple


@dataclass(frozen=True)
class Rep:
    node: object
    lo: int
    hi: int


@dataclass(frozen=True)
class TitleChars:
    lo: int
    hi: int


def _gbnf(n) -> str:
    if isinstance(n, Lit):
        return '"' + n.text.replace('\\', '\\\\').replace('"', '\\"') + '"'
    if isinstance(n, Alt):
        return '(' + ' | '.join(_gbnf(o) for o in n.options) + ')'
    if isinstance(n, Seq):
        return ' '.join(_gbnf(p) for p in n.parts)
    if isinstance(n, Rep):
        return f'({_gbnf(n.node)}){{{n.lo},{n.hi}}}'
    if isinstance(n, TitleChars):
        return f'{TITLE_CHAR_GBNF}{{{n.lo},{n.hi}}}'
    raise TypeError(n)


def _re(n) -> str:
    if isinstance(n, Lit):
        return re.escape(n.text)
    if isinstance(n, Alt):
        return '(?:' + '|'.join(_re(o) for o in n.options) + ')'
    if isinstance(n, Seq):
        return ''.join(_re(p) for p in n.parts)
    if isinstance(n, Rep):
        return f'(?:{_re(n.node)}){{{n.lo},{n.hi}}}'
    if isinstance(n, TitleChars):
        return f'{TITLE_CHAR_RE}{{{n.lo},{n.hi}}}'
    raise TypeError(n)


def _quoted(aliases) -> Alt:
    return Alt(tuple(Lit(f'"{a}"') for a in aliases))


def _list(item, lo: int, hi: int) -> Seq:
    """`[item(,item){lo-1,hi-1}]` (lo >= 1)."""
    if hi <= 1:
        return Seq((Lit('['), item, Lit(']')))
    return Seq((Lit('['), item, Rep(Seq((Lit(','), item)), lo - 1, hi - 1), Lit(']')))


def build(ctx: Context, catalog: dict, limits: dict):
    """The grammar tree for one request, or None when no component can be filled from the selected context."""
    sections = []
    for name, rule in catalog['components'].items():
        items = [a for a, k in ctx.kinds.items() if k in rule['items']]
        hi = min(rule['max'], limits['maxItemsPerSection'], len(items))
        if not items or hi < rule['min']:
            continue
        parts = [Lit('{"component":"' + name + '","title":"'), TitleChars(1, SECTION_TITLE_CHARS),
                 Lit('","items":'), _list(_quoted(items), rule['min'], hi)]
        if rule['actions']:
            acts = [a for a, t in ctx.targets.items() if ctx.kinds.get(t) in rule['items']]
            if acts:
                more = rule['actions'] - 1
                first = Seq((_quoted(acts),) + ((Rep(Seq((Lit(','), _quoted(acts))), 0, more),) if more else ()))
                parts += [Rep(Seq((Lit(',"actions":['), first, Lit(']'))), 0, 1)]
        parts.append(Lit('}'))
        sections.append(Seq(tuple(parts)))
    if not sections:
        return None
    section = Alt(tuple(sections))
    return Seq((Lit('{"title":"'), TitleChars(1, LAYOUT_TITLE_CHARS), Lit('","sections":'),
                _list(section, 1, limits['maxSections']), Lit('}')))


def to_gbnf(tree) -> str:
    return 'root ::= ' + _gbnf(tree) + '\n'


def to_regex(tree):
    return re.compile(_re(tree))


def max_chars(n) -> int:
    """The longest output the grammar admits, in characters (titles counted as 1 char each; a token budget bound)."""
    if isinstance(n, Lit):
        return len(n.text)
    if isinstance(n, Alt):
        return max(max_chars(o) for o in n.options)
    if isinstance(n, Seq):
        return sum(max_chars(p) for p in n.parts)
    if isinstance(n, Rep):
        return n.hi * max_chars(n.node)
    if isinstance(n, TitleChars):
        return n.hi
    raise TypeError(n)
