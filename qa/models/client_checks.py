#!/usr/bin/env python3
"""Models QA client checks against an exact rose-chat-core checkout.

Usage:
  python3 qa/models/client_checks.py --crate PATH/TO/rose-chat-core [--out results.json]

Two kinds of check, each labelled in the output:

- cli: runs the crate's rose-chat binary (config-check, chat, usage) with
  base_url pointed at a live loopback fixture, and asserts exit codes, output
  redaction and that the fixture saw zero connections.
- bridged: a small out-of-tree Rust probe drives the crate's own ChatSession,
  wire::encode_request and wire::decode_response. The probe hands every encoded
  request to this script, which POSTs it to the fixture over loopback and returns
  the fixture's status, Retry-After and body bytes. This exercises the crate's
  history, encoding, decoding and error mapping on real fixture bytes. It does
  NOT exercise a Rust socket transport (the crate has none yet), connect/response
  timeouts, cancellation, a model or an endpoint.

Nothing here contacts anything except 127.0.0.1. Auth uses the synthetic sentinel.
"""
import argparse
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixture_server import SENTINEL, FixtureServer  # noqa: E402

MODEL = 'qa-explicit-model-id'

PROBE_MAIN = r'''
use std::io::{BufRead, Write};
use rose_chat_core::{wire, ChatError, ChatRequest, ChatResponse, ChatSession, ChatTransport, Config, SendContext};

struct Bridge;

impl ChatTransport for Bridge {
    fn send(&mut self, request: &ChatRequest, _ctx: &SendContext<'_>) -> Result<ChatResponse, ChatError> {
        let body = wire::encode_request(request);
        let out = std::io::stdout();
        let mut out = out.lock();
        writeln!(out, "REQ {}", serde_json::json!({ "body": body })).unwrap();
        out.flush().unwrap();
        let mut line = String::new();
        std::io::stdin().lock().read_line(&mut line).unwrap();
        let v: serde_json::Value = serde_json::from_str(&line).unwrap();
        let status = v["status"].as_u64().unwrap() as u16;
        let retry_after = v["retry_after"].as_str().map(str::to_string);
        let hex = v["body_hex"].as_str().unwrap();
        let bytes: Vec<u8> = (0..hex.len()).step_by(2)
            .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap()).collect();
        wire::decode_response(status, retry_after.as_deref(), &bytes)
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let config = Config::from_toml(&std::fs::read_to_string(&args[1]).unwrap()).unwrap();
    let turns: Vec<String> = serde_json::from_str(&args[2]).unwrap();
    let mut session = ChatSession::new(&config, Bridge, None);
    for text in turns {
        let result = match session.send(&text) {
            Ok(r) => serde_json::json!({
                "ok": true, "content": r.content, "truncated": r.truncated,
                "finish": format!("{:?}", r.finish_reason), "served_model": r.served_model,
            }),
            Err(e) => {
                let retry_after = match &e { ChatError::RateLimited { retry_after } => retry_after.map(|d| d.as_secs()), _ => None };
                serde_json::json!({ "ok": false, "kind": e.kind(), "retry_after_s": retry_after })
            }
        };
        let mut result = result;
        result["history_len"] = serde_json::json!(session.history().len());
        println!("RESULT {}", result);
        std::io::stdout().flush().unwrap();
    }
}
'''


def config_toml(base_url, extra=''):
    return f'''[endpoint]
base_url = "{base_url}"
protocol = "openai-chat-candidate"

[model]
id = "{MODEL}"
{extra}'''


