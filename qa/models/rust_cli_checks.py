#!/usr/bin/env python3
"""Direct Models QA checks of the rose-chat binary over loopback sockets.

Usage:
  python3 qa/models/rust_cli_checks.py --bin PATH/TO/rose-chat [--out results.json]

The binary itself opens every connection. This script only starts the loopback
fixture, writes a config and a turn script, runs the binary, and judges the
result from the fixture's own records (request bodies, auth class, connection
counts, disconnects), the exit code, wall time, peak RSS and a sentinel grep of
stdout/stderr/transcript. It does not parse the client's display format, except
for the optional truncation marker noted below.

The CLI surface is Product's plan (rose-chat chat --config FILE --script FILE
--transcript FILE, TOML endpoint.base_url). Override --argv and --endpoint-toml
when Product publishes a different contract. If the binary reports live chat
unavailable (exit 5), every direct case is UNAVAILABLE, not FAIL.

Exercises the client only; no model, serving runtime or endpoint.
"""
import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixture_server import REASONING_SENTINEL, SENTINEL, FixtureServer  # noqa: E402
from test_fixture_server import make_self_signed  # noqa: E402

MODEL = 'qa-explicit-model-id'
TOKEN_VAR = 'QA_MODELS_TOKEN'
DEFAULT_ARGV = ['{bin}', 'chat', '--config', '{config}', '--script', '{script}', '--transcript', '{transcript}']
DEFAULT_ENDPOINT = 'base_url = "{base}"\nprotocol = "openai-chat-candidate"'
DEADLINE_MS = 1500
TOLERANCE_S = 1.5   # allowed overrun past a configured deadline
CANCEL_S = 2.0      # allowed time from signal to exit
PANIC_MARKERS = ('panicked at', 'RUST_BACKTRACE')


class Run:
    def __init__(self, code, elapsed, maxrss_kib, out, transcript):
        self.code, self.elapsed, self.maxrss_kib, self.out, self.transcript = code, elapsed, maxrss_kib, out, transcript

    @property
    def panicked(self):
        return self.code == 101 or any(m in self.out for m in PANIC_MARKERS)

    def leaked(self):
        return SENTINEL in self.out or SENTINEL in self.transcript

    def summary(self):
        return f'exit {self.code}; {self.elapsed:.2f}s; rss {self.maxrss_kib // 1024} MiB'


