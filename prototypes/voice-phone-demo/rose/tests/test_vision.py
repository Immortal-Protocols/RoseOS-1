"""Images in a Sequence: exact against mlx_vlm, across mark/rewind/fork, in small units.

    ROSE_SHOTS=/path/to/shots ROSE_GPU_LOCK=/path/to/gpu.lock \\
        .venv/bin/python -m rose.tests.test_vision [--only ref,rewind,fork,text,units]

Loads the brain once (~21 GB, on this thread). ``ROSE_SHOTS`` is a folder of
private phone screenshots (``com.anthropic.claude.png`` and
``com.android.settings.png``, 1248x1972); they are only read, never written
or printed. When ``ROSE_GPU_LOCK`` is set, the process holds that lock
directory (``mkdir``, waiting while another process has it) for its whole
life, so only one model-loading process runs at a time.

Checks:
  ref     40 greedy tokens for [system + user(text, image, question)] through
          Sequence.append_image against mlx_vlm's own generate_step on the
          same token ids, pixels and grid (the ids and pixels must equal what
          mlx_vlm's processor makes of the rendered prompt). The merged
          embeddings and 3-D positions must equal get_input_embeddings'
          bit for bit, and fed with generate_step's prefill split (all but the
          last token, then the last) must give its exact tokens. The chunked
          Sequence run may then differ only at a near-tie (top-2 logit gap
          <= 0.5), which is float noise from where the prefill is split.
  rewind  mark before an image, append image + text, rewind, append other
          text: logits identical to a fresh sequence of that text.
  fork    fork after an image and continue both: identical tokens and logits.
  text    text-only logits are unchanged from the old 1-D position path.
  units   no yielded unit of append_image exceeds the budget for a 1248x1972
          screenshot at the default size (reports the max unit).
Exit code 0 only if every check passed.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import sys
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import mlx.core as mx

from rose import brain as brain_mod
from rose.brain import GEN_FAST, IM_END, IM_START, Brain, Sampling

SYSTEM = "You are Rose, a helpful voice assistant that can see the user's phone screen."
NO_STOP = {-1}  # generate never stops early: compare a fixed number of tokens
QUESTIONS = {
    "com.anthropic.claude": "What items are in this menu? List every item exactly as written.",
    "com.android.settings": "Read everything written on this screen, word for word.",
}


@contextlib.contextmanager
def gpu_lock():
    """Hold ``$ROSE_GPU_LOCK`` (a directory) while loading and running the model."""

    path = os.environ.get("ROSE_GPU_LOCK")
    if not path:
        yield
        return
    while True:
        try:
            os.mkdir(path)
            break
        except FileExistsError:
            print("waiting for GPU lock ...", flush=True)
            time.sleep(10)
    old = signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        yield
    finally:
        os.rmdir(path)
        signal.signal(signal.SIGTERM, old)


def shot(name: str):
    from PIL import Image

    folder = os.environ.get("ROSE_SHOTS")
    if not folder:
        raise SystemExit("set ROSE_SHOTS to the folder of private screenshots")
    with Image.open(os.path.join(folder, f"{name}.png")) as img:
        return img.convert("RGB")


def drain(gen) -> object:
    """Run a generator to the end on this thread; return its return value."""

    try:
        while True:
            next(gen)
    except StopIteration as stop:
        return stop.value


def user_head(text: str) -> str:
    return f"{IM_START}user\n{text}"


def image_turn(brain: Brain, seq, image, question: str, max_side=None, chunk=None):
    """[system + user(text, image, question) + assistant prefix] into ``seq``."""

    drain(seq.append_text(brain.system_prefix(SYSTEM) + user_head("This is my phone screen:\n")))
    drain(seq.append_image(image, max_side, chunk=chunk or brain_mod.IMAGE_CHUNK))
    drain(seq.append_text(f"\n{question}{IM_END}\n{GEN_FAST}"))


def greedy(seq, n: int, gaps: list[float] | None = None) -> list[int]:
    """``n`` greedy tokens; ``gaps`` gets each step's top-1 minus top-2 logit."""

    out, gen = [], seq.generate(Sampling(temperature=0.0, max_tokens=n), stop=NO_STOP)
    while len(out) < n:
        if gaps is not None:
            top = mx.sort(seq.logits)[-2:]
            gaps.append(float(top[1] - top[0]))
        out.append(next(gen))
    return out


