"""The pure-Python QR encoder round-trips through ZXing (the decoder Rose Agent uses).

    .venv/bin/python -m rose.tests.test_qr

Needs ``java`` (17+) and <repo>/.tools/zxing/core-3.5.4.jar (scripts/bootstrap.sh fetches it).
Every string is encoded to a PNG, decoded by rose/tests/QrDecode.java and compared exactly.
"""

from __future__ import annotations

import base64
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from rose import qr

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
ZXING = REPO / ".tools" / "zxing" / "core-3.5.4.jar"

PAIR_URI = ("roseagent://pair?v=1&name=Nathan%E2%80%99s%20MacBook%20Pro%20%282%29"
            "&hosts=nathans-macbook-pro.tail1234.ts.net,100.101.102.103,192.168.1.70&port=8443"
            "&pin=3f9a1c0d5e7b2a4c6d8e0f1a3b5c7d9e1f2a4b6c8d0e2f4a6b8c0d2e4f6a8b0c"
            "&code=ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def samples() -> list[str]:
    rng = random.Random(1234)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~:/?#[]@!$&'()*+,;=% "
    out = ["a", "Hello, world!", "https://example.com/", "0", "roseagent://pair",
           "Grüße aus Köln, ☕ and 🌹", PAIR_URI, PAIR_URI.replace("8443", "18443"), PAIR_URI + "&x=" + "y" * 40]
    for n in (1, 7, 14, 17, 26, 32, 42, 53, 62, 78, 84, 106, 122, 134, 154, 180, 202, 213, 230, 251, 280, 331, 362, 412, 450, 520, 600, 666):
        out.append("".join(rng.choice(alphabet) for _ in range(n)))
    b32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    for _ in range(40):  # pair URIs with random pins, codes and address lists, as the Mac makes them
        hosts = rng.sample(["mac.tail1a2b3c.ts.net", f"100.{rng.randint(64, 127)}.{rng.randint(0, 255)}.7",
                            f"192.168.1.{rng.randint(2, 254)}", f"10.0.{rng.randint(0, 255)}.9"], rng.randint(1, 4))
        out.append(f"roseagent://pair?v=1&name=Nathan%E2%80%99s%20MacBook%20Pro&hosts={','.join(hosts)}&port=8443"
                   f"&pin={''.join(rng.choice('0123456789abcdef') for _ in range(64))}"
                   f"&code={''.join(rng.choice(b32) for _ in range(26))}")
    return out


def from_terminal(text: str, border: int) -> list[list[bool]]:
    """Read qr.terminal() back into modules (drops the colour codes and the quiet zone)."""

    top = {" ": (False, False), "\u2580": (True, False), "\u2584": (False, True), "\u2588": (True, True)}
    rows: list[list[bool]] = []
    for line in text.split("\n"):
        cells = [top[c] for c in line.split("m", 1)[1].rsplit("\x1b", 1)[0]]
        rows.append([c[0] for c in cells])
        rows.append([c[1] for c in cells])
    n = len(rows[0]) - 2 * border
    return [r[border:border + n] for r in rows[border:border + n]]


def decode(paths: list[Path]) -> list[str | None]:
    proc = subprocess.run(["java", "-cp", str(ZXING), str(HERE / "QrDecode.java"), *map(str, paths)],
                          capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"java failed: {proc.stderr.strip()[-400:]}")
    out = []
    for line in proc.stdout.splitlines():
        status, _, payload = line.partition(" ")
        out.append(base64.b64decode(payload).decode("utf-8") if status == "OK" else None)
    return out


def main() -> None:
    if not ZXING.is_file():
        sys.exit(f"missing {ZXING}: run scripts/bootstrap.sh")
    bad = []
    texts = samples()
    versions = set()
    with tempfile.TemporaryDirectory(prefix="rose-qr-") as tmp:
        paths = []
        for i, text in enumerate(texts):
            m = qr.matrix(text)
            size = len(m)
            versions.add((size - 17) // 4)
            if any(len(row) != size for row in m):
                bad.append(f"sample {i}: matrix not square")
            scale = 6 if i % 2 else 8
            data = qr.png(text, scale=scale, border=4)
            if not data.startswith(b"\x89PNG"):
                bad.append(f"sample {i}: not a PNG")
            p = Path(tmp) / f"{i:03d}.png"
            p.write_bytes(data)
            paths.append(p)
        for i, (text, got) in enumerate(zip(texts, decode(paths), strict=True)):
            if got != text:
                bad.append(f"sample {i} ({len(text.encode())} bytes): decoded {got!r}")
    try:
        qr.matrix("x" * 700)
        bad.append("700 bytes should not fit version 20-M")
    except ValueError:
        pass
    if from_terminal(qr.terminal(PAIR_URI, border=2), 2) != qr.matrix(PAIR_URI):
        bad.append("terminal() does not draw the matrix")
    term = qr.terminal(PAIR_URI)
    if "▀" not in term or "\x1b[48;5;231m" not in term.split("\n")[0].replace("38;5;16;", ""):
        bad.append("terminal output lacks half blocks or explicit colours")
    print(f"{len(texts)} strings, versions {sorted(versions)}; pair URI is {len(PAIR_URI)} chars, "
          f"version {(len(qr.matrix(PAIR_URI)) - 17) // 4}")
    if bad:
        print("FAIL")
        for b in bad:
            print(" -", b)
        sys.exit(1)
    print("OK: every QR decoded to the exact input")


if __name__ == "__main__":
    main()
