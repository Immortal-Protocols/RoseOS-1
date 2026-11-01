"""Fixtures for the vLLM brain tests: a stand-in tokenizer and a stand-in vLLM completions server.

Neither is the real thing. The tokenizer only knows Qwen's special tokens and single characters; the server
replays scripted token ids in vLLM's streaming completions format (``token_ids`` per chunk, or the older
``logprobs.tokens`` "token_id:N" form) and honours ``stop_token_ids`` and ``max_tokens``. They test the adapter's
handling of the protocol, not a model.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SPECIALS = ['<|im_start|>', '<|im_end|>', '<|endoftext|>', '<tool_call>', '</tool_call>', '<think>', '</think>',
            '<|vision_start|>', '<|image_pad|>', '<|vision_end|>']


class CharTokenizer:
    def __init__(self, specials=SPECIALS):
        self.specials = list(specials)

    def encode(self, text, add_special_tokens=False):
        ids, i = [], 0
        while i < len(text):
            for n, s in enumerate(self.specials):
                if text.startswith(s, i):
                    ids.append(n)
                    i += len(s)
                    break
            else:
                ids.append(100 + ord(text[i]))
                i += 1
        return ids

    def decode(self, ids, skip_special_tokens=False):
        return ''.join(self.specials[i] if i < 100 else chr(i - 100) for i in ids)

    def apply_chat_template(self, messages, tools=None, chat_template=None, tokenize=False,
                            add_generation_prompt=False, **_):
        head = f"# Tools\n{json.dumps(tools)}\n" if tools else ''
        return ''.join(f"<|im_start|>{m['role']}\n{head if m['role'] == 'system' else ''}{m['content']}<|im_end|>\n"
                       for m in messages)


class FakeVllm:
    """Scripted completions server on 127.0.0.1. ``script`` is a list of token-id lists, one per request."""

    def __init__(self, script=(), mode='ids', chunk=1, delay_s=0.0, status=200, send_stop=True, raw=None,
                 header_delay_s=0.0, stall_at=None, stall_s=0.0, prompt_ids=None):
        self.script = [list(s) for s in script]
        self.mode, self.chunk, self.delay_s, self.status, self.send_stop, self.raw = (
            mode, chunk, delay_s, status, send_stop, raw)
        self.header_delay_s, self.stall_at, self.stall_s = header_delay_s, stall_at, stall_s
        self.requests: list[dict] = []
        self.paths: list[str] = []
        self.prompt_ids = prompt_ids  # chat route: f(body) -> the prompt_token_ids the first chunk reports
        self.aborted = 0
        self.sent: list[int] = []  # tokens written to the socket, all requests
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fake.requests.append(body)
                fake.paths.append(self.path)
                time.sleep(fake.header_delay_s)  # vLLM still queueing or prefilling: no headers yet
                if fake.status != 200:
                    data = b'{"error": "SECRET-TEXT /root/.cache"}'
                    self.send_response(fake.status)
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    if fake.raw is not None:
                        self.wfile.write(fake.raw)
                        return
                    fake.serve(self.wfile, body)
                except (BrokenPipeError, ConnectionResetError):
                    fake.aborted += 1

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def event(self, ids, finish=None, stop_reason=None):
        choice = {'index': 0, 'text': '', 'finish_reason': finish, 'stop_reason': stop_reason}
        if self.mode == 'ids':
            choice['token_ids'] = ids
        else:
            choice['logprobs'] = {'tokens': [f'token_id:{i}' for i in ids], 'token_logprobs': [0.0] * len(ids)}
        return b'data: ' + json.dumps({'id': 'cmpl-x', 'object': 'text_completion', 'choices': [choice]}).encode()

    def serve(self, out, body):
        if 'messages' in body and self.prompt_ids is not None:
            first = {'id': 'chatcmpl-x', 'object': 'chat.completion.chunk', 'prompt_token_ids': self.prompt_ids(body),
                     'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}}]}
            out.write(b'data: ' + json.dumps(first).encode() + b'\n\n')
            out.flush()
        tokens = self.script.pop(0) if self.script else []
        stop = set(body.get('stop_token_ids') or [])
        emitted, finish, reason = [], 'length', None
        for t in tokens[: body['max_tokens']]:
            if t in stop:
                finish, reason = 'stop', t
                if self.send_stop:
                    emitted.append(t)
                break
            emitted.append(t)
        else:
            if len(tokens) < body['max_tokens']:
                finish = 'stop'  # script ran out: EOS-like stop with no stop_reason
        for i in range(0, len(emitted), self.chunk):
            if self.stall_at is not None and i == self.stall_at:
                time.sleep(self.stall_s)  # a mid-stream stall
            part = emitted[i:i + self.chunk]
            out.write(self.event(part) + b'\n\n')
            out.flush()
            self.sent.extend(part)
            time.sleep(self.delay_s)
        out.write(self.event([], finish, reason) + b'\n\n' + b'data: [DONE]\n\n')
        out.flush()