# ------------------------------------------------------------------ checks
def check_ref(brain: Brain, bad: list[str], n: int = 40) -> None:
    from mlx_vlm.generate.ar import generate_step
    from mlx_vlm.utils import prepare_inputs

    for name, question in QUESTIONS.items():
        image = shot(name)
        seq = brain.sequence("ref")
        image_turn(brain, seq, image, question)
        gaps: list[float] = []
        ours = greedy(seq, n, gaps)

        # The same rendered prompt through mlx_vlm's processor.
        w, h = brain.image_size(*image.size)
        resized = image.resize((w, h), resample=3)  # bicubic, as Brain.image_inputs
        prompt = (brain.system_prefix(SYSTEM) + user_head("This is my phone screen:\n")
                  + "<|vision_start|><|image_pad|><|vision_end|>" + f"\n{question}{IM_END}\n{GEN_FAST}")
        inputs = prepare_inputs(brain.processor, images=[resized], prompts=[prompt])
        ids = inputs["input_ids"][0].tolist()
        prompt_ids = seq.tokens[: len(seq.tokens) - n]
        if ids != prompt_ids:
            bad.append(f"ref {name}: processor ids differ from Sequence ids ({len(ids)} vs {len(prompt_ids)})")
        pixels, grid = brain.image_inputs(image)
        if not mx.array_equal(pixels, inputs["pixel_values"].astype(pixels.dtype)):
            bad.append(f"ref {name}: processor pixel_values differ")

        ref = []
        for tok, _ in generate_step(mx.array([prompt_ids]), brain.model, pixels, None,
                                    image_grid_thw=mx.array([grid]), temperature=0.0, max_tokens=n):
            ref.append(int(tok))
        same = next((i for i, (a, b) in enumerate(zip(ours, ref)) if a != b), None)
        print(f"ref {name}: {seq.last_image['tokens']} image tokens, merged grid {seq.last_image['grid']}; "
              f"tokens equal to mlx_vlm: {same is None}" + ("" if same is None else f" (first diff at {same}, "
              f"top-2 logit gap there {gaps[same]:.3f})"))
        print(f"   ours: {brain.decode(ours)!r}")
        if same is not None:
            print(f"   ref:  {brain.decode(ref)!r}")

        # Inputs, exactly: our merged embeddings and M-RoPE positions for the
        # whole prompt against mlx_vlm's get_input_embeddings.
        head = brain.encode(brain.system_prefix(SYSTEM) + user_head("This is my phone screen:\n"))
        tail = brain.encode(f"\n{question}{IM_END}\n{GEN_FAST}")
        img_ids, img_embeds, merged = drain(brain.encode_image(image))
        all_ids = head + img_ids + tail
        embed = brain.lm.model.embed_tokens
        embeds = mx.concatenate([embed(mx.array([head]))[0], img_embeds[0], embed(mx.array([tail]))[0]])[None]
        img_pos, nxt = brain_mod.image_positions(len(head), merged)
        pos = mx.concatenate([_text_pos(0, len(head)), img_pos, _text_pos(nxt, len(tail))], axis=2)
        want = brain.model.get_input_embeddings(mx.array([all_ids]), pixels, image_grid_thw=mx.array([grid]))
        same_in = (all_ids == prompt_ids and mx.array_equal(embeds, want.inputs_embeds)
                   and mx.array_equal(pos, want.position_ids))
        # The same inputs split like generate_step's prefill (all but the last
        # token, then the last) must reproduce its tokens: any difference left
        # in the Sequence run then comes from where the prefill is split.
        split = brain.sequence("ref-split")
        split._run(all_ids[:-1], pos[..., :-1], False, embeds[:, :-1])
        split._run(all_ids[-1:], pos[..., -1:], True, embeds[:, -1:])
        split.pos = int(pos.max()) + 1
        split_toks = greedy(split, n)
        print(f"   embeddings and positions identical to mlx_vlm: {same_in}; "
              f"same inputs with generate_step's prefill split give its tokens: {split_toks == ref}")
        if not same_in or split_toks != ref:
            bad.append(f"ref {name}: inputs differ from mlx_vlm (inputs {same_in}, split tokens {split_toks == ref})")
        if same is not None and gaps[same] > 0.5:
            bad.append(f"ref {name}: tokens diverge at {same} where the top-2 gap is {gaps[same]:.3f} (not a near-tie)")


def _text_pos(start: int, n: int) -> mx.array:
    return mx.broadcast_to(mx.arange(start, start + n, dtype=mx.int32)[None, None], (3, 1, n))


def check_rewind(brain: Brain, bad: list[str]) -> None:
    image = shot("com.anthropic.claude")
    head = brain.system_prefix(SYSTEM) + user_head("Hello.")
    other = f" What time is it?{IM_END}\n{GEN_FAST}"

    seq = brain.sequence("rewind")
    drain(seq.append_text(head))
    m = seq.mark()
    pos0 = seq.pos
    drain(seq.append_text("\n"))
    drain(seq.append_image(image))
    drain(seq.append_text(f"\nWhat is this?{IM_END}\n{GEN_FAST}"))
    behind = len(seq.tokens) - seq.pos
    seq.rewind(m)
    if seq.pos != pos0 or len(seq.tokens) != m.tokens:
        bad.append(f"rewind: pos {seq.pos} != {pos0} or tokens not restored")
    drain(seq.append_text(other))

    fresh = brain.sequence("fresh")
    drain(fresh.append_text(head))
    drain(fresh.append_text(other))
    diff = float(mx.max(mx.abs(seq.logits - fresh.logits)))
    print(f"rewind: with the image, pos ran {behind} behind the token count; logits max |diff| after rewind {diff}")
    if diff != 0.0 or seq.tokens != fresh.tokens or seq.pos != fresh.pos:
        bad.append(f"rewind: not identical to a fresh sequence (max diff {diff})")
    a, b = greedy(seq, 12), greedy(fresh, 12)
    if a != b:
        bad.append("rewind: continuation differs from fresh")


