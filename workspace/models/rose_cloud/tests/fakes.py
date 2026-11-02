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
                 header_delay_s=0.0, stall_at=None, stall_s=0.0, prompt_ids=None, serial=False,
                 ignore_disconnect=False, metrics=False, port=0):
        self.script = [list(s) for s in script]
        self.mode, self.chunk, self.delay_s, self.status, self.send_stop, self.raw = (
            mode, chunk, delay_s, status, send_stop, raw)
        self.header_delay_s, self.stall_at, self.stall_s = header_delay_s, stall_at, stall_s
        self.requests: list[dict] = []
        self.paths: list[str] = []
        self.prompt_ids = prompt_ids  # chat route: f(body) -> the prompt_token_ids the first chunk reports
        self.aborted = 0
        self.sent: list[int] = []  # tokens written to the socket, all requests
        # serial: one sequence at a time (vLLM --max-num-seqs 1), so a request still generating holds the next one.
        # ignore_disconnect: keep "generating" the rest after the client goes away (a server that does not abort).
        # metrics: serve GET /metrics with vllm:num_requests_running.
        self.serial, self.ignore_disconnect, self.metrics = serial, ignore_disconnect, metrics
        self.lock = threading.Lock()
        self.running = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_):
                pass

            def do_GET(self):
                if self.path == '/health':
                    self.send_response(200)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                if self.path != '/metrics' or not fake.metrics:
                    self.send_response(404)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                data = (f'# TYPE vllm:num_requests_running gauge\n'
                        f'vllm:num_requests_running{{model_name="rose-brain"}} {float(fake.running)}\n').encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                if fake.serial:
                    with fake.lock:
                        return self.post()
                return self.post()

            def post(self):
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
                fake.running += 1
                try:
                    if fake.raw is not None:
                        self.wfile.write(fake.raw)
                        return
                    fake.serve(self.wfile, body)
                except (BrokenPipeError, ConnectionResetError):
                    fake.aborted += 1
                    if fake.ignore_disconnect:  # keep generating what is left, unseen, still holding the slot
                        time.sleep(fake.delay_s * fake.left)
                finally:
                    fake.running -= 1

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
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
        self.left = len(emitted)
        for i in range(0, len(emitted), self.chunk):
            if self.stall_at is not None and i == self.stall_at:
                time.sleep(self.stall_s)  # a mid-stream stall
            part = emitted[i:i + self.chunk]
            out.write(self.event(part) + b'\n\n')
            out.flush()
            self.sent.extend(part)
            self.left -= len(part)
            time.sleep(self.delay_s)
        out.write(self.event([], finish, reason) + b'\n\n' + b'data: [DONE]\n\n')
        out.flush()


def chat_prompt_ids(tok):
    """What vLLM's processor would report as prompt_token_ids for a chat-route body rendered by RAW_TEMPLATE: the
    text parts' tokens and, per image, vision_start + (w/32)*(h/32) image pads + vision_end."""
    import base64
    import struct
    vs, pad, ve = (tok.encode(t)[0] for t in ('<|vision_start|>', '<|image_pad|>', '<|vision_end|>'))

    def ids(body):
        out = []
        for part in body['messages'][0]['content']:
            if part['type'] == 'text':
                out += tok.encode(part['text'])
            else:
                png = base64.b64decode(part['image_url']['url'].split(',', 1)[1])
                w, h = struct.unpack('>II', png[16:24])
                out += [vs] + [pad] * ((w // 32) * (h // 32)) + [ve]
        return out
    return ids


class FakeOpenAI:
    """A stand-in for `vllm serve` with the proposed contract profile on 127.0.0.1 (MODELS-QWEN-CONTRACT-001): GET
    /health and /v1/models, and non-streaming POST /v1/chat/completions answered by a scripted policy, never a model.

    The default policy behaves as the contract expects (a reasoning parser returning thinking apart, local-agent-v1
    envelopes, the code word recalled from the start of a long history). ``mode`` breaks one thing at a time:
    inline_think, wrong_id, no_reasoning, prose, forget, short_prompt, truncate, extra_model, max_model_len, and
    delay_s ({kind: seconds}, see ``kind``) holds a reply back."""

    THINKING = 'SECRET-FAKE-THINKING about the request'

    def __init__(self, served='rose-qwen', mode=None, port=0):
        self.served, self.mode = served, dict(mode or {})
        self.bodies: list[bytes] = []
        self.paths: list[str] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_):
                pass

            def reply(self, status, data=b''):
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fake.paths.append(self.path)
                if self.path == '/health':
                    return self.reply(200)
                if self.path == '/v1/models':
                    ids = [fake.served] + (['muse-glimmer'] if fake.mode.get('extra_model') else [])
                    mml = fake.mode.get('max_model_len', 8192)
                    return self.reply(200, json.dumps({'object': 'list', 'data': [
                        {'id': i, 'object': 'model', 'owned_by': 'vllm', 'max_model_len': mml} for i in ids]}).encode())
                return self.reply(404)

            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                fake.paths.append(self.path)
                fake.bodies.append(body)
                if self.path != '/v1/chat/completions':
                    return self.reply(404)
                self.reply(200, json.dumps(fake.answer(json.loads(body))).encode())

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def kind(self, body):
        """Which pinned request this is: 'after' (a tool result), 'tool', 'long' or 'chat'."""
        last = json.loads(body['messages'][-1]['content'])
        if last.get('rose_event') == 'tool_result':
            return 'after'
        text = last.get('text', '')
        return 'tool' if 'calculator' in text.lower() else 'long' if 'code word' in text else 'chat'

    def answer(self, body):
        m = self.mode
        time.sleep((m.get('delay_s') or {}).get(self.kind(body), 0))  # a slow reply: generation still running
        last = json.loads(body['messages'][-1]['content'])
        text = last.get('text', '')
        everything = json.dumps(body['messages'])
        if last.get('rose_event') == 'tool_result':
            envelope = {'type': 'final', 'text': 'Opening the calculator was requested.'}
        elif 'calculator' in text.lower():
            envelope = {'type': 'open_app', 'app_id': 'calculator'}
        elif 'code word' in text and 'AMBERFALCON' in everything and not m.get('forget'):
            envelope = {'type': 'final', 'text': 'AMBERFALCON'}
        else:
            envelope = {'type': 'final', 'text': 'Hello, I am Rose.'}
        content = json.dumps({'rose_action': 'local-agent-v1', **envelope})
        if m.get('prose'):
            content = 'Sure! ' + content
        message = {'role': 'assistant', 'content': content}
        if m.get('inline_think'):
            message['content'] = '<think>\n' + self.THINKING + '\n</think>\n\n' + content
        elif not m.get('no_reasoning'):
            message['reasoning_content'] = self.THINKING
        finish = 'stop'
        if m.get('truncate'):
            message['content'], finish = None, 'length'
        prompt = 900 if m.get('short_prompt') else len(everything) // 4
        return {'id': 'chatcmpl-fake', 'object': 'chat.completion', 'model': 'muse-glimmer' if m.get('wrong_id')
                else self.served, 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                'usage': {'prompt_tokens': prompt, 'completion_tokens': 60, 'total_tokens': prompt + 60}}
