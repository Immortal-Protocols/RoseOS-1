"""The Rose brain interface (PR288 rose/brain.py: Brain and Sequence) on a vLLM server, for a Linux GPU host.

PR288's conductor and task manager drive the brain through a small token-level interface: ``encode``, ``decode``,
``system_prefix``, ``sequence()``, and per Sequence ``mark``/``rewind``/``fork``, ``prefill``/``append_text`` and
``generate`` (one token id per ``next()``, the stop token included). On the Mac that is an MLX model with
rewindable caches. Here a Sequence keeps the same token list on the client and sends it as a token-id prompt to
vLLM's OpenAI-compatible ``/v1/completions`` with ``stream`` and ``return_token_ids``, so:

- marks, rewinds and forks are exact: they are operations on the token list, and the next request carries exactly
  the tokens the Mac's cache would hold. vLLM's prefix caching only makes the resend cheap;
- prefill is deferred: no server work happens until ``generate``, which pays for the whole prompt (the same tokens,
  different latency from the Mac's eager prefill);
- the request runs on a worker thread (pump.Pump). ``generate`` waits for each token with coop.Waiter, so on PR288's
  runtime thread higher-priority jobs (listening, speaking) keep running during delayed headers or stalls, and the
  consumer still receives only token ids;
- ``generate`` yields one server-sampled token per ``next()`` and appends it only when it is yielded. Closing the
  generator (barge-in, a caller's ``break``), the ``cancel`` callable or cancelling the runtime job shuts the socket
  down, which makes vLLM abort the request; tokens the server produced after that are never added;
- sampling maps onto vLLM's parameters (temperature, top_p, top_k, presence_penalty, max_tokens, stop_token_ids);
  the server samples, so outputs are not bit-identical to the Mac's MLX sampler;
- if the server stops on a token the caller did not ask to stop on (vLLM merges the model's default stop ids into
  every request), generate raises ``Unsupported`` instead of continuing differently from the Mac;
- images (``append_image``, PR288's talker look and phone-agent screenshots): the completions route cannot carry an
  image, so a sequence that holds one is sent to ``/v1/chat/completions`` instead. Its text segments are decoded from
  the client's tokens and joined, in order, with the images, by a raw chat template that adds nothing
  (``RAW_TEMPLATE``: no roles, no generation prompt); each image is ``<|vision_start|><|image_pad|><|vision_end|>``
  plus a PNG of the image resized exactly as PR288's Brain resizes it (long side at most 768 px, both sides on the
  32 px grid), so vLLM's processor expands the pad to the same (h/32)*(w/32) tokens PR288 appends. The server's
  ``prompt_token_ids`` (first chunk) are compared with the client's tokens: ``last_prompt_exact`` records whether
  the decode/re-tokenize round trip and the image expansion reproduced them, and ``strict_prompt`` makes a mismatch
  an error. Vision needs a server that loads the vision tower (NOT ``--language-model-only``) and admits enough
  images per prompt (``--limit-mm-per-prompt``); with ``vision=False`` images stay ``Unsupported``.

The tokenizer must be the selected model's own (``load_tokenizer`` with its local files); nothing here falls back
to another model or tokenizer.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import time
import urllib.parse
from dataclasses import dataclass
from functools import partial
from typing import Any, Iterator

from .coop import Waiter
from .pump import END, Pump

# PR288 rose/brain.py: screenshots are resized so the long side is at most IMAGE_MAX_SIDE, both sides on the vision
# grid (patch 16 x spatial merge 2 = 32 px); an image is <|vision_start|> + (h/32)*(w/32) <|image_pad|>
# + <|vision_end|>.
IMAGE_MAX_SIDE, IMAGE_UNIT = 768, 32
MAX_INPUT_PIXELS = 50_000_000  # refuse anything larger before resizing (a 12k x 4k screenshot is already absurd)
MAX_PNG_BYTES = 8 << 20
MAX_IMAGES = 16  # per request; the server's --limit-mm-per-prompt must admit at least this many
VISION_TOKENS = ('<|vision_start|>', '<|image_pad|>', '<|vision_end|>')
# The chat template for the image route: content parts in order, text verbatim, each image one placeholder; no roles
# and no generation prompt, because the client's text already holds them. The server must render content as a list
# (vLLM --chat-template-content-format openai; a template that loops over content is detected as such).
RAW_TEMPLATE = ("{%- for m in messages -%}{%- for p in m['content'] -%}"
                "{%- if p['type'] == 'text' -%}{{ p['text'] }}"
                "{%- else -%}<|vision_start|><|image_pad|><|vision_end|>{%- endif -%}"
                "{%- endfor -%}{%- endfor -%}")

IM_START, IM_END = '<|im_start|>', '<|im_end|>'
MAX_LINE = 1 << 20  # one server-sent event line; anything longer is refused


class Unsupported(RuntimeError):
    """A PR288 brain feature this adapter does not provide, raised instead of a silent approximation."""


class ServerError(RuntimeError):
    """The vLLM server refused or broke a request. The message holds only fixed text and the HTTP status."""


@dataclass
class Mark:
    tokens: int
    state: list[Any]
    logits: Any
    pos: int


def load_tokenizer(model_dir: str):
    """The selected model's tokenizer and chat template from local files (transformers, never the network)."""
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False)
    template = os.path.join(model_dir, 'chat_template.jinja')
    return tok, (open(template).read() if os.path.exists(template) else None)


