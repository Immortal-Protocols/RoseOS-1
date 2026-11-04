"""Qwen3.6-35B-A3B as a token-level engine with persistent, rewindable caches.

All methods run on the runtime's MLX thread. Generators yield after every
bounded unit of GPU work (a prefill chunk or one decoded token), so the
scheduler can interleave the talker, background tasks, ASR and TTS.

The model is hybrid: 10 full-attention layers (KVCache, trimmable by offset)
and 30 Gated DeltaNet layers (ArraysCache, fixed-size state that is replaced,
never mutated in place). A *mark* records KV offsets plus references to the
DeltaNet state arrays, so rewinding to a mark is O(1) and exact.

Prompts are never re-rendered: each Sequence appends token segments built
from the chat template's own formats, so the cache is always valid.

Images (``Sequence.append_image``) go through the model's own vision tower,
one small unit at a time (patch embedding, a few vision blocks, the merger),
and their merged embeddings are prefilled in chunks like text. Qwen3.5 uses
M-RoPE: every token has a 3-D position (t, h, w). Text tokens use the same
value on all three axes; an image's tokens use (base, base + row, base + col),
and the text after it continues at base + max(rows, cols). A Sequence keeps
that next text position in ``pos`` (equal to the token count until the first
image), and marks and forks carry it.
"""

from __future__ import annotations

import glob
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import mlx.core as mx

REPO = os.environ.get("ROSE_BRAIN", "mlx-community/Qwen3.6-35B-A3B-4bit")

IM_START, IM_END = "<|im_start|>", "<|im_end|>"
GEN_FAST = "<|im_start|>assistant\n<think>\n\n</think>\n\n"
GEN_THINK = "<|im_start|>assistant\n<think>\n"

# Screenshots are scaled so their long side is at most this many pixels
# before encoding. Measured on 1248x1972 Fold cover screenshots
# (rose/tests/bench_vision.py, M-series GPU shared with a running server):
#   side   tokens  encode ms  prefill ms  reads every visible item
#    640     260      115        380      no (misreads a half-covered row)
#    768     360      157        525      yes (Claude, Settings, Maps, Spotify)
#    896     504      230        775      yes
#   1024     640      312       1070      yes (skips the half-covered row)
#   1280    1000      610       1720      yes
# 768 reads everything 896+ does at ~2/3 of the cost (~0.7 s per screenshot).
IMAGE_MAX_SIDE = 768
# Each yielded unit of image work aims to stay under this many milliseconds.
UNIT_BUDGET_MS = 60.0
# Image embeddings are prefilled this many tokens per unit: one unit costs
# ~33 ms at 16, ~46 ms at 32, ~72 ms at 48, ~144 ms at 128 (a fixed ~20 ms
# per forward), so 32 is the largest chunk inside the budget.
IMAGE_CHUNK = 32
# Background prefill (phone agent, thinker) runs in the largest of these
# chunks whose forward fits CHUNK_BUDGET_MS on this model, timed once at load,
# so a reply waiting behind one is not held up for long. Measured after 1,024
# cached tokens on an M4 Max: 128 tokens take ~100 ms on the 35B-A3B; on
# Qwen3.8-27B (dense) 32 take ~165 ms and 128 ~635 ms.
CHUNKS = (128, 64, 32, 16)
CHUNK_BUDGET_MS = 180.0


def user_segment(text: str) -> str:
    return f"{IM_START}user\n{text.strip()}{IM_END}\n"


def tool_segment(results: list[str]) -> str:
    body = "".join(f"\n<tool_response>\n{r}\n</tool_response>" for r in results)
    return f"{IM_START}user{body}{IM_END}\n"


@dataclass
class Sampling:
    temperature: float = 0.7
    top_p: float = 0.8
    top_k: int = 20
    presence_penalty: float = 0.0
    max_tokens: int = 400


GREEDY = Sampling(temperature=0.0)


@dataclass
class Mark:
    tokens: int
    state: list[Any]
    logits: Any
    pos: int  # next text position (Sequence.pos)


