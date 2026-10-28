#!/usr/bin/env python3
"""Stand-in chat CLI used ONLY to self-test rust_cli_checks.py.

It is a Python client, not the Rust client. Results against it prove the harness
judges correctly; they are never evidence about rose-chat. It follows the same
planned surface (chat --config FILE --script FILE --transcript FILE) so the
harness can run unchanged. QA_STANDIN_BUG injects one known defect so the
harness can be shown to report FAIL.
"""
import http.client
import json
import os
import socket
import sys
import time
import tomllib
from urllib.parse import urlsplit

MAX_BYTES = 8 << 20
BUG = os.environ.get('QA_STANDIN_BUG', '')


def post(url, body, token, connect_s, deadline):
    u = urlsplit(url)
    path = u.path if u.path.endswith(':rawPredict') else u.path.rstrip('/') + '/chat/completions'
    cls = http.client.HTTPSConnection if u.scheme == 'https' else http.client.HTTPConnection
    conn = cls(u.hostname, u.port, timeout=connect_s)
    conn.connect()
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}
    conn.sock.settimeout(None if BUG == 'no-deadline' else max(0.01, deadline - time.monotonic()))
    conn.request('POST', path, json.dumps(body).encode(), headers)
    r = conn.getresponse()
    if BUG == 'follow-redirect-with-auth' and r.status in (301, 302, 307, 308):
        r.read()
        return post(r.getheader('Location').rsplit('/chat/completions', 1)[0], body, token, connect_s, deadline)
    data = b''
    while True:
        if BUG != 'no-deadline':
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('response deadline')
            conn.sock.settimeout(remaining)
        chunk = r.read1(65536)
        if not chunk:
            break
        data += chunk
        if len(data) > MAX_BYTES:
            raise ValueError('response too large')
    conn.close()
    return r.status, data


def main():
    args = dict(zip(sys.argv[2::2], sys.argv[3::2]))
    if sys.argv[1:2] != ['chat']:
        return 2
    with open(args['--config'], 'rb') as f:
        cfg = tomllib.load(f)
    token = os.environ[cfg['auth']['env_var']]
    model = cfg['model']['id']
    connect_s = cfg['timeouts']['connect_ms'] / 1000
    request_s = cfg['timeouts']['request_ms'] / 1000
    history = []
    if cfg.get('chat', {}).get('system_prompt'):
        history.append({'role': 'system', 'content': cfg['chat']['system_prompt']})
    failed = False
    with open(args['--script']) as f:
        turns = [line.rstrip('\n') for line in f if line.strip()]
    for text in turns:
        messages = history + [{'role': 'user', 'content': text}]
        try:
            status, data = post(cfg['endpoint']['base_url'], {'model': model, 'messages': messages, 'stream': False},
                                token, connect_s, time.monotonic() + request_s)
            if status != 200:
                raise ValueError(f'HTTP {status}')
            payload = json.loads(data.decode('utf-8'))
            message = payload['choices'][0]['message']
            if payload.get('model') not in (None, model):
                raise ValueError('model identity mismatch')
            content = message.get('content')
            if BUG == 'show-reasoning':
                print('reasoning:', message.get('reasoning'))
            if not isinstance(content, str) or not content.strip():
                raise ValueError('empty completion')
            history = messages + [{'role': 'assistant', 'content': content}]
            mark = ' [truncated]' if payload['choices'][0].get('finish_reason') == 'length' else ''
            print(f'assistant> {content[:200]}{mark}')
        except (OSError, ValueError, KeyError, IndexError, TypeError, http.client.HTTPException) as e:
            failed = True
            if BUG == 'commit-failed':
                history = messages + [{'role': 'assistant', 'content': 'error'}]
            print(f'error: {type(e).__name__}', file=sys.stderr)
    if BUG == 'leak-token':
        print('debug token', token)
    return 4 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