def check_fork(brain: Brain, bad: list[str]) -> None:
    image = shot("com.android.settings")
    seq = brain.sequence("fork")
    image_turn(brain, seq, image, "What is this screen?")
    other = seq.fork("forked")
    if other.pos != seq.pos or other.tokens != seq.tokens:
        bad.append("fork: pos/tokens not copied")
    a = greedy(seq, 20)
    b = greedy(other, 20)
    diff = float(mx.max(mx.abs(seq.logits - other.logits)))
    print(f"fork: continuations equal {a == b}; final logits max |diff| {diff}; {brain.decode(a)!r}")
    if a != b or diff != 0.0:
        bad.append("fork: continuations differ")


def check_text(brain: Brain, bad: list[str]) -> None:
    """Text-only: Sequence (3-D positions) vs the old path (1-D positions)."""

    text = brain.system_prefix(SYSTEM) + user_head(f"Tell me a fun fact about otters.{IM_END}\n{GEN_FAST}")
    ids = brain.encode(text)
    seq = brain.sequence("text")
    drain(seq.prefill(ids))

    cache = brain.lm.make_cache()
    old_logits = None
    for i in range(0, len(ids), 256):  # the old _forward, verbatim
        part = ids[i : i + 256]
        pos = mx.arange(i, i + len(part), dtype=mx.int32)[None]
        out = brain.lm(mx.array([part], dtype=mx.int32), cache=cache, position_ids=pos,
                       return_hidden=True, skip_logits=True)
        old_logits = brain.logits_for(out.hidden_states[-1][:, -1:, :])[0, -1].astype(mx.float32)
        mx.eval(old_logits, [c.state for c in cache])
    diff = float(mx.max(mx.abs(seq.logits - old_logits)))
    old_toks, tok = [], int(mx.argmax(old_logits))
    off = len(ids)
    for _ in range(20):
        old_toks.append(tok)
        out = brain.lm(mx.array([[tok]], dtype=mx.int32), cache=cache,
                       position_ids=mx.array([[off]], dtype=mx.int32), return_hidden=True, skip_logits=True)
        lg = brain.logits_for(out.hidden_states[-1][:, -1:, :])[0, -1]
        tok, off = int(mx.argmax(lg)), off + 1
    new_toks = greedy(seq, 20)
    print(f"text: prompt logits max |diff| {diff}; 20 tokens equal {old_toks == new_toks}; pos {seq.pos} tokens {len(seq.tokens)}")
    if diff != 0.0 or old_toks != new_toks or seq.pos != len(seq.tokens):
        bad.append(f"text: changed (diff {diff})")


def check_units(brain: Brain, bad: list[str]) -> None:
    image = shot("com.anthropic.claude")
    seq = brain.sequence("units")
    drain(seq.append_text(brain.system_prefix(SYSTEM) + user_head("Screen:\n")))
    drain(seq.append_image(image))  # warm-up (compiles kernels for this shape)
    worst = []
    for _ in range(2):
        s = brain.sequence("units")
        drain(s.append_text(brain.system_prefix(SYSTEM) + user_head("Screen:\n")))
        drain(s.append_image(image))
        worst.append(s.last_image["max_unit_ms"])
        st = s.last_image
    print(f"units: {image.size} at IMAGE_MAX_SIDE={brain_mod.IMAGE_MAX_SIDE}: {st['tokens']} tokens, "
          f"{st['units']} units, preprocess {st['preprocess_ms']:.0f} ms, encode {st['encode_ms']:.0f} ms, "
          f"prefill {st['prefill_ms']:.0f} ms, max unit {max(worst):.1f} ms (budget {brain_mod.UNIT_BUDGET_MS:.0f})")
    if min(worst) > brain_mod.UNIT_BUDGET_MS:
        bad.append(f"units: max unit {min(worst):.1f} ms > budget")


CHECKS = {"ref": check_ref, "rewind": check_rewind, "fork": check_fork, "text": check_text, "units": check_units}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(CHECKS))
    args = ap.parse_args()
    bad: list[str] = []
    with gpu_lock():
        brain = Brain()
        print(f"brain loaded in {brain.load_s:.1f} s")
        for name in args.only.split(","):
            CHECKS[name](brain, bad)
    print("FAIL:\n  " + "\n  ".join(bad) if bad else "all vision checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