class Harness:
    def __init__(self, crate, work):
        self.crate = os.path.abspath(crate)
        self.work = work
        self.target = os.path.join(work, 'target')
        self.server = FixtureServer(hold=5.0, drip=0.05)
        threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True).start()
        self.results = []

    # -- helpers ------------------------------------------------------------
    def record(self, case, state, kind, expected, observed):
        self.results.append({'case': case, 'state': state, 'kind': kind, 'expected': expected, 'observed': observed})

    def fixture(self, path, method='GET', body=b''):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        try:
            conn.request(method, path, body)
            return json.loads(conn.getresponse().read())
        finally:
            conn.close()

    def reset(self):
        self.fixture('/__qa/reset', 'POST')

    def requests(self):
        return self.fixture('/__qa/requests')

    def write(self, name, text):
        path = os.path.join(self.work, name)
        with open(path, 'w') as f:
            f.write(text)
        return path

    def cargo(self, *args, cwd):
        env = dict(os.environ, CARGO_TARGET_DIR=self.target)
        return subprocess.run(['cargo', *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=600)

    # -- build ----------------------------------------------------------------
    def build(self):
        built = self.cargo('build', '--locked', '-q', '--bin', 'rose-chat', cwd=self.crate)
        if built.returncode:
            raise SystemExit('rose-chat build failed:\n' + built.stderr[-2000:])
        self.bin = os.path.join(self.target, 'debug', 'rose-chat')
        probe = os.path.join(self.work, 'probe')
        os.makedirs(os.path.join(probe, 'src'), exist_ok=True)
        with open(os.path.join(probe, 'Cargo.toml'), 'w') as f:
            f.write('[package]\nname = "qa-probe"\nversion = "0.0.0"\nedition = "2024"\npublish = false\n\n'
                    f'[dependencies]\nrose-chat-core = {{ path = {json.dumps(self.crate)} }}\nserde_json = "1"\n')
        with open(os.path.join(probe, 'src', 'main.rs'), 'w') as f:
            f.write(PROBE_MAIN)
        built = self.cargo('build', '-q', cwd=probe)
        if built.returncode:
            raise SystemExit('probe build failed:\n' + built.stderr[-4000:])
        self.probe = os.path.join(self.target, 'debug', 'qa-probe')

    # -- cli checks -------------------------------------------------------------
    def cli(self, *args, env=None):
        full_env = dict(os.environ, **(env or {}))
        p = subprocess.run([self.bin, *args], capture_output=True, text=True, timeout=30, env=full_env)
        return p.returncode, p.stdout + p.stderr

    def cli_checks(self):
        url = self.server.url('ok')
        self.reset()
        cases = [
            ('MQA-L02 missing model.id', '[endpoint]\nbase_url = "%s"\nprotocol = "openai-chat-candidate"\n' % url, 3),
            ('MQA-L02 malformed base_url', config_toml('not a url'), 3),
            ('MQA-L02 unknown key', config_toml(url, '\n[timeouts]\nconnect_msx = 5\n'), 3),
            ('MQA-L02 non-numeric timeout', config_toml(url, '\n[timeouts]\nconnect_ms = "soon"\n'), 3),
            ('MQA-L05 valid config echo', config_toml(url), 0),
        ]
        for case, text, want in cases:
            code, out = self.cli('config-check', '--config', self.write('c.toml', text))
            missing = [s for s in (MODEL, '127.0.0.1') if s not in out] if want == 0 else []
            extra = ''
            if want == 0:
                extra = f'; summary missing {missing}' if missing else '; summary names model id and host'
            self.record(case, 'PASS' if code == want and not missing else 'FAIL', 'cli',
                        f'exit {want}', f'exit {code}{extra}')
        code, out = self.cli('config-check', '--config', self.write('c.toml', config_toml('http://example.invalid/v1')))
        self.record('MQA-L03 plain http to non-loopback host', 'INCONCLUSIVE', 'cli',
                    'QA proposal: refuse unless explicit insecure flag; Product contract allows http(s)',
                    f'exit {code} (accepted)' if code == 0 else f'exit {code} (refused)')
        auth = '\n[auth]\nmode = "bearer-env"\nenv_var = "QA_MODELS_TOKEN"\n'
        code, out = self.cli('config-check', '--config', self.write('c.toml', config_toml(url, auth)),
                             env={'QA_MODELS_TOKEN': SENTINEL})
        self.record('MQA-L04 bearer-env value redacted', 'PASS' if code == 0 and SENTINEL not in out else 'FAIL', 'cli',
                    'exit 0; sentinel absent from all output', f'exit {code}; sentinel present={SENTINEL in out}')
        marker = os.path.join(self.work, 'token-command-ran')
        cmd = '\n[auth]\nmode = "bearer-command"\ncommand = ["sh", "-c", "touch %s; echo %s"]\n' % (marker, SENTINEL)
        code, out = self.cli('config-check', '--config', self.write('c.toml', config_toml(url, cmd)))
        ran = os.path.exists(marker)
        self.record('MQA-L04 bearer-command not run by config-check',
                    'PASS' if code == 0 and not ran and SENTINEL not in out else 'FAIL', 'cli',
                    'exit 0; command not executed; sentinel absent', f'exit {code}; executed={ran}; sentinel present={SENTINEL in out}')
        code, _ = self.cli('chat', '--config', self.write('c.toml', config_toml(url)))
        self.record('MQA-L07 live chat unavailable in this build', 'PASS' if code == 5 else 'FAIL', 'cli', 'exit 5', f'exit {code}')
        code, _ = self.cli('config-check', '--bogus', 'x')
        self.record('MQA-L07 usage error', 'PASS' if code == 2 else 'FAIL', 'cli', 'exit 2', f'exit {code}')
        connections = self.requests()['connections'] - 1  # minus this GET
        self.record('MQA-L02 zero network I/O from CLI', 'PASS' if connections == 0 else 'FAIL', 'cli',
                    '0 fixture connections across all CLI runs above', f'{connections} connections')

    # -- bridged checks ----------------------------------------------------------
    def bridge(self, turns, scenarios, extra=''):
        """Run the probe; scenarios[i] answers the i-th request the probe actually sends."""
        self.reset()
        cfg = self.write('probe.toml', config_toml('http://127.0.0.1:9/v1', extra))
        p = subprocess.Popen([self.probe, cfg, json.dumps(turns)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             text=True, bufsize=1)
        results, sent = [], 0
        for line in p.stdout:
            tag, _, payload = line.rstrip('\n').partition(' ')
            if tag == 'REQ':
                name = scenarios[sent]
                sent += 1
                body = json.loads(payload)['body'].encode()
                url = urlsplit(self.server.url(name))
                conn = http.client.HTTPConnection(url.hostname, url.port, timeout=10)
                conn.request('POST', url.path + '/chat/completions', body,
                             {'Content-Type': 'application/json', 'Authorization': f'Bearer {SENTINEL}'})
                resp = conn.getresponse()
                try:
                    data = resp.read()
                except http.client.IncompleteRead as e:
                    data = e.partial
                conn.close()
                reply = {'status': resp.status, 'retry_after': resp.getheader('Retry-After'), 'body_hex': data.hex()}
                p.stdin.write(json.dumps(reply) + '\n')
                p.stdin.flush()
            elif tag == 'RESULT':
                results.append(json.loads(payload))
        p.wait(timeout=30)
        return results, self.requests()['requests']

    def bridged_checks(self):
        u1, u2 = 'My codeword is N-7f3a. Reply OK.', 'What is my codeword?'
        res, seen = self.bridge([u1, u2], ['ok', 'ok'])
        msgs = seen[1]['body']['messages'] if len(seen) == 2 else None
        want = [{'role': 'user', 'content': u1}, {'role': 'assistant', 'content': res[0].get('content')},
                {'role': 'user', 'content': u2}]
        ok = msgs == want and seen[1]['body'].get('model') == MODEL and seen[1]['body'].get('stream') is False \
            and res[1]['history_len'] == 4
        self.record('MQA-C01 two-turn history in wire request', 'PASS' if ok else 'FAIL', 'bridged',
                    '2nd request = [user1, assistant1, user2], exact model id, stream false; history 4',
                    f'requests={len(seen)}; roles={[m["role"] for m in msgs or []]}; history={res[-1]["history_len"]}')

        res, seen = self.bridge(['hello'], ['ok'], '\n[chat]\nsystem_prompt = "QA synthetic system prompt"\n')
        roles = [m['role'] for m in seen[0]['body']['messages']]
        self.record('MQA-C02 system prompt first', 'PASS' if roles == ['system', 'user'] else 'FAIL', 'bridged',
                    "['system', 'user']", str(roles))

        res, seen = self.bridge([f'turn {i}' for i in range(12)], ['ok'] * 12)
        counts = [len(r['body']['messages']) for r in seen]
        self.record('MQA-C03 12-turn growth', 'PASS' if counts == [1 + 2 * i for i in range(12)] else 'FAIL', 'bridged',
                    'messages per request 1,3,...,23', str(counts))

        text = 'CJK 你好 RTL שלום emoji \U0001f339 é "q" \\ \n line2 ' + 'x' * 32768
        res, seen = self.bridge([text], ['ok'], '\n[limits]\ncontext_budget_chars = 40000\n')
        got = seen[0]['body']['messages'][-1]['content'] if seen else None
        self.record('MQA-C05 Unicode + 32 KiB round trip', 'PASS' if got == text and res[0]['ok'] else 'FAIL', 'bridged',
                    'byte-exact content in request; turn ok', f'exact={got == text}; ok={res[0]["ok"]}')

        res, seen = self.bridge(['   '], [])
        self.record('MQA-C06 blank input', 'PASS' if res[0].get('kind') == 'invalid_input' and not seen else 'FAIL',
                    'bridged', 'invalid_input; 0 requests', f'{res[0].get("kind")}; {len(seen)} requests')

        res, seen = self.bridge(['y' * 100], [], '\n[limits]\ncontext_budget_chars = 50\n')
        self.record('MQA-C04 client context guard', 'PASS' if res[0].get('kind') == 'context_limit' and not seen else 'FAIL',
                    'bridged', 'context_limit; 0 requests', f'{res[0].get("kind")}; {len(seen)} requests')

        res, seen = self.bridge(['first', 'second', 'third'], ['ok', '500', 'ok'])
        third = [m['content'] for m in seen[2]['body']['messages']] if len(seen) == 3 else None
        ok = res[1].get('kind') == 'server' and third == ['first', res[0]['content'], 'third'] and res[2]['history_len'] == 4
        self.record('MQA-C07 failed turn not committed', 'PASS' if ok else 'FAIL', 'bridged',
                    "turn 2 server; 3rd request = ['first', reply1, 'third']; history 4", f'{res[1].get("kind")}; {third}')

        expectations = [
            ('MQA-M01 non-JSON 200', 'non-json', 'malformed_response'),
            ('MQA-M01 HTML 502', 'html-502', 'server'),
            ('MQA-M02 missing content', 'missing-content', 'empty_completion'),
            ('MQA-M02 null content', 'null-content', 'empty_completion'),
            ('MQA-M02 empty content', 'empty-content', 'empty_completion'),
            ('MQA-M02 empty choices', 'empty-choices', 'malformed_response'),
            ('MQA-M03 content is a number', 'wrong-type', 'malformed_response'),
            ('MQA-M04 truncated body (decoder on partial bytes only)', 'truncated', 'malformed_response'),
            ('MQA-M09 served model differs', 'model-mismatch', 'model_identity_mismatch'),
            ('MQA-E01 401', '401', 'unauthorized'),
            ('MQA-E01 403', '403', 'forbidden'),
            ('MQA-E02 404', '404', 'unexpected_status'),
            ('MQA-E03 400 context length', '400-context', 'server_context_limit'),
            ('MQA-E04 429 Retry-After 2', '429', 'rate_limited'),
            ('MQA-E04 429 no Retry-After', '429-no-retry-after', 'rate_limited'),
            ('MQA-E05 500', '500', 'server'),
            ('MQA-E05 502', '502', 'server'),
            ('MQA-E05 503', '503', 'server'),
            ('MQA-E05 504', '504', 'server'),
        ]
        for case, name, kind in expectations:
            res, seen = self.bridge(['hello'], [name, 'ok', 'ok'])
            r = res[0]
            observed = f'{r.get("kind", "ok")}; requests={len(seen)}; history={r["history_len"]}'
            ok = r.get('kind') == kind and len(seen) == 1 and r['history_len'] == 0
            if name == '429':
                ok = ok and r.get('retry_after_s') == 2
                observed += f'; retry_after={r.get("retry_after_s")}'
            if name == '429-no-retry-after':
                ok = ok and r.get('retry_after_s') is None
            self.record(case, 'PASS' if ok else 'FAIL', 'bridged', f'{kind}; exactly 1 request (no retry); history 0',
                        observed)

        res, seen = self.bridge(['hello'], ['length'])
        r = res[0]
        self.record('MQA-M06 finish_reason=length', 'PASS' if r['ok'] and r['truncated'] and r['history_len'] == 2 else 'FAIL',
                    'bridged', 'ok, truncated=true, committed (history 2)',
                    f'ok={r["ok"]}; truncated={r.get("truncated")}; history={r["history_len"]}')


UNAVAILABLE = [
    ('MQA-T01..T04 connect/first-byte/stall/total-deadline timeouts', 'no Rust socket transport in this revision'),
    ('MQA-T05..T07 cancellation, cancel-then-continue, EOF/SIGTERM', 'CancelToken exists; no socket transport or CLI chat loop to interrupt'),
    ('MQA-E06..E08 refused/DNS/TLS, redirects, HTTPS_PROXY', 'no Rust socket transport in this revision'),
    ('MQA-M04 truncation detected by transport', 'only decoder-on-partial-bytes was exercised'),
    ('MQA-M05 streaming', 'non-streaming by design in this revision'),
    ('MQA-M07/M08 invalid UTF-8 and oversized bodies', 'fixture scenarios not yet written'),
    ('MQA-L06 transcript reproducibility', 'no transcript writer in this revision'),
    ('MQA-L08, C09, R01-R06 real endpoint / model identity', 'no served model or endpoint; "Muse Glimmer" unresolved'),
]


def main():
    parser = argparse.ArgumentParser(description='Models QA client checks')
    parser.add_argument('--crate', required=True)
    parser.add_argument('--out')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='models-qa-') as work:
        h = Harness(args.crate, work)
        h.build()
        h.cli_checks()
        h.bridged_checks()
        h.server.shutdown()
    for case, why in UNAVAILABLE:
        h.results.append({'case': case, 'state': 'UNAVAILABLE', 'kind': '-', 'expected': '-', 'observed': why})
    width = max(len(r['case']) for r in h.results)
    for r in h.results:
        print(f'{r["state"]:12} {r["kind"]:8} {r["case"]:{width}}  {r["observed"]}')
    counts = {}
    for r in h.results:
        counts[r['state']] = counts.get(r['state'], 0) + 1
    print('summary', json.dumps(counts, sort_keys=True))
    if args.out:
        with open(args.out, 'w') as f:
            json.dump({'crate': h.crate, 'results': h.results, 'summary': counts}, f, indent=1)
    return 1 if counts.get('FAIL') else 0


if __name__ == '__main__':
    sys.exit(main())
