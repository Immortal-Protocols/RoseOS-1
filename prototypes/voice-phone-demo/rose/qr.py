"""A small QR code encoder: byte mode, error correction level M, versions 1 to 20.

    from rose import qr
    qr.matrix("roseagent://pair?...")   # list of rows, True = dark module
    qr.png("...", scale=8, border=4)    # PNG bytes (Pillow)
    print(qr.terminal("..."))           # unicode half blocks with explicit colours

Pure Python so pairing needs no new packages. The construction follows ISO/IEC
18004: data bits (mode, length, bytes, terminator, pad codewords), Reed-Solomon
error correction per block, interleaving, the function patterns (finders,
timing, alignment, format and version information), the zigzag data
placement, and the mask with the lowest penalty score of all eight. The tests
decode every output with ZXing (rose/tests/test_qr.py).
"""

from __future__ import annotations

import io

MIN_VERSION, MAX_VERSION = 1, 20
_FORMAT_ECL_M = 0  # the two format bits for level M (L=1, M=0, Q=3, H=2)

# Level M, indexed by version (index 0 unused): error correction codewords per
# block and number of blocks (ISO/IEC 18004 table 9).
_ECC_PER_BLOCK = [0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26]
_NUM_BLOCKS = [0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16]


# ------------------------------------------------------------- GF(256) maths
def _gf_mul(x: int, y: int) -> int:
    """Multiply in GF(2^8) modulo x^8 + x^4 + x^3 + x^2 + 1 (0x11D)."""

    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    """Generator polynomial coefficients (highest power first, leading 1 dropped)."""

    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


# ----------------------------------------------------------- version sizing
def _raw_data_modules(ver: int) -> int:
    """Modules available for data and ECC bits once the function patterns are placed."""

    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        num_align = ver // 7 + 2
        result -= (25 * num_align - 10) * num_align - 55
        if ver >= 7:
            result -= 36
    return result


def _data_codewords(ver: int) -> int:
    return _raw_data_modules(ver) // 8 - _ECC_PER_BLOCK[ver] * _NUM_BLOCKS[ver]


def _count_bits(ver: int) -> int:
    return 8 if ver <= 9 else 16  # byte mode character count indicator


def _choose_version(n_bytes: int) -> int:
    for ver in range(MIN_VERSION, MAX_VERSION + 1):
        if 4 + _count_bits(ver) + 8 * n_bytes <= _data_codewords(ver) * 8:
            return ver
    raise ValueError(f"{n_bytes} bytes do not fit a version {MAX_VERSION} QR code at level M")


def _alignment_positions(ver: int) -> list[int]:
    if ver == 1:
        return []
    num_align = ver // 7 + 2
    step = (ver * 8 + num_align * 3 + 5) // (num_align * 4 - 4) * 2
    size = ver * 4 + 17
    result = [6]
    pos = size - 7
    while len(result) < num_align:
        result.insert(1, pos)
        pos -= step
    return result