class Brain:
    def __init__(self, repo: str = REPO):
        from mlx_vlm import load

        t = time.perf_counter()
        self.model, self.processor = load(repo)
        self.lm = self.model.language_model
        self.tok = self.processor.tokenizer
        snap = _snapshot_dir(repo)
        self.template = open(os.path.join(snap, "chat_template.jinja")).read() if snap else None
        self.im_end = self._single("<|im_end|>")
        self.eos = {self.im_end, self._single("<|endoftext|>")}
        self.tool_call_start = self._single("<tool_call>")
        cfg = self.model.config
        self.vision_start = cfg.vision_start_token_id
        self.vision_end = cfg.vision_end_token_id
        self.image_pad = cfg.image_token_id
        self.merge = cfg.vision_config.spatial_merge_size
        self.repo = repo
        self.bg_chunk = self._fit_chunk()
        self.load_s = time.perf_counter() - t

    def _fit_chunk(self) -> int:
        """Largest of CHUNKS whose prefill fits CHUNK_BUDGET_MS (else the smallest)."""

        seq = self.sequence("calibrate")
        ids = self.encode("Rose is a voice assistant on the phone. " * 160)
        for _ in seq.prefill(ids[:1024]):
            pass
        mark = seq.mark()
        seq._forward(ids[1024:1040], want_logits=True)  # warm up the kernels
        mx.eval(seq.logits)
        for n in CHUNKS:
            seq.rewind(mark)
            t = time.perf_counter()
            seq._forward(ids[1024 : 1024 + n], want_logits=True)
            mx.eval(seq.logits)
            if (time.perf_counter() - t) * 1e3 <= CHUNK_BUDGET_MS:
                return n
        return CHUNKS[-1]

    def _single(self, text: str) -> int:
        ids = self.tok.encode(text, add_special_tokens=False)
        assert len(ids) == 1, (text, ids)
        return ids[0]

    def encode(self, text: str) -> list[int]:
        return list(self.tok.encode(text, add_special_tokens=False))

    def decode(self, ids: list[int]) -> str:
        return self.tok.decode(ids, skip_special_tokens=False)

    def system_prefix(self, system: str, tools: list[dict] | None = None) -> str:
        """Tools block + system prompt exactly as the chat template renders them."""

        rendered = self.tok.apply_chat_template(
            [{"role": "system", "content": system}, {"role": "user", "content": "x"}],
            tools=tools or None, chat_template=self.template, tokenize=False,
            add_generation_prompt=False, enable_thinking=False, preserve_thinking=True)
        return rendered[: rendered.index(f"{IM_START}user")]

    # ------------------------------------------------------------ images
    def image_size(self, width: int, height: int, max_side: int | None = None) -> tuple[int, int]:
        """(width, height) an image is resized to before encoding.

        The long side is scaled down (never up) to ``max_side``, then both
        sides are rounded to the vision grid (patch 16 x merge 2 = 32 px),
        never past ``max_side``.
        """

        max_side = max_side or IMAGE_MAX_SIDE
        unit = self.processor.image_processor.patch_size * self.merge
        scale = min(1.0, max_side / max(width, height))
        cap = max(unit, max_side // unit * unit)

        def fit(n: int) -> int:
            return min(cap, max(unit, round(n * scale / unit) * unit))

        return fit(width), fit(height)

    def image_tokens(self, width: int, height: int, max_side: int | None = None) -> int:
        """Number of image pad tokens ``append_image`` adds for this size."""

        w, h = self.image_size(width, height, max_side)
        return int(self.processor.image_processor.num_image_tokens(h, w))

    def image_inputs(self, image, max_side: int | None = None) -> tuple[mx.array, tuple[int, int, int]]:
        """(pixel_values, (t, h, w) patch grid) for a PIL image (CPU only).

        The image is resized here (bicubic, like the processor) to exactly
        ``image_size``, so the processor's own smart resize leaves it as is.
        """

        from PIL import Image

        image = image.convert("RGB")
        size = self.image_size(*image.size, max_side)
        if size != image.size:
            image = image.resize(size, resample=Image.BICUBIC)
        out = self.processor.image_processor([image])
        grid = tuple(int(x) for x in out["image_grid_thw"][0])
        return mx.array(out["pixel_values"]), grid

    def encode_image(self, image, max_side: int | None = None, budget_ms: float = UNIT_BUDGET_MS,
                     units: list[float] | None = None):
        """Generator: the vision tower in small units; returns (ids, embeds, (rows, cols)).

        ``ids`` are ``<|vision_start|>`` + one ``<|image_pad|>`` per merged
        patch + ``<|vision_end|>``; ``embeds`` (1, len(ids), hidden) are their
        input embeddings with the image features in place of the pads (what
        mlx_vlm's get_input_embeddings builds); (rows, cols) is the merged
        patch grid. Runs as mlx_vlm's VisionModel.__call__ for one image (this
        model has no deepstack layers), yielding after the CPU preprocessing,
        the patch embedding, every few blocks (as many as fit ``budget_ms``,
        at least one) and the merger. Each unit's milliseconds go to ``units``.
        """

        vt = self.model.vision_tower
        units = [] if units is None else units
        t = time.perf_counter()
        pixels, (gt, gh, gw) = self.image_inputs(image, max_side)
        units.append((time.perf_counter() - t) * 1e3)
        yield None

        assert not vt.deepstack_visual_indexes, "deepstack vision features are not supported"
        assert gt == 1, "still images have one temporal patch"
        t = time.perf_counter()
        grid_thw = mx.array([[gt, gh, gw]], dtype=mx.int32)
        h = vt.patch_embed(pixels.astype(vt.patch_embed.proj.weight.dtype))
        h = h + vt.fast_pos_embed_interpolate(grid_thw)
        rot = vt.rot_pos_emb(grid_thw)
        n_patch = h.shape[0]
        h, rot = h.reshape(n_patch, -1), rot.reshape(n_patch, -1)
        cu = mx.array([0, n_patch], dtype=mx.int32)  # one image: attention over all patches
        mx.eval(h, rot)
        units.append((time.perf_counter() - t) * 1e3)
        yield None

        i, per = 0, 1
        while i < len(vt.blocks):
            t = time.perf_counter()
            for blk in vt.blocks[i : i + per]:
                h = blk(h, cu_seqlens=cu, rotary_pos_emb=rot)
            mx.eval(h)
            ms = (time.perf_counter() - t) * 1e3
            units.append(ms)
            i += per
            per = max(1, int(budget_ms * 0.8 / max(ms / per, 1e-3)))
            yield None

        t = time.perf_counter()
        rows, cols = gh // self.merge, gw // self.merge
        feats = vt.merger(h)
        assert feats.shape[0] == rows * cols, (feats.shape, gh, gw)
        edge = self.lm.model.embed_tokens(mx.array([self.vision_start, self.vision_end], dtype=mx.int32))
        embeds = mx.concatenate([edge[:1], feats.astype(edge.dtype), edge[1:]])[None]
        mx.eval(embeds)
        units.append((time.perf_counter() - t) * 1e3)
        yield None
        ids = [self.vision_start] + [self.image_pad] * (rows * cols) + [self.vision_end]
        return ids, embeds, (rows, cols)

    def sequence(self, name: str = "seq") -> "Sequence":
        return Sequence(self, name)

    def logits_for(self, hidden: mx.array) -> mx.array:
        if self.lm.args.tie_word_embeddings:
            return self.lm.model.embed_tokens.as_linear(hidden)
        return self.lm.lm_head(hidden)


class Sequence:
    """One conversation's cache. Not thread-safe: use from the MLX thread."""

    def __init__(self, brain: Brain, name: str):
        self.brain = brain
        self.name = name
        self.cache = brain.lm.make_cache()
        self.tokens: list[int] = []
        self.pos = 0  # next text position (M-RoPE); == len(tokens) until an image
        self.logits: mx.array | None = None  # next-token logits after self.tokens
        self.prefill_tokens = 0
        self.decode_tokens = 0
        self.image_tokens = 0  # image pad tokens appended so far
        self.last_image: dict | None = None  # stats of the latest append_image

    # ------------------------------------------------------------ marks
    def mark(self) -> Mark:
        from mlx_vlm.models.cache import ArraysCache

        state = [list(c.cache) if isinstance(c, ArraysCache) else c.offset for c in self.cache]
        return Mark(len(self.tokens), state, self.logits, self.pos)

    def rewind(self, mark: Mark) -> None:
        from mlx_vlm.models.cache import ArraysCache

        for c, s in zip(self.cache, mark.state):
            if isinstance(c, ArraysCache):
                c.cache = list(s)
            else:
                c.offset = s
        del self.tokens[mark.tokens:]
        self.pos = mark.pos
        self.logits = mark.logits

    def fork(self, name: str) -> "Sequence":
        from mlx_vlm.models.cache import ArraysCache, KVCache

        other = Sequence.__new__(Sequence)
        other.brain, other.name = self.brain, name
        other.cache = []
        for c in self.cache:
            if isinstance(c, ArraysCache):
                n = ArraysCache(len(c.cache))
                n.cache = list(c.cache)
            else:
                n = KVCache()
                if c.keys is not None and c.offset:
                    n.keys = mx.contiguous(c.keys[..., : c.offset, :])
                    n.values = mx.contiguous(c.values[..., : c.offset, :])
                    n.offset = c.offset
            other.cache.append(n)
        mx.eval([c.state for c in other.cache if getattr(c, "state", None) is not None])
        other.tokens = list(self.tokens)
        other.pos = self.pos
        other.logits = self.logits
        other.prefill_tokens = other.decode_tokens = 0
        other.image_tokens = self.image_tokens
        other.last_image = None
        return other

    # ------------------------------------------------------------ forward
    def _run(self, ids: list[int], pos: mx.array, want_logits: bool,
             embeds: mx.array | None = None) -> None:
        """One forward of ``ids`` (or their ``embeds``) at 3-D ``pos`` (3, 1, L)."""

        inputs = mx.array([ids], dtype=mx.int32)
        if want_logits:
            out = self.brain.lm(inputs, inputs_embeds=embeds, cache=self.cache, position_ids=pos,
                                return_hidden=True, skip_logits=True)
            hidden = out.hidden_states[-1][:, -1:, :]
            self.logits = self.brain.logits_for(hidden)[0, -1].astype(mx.float32)
            mx.eval(self.logits, [c.state for c in self.cache])
        else:
            self.brain.lm(inputs, inputs_embeds=embeds, cache=self.cache, position_ids=pos,
                          skip_logits=True)
            mx.eval([c.state for c in self.cache])
            self.logits = None
        self.tokens.extend(ids)

    def _forward(self, ids: list[int], want_logits: bool) -> None:
        # Text: the same position on all three M-RoPE axes.
        pos = mx.arange(self.pos, self.pos + len(ids), dtype=mx.int32)
        self._run(ids, mx.broadcast_to(pos[None, None], (3, 1, len(ids))), want_logits)
        self.pos += len(ids)

    def prefill(self, ids: list[int], chunk: int = 256) -> Iterator[None]:
        """Append ``ids``; leaves next-token logits ready. Yields per chunk."""

        if not ids:
            return
        for i in range(0, len(ids), chunk):
            part = ids[i : i + chunk]
            self._forward(part, want_logits=i + chunk >= len(ids))
            self.prefill_tokens += len(part)
            yield None

    def append_image(self, image, max_side: int | None = None, want_logits: bool = False,
                     chunk: int = IMAGE_CHUNK, budget_ms: float = UNIT_BUDGET_MS) -> Iterator[None]:
        """Append ``<|vision_start|>`` + image pads + ``<|vision_end|>`` for a PIL image.

        Call it where the chat format puts an image: inside a user (or tool
        response) turn, between text. Yields after every bounded unit: the
        CPU resize/patchify, the patch embedding, a few vision blocks at a
        time (as many as fit ``budget_ms``, at least one), the merger, and
        each ``chunk`` of merged embeddings prefilled. Next-token logits are
        left ready only with ``want_logits`` (usually text follows). Returns
        the number of image tokens; stats go to ``self.last_image``.
        """

        units: list[float] = []
        ids, embeds, grid = yield from self.brain.encode_image(image, max_side, budget_ms, units)
        n_encode = len(units)
        pos, end = image_positions(self.pos, grid)
        t_pre = time.perf_counter()
        total = len(ids)
        for s in range(0, total, chunk):
            t = time.perf_counter()
            e = min(total, s + chunk)
            self._run(ids[s:e], pos[..., s:e], want_logits and e == total, embeds[:, s:e])
            self.prefill_tokens += e - s
            units.append((time.perf_counter() - t) * 1e3)
            yield None
        n = total - 2
        self.pos = end
        self.image_tokens += n
        patch = self.brain.model.vision_tower.config.patch_size * self.brain.merge
        self.last_image = {
            "tokens": n, "grid": grid, "size": (grid[1] * patch, grid[0] * patch),
            "preprocess_ms": units[0], "encode_ms": sum(units[1:n_encode]),
            "encode_max_ms": max(units[1:n_encode]),
            "prefill_ms": (time.perf_counter() - t_pre) * 1e3, "prefill_max_ms": max(units[n_encode:]),
            "max_unit_ms": max(units), "units": len(units), "unit_ms": units,
        }
        return n

    def append_text(self, text: str, chunk: int = 256) -> Iterator[None]:
        yield from self.prefill(self.brain.encode(text), chunk)

    def generate(self, sampling: Sampling, stop: set[int] | None = None,
                 cancel=None) -> Iterator[int]:
        """Sample tokens one by one (each fed back into the cache).

        Yields every token id, including the stop token (which is also fed,
        so the cache ends with ``<|im_end|>``).
        """

        stop = stop or self.brain.eos
        seen: dict[int, int] = {}
        for _ in range(sampling.max_tokens):
            if cancel is not None and cancel():
                return
            assert self.logits is not None, "prefill before generate"
            token = _sample(self.logits, sampling, seen)
            seen[token] = seen.get(token, 0) + 1
            self._forward([token], want_logits=token not in stop)
            self.decode_tokens += 1
            yield token
            if token in stop:
                return


def image_positions(start: int, grid: tuple[int, int]) -> tuple[mx.array, int]:
    """M-RoPE positions (3, 1, rows*cols + 2) of an image segment, and the next text position.

    As mlx_vlm's get_rope_index: ``<|vision_start|>`` (text) at ``start``,
    the merged patches row by row at base + (0, row, col) with
    base = start + 1, and ``<|vision_end|>`` at base + max(rows, cols).
    """

    rows, cols = grid
    base = start + 1
    r = mx.repeat(mx.arange(rows, dtype=mx.int32), cols)
    c = mx.tile(mx.arange(cols, dtype=mx.int32), rows)
    img = mx.stack([mx.zeros_like(r), r, c]) + base
    end = base + max(rows, cols)
    pos = mx.concatenate([mx.full((3, 1), start, mx.int32), img, mx.full((3, 1), end, mx.int32)], axis=1)
    return pos[:, None, :], end + 1


def _sample(logits: mx.array, s: Sampling, seen: dict[int, int]) -> int:
    if s.presence_penalty and seen:
        idx = mx.array(list(seen.keys()), dtype=mx.int32)
        logits = logits.at[idx].add(-s.presence_penalty)
    if s.temperature <= 0:
        return int(mx.argmax(logits))
    logits = logits / s.temperature
    if s.top_k and s.top_k < logits.shape[-1]:
        kth = mx.sort(mx.argpartition(-logits, s.top_k - 1)[: s.top_k])
        top = logits[kth]
    else:
        kth, top = mx.arange(logits.shape[-1]), logits
    probs = mx.softmax(top, axis=-1)
    if s.top_p < 1.0:
        order = mx.argsort(-probs)
        sorted_p = probs[order]
        keep = (mx.cumsum(sorted_p) - sorted_p) < s.top_p
        probs = mx.zeros_like(probs).at[order].add(mx.where(keep, sorted_p, 0.0))
    choice = mx.random.categorical(mx.log(probs + 1e-12))
    return int(kth[choice])


def _snapshot_dir(repo: str) -> str | None:
    base = os.path.expanduser(f"~/.cache/huggingface/hub/models--{repo.replace('/', '--')}/snapshots/*")
    dirs = sorted(glob.glob(base))
    return dirs[-1] if dirs else None


# ------------------------------------------------------------- tool calls
_CALL = re.compile(r"<tool_call>\s*<function=([^>\s]+)>(.*?)</function>\s*</tool_call>", re.S)
_PARAM = re.compile(r"<parameter=([^>\s]+)>\s*\n?(.*?)\n?\s*</parameter>", re.S)


@dataclass
class ToolCall:
    name: str
    arguments: dict = field(default_factory=dict)


def parse_tool_calls(text: str) -> list[ToolCall]:
    calls = []
    for name, body in _CALL.findall(text):
        args = {k: v.strip() for k, v in _PARAM.findall(body)}
        calls.append(ToolCall(name.strip(), args))
    return calls


class SpeechSplitter:
    """Split streamed assistant text into speech and tool-call text.

    ``feed(piece)`` returns speakable text that is safe to say now (never part
    of a tool call); call text accumulates in ``self.calls_text``.
    """

    def __init__(self):
        self.buffer = ""
        self.in_call = False
        self.calls_text = ""
        self.spoken = ""

    def feed(self, piece: str) -> str:
        self.buffer += piece
        out = ""
        while self.buffer:
            if self.in_call:
                end = self.buffer.find("</tool_call>")
                if end < 0:
                    self.calls_text += self.buffer
                    self.buffer = ""
                    break
                end += len("</tool_call>")
                self.calls_text += self.buffer[:end]
                self.buffer = self.buffer[end:]
                self.in_call = False
                continue
            start = self.buffer.find("<tool_call>")
            if start >= 0:
                out += self.buffer[:start]
                self.calls_text += "<tool_call>"
                self.buffer = self.buffer[start + len("<tool_call>"):]
                self.in_call = True
                continue
            # Hold back a possible partial "<tool_call>" prefix.
            hold = 0
            for k in range(min(len(self.buffer), 10), 0, -1):
                if "<tool_call>".startswith(self.buffer[-k:]):
                    hold = k
                    break
            out += self.buffer[: len(self.buffer) - hold]
            self.buffer = self.buffer[len(self.buffer) - hold:]
            break
        self.spoken += out
        return out