class Harness:
    def __init__(self, binary, argv, endpoint_toml, raw_predict_toml, work):
        self.bin = os.path.abspath(binary)
        self.argv = argv
        self.endpoint_toml = endpoint_toml
        self.raw_predict_toml = raw_predict_toml
        self.work = work
        self.results = []
        self.leaks = []
        self.server = self.start(FixtureServer(hold=20.0, drip=0.05, big_mib=64))

    @staticmethod
    def start(server):
        threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True).start()
        return server

    # -- plumbing -------------------------------------------------------------
    def record(self, case, state, expected, observed):
        self.results.append({'case': case, 'state': state, 'kind': 'direct-rust', 'expected': expected,
                             'observed': observed})

    def config(self, base, extra='', endpoint=None, request_ms=DEADLINE_MS):
        endpoint = (endpoint or self.endpoint_toml).format(base=base)
        return (f'[endpoint]\n{endpoint}\n\n[model]\nid = "{MODEL}"\n\n'
                f'[auth]\nmode = "bearer-env"\nenv_var = "{TOKEN_VAR}"\n\n'
                f'[timeouts]\nconnect_ms = 1000\nrequest_ms = {request_ms}\n{extra}')

    def run(self, server, selector, turns, extra='', env=None, request_ms=DEADLINE_MS, endpoint=None,
            base=None, signal_after=None, sig=signal.SIGINT):
        server.requests.clear()
        server.connections = server.tcp_accepts = server.tls_handshake_failures = 0
        server.seq_counters.clear()
        base = base or server.url(selector)
        paths = {name: os.path.join(self.work, name) for name in ('config.toml', 'turns.txt', 'transcript.jsonl')}
        with open(paths['config.toml'], 'w') as f:
            f.write(self.config(base, extra, endpoint, request_ms))
        with open(paths['turns.txt'], 'w') as f:
            f.write(''.join(t + '\n' for t in turns))
        if os.path.exists(paths['transcript.jsonl']):
            os.remove(paths['transcript.jsonl'])
        argv = [a.format(bin=self.bin, config=paths['config.toml'], script=paths['turns.txt'],
                         transcript=paths['transcript.jsonl']) for a in self.argv]
        full_env = dict(os.environ, **{TOKEN_VAR: SENTINEL}, **(env or {}))
        started = time.monotonic()
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env=full_env)
        if signal_after is not None:
            threading.Timer(signal_after, lambda: p.poll() is None and p.send_signal(sig)).start()
        killer = threading.Timer(60, p.kill)
        killer.start()
        out = p.stdout.read().decode('utf-8', 'replace')
        _, status, usage = os.wait4(p.pid, 0)
        killer.cancel()
        elapsed = time.monotonic() - started
        code = os.waitstatus_to_exitcode(status)
        transcript = ''
        if os.path.exists(paths['transcript.jsonl']):
            with open(paths['transcript.jsonl'], errors='replace') as f:
                transcript = f.read()
        run = Run(code, elapsed, usage.ru_maxrss, out, transcript)
        if run.leaked():
            self.leaks.append(selector)
        time.sleep(0.1)  # let the fixture finish recording a disconnect
        return run, list(server.requests)

    # -- cases ------------------------------------------------------------------
    def available(self):
        run, seen = self.run(self.server, 'ok', ['availability probe'])
        if run.code == 5 and not seen:
            return False, f'chat exit 5 (live chat unavailable in this build), {len(seen)} requests'
        return True, run.summary()

    def checks(self):
        s = self.server
        u1, u2 = 'My codeword is N-7f3a. Reply OK.', 'What is my codeword?'

        run, seen = self.run(s, 'ok', [u1, u2])
        msgs = seen[1]['body']['messages'] if len(seen) == 2 and seen[1]['body'] else []
        first_reply = 'fixture reply 1 (received 1 messages)'
        ok = (run.code == 0 and len(seen) == 2 and [m.get('role') for m in msgs] == ['user', 'assistant', 'user']
              and [m.get('content') for m in msgs] == [u1, first_reply, u2]
              and all(r['body'].get('model') == MODEL for r in seen) and {r['auth'] for r in seen} == {'sentinel'})
        self.record('MQA-C01 two-turn history over a socket', 'PASS' if ok else 'FAIL',
                    'exit 0; 2 requests; 2nd = [user1, reply1, user2]; exact model id; sentinel bearer',
                    f'{run.summary()}; requests={len(seen)}; roles={[m.get("role") for m in msgs]}; '
                    f'auth={sorted({r["auth"] for r in seen})}')
        bodies_a = [r['body'] for r in seen]

        run, seen = self.run(s, 'ok', [u1, u2])
        same = [r['body'] for r in seen] == bodies_a
        self.record('MQA-L06 same script reproduces identical request bodies', 'PASS' if same and run.code == 0 else 'FAIL',
                    'identical fixture-recorded bodies across two runs', f'identical={same}; {run.summary()}')

        run, seen = self.run(s, 'ok', ['hello'], extra='\n[chat]\nsystem_prompt = "QA synthetic system prompt"\n')
        roles = [m.get('role') for m in seen[0]['body']['messages']] if seen else []
        self.record('MQA-C02 system prompt first', 'PASS' if roles == ['system', 'user'] and run.code == 0 else 'FAIL',
                    "['system', 'user']", f'{roles}; {run.summary()}')

        run, seen = self.run(s, 'seq:ok,500,ok', ['first', 'second', 'third'])
        if len(seen) == 3:
            contents = [m.get('content') for m in seen[2]['body']['messages']]
            ok = contents == ['first', 'fixture reply 1 (received 1 messages)', 'third']
            self.record('MQA-C07 failed turn rolled back (CLI continues)', 'PASS' if ok else 'FAIL',
                        "3rd request = ['first', reply1, 'third']", f'{contents}; {run.summary()}')
        else:
            self.record('MQA-C07 failed turn rolled back', 'INCONCLUSIVE',
                        'CLI policy after a failed scripted turn is not published',
                        f'{len(seen)} requests; {run.summary()} (stops at first error?)')

        run, seen = self.run(s, 'model-mismatch', ['hello'])
        self.judge_error('MQA-M09 served model differs', run, seen)

        for name in ('401', '403', '404', '400-context', '429', '429-no-retry-after', '500', '502', '503', '504'):
            run, seen = self.run(s, name, ['hello'])
            self.judge_error(f'MQA-E0x HTTP {name} (no retry)', run, seen)
        for name in ('non-json', 'html-502', 'missing-content', 'null-content', 'empty-content', 'empty-choices',
                     'wrong-type', 'truncated', 'invalid-utf8', 'reasoning-only'):
            run, seen = self.run(s, name, ['hello'])
            self.judge_error(f'MQA-M0x {name}', run, seen)

        run, seen = self.run(s, 'oversized', ['hello'])
        bounded = run.maxrss_kib < 48 * 1024
        state = 'PASS' if run.code not in (0, 101) and not run.panicked and bounded else 'FAIL'
        self.record('MQA-M08 64 MiB response is bounded', state, 'non-zero exit, no panic, peak RSS < 48 MiB',
                    f'{run.summary()}; fixture sent {seen[0].get("body_bytes_sent") if seen else "?"} bytes')

        run, seen = self.run(s, 'reasoning', [u1, u2])
        replay = json.dumps(seen[1]['body']) if len(seen) == 2 else ''
        ok = run.code == 0 and len(seen) == 2 and REASONING_SENTINEL not in run.out \
            and REASONING_SENTINEL not in replay and REASONING_SENTINEL not in run.transcript
        self.record('MQA-M10 reasoning neither displayed nor replayed', 'PASS' if ok else 'FAIL',
                    'exit 0; reasoning sentinel absent from output, transcript and 2nd request',
                    f'{run.summary()}; shown={REASONING_SENTINEL in run.out}; replayed={REASONING_SENTINEL in replay}')

        run, seen = self.run(s, 'length', ['hello'])
        marked = 'truncat' in run.out.lower() or 'length' in run.out.lower()
        self.record('MQA-M06 finish_reason=length', 'PASS' if run.code == 0 and marked else
                    ('INCONCLUSIVE' if run.code == 0 else 'FAIL'),
                    'exit 0 and output marks the reply truncated', f'{run.summary()}; marker in output={marked}')

        closed = socket.socket()
        closed.bind(('127.0.0.1', 0))
        port = closed.getsockname()[1]
        closed.close()
        run, _ = self.run(s, 'ok', ['hello'], base=f'http://127.0.0.1:{port}/v1')
        self.record('MQA-E06 connection refused', 'PASS' if self.failed_cleanly(run) and run.elapsed < 1.0 + TOLERANCE_S
                    else 'FAIL', 'non-zero exit within connect bound', run.summary())

        for case, name in (('MQA-T02 no headers vs whole-turn deadline', 'no-headers'),
                           ('MQA-T03 stall after headers vs deadline', 'stall-after-headers'),
                           ('MQA-T04 slow drip vs whole-turn deadline', 'slow-body')):
            run, seen = self.run(s, name, ['hello'])
            limit = DEADLINE_MS / 1000 + TOLERANCE_S
            closed_by_client = bool(seen) and seen[0]['client_disconnected']
            ok = self.failed_cleanly(run) and run.elapsed <= limit and closed_by_client
            self.record(case, 'PASS' if ok else 'FAIL',
                        f'non-zero exit by {limit:.1f}s (deadline {DEADLINE_MS} ms + {TOLERANCE_S}s); client closed socket',
                        f'{run.summary()}; client closed={closed_by_client}')

        for case, sig in (('MQA-T05 SIGINT during a held request', signal.SIGINT),
                          ('MQA-T07 SIGTERM during a held request', signal.SIGTERM)):
            run, seen = self.run(s, 'no-headers', ['hello'], request_ms=30000, signal_after=1.0, sig=sig)
            took = run.elapsed - 1.0
            closed_by_client = bool(seen) and seen[0]['client_disconnected']
            ok = run.code != 0 and not run.panicked and took <= CANCEL_S and closed_by_client
            self.record(case, 'PASS' if ok else 'FAIL', f'exit within {CANCEL_S}s of the signal; socket closed',
                        f'{run.summary()}; {took:.2f}s after signal; client closed={closed_by_client}')

        run, seen = self.run(s, 'seq:ok,no-headers', ['one', 'two'], request_ms=30000, signal_after=1.0)
        lines = [line for line in run.transcript.splitlines() if line.strip()]
        self.record('MQA-T05b completed turn survives SIGINT in transcript', 'PASS' if lines else 'INCONCLUSIVE',
                    'QA proposal: turn 1 (succeeded) recorded before SIGINT during turn 2; durability policy unpublished',
                    f'{len(lines)} transcript lines; turn 1 ok={"assistant>" in run.out}; {run.summary()}')

        run, seen = self.run(s, 'redirect-cross-origin', ['hello'])
        followed = [r for r in seen if r['selector'] == 'ok']
        if followed:
            leaked = any(r['auth'] != 'absent' for r in followed)
            self.record('MQA-E07 cross-origin redirect', 'FAIL' if leaked else 'INCONCLUSIVE',
                        'not followed, or followed without the Authorization header',
                        f'followed; auth on new origin={followed[0]["auth"]}; {run.summary()}')
        else:
            self.record('MQA-E07 cross-origin redirect', 'PASS' if self.failed_cleanly(run) else 'FAIL',
                        'not followed (Product contract): non-zero exit, 1 request', f'{len(seen)} requests; {run.summary()}')
        run, seen = self.run(s, 'redirect-same-origin', ['hello'])
        self.record('MQA-E07 same-origin redirect', 'PASS' if self.failed_cleanly(run) and len(seen) == 1 else 'INCONCLUSIVE',
                    'not followed (Product contract)', f'{len(seen)} requests; {run.summary()}')

        self.tls_check()
        self.proxy_check()

        if self.raw_predict_toml:
            run, seen = self.run(s, 'ok', ['hello'], endpoint=self.raw_predict_toml,
                                 base=f'{s.base}/s/ok/v1/projects/p/locations/l/endpoints/e:rawPredict')
            path = seen[0]['path'] if seen else None
            ok = path == '/s/ok/v1/projects/p/locations/l/endpoints/e:rawPredict' and run.code == 0
            self.record('MQA-E02 rawPredict URL used verbatim', 'PASS' if ok else 'FAIL',
                        'request path ends in :rawPredict with nothing appended', f'path={path}; {run.summary()}')
        else:
            self.record('MQA-E02 rawPredict URL used verbatim', 'UNAVAILABLE', '-',
                        'no full-URL config surface given (--raw-predict-toml)')

        self.record('MQA-L04 sentinel absent from all output and transcripts', 'FAIL' if self.leaks else 'PASS',
                    'sentinel never in stdout/stderr/transcript across every run', f'leaked in {self.leaks or "none"}')

    def failed_cleanly(self, run):
        return run.code not in (0, 101) and not run.panicked

    def judge_error(self, case, run, seen):
        ok = self.failed_cleanly(run) and len(seen) == 1
        self.record(case, 'PASS' if ok else 'FAIL', 'non-zero exit, no panic, exactly 1 request',
                    f'{run.summary()}; requests={len(seen)}')

    def tls_check(self):
        if not shutil.which('openssl'):
            self.record('MQA-E06 untrusted TLS certificate', 'UNAVAILABLE', '-', 'openssl not available')
            return
        cert, key = make_self_signed(self.work)
        tls = self.start(FixtureServer(tls_cert=cert, tls_key=key))
        try:
            run, seen = self.run(tls, 'ok', ['hello'])
            ok = self.failed_cleanly(run) and not seen and tls.tls_handshake_failures >= 1
            self.record('MQA-E06 untrusted TLS certificate', 'PASS' if ok else 'FAIL',
                        'non-zero exit; handshake rejected by client; 0 HTTP requests',
                        f'{run.summary()}; handshake failures={tls.tls_handshake_failures}; requests={len(seen)}')
        finally:
            tls.shutdown()
            tls.server_close()

    def proxy_check(self):
        """Point proxy variables at a recording listener and see whether loopback traffic uses it."""
        seen_by_proxy = []
        proxy = socket.socket()
        proxy.bind(('127.0.0.1', 0))
        proxy.listen(8)
        proxy.settimeout(0.2)
        stop = threading.Event()

        def accept():
            while not stop.is_set():
                try:
                    conn, _ = proxy.accept()
                except socket.timeout:
                    continue
                conn.settimeout(1)
                try:
                    seen_by_proxy.append(conn.recv(200).split(b'\r\n')[0].decode('latin-1'))
                except OSError:
                    pass
                conn.close()
        listener = threading.Thread(target=accept, daemon=True)
        listener.start()
        purl = f'http://127.0.0.1:{proxy.getsockname()[1]}'
        env = {k: purl for k in ('HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy', 'ALL_PROXY')}
        env.update({'NO_PROXY': '', 'no_proxy': ''})
        try:
            run, seen = self.run(self.server, 'ok', ['hello'], env=env)
        finally:
            stop.set()
            listener.join()
            proxy.close()
        route = 'proxy' if seen_by_proxy else ('direct' if seen else 'neither')
        self.record('MQA-E08 loopback bypasses proxy variables (NO_PROXY empty)',
                    'PASS' if route == 'direct' and run.code == 0 else 'FAIL',
                    'direct to loopback (policy in rose-chat-core src/http.rs at bab3cd14)',
                    f'route={route}; proxy saw {seen_by_proxy[:1]}; {run.summary()}')