class VllmBrain:
    """Brain on a vLLM server: ``base_url`` like http://127.0.0.1:8000, ``model`` the served model name."""

    def __init__(self, tokenizer, base_url: str, model: str, template: str | None = None,
                 timeout_s: float = 60.0, runtime: Any = None, max_events: int = 256, vision: bool = True,
                 strict_prompt: bool = False, session_id: str | None = None, request_template: bool = False):
        t = time.perf_counter()
        self.tok = tokenizer
        self.template = template
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.timeout_s = timeout_s
        url = urllib.parse.urlsplit(self.base_url)
        if url.scheme not in ('http', 'https') or not url.hostname:
            raise ValueError('base_url must be http(s)://host[:port]')
        self._url = url
        self.waiter = Waiter(runtime)  # waits step only higher-priority PR288 jobs (coop.py)
        self.max_events = max_events
        self.im_end = self._single(IM_END)
        self.eos = {self.im_end, self._single('<|endoftext|>')}
        self.tool_call_start = self._single('<tool_call>')
        self.vision_start = self.vision_end = self.image_pad = None
        self.merge = 2
        self.vision = False
        if vision:
            self.vision_start, self.image_pad, self.vision_end = (self._single(t) for t in VISION_TOKENS)
            self.vision = True
        self.strict_prompt = strict_prompt
        # False (default): the server was started with --chat-template set to RAW_TEMPLATE (write_raw_template), so
        # requests carry none. True sends it per request, which vLLM main refuses unless the server runs with
        # --trust-request-chat-template (default off); keep that switch off.
        self.request_template = request_template
        self.session_id = session_id  # vLLM's stable session field: one id per Rose session, if the caller has one
        self.load_s = time.perf_counter() - t

    def _single(self, text: str) -> int:
        ids = self.tok.encode(text, add_special_tokens=False)
        if len(ids) != 1:
            raise Unsupported(f'tokenizer does not have {text} as one token: not the selected model')
        return ids[0]

    def encode(self, text: str) -> list[int]:
        return list(self.tok.encode(text, add_special_tokens=False))

    def decode(self, ids: list[int]) -> str:
        return self.tok.decode(ids, skip_special_tokens=False)

    def system_prefix(self, system: str, tools: list[dict] | None = None) -> str:
        """Tools block + system prompt exactly as the chat template renders them (as PR288's Brain does)."""
        rendered = self.tok.apply_chat_template(
            [{'role': 'system', 'content': system}, {'role': 'user', 'content': 'x'}],
            tools=tools or None, chat_template=self.template, tokenize=False,
            add_generation_prompt=False, enable_thinking=False, preserve_thinking=True)
        return rendered[: rendered.index(f'{IM_START}user')]

    def image_size(self, width: int, height: int, max_side: int | None = None) -> tuple[int, int]:
        """PR288's Brain.image_size: the long side scaled down (never up) to ``max_side``, both sides rounded to the
        32 px grid, never past ``max_side``."""
        if not self.vision:
            raise Unsupported('vision is off for this brain (language-model-only server)')
        max_side = max_side or IMAGE_MAX_SIDE
        unit = IMAGE_UNIT
        scale = min(1.0, max_side / max(width, height))
        cap = max(unit, max_side // unit * unit)

        def fit(n: int) -> int:
            return min(cap, max(unit, round(n * scale / unit) * unit))
        return fit(width), fit(height)

    def image_tokens(self, width: int, height: int, max_side: int | None = None) -> int:
        w, h = self.image_size(width, height, max_side)
        return (w // IMAGE_UNIT) * (h // IMAGE_UNIT)

    def prepare_image(self, image, max_side: int | None = None) -> tuple[bytes, tuple[int, int], int]:
        """(PNG bytes, (width, height), pad tokens) for a PIL image, resized like PR288 (bicubic). Refuses what is not
        an image, an empty one, or one past MAX_INPUT_PIXELS, before any resize."""
        size = getattr(image, 'size', None)
        if not (isinstance(size, tuple) and len(size) == 2 and all(isinstance(v, int) for v in size)
                and hasattr(image, 'convert')):
            raise ValueError('not an image')
        w0, h0 = size
        if w0 <= 0 or h0 <= 0:
            raise ValueError('empty image')
        if w0 * h0 > MAX_INPUT_PIXELS:
            raise ValueError('image too large')
        import io
        from PIL import Image

        rgb = image.convert('RGB')
        w, h = self.image_size(w0, h0, max_side)
        if (w, h) != rgb.size:
            rgb = rgb.resize((w, h), resample=Image.BICUBIC)
        buf = io.BytesIO()
        rgb.save(buf, format='PNG')
        data = buf.getvalue()
        if len(data) > MAX_PNG_BYTES:
            raise ValueError('encoded image too large')
        return data, (w, h), (w // IMAGE_UNIT) * (h // IMAGE_UNIT)

    def sequence(self, name: str = 'seq') -> 'VllmSequence':
        return VllmSequence(self, name)

    def stream(self, body: dict, path: str = '/v1/completions') -> Pump:
        """Start POST ``path`` (stream) on a worker thread; events arrive through the returned Pump.

        The worker owns the socket: delayed headers and stalled reads wait there, not on the runtime thread.
        ``Pump.close`` shuts the socket down, which ends a blocked read (and makes vLLM abort the request)."""
        data = json.dumps(body).encode()
        url = self._url

        def produce(pump: Pump) -> None:
            cls = http.client.HTTPSConnection if url.scheme == 'https' else http.client.HTTPConnection
            conn = cls(url.hostname, url.port, timeout=self.timeout_s)
            try:
                try:
                    conn.connect()
                    # The response takes the socket over (conn.sock becomes None), so keep it for close().
                    pump.on_stop.append(partial(_shutdown, conn.sock))
                    if pump.stopped.is_set():
                        return
                    conn.request('POST', (url.path or '') + path, body=data,
                                 headers={'Content-Type': 'application/json', 'Accept': 'text/event-stream'})
                    resp = conn.getresponse()
                except (OSError, http.client.HTTPException) as e:
                    raise ServerError(f'completions unreachable: {type(e).__name__}') from None
                if resp.status != 200:
                    raise ServerError(f'completions refused: HTTP {resp.status}')
                while not pump.stopped.is_set():
                    try:
                        line = resp.readline(MAX_LINE + 1)
                    except (OSError, http.client.HTTPException) as e:
                        raise ServerError(f'completions stream broke: {type(e).__name__}') from None
                    if not line:
                        raise ServerError('completions stream ended without [DONE]')
                    if len(line) > MAX_LINE:
                        raise ServerError('completions event too long')
                    line = line.strip()
                    if not line.startswith(b'data:'):
                        continue
                    payload = line[5:].strip()
                    if payload == b'[DONE]':
                        return
                    try:
                        event = json.loads(payload)
                    except ValueError:
                        raise ServerError('completions event is not JSON') from None
                    if not isinstance(event, dict):
                        raise ServerError('completions event is not an object')
                    if 'error' in event:
                        raise ServerError('completions stream reported an error')
                    if not pump.put(event):
                        return
            finally:
                conn.close()

        return Pump(self.waiter, produce, maxsize=self.max_events, name='vllm-stream')


def _shutdown(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)  # wakes a read blocked in the worker
    except OSError:
        pass


def write_raw_template(path) -> None:
    """Write RAW_TEMPLATE for vLLM's --chat-template (the server-side way to use the image route)."""
    with open(path, 'w') as f:
        f.write(RAW_TEMPLATE)


def _token_ids(choice: dict) -> list[int]:
    ids = choice.get('token_ids')
    if ids is None:  # older vLLM: logprobs with return_tokens_as_token_ids ("token_id:123")
        lp = choice.get('logprobs') or {}
        tokens = lp.get('tokens') or [c.get('token') for c in lp.get('content') or [] if isinstance(c, dict)]
        try:
            ids = [int(t.split(':', 1)[1]) for t in tokens if isinstance(t, str) and t.startswith('token_id:')]
        except ValueError:
            raise ServerError('completions token ids are malformed') from None
        if len(ids) != len(tokens):
            raise ServerError('completions chunk has tokens without ids')
    if not isinstance(ids, list) or not all(isinstance(i, int) and i >= 0 for i in ids):
        raise ServerError('completions token ids are malformed')
    return ids


class VllmSequence:
    """One conversation's token list. Like PR288's Sequence it is used from one thread."""

    def __init__(self, brain: VllmBrain, name: str):
        self.brain = brain
        self.name = name
        self.tokens: list[int] = []
        self.pos = 0  # == len(tokens) (vLLM computes the M-RoPE positions of images itself)
        self.images: list[tuple[int, int, bytes]] = []  # (index of <|vision_start|>, pad tokens, PNG)
        self.last_prompt_exact: bool | None = None  # chat route: did vLLM's prompt tokens equal ours
        self.logits = None
        self.prefill_tokens = 0
        self.decode_tokens = 0
        self.image_tokens = 0
        self.last_image: dict | None = None
        self.requests = 0  # completions requests made (for the smoke and tests)
        self.worker_stopped: bool | None = None  # whether the last request's worker thread was joined

    def mark(self) -> Mark:
        return Mark(len(self.tokens), [], None, self.pos)

    def rewind(self, mark: Mark) -> None:
        if mark.tokens > len(self.tokens):
            raise ValueError('mark is ahead of this sequence')
        del self.tokens[mark.tokens:]
        self.pos = mark.pos
        self.images = [img for img in self.images if img[0] < mark.tokens]  # a rewound image is never sent

    def fork(self, name: str) -> 'VllmSequence':
        other = VllmSequence(self.brain, name)
        other.tokens = list(self.tokens)
        other.pos = self.pos
        other.image_tokens = self.image_tokens
        other.images = list(self.images)
        return other

    def prefill(self, ids: list[int], chunk: int = 256) -> Iterator[None]:
        for i in range(0, len(ids), chunk):
            part = list(ids[i:i + chunk])
            self.tokens.extend(part)
            self.pos += len(part)
            self.prefill_tokens += len(part)
            yield None

    def append_text(self, text: str, chunk: int = 256) -> Iterator[None]:
        yield from self.prefill(self.brain.encode(text), chunk)

    def append_image(self, image, max_side: int | None = None, want_logits: bool = False, chunk: int = 0,
                     budget_ms: float = 0.0) -> Iterator[None]:
        """PR288's Sequence.append_image: appends <|vision_start|> + pads + <|vision_end|> and returns the pad count.
        Here the only local work is the resize and PNG encode (one unit); the vision encode and prefill happen on the
        server at the next generate, so last_image reports them as 0 ms."""
        brain = self.brain
        if not brain.vision:
            raise Unsupported('vision is off for this brain (language-model-only server)')
        if len(self.images) >= MAX_IMAGES:
            raise Unsupported(f'more than {MAX_IMAGES} images in one sequence')
        t = time.perf_counter()
        png, (w, h), n = brain.prepare_image(image, max_side)
        ms = (time.perf_counter() - t) * 1e3
        yield None
        self.images.append((len(self.tokens), n, png))
        ids = [brain.vision_start] + [brain.image_pad] * n + [brain.vision_end]
        self.tokens.extend(ids)
        self.pos += len(ids)
        self.prefill_tokens += len(ids)
        self.image_tokens += n
        self.last_image = {'tokens': n, 'grid': (h // IMAGE_UNIT, w // IMAGE_UNIT), 'size': (w, h),
                           'preprocess_ms': ms, 'encode_ms': 0.0, 'prefill_ms': 0.0, 'max_unit_ms': ms,
                           'png_bytes': len(png), 'units': 1}
        return n

    def chat_messages(self) -> list[dict]:
        """The one raw user message RAW_TEMPLATE renders back into this sequence: decoded text between images."""
        import base64

        parts, at = [], 0
        for start, n, png in self.images:
            if start > at:
                parts.append({'type': 'text', 'text': self.brain.decode(self.tokens[at:start])})
            parts.append({'type': 'image_url',
                          'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(png).decode()}})
            at = start + n + 2
        if at < len(self.tokens):
            parts.append({'type': 'text', 'text': self.brain.decode(self.tokens[at:])})
        return [{'role': 'user', 'content': parts}]

    def generate(self, sampling, stop: set[int] | None = None, cancel=None) -> Iterator[int]:
        """Server-sampled tokens one by one, each appended as it is yielded; the stop token is yielded too."""
        stop = set(stop or self.brain.eos)
        if not self.tokens:
            raise ValueError('prefill before generate')
        if sampling.max_tokens <= 0:
            return
        body = {
            'model': self.brain.model, 'max_tokens': sampling.max_tokens,
            'temperature': max(0.0, sampling.temperature), 'top_p': sampling.top_p,
            'top_k': sampling.top_k if sampling.top_k and sampling.top_k > 0 else -1,
            'presence_penalty': sampling.presence_penalty, 'stop_token_ids': sorted(stop),
            'skip_special_tokens': False, 'spaces_between_special_tokens': False, 'stream': True,
            'return_token_ids': True, 'return_tokens_as_token_ids': True, 'n': 1,
        }
        expected = None
        if self.images:  # images ride the chat route; see the module docstring
            body.update(messages=self.chat_messages(), add_generation_prompt=False, continue_final_message=False,
                        add_special_tokens=False, logprobs=True, top_logprobs=0)
            if self.brain.request_template:
                body['chat_template'] = RAW_TEMPLATE
            path, expected = '/v1/chat/completions', list(self.tokens)
        else:
            body.update(prompt=list(self.tokens), logprobs=0)
            path = '/v1/completions'
        if self.brain.session_id:
            body['session_id'] = self.brain.session_id
        self.requests += 1
        self.last_prompt_exact = None
        pump = self.brain.stream(body, path)
        produced = 0
        try:
            while True:
                ok, event = pump.get(abort=lambda: cancel is not None and cancel())
                if not ok:
                    return  # cancelled while waiting: nothing more is appended
                if event is END:
                    break
                if expected is not None and isinstance(event.get('prompt_token_ids'), list):
                    self.last_prompt_exact = event['prompt_token_ids'] == expected
                    if not self.last_prompt_exact and self.brain.strict_prompt:
                        raise Unsupported('the server tokenized a different prompt than this sequence holds')
                    expected = None
                choices = event.get('choices') or []
                if not choices:
                    continue
                choice = choices[0]
                for token in _token_ids(choice):
                    if cancel is not None and cancel():
                        return
                    self.tokens.append(token)
                    self.pos += 1
                    self.decode_tokens += 1
                    produced += 1
                    yield token
                    if token in stop:
                        return
                reason = choice.get('finish_reason')
                if reason is None:
                    continue
                if reason == 'length' and produced >= sampling.max_tokens:
                    return
                if reason == 'stop' and isinstance(choice.get('stop_reason'), int):
                    token = choice['stop_reason']
                    if token not in stop:
                        raise Unsupported('server stopped on a token the caller did not stop on')
                    self.tokens.append(token)  # the server stopped on it without sending it
                    self.pos += 1
                    self.decode_tokens += 1
                    yield token
                    return
                raise Unsupported(f'server finished ({reason}) before a requested stop token')
            raise ServerError('completions stream ended without a finish reason')
        finally:
            self.worker_stopped = pump.close()
