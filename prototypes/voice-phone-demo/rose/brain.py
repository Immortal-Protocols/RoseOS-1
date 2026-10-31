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
        self.load_s = time.perf_counter() - t

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
        self.logits: mx.array | None = None  # next-token logits after self.tokens
        self.prefill_tokens = 0
        self.decode_tokens = 0

    # ------------------------------------------------------------ marks
    def mark(self) -> Mark:
        from mlx_vlm.models.cache import ArraysCache

        state = [list(c.cache) if isinstance(c, ArraysCache) else c.offset for c in self.cache]
        return Mark(len(self.tokens), state, self.logits)

    def rewind(self, mark: Mark) -> None:
        from mlx_vlm.models.cache import ArraysCache

        for c, s in zip(self.cache, mark.state):
            if isinstance(c, ArraysCache):
                c.cache = list(s)
            else:
                c.offset = s
        del self.tokens[mark.tokens:]
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
        other.logits = self.logits
        other.prefill_tokens = other.decode_tokens = 0
        return other

    # ------------------------------------------------------------ forward
    def _forward(self, ids: list[int], want_logits: bool) -> None:
        off = len(self.tokens)
        inputs = mx.array([ids], dtype=mx.int32)
        pos = mx.arange(off, off + len(ids), dtype=mx.int32)[None]
        if want_logits:
            out = self.brain.lm(inputs, cache=self.cache, position_ids=pos,
                                return_hidden=True, skip_logits=True)
            hidden = out.hidden_states[-1][:, -1:, :]
            self.logits = self.brain.logits_for(hidden)[0, -1].astype(mx.float32)
            mx.eval(self.logits, [c.state for c in self.cache])
        else:
            self.brain.lm(inputs, cache=self.cache, position_ids=pos, skip_logits=True)
            mx.eval([c.state for c in self.cache])
            self.logits = None
        self.tokens.extend(ids)

    def prefill(self, ids: list[int], chunk: int = 256) -> Iterator[None]:
        """Append ``ids``; leaves next-token logits ready. Yields per chunk."""

        if not ids:
            return
        for i in range(0, len(ids), chunk):
            part = ids[i : i + chunk]
            self._forward(part, want_logits=i + chunk >= len(ids))
            self.prefill_tokens += len(part)
            yield None

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