# --------------------------------------------------------------- codewords
def _codewords(data: bytes, ver: int) -> list[int]:
    """Data bits -> data codewords -> interleaved data + ECC codewords."""

    bits: list[int] = []

    def put(value: int, n: int) -> None:
        bits.extend((value >> i) & 1 for i in range(n - 1, -1, -1))

    put(0b0100, 4)                       # byte mode
    put(len(data), _count_bits(ver))
    for b in data:
        put(b, 8)
    capacity = _data_codewords(ver) * 8
    put(0, min(4, capacity - len(bits)))  # terminator
    put(0, -len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < capacity // 8:
        words.append(pad)
        pad ^= 0xEC ^ 0x11

    num_blocks, ecc_len = _NUM_BLOCKS[ver], _ECC_PER_BLOCK[ver]
    raw = _raw_data_modules(ver) // 8
    num_short = num_blocks - raw % num_blocks
    short_len = raw // num_blocks
    divisor = _rs_divisor(ecc_len)
    blocks, k = [], 0
    for i in range(num_blocks):
        n = short_len - ecc_len + (0 if i < num_short else 1)
        dat = words[k:k + n]
        k += n
        ecc = _rs_remainder(dat, divisor)
        if i < num_short:
            dat = dat + [0]  # placeholder so all blocks line up; skipped below
        blocks.append(dat + ecc)
    out = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != short_len - ecc_len or j >= num_short:
                out.append(blk[i])
    return out


# ------------------------------------------------------------------ symbol
class _Symbol:
    def __init__(self, ver: int):
        self.ver = ver
        self.size = ver * 4 + 17
        self.mod = [[False] * self.size for _ in range(self.size)]
        self.fn = [[False] * self.size for _ in range(self.size)]

    def set_fn(self, x: int, y: int, dark: bool) -> None:
        self.mod[y][x] = dark
        self.fn[y][x] = True

    def draw_function_patterns(self) -> None:
        size = self.size
        for i in range(size):
            self.set_fn(6, i, i % 2 == 0)
            self.set_fn(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self.set_fn(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _alignment_positions(self.ver)
        last = len(pos) - 1
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue  # finder corners
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_fn(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)  # reserve the areas; real bits once the mask is known
        self.draw_version()

    def draw_format(self, mask: int) -> None:
        data = _FORMAT_ECL_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1 == 1  # noqa: E731
        size = self.size
        for i in range(6):
            self.set_fn(8, i, bit(i))
        self.set_fn(8, 7, bit(6))
        self.set_fn(8, 8, bit(7))
        self.set_fn(7, 8, bit(8))
        for i in range(9, 15):
            self.set_fn(14 - i, 8, bit(i))
        for i in range(8):
            self.set_fn(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_fn(8, size - 15 + i, bit(i))
        self.set_fn(8, size - 8, True)  # the dark module

    def draw_version(self) -> None:
        if self.ver < 7:
            return
        rem = self.ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.ver << 12 | rem
        for i in range(18):
            dark = (bits >> i) & 1 == 1
            a, b = self.size - 11 + i % 3, i // 3
            self.set_fn(a, b, dark)
            self.set_fn(b, a, dark)

    def draw_codewords(self, words: list[int]) -> None:
        size, i, total = self.size, 0, len(words) * 8
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5  # skip the vertical timing column
            upward = ((right + 1) & 2) == 0
            for vert in range(size):
                y = size - 1 - vert if upward else vert
                for x in (right, right - 1):
                    if not self.fn[y][x] and i < total:
                        self.mod[y][x] = (words[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        test = _MASKS[mask]
        for y in range(self.size):
            row, fn = self.mod[y], self.fn[y]
            for x in range(self.size):
                if not fn[x] and test(x, y):
                    row[x] = not row[x]


_MASKS = [
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
]

_FINDER_LIKE = ("10111010000", "00001011101")


def _penalty(mod: list[list[bool]]) -> int:
    """The four ISO penalty rules: runs, 2x2 blocks, finder-like patterns, dark balance."""

    size = len(mod)
    score = 0
    lines = ["".join("1" if v else "0" for v in row) for row in mod]
    lines += ["".join("1" if mod[y][x] else "0" for y in range(size)) for x in range(size)]
    for line in lines:
        run, prev = 0, ""
        for c in line:
            if c == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + run - 5
                run, prev = 1, c
        if run >= 5:
            score += 3 + run - 5
        padded = "0000" + line + "0000"  # the quiet zone counts as light
        for pat in _FINDER_LIKE:
            start = padded.find(pat)
            while start != -1:
                score += 40
                start = padded.find(pat, start + 1)
    for y in range(size - 1):
        for x in range(size - 1):
            c = mod[y][x]
            if c == mod[y][x + 1] == mod[y + 1][x] == mod[y + 1][x + 1]:
                score += 3
    dark = sum(sum(row) for row in mod)
    total = size * size
    k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
    return score + max(0, k) * 10 + FALSE_FINDER_PENALTY * len(false_finders(mod))


# Not an ISO rule. ZXing's detector (the phone's scanner) accepts any 2D cross
# whose runs are roughly 1:1:3:1:1 as a finder pattern and stops scanning once
# three agree, so such a cross inside the data area can win over the real
# bottom-left finder and the code will not scan. The ISO run rule only sees
# one dimension; a mask that leaves a cross like that is avoided whenever
# another mask does not.
FALSE_FINDER_PENALTY = 10_000


def _cross_ok(counts: list[int]) -> float | None:
    """ZXing's run test (FinderPatternFinder.foundPatternCross): module size, or None."""

    total = sum(counts)
    if 0 in counts:
        return None
    m = total / 7.0
    v = m / 2.0
    if (abs(m - counts[0]) < v and abs(m - counts[1]) < v and abs(3 * m - counts[2]) < 3 * v
            and abs(m - counts[3]) < v and abs(m - counts[4]) < v):
        return m
    return None


def _runs_through(line: list[bool], i: int) -> tuple[list[int], int] | None:
    """dark/light/DARK/light/dark run lengths around the dark module ``line[i]``, and the centre."""

    if not line[i]:
        return None
    lo = hi = i
    while lo > 0 and line[lo - 1]:
        lo -= 1
    while hi < len(line) - 1 and line[hi + 1]:
        hi += 1
    counts = [0, 0, hi - lo + 1, 0, 0]
    k = lo - 1
    for slot, colour in ((1, False), (0, True)):
        while k >= 0 and line[k] == colour:
            counts[slot] += 1
            k -= 1
    k = hi + 1
    for slot, colour in ((3, False), (4, True)):
        while k < len(line) and line[k] == colour:
            counts[slot] += 1
            k += 1
    return counts, (lo + hi + 1) // 2  # an even centre run: ZXing lands on the later module


def false_finders(mod: list[list[bool]]) -> list[tuple[int, int]]:
    """Centres of finder-like crosses outside the three real finder patterns.

    Mirrors ZXing's candidate test: a horizontal 1:1:3:1:1 run set, a vertical
    one through its centre whose total is within 40 % of the horizontal one,
    and a horizontal recheck at the vertical centre whose module size matches
    the real finders (about one module).
    """

    size = len(mod)
    cols = [[mod[y][x] for y in range(size)] for x in range(size)]
    found: set[tuple[int, int]] = set()
    for y in range(size):
        row = mod[y]
        for x in range(size):
            # Only start at the left edge of a dark run in the middle of a candidate.
            if not row[x] or (x > 0 and row[x - 1]):
                continue
            h = _runs_through(row, x)
            if h is None or _cross_ok(h[0]) is None:
                continue
            cx = h[1]
            v = _runs_through(cols[cx], y)
            if v is None or _cross_ok(v[0]) is None:
                continue
            if 5 * abs(sum(v[0]) - sum(h[0])) >= 2 * sum(h[0]):
                continue
            cy = v[1]
            h2 = _runs_through(mod[cy], cx)
            m = _cross_ok(h2[0]) if h2 is not None else None
            if m is None or not 0.75 <= m <= 1.35:
                continue
            if (cx < 9 and cy < 9) or (cx >= size - 9 and cy < 9) or (cx < 9 and cy >= size - 9):
                continue  # a real finder
            found.add((cx, cy))
    return sorted(found)


# --------------------------------------------------------------------- API
def matrix(text: str) -> list[list[bool]]:
    """The QR symbol for ``text`` (UTF-8, byte mode, level M), without a quiet zone."""

    data = text.encode("utf-8")
    ver = _choose_version(len(data))
    words = _codewords(data, ver)
    best: tuple[int, list[list[bool]]] | None = None
    for mask in range(8):
        sym = _Symbol(ver)
        sym.draw_function_patterns()
        sym.draw_codewords(words)
        sym.apply_mask(mask)
        sym.draw_format(mask)
        score = _penalty(sym.mod)
        if best is None or score < best[0]:
            best = (score, sym.mod)
    assert best is not None
    return best[1]


def png(text: str, scale: int = 8, border: int = 4) -> bytes:
    """PNG bytes: black modules on white, ``border`` modules of quiet zone, ``scale`` px per module."""

    from PIL import Image

    mod = matrix(text)
    n = len(mod) + 2 * border
    img = Image.new("1", (n, n), 1)
    px = img.load()
    for y, row in enumerate(mod):
        for x, dark in enumerate(row):
            if dark:
                px[x + border, y + border] = 0
    img = img.resize((n * scale, n * scale), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def terminal(text: str, border: int = 2) -> str:
    """Two module rows per line with unicode half blocks.

    Colours are explicit (black on white) so the code scans in light and dark
    terminals alike; each line resets the colours at its end.
    """

    mod = matrix(text)
    n = len(mod) + 2 * border
    grid = [[False] * n for _ in range(n + 1)]  # one spare light row for odd heights
    for y, row in enumerate(mod):
        for x, dark in enumerate(row):
            grid[y + border][x + border] = dark
    on, off = "\x1b[38;5;16;48;5;231m", "\x1b[0m"
    glyph = {(False, False): " ", (True, False): "▀", (False, True): "▄", (True, True): "█"}
    lines = []
    for y in range(0, n, 2):
        lines.append(on + "".join(glyph[(grid[y][x], grid[y + 1][x])] for x in range(n)) + off)
    return "\n".join(lines)
