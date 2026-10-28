# Models QA loopback fixture

Advisory QA for the Rust-to-model chat (MODELS-QA-ONBOARD-001). The test matrix
and current results live on the Notion task page; this directory holds the
runnable fixture.

`fixture_server.py` is a standard-library HTTP server that binds only
`127.0.0.1` on an ephemeral port. It answers OpenAI-style chat-completions
POSTs with scripted behaviour and records what the client sent. It never calls
a model, endpoint or cloud service.

What a result proves:

- `test_fixture_server.py` passing proves the fixture serves each scenario as
  documented. Its probe is a small Python client, not the Rust client.
- A Rust client run against the fixture proves client behaviour (T1 client
  fixture). It proves nothing about a model, serving runtime or GCP endpoint.

## Run

```bash
python3 qa/models/test_fixture_server.py
```

```bash
python3 qa/models/fixture_server.py --list
```

```bash
python3 qa/models/fixture_server.py --require-auth --hold 30 --drip 0.5
```

The server prints one JSON line with its `base` URL. Point a client's base URL at
`<base>/s/<scenario>/v1`; any path under `/s/<scenario>/` is accepted, so the
client's own `/chat/completions` suffix works unchanged. Loopback must bypass
the container proxy: `NO_PROXY` already lists `127.0.0.1` here, and curl needs
`--noproxy '*'` only if that variable is absent.

- `GET <base>/__qa/requests` returns every recorded request: scenario, path,
  parsed JSON body, auth class (`absent`, `sentinel` or `other`), first-byte and
  handled times, and whether the client disconnected. Plus the connection count.
- `POST <base>/__qa/reset` clears the record.
- `GET <base>/__qa/scenarios` lists scenarios.

Auth uses the synthetic sentinel `Bearer QA-SENTINEL-NOT-A-SECRET`. The fixture
never stores the Authorization value, only its class, so records can be shared.

## Scenarios

| Scenario | Behaviour | Matrix cases |
| --- | --- | --- |
| `ok` | Valid completion, reply `fixture reply N (received M messages)` | C01–C03, C05, L06 |
| `length` | `finish_reason=length` | M06 |
| `model-mismatch` | Response `model` differs from the request | M09 |
| `missing-content`, `null-content`, `empty-content`, `empty-choices`, `wrong-type` | Malformed or empty completions | M02, M03 |
| `non-json`, `html-502` | Non-JSON 200, HTML 502 page | M01 |
| `truncated` | Content-Length larger than the body, then close | M04 |
| `401`, `403`, `404`, `400-context` | Auth, route and context-length errors | E01–E03, C04 |
| `429`, `429-no-retry-after`, `500`, `502`, `503`, `504` | Rate limit and server errors (`Retry-After` on 429 and 503) | E04, E05 |
| `no-headers` | Reads the request, sends nothing for `--hold` s | T02 |
| `stall-after-headers` | Headers and half the body, then holds | T03 |
| `slow-body` | Headers, then one body byte every `--drip` s | T04 |

## Client checks

```bash
python3 qa/models/client_checks.py --crate workspace/models/rose-chat-core --out results.json
```

Builds the crate's `rose-chat` binary and a small out-of-tree probe (in a temp
directory) against the given checkout, then reports PASS, FAIL, INCONCLUSIVE or
UNAVAILABLE per matrix case. `cli` checks run the binary with `base_url` at the
fixture and confirm zero connections. `bridged` checks drive the crate's own
session, encoder and decoder, with this script carrying each request to the
fixture over loopback. Neither kind exercises a Rust socket transport, timeouts,
cancellation, a model or an endpoint. Those cases print as UNAVAILABLE. It needs
cargo and the crate's dependencies, so it is not part of `scripts/test-local.sh`.

Not covered yet: streaming (only if Product chooses it), user cancellation
(needs Product's cancel surface), invalid UTF-8 and oversized bodies,
cross-origin redirects, and anything requiring a real endpoint or model.
