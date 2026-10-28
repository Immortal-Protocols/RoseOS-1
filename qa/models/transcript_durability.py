#!/usr/bin/env python3
"""Transcript durability of rose-chat across SIGINT/SIGTERM during a later turn.

Usage:
  python3 qa/models/transcript_durability.py --bin PATH/TO/rose-chat [--runs 10] [--out results.json]

Contract under test (Models Manager, Cycle C): once turn 1 has finished, its
complete metadata record is readable before turn 2 starts and is still there
after turn 2 is interrupted. The interrupted turn has no successful record, and
the transcript holds no credential, message or reasoning text. No power-loss
durability is claimed or tested.

Each run is deterministic about ordering: turn 1 is answered by the scenario
under test, turn 2 by `no-headers` (held open). The signal is sent only after
the fixture has received the turn-2 request, and the transcript is read at that
moment (the "before signal" snapshot), so no sleep decides the outcome. Repeated
runs look for races. An uninterrupted control checks the normal file shape.

Exercises the rose-chat binary over loopback only; no model or endpoint.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixture_server import REASONING_SENTINEL, SENTINEL, FixtureServer  # noqa: E402

MODEL = 'qa-explicit-model-id'
TOKEN_VAR = 'QA_MODELS_TOKEN'
MESSAGES = ['QA-MSG-ONE durability turn', 'QA-MSG-TWO durability turn']
FORBIDDEN = [SENTINEL, REASONING_SENTINEL, 'QA-MSG-ONE', 'QA-MSG-TWO', 'fixture reply']
EXIT_BOUND_S = 2.0
# turn-1 scenario -> expected outcome field of the turn-1 record
TURN1 = {'ok': 'ok', 'reasoning': 'ok', '500': 'server'}


def parse_lines(raw):
    """Return (records, complete). complete: ends with a newline and every line is JSON."""
    text = raw.decode('utf-8', 'replace')
    lines = text.split('\n')
    tail = lines.pop()
    records, complete = [], tail == ''
    for line in lines:
        try:
            records.append(json.loads(line))
        except ValueError:
            complete = False
    if tail:
        complete = False
    return records, complete


class Durability:
    def __init__(self, binary, work):
        self.bin = os.path.abspath(binary)
        self.work = work
        self.server = FixtureServer(hold=20.0)
        threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True).start()

    def reset(self):
        with self.server.lock:
            self.server.requests.clear()
            self.server.seq_counters.clear()

    def launch(self, selector, turns, transcript=None):
        cfg = os.path.join(self.work, 'config.toml')
        with open(cfg, 'w') as f:
            f.write(f'[endpoint]\nbase_url = "{self.server.url(selector)}"\nprotocol = "openai-chat-candidate"\n\n'
                    f'[model]\nid = "{MODEL}"\n\n[auth]\nmode = "bearer-env"\nenv_var = "{TOKEN_VAR}"\n\n'
                    '[timeouts]\nconnect_ms = 1000\nrequest_ms = 30000\n')
        script = os.path.join(self.work, 'turns.txt')
        with open(script, 'w') as f:
            f.write(''.join(t + '\n' for t in turns))
        if transcript is None:
            transcript = os.path.join(self.work, 'transcript.jsonl')
            if os.path.exists(transcript):
                os.remove(transcript)
        p = subprocess.Popen([self.bin, 'chat', '--config', cfg, '--script', script, '--transcript', transcript],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env=dict(os.environ, **{TOKEN_VAR: SENTINEL}))
        return p, transcript

    @staticmethod
    def read(path):
        try:
            with open(path, 'rb') as f:
                return f.read()
        except FileNotFoundError:
            return b''

    def control(self):
        """Two successful turns, no signal: run + 2 turn records + summary, exit 0."""
        self.reset()
        p, path = self.launch('ok', MESSAGES)
        out = p.communicate(timeout=30)[0].decode('utf-8', 'replace')
        raw = self.read(path)
        records, complete = parse_lines(raw)
        types = [r.get('type') for r in records]
        leaks = [s for s in FORBIDDEN if s in raw.decode('utf-8', 'replace')]
        ok = p.returncode == 0 and complete and types == ['run', 'turn', 'turn', 'summary'] and not leaks
        return {'case': 'control: two ok turns, no signal', 'state': 'PASS' if ok else 'FAIL',
                'expected': "exit 0; complete JSONL [run, turn, turn, summary]; no forbidden text",
                'observed': f'exit {p.returncode}; types={types}; complete={complete}; leaks={leaks}; '
                            f'stdout lines={len(out.splitlines())}'}

    def unwritable(self):
        """Transcript on /dev/full: Product contract (a286d2dc README) is exit 2 before any request."""
        if not os.path.exists('/dev/full'):
            return {'case': 'unwritable transcript (/dev/full)', 'state': 'UNAVAILABLE', 'expected': '-',
                    'observed': '/dev/full not present'}
        self.reset()
        p, _ = self.launch('ok', MESSAGES, transcript='/dev/full')
        out = p.communicate(timeout=30)[0].decode('utf-8', 'replace')
        sent = len(self.server.requests)
        ok = p.returncode == 2 and sent == 0 and 'panicked at' not in out
        return {'case': 'unwritable transcript (/dev/full)', 'state': 'PASS' if ok else 'FAIL',
                'expected': 'exit 2 before any request (0 fixture requests), no panic',
                'observed': f'exit {p.returncode}; fixture requests={sent}'}

    def one(self, turn1, sig):
        self.reset()
        p, path = self.launch(f'seq:{turn1},no-headers', MESSAGES)
        t0 = time.monotonic()
        while len(self.server.requests) < 2:
            if p.poll() is not None or time.monotonic() - t0 > 10:
                p.kill()
                p.wait()
                return {'ok': False, 'why': f'turn 2 never reached the fixture (exit {p.returncode})'}
            time.sleep(0.002)
        before = self.read(path)
        signalled = time.monotonic()
        p.send_signal(sig)
        try:
            out = p.communicate(timeout=10)[0].decode('utf-8', 'replace')
        except subprocess.TimeoutExpired:
            p.kill()
            out = p.communicate()[0].decode('utf-8', 'replace')
        took = time.monotonic() - signalled
        deadline = time.monotonic() + 2
        while not self.server.requests[1]['client_disconnected'] and time.monotonic() < deadline:
            time.sleep(0.01)
        after = self.read(path)
        rb, cb = parse_lines(before)
        ra, ca = parse_lines(after)
        want = TURN1[turn1]

        def turn(records, index):
            return [r for r in records if r.get('type') == 'turn' and r.get('index') == index]

        checks = {
            'turn1 complete before signal': cb and len(turn(rb, 1)) == 1 and turn(rb, 1)[0].get('outcome') == want,
            'turn1 retained after exit': ca and turn(ra, 1) == turn(rb, 1) and bool(turn(ra, 1)),
            'no ok record for interrupted turn 2': all(r.get('outcome') != 'ok' for r in turn(ra, 2)),
            'no forbidden text': not any(s in after.decode('utf-8', 'replace') for s in FORBIDDEN),
            f'exit within {EXIT_BOUND_S}s, non-zero, no panic': took <= EXIT_BOUND_S and p.returncode != 0
            and 'panicked at' not in out,
            'client closed turn-2 socket': self.server.requests[1]['client_disconnected'],
        }
        return {
            'ok': all(checks.values()),
            'failed_checks': [k for k, v in checks.items() if not v],
            'exit': p.returncode,
            'signal_to_exit_s': round(took, 3),
            'before_types': [r.get('type') for r in rb],
            'after_types': [r.get('type') for r in ra],
            'turn2_outcomes': [r.get('outcome') for r in turn(ra, 2)],
            'summary_present': any(r.get('type') == 'summary' for r in ra),
        }


def main():
    parser = argparse.ArgumentParser(description='rose-chat transcript durability across SIGINT/SIGTERM')
    parser.add_argument('--bin', required=True)
    parser.add_argument('--runs', type=int, default=10)
    parser.add_argument('--out')
    args = parser.parse_args()
    results = []
    with tempfile.TemporaryDirectory(prefix='models-qa-durability-') as work:
        d = Durability(args.bin, work)
        results.append(d.control())
        results.append(d.unwritable())
        for sig in (signal.SIGINT, signal.SIGTERM):
            for turn1 in TURN1:
                runs = [d.one(turn1, sig) for _ in range(args.runs)]
                passed = sum(r['ok'] for r in runs)
                first_bad = next((r for r in runs if not r['ok']), None)
                times = [r['signal_to_exit_s'] for r in runs if 'signal_to_exit_s' in r]
                sample = first_bad or runs[0]
                results.append({
                    'case': f'{sig.name} during turn 2 after turn 1 {turn1}',
                    'state': 'PASS' if passed == len(runs) else 'FAIL',
                    'expected': 'every run: turn-1 record complete before signal and retained; no ok turn-2 '
                                f'record; no forbidden text; exit <= {EXIT_BOUND_S}s; socket closed',
                    'observed': f'{passed}/{len(runs)} runs passed; signal->exit max {max(times, default=0):.3f}s; '
                                f'exit {sample.get("exit")}; before={sample.get("before_types")}; '
                                f'after={sample.get("after_types")}; turn2={sample.get("turn2_outcomes")}; '
                                f'summary={sample.get("summary_present")}'
                                + (f'; failed: {first_bad.get("failed_checks", first_bad.get("why"))}' if first_bad else ''),
                })
        d.server.shutdown()
    width = max(len(r['case']) for r in results)
    for r in results:
        print(f'{r["state"]:5} {r["case"]:{width}}  {r["observed"]}')
    counts = {}
    for r in results:
        counts[r['state']] = counts.get(r['state'], 0) + 1
    print('summary', json.dumps(counts, sort_keys=True))
    if args.out:
        with open(args.out, 'w') as f:
            json.dump({'bin': os.path.abspath(args.bin), 'runs': args.runs, 'results': results}, f, indent=1)
    return 1 if counts.get('FAIL') else 0


if __name__ == '__main__':
    sys.exit(main())