UNAVAILABLE_ALWAYS = [
    ('MQA-T01 connect timeout to a non-answering host', 'needs a blackholed route; loopback refuses instead (see E06)'),
    ('MQA-E08 proxy use for non-loopback hosts', 'needs a non-loopback host; no external endpoint is in scope'),
    ('MQA-T06 cancel then continue', 'needs an interactive or library cancel surface'),
    ('MQA-L08, C09, R01-R06 real endpoint / model identity', 'no served model or endpoint; "Muse Glimmer" unresolved'),
]


def main():
    parser = argparse.ArgumentParser(description='Direct Models QA checks of the rose-chat binary')
    parser.add_argument('--bin', required=True)
    parser.add_argument('--argv', type=json.loads, default=DEFAULT_ARGV,
                        help='JSON argv template with {bin} {config} {script} {transcript}')
    parser.add_argument('--endpoint-toml', default=DEFAULT_ENDPOINT, help='[endpoint] body with {base}')
    parser.add_argument('--raw-predict-toml', help='[endpoint] body with {base} for a full :rawPredict URL')
    parser.add_argument('--out')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='models-qa-direct-') as work:
        h = Harness(args.bin, args.argv, args.endpoint_toml, args.raw_predict_toml, work)
        ready, why = h.available()
        if ready:
            h.checks()
        else:
            h.record('All direct Rust socket cases', 'UNAVAILABLE', '-', why)
        h.server.shutdown()
    for case, why in UNAVAILABLE_ALWAYS:
        h.results.append({'case': case, 'state': 'UNAVAILABLE', 'kind': '-', 'expected': '-', 'observed': why})
    width = max(len(r['case']) for r in h.results)
    for r in h.results:
        print(f'{r["state"]:12} {r["case"]:{width}}  {r["observed"]}')
    counts = {}
    for r in h.results:
        counts[r['state']] = counts.get(r['state'], 0) + 1
    print('summary', json.dumps(counts, sort_keys=True))
    if args.out:
        with open(args.out, 'w') as f:
            json.dump({'bin': h.bin, 'argv': args.argv, 'results': h.results, 'summary': counts}, f, indent=1)
    return 1 if counts.get('FAIL') else 0


if __name__ == '__main__':
    sys.exit(main())
