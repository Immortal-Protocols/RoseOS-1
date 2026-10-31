"""Image size vs speed and reading accuracy, to choose brain.IMAGE_MAX_SIDE.

    ROSE_SHOTS=/path/to/shots ROSE_GPU_LOCK=/path/to/gpu.lock \\
        .venv/bin/python -m rose.tests.bench_vision [--sides 640,768,896,1024,1280] [--chunks 32,64,96,128]

For each long side: image tokens, vision encode ms (total and the largest
unit), prefill ms, and whether the model reads every item of the Claude
app's side menu and every line of the Settings screen. Expected strings come
from the uiautomator dumps next to the screenshots (``<package>.xml``), so
nothing private is stored in the repo; a dump also lists rows that are
covered on screen (the Claude menu's last recents sit under its bottom
panel), so those are reported missing at every size. ``--chunks`` also times one image
prefill unit per chunk size (to choose brain.IMAGE_CHUNK). Holds
``$ROSE_GPU_LOCK`` like test_vision.
"""

from __future__ import annotations

import argparse
import os
import re
import statistics
import sys
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from rose.brain import GEN_FAST, IM_END, Brain, Sampling
from rose.tests.test_vision import SYSTEM, drain, gpu_lock, shot, user_head

PROMPTS = {
    "com.anthropic.claude": "List every piece of text on this screen, top to bottom, exactly as written, one per line.",
    "com.android.settings": "Read all the text on this screen, word for word.",
    # Denser screens with smaller text (scored the same way; their dumps
    # include more off-screen or covered rows, so compare sizes, not totals).
    "com.google.android.apps.maps": "List every piece of text on this screen, top to bottom, exactly as written, one per line.",
    "com.spotify.music": "List every piece of text on this screen, top to bottom, exactly as written, one per line.",
}


def _norm(s: str) -> str:
    s = s.replace("—", "-").replace("–", "-").replace("’", "'").lower()
    return re.sub(r"[^a-z0-9']+", " ", s).strip()


def key(s: str, chars: int = 20) -> str:
    """The longest whole-word prefix of ``s`` within ``chars`` characters (normalized).

    Long rows are ellipsized on screen ("Local AI prototype with voice and
    ph..."), and the dump holds the full text, so only the start must be read.
    """

    out = ""
    for word in _norm(s).split():
        if out and len(out) + 1 + len(word) > chars:
            break
        out = f"{out} {word}".strip()
    return out


def expected(name: str) -> list[str]:
    """Visible strings of the screen, from its uiautomator dump (sentences for long text)."""

    xml = open(os.path.join(os.environ["ROSE_SHOTS"], f"{name}.xml"), encoding="utf-8").read()
    out: list[str] = []
    for t in re.findall(r' text="([^"]*)"', xml):
        t = t.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
        for part in re.split(r"(?<=[.!?])\s+", t):
            if part and _norm(part) not in {_norm(o) for o in out}:
                out.append(part)
    return out


def read_screen(brain: Brain, image, prompt: str, side: int) -> tuple[str, dict]:
    seq = brain.sequence("bench")
    drain(seq.append_text(brain.system_prefix(SYSTEM) + user_head("This is my phone screen:\n")))
    drain(seq.append_image(image, side))
    stats = seq.last_image
    drain(seq.append_text(f"\n{prompt}{IM_END}\n{GEN_FAST}"))
    toks = list(seq.generate(Sampling(temperature=0.0, max_tokens=400)))
    return brain.decode(toks), stats


def bench_chunks(brain: Brain, chunks: list[int]) -> None:
    image = shot("com.anthropic.claude")
    for chunk in chunks:
        times = []
        for _ in range(3):
            seq = brain.sequence("chunk")
            drain(seq.append_text(brain.system_prefix(SYSTEM) + user_head("Screen:\n")))
            drain(seq.append_image(image, chunk=chunk))
            st = seq.last_image
            n_pre = -(-(st["tokens"] + 2) // chunk)
            times.extend(st["unit_ms"][-n_pre:][: max(1, (st["tokens"] + 2) // chunk)])  # full chunks
        print(f"chunk {chunk:4d}: prefill unit median {statistics.median(times):6.1f} ms, "
              f"max {max(times):6.1f} ms ({chunk / statistics.median(times) * 1e3:.0f} tok/s)", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sides", default="640,768,896,1024,1280")
    ap.add_argument("--chunks", default="")
    ap.add_argument("--show", action="store_true", help="print what the model read")
    ap.add_argument("--screens", default=",".join(PROMPTS))
    args = ap.parse_args()
    with gpu_lock():
        brain = Brain()
        if args.chunks:
            bench_chunks(brain, [int(c) for c in args.chunks.split(",")])
        screens = {name: PROMPTS[name] for name in args.screens.split(",")}
        shots = {name: shot(name) for name in screens}
        wants = {name: expected(name) for name in screens}
        for side in [int(s) for s in args.sides.split(",") if s]:
            row = {}
            for name, prompt in screens.items():
                read_screen(brain, shots[name], prompt, side)  # warm-up for this shape
                t = time.perf_counter()
                text, st = read_screen(brain, shots[name], prompt, side)
                got = _norm(text)
                missing = [w for w in wants[name] if f" {key(w)} " not in f" {got} "]
                row[name] = (st, missing, time.perf_counter() - t)
                if args.show:
                    print(f"--- {side} {name}\n{text}", flush=True)
            st = next(iter(row.values()))[0]
            print(f"side {side}: {st['size'][0]}x{st['size'][1]} -> {st['tokens']} tokens; "
                  f"preprocess {st['preprocess_ms']:.0f} ms; encode {st['encode_ms']:.0f} ms "
                  f"(max unit {st['encode_max_ms']:.0f} ms); prefill {st['prefill_ms']:.0f} ms "
                  f"(max unit {st['prefill_max_ms']:.0f} ms); "
                  f"max unit {st['max_unit_ms']:.0f} ms", flush=True)
            for name, (_, missing, _) in row.items():
                n = len(wants[name])
                print(f"   {name}: read {n - len(missing)}/{n}" + (f"; missing {missing}" if missing else ""),
                      flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
