# rose-chat-core

Rust chat client for the Rose Models demo (MODELS-PRODUCT-ONBOARD-001, Cycles A and B). It covers
configuration, ordered multi-turn history, a transport boundary, a real non-streaming HTTP
transport and the `rose-chat` CLI.

**What the tests prove:** client behaviour against in-process scripted responses and against
loopback fixture servers on 127.0.0.1. Nothing here is evidence about a served model, a GPU
deployment, Agent Platform or the final wire protocol. No live endpoint has been used.

## On-device agent (`local-agent-v1`)

`src/agent.rs` is the pure state machine for the Rose Models Android app (MODELS-ANDROID-CORE-001).
The Android host owns HTTPS, timers and app launch. It calls `Agent::from_config_json` once,
then `Agent::step_json` with each input, and carries out the returned outputs.

The model may answer with one closed JSON envelope, either final text or `open_app` for a local
allowlist id. Each turn allows at most two inferences and one action. Only platform tool results
record what happened. Every input is fenced by turn id and generation.

The contract is in `../contracts/local-agent-v1.md`, with exact JSON traces in
`../contracts/local-agent-v1.examples.json`. Regenerate the traces with:

```sh
cargo run --example local_agent_trace > ../contracts/local-agent-v1.examples.json
```

`tests/agent.rs` checks that the file still matches.

The desktop HTTP transport and the `rose-chat` CLI sit behind the default `http` feature.
The Android library builds with `--no-default-features`, which leaves only `serde`,
`serde_json` and `toml`, with no `ureq` or `rustls`. CI checks that.

Every agent test runs against fixtures. Whether the pinned model follows the envelope is unmeasured.

## Build and test

Requires Rust 1.97 or newer (`rust-version` in `Cargo.toml`; CI pins rustc/cargo 1.97.0) and
`python3` for the loopback tests, which start `qa/models/fixture_server.py` from this checkout.
The first build fetches `serde`, `serde_json`, `toml` and `ureq` (rustls with the platform
certificate verifier) from crates.io, pinned by `Cargo.lock`.

```sh
cd workspace/models/rose-chat-core
cargo test --locked
cargo clippy --all-targets --locked -- -D warnings
cargo run --locked --bin rose-chat -- fixture-demo
```

CI: `.github/workflows/models-rust.yml` runs format, clippy and every test above on changes to
this crate or `qa/models/`. The crate is deliberately not in `scripts/test-local.sh`, which stays
offline; building this crate needs a crates.io fetch.

### Chat against a server

```sh
cargo build --locked --bin rose-chat
target/debug/rose-chat chat --config my.toml --script turns.txt --transcript run.jsonl
```

`turns.txt` holds one user message per line; blank lines and `#` lines are skipped. Without
`--script`, messages are read from stdin. Against the QA fixture:

```sh
python3 qa/models/fixture_server.py      # prints {"base": "http://127.0.0.1:PORT", ...}
# base_url = "http://127.0.0.1:PORT/s/ok/v1" in my.toml
```

## Configuration

TOML, with overrides. Precedence is command line > environment > file. Unknown keys are rejected,
so typos and token-looking fields fail rather than being ignored.

```toml
[endpoint]
base_url = "http://127.0.0.1:8080/v1"   # either this: "/chat/completions" is appended
# request_url = "https://.../endpoints/E:rawPredict"  # or this: POSTed verbatim
protocol = "openai-chat-candidate"      # required; the only value this build accepts

[model]
id = "exact-model-id-from-models-core"  # required; opaque; used verbatim; no default
expected_revision = "..."               # optional; recorded in evidence only

[auth]
mode = "none"                           # none | bearer-env | bearer-command
# env_var = "ROSE_CHAT_TOKEN"           # bearer-env: variable NAME, never the value
# command = ["your-token-tool", "arg"]  # bearer-command: run without a shell; stdout is the token

[timeouts]
connect_ms = 10000                      # default 10000
request_ms = 120000                     # default 120000; whole-turn deadline

[limits]
max_tokens = 512                        # default 512; sent as max_tokens
context_budget_chars = 32000            # default 32000; client-side character guard
max_response_bytes = 4194304            # default 4 MiB, at most 64 MiB

[retry]
max_attempts = 0                        # only 0 is supported: no automatic retry

[chat]
# system_prompt = "..."                 # optional; first message of every request
# temperature = 0.2                     # optional, 0..=2; omitted means server default
```

Environment overrides: `ROSE_CHAT_BASE_URL`, `ROSE_CHAT_REQUEST_URL`, `ROSE_CHAT_MODEL_ID`.
CLI overrides (config-check and chat): `--base-url`, `--request-url`, `--model-id`.

**Endpoint URL contract.** Exactly one of `base_url` and `request_url` is set per layer. The
highest layer that names an endpoint replaces the whole endpoint from lower layers.
- `base_url` gets exactly one `/chat/completions` appended (a trailing `/` is ignored). It must not
  have a query or fragment. It must not already end in `/chat/completions`, `:rawPredict`,
  `:streamRawPredict` or `:predict`; use `request_url` for those.
- `request_url` is used verbatim. Use it for an Agent Platform URL such as
  `https://us-central1-aiplatform.googleapis.com/v1/projects/P/locations/us-central1/endpoints/E:rawPredict`.
  (`:rawPredict` comes from Models Core's draft; it is not verified against a live endpoint.)
- `http://` is accepted only for loopback hosts (`127.0.0.0/8`, `::1`, `localhost`). Every other
  host needs `https://`. No `user:password@` in either URL.

## Contract

- **Model identity.** `model.id` must be set explicitly, non-blank and without surrounding
  whitespace. It is sent exactly as configured. If a response reports a model, it must equal
  `model.id` exactly, otherwise the turn fails with `model_identity_mismatch`. A response that
  reports no model is accepted, and `served_model` stays `None`.
- **History.** Each request carries the full committed history (system prompt first, if set),
  then the new user message. The user message and the assistant reply are committed together
  only when the turn succeeds. Any error leaves history unchanged, so the turn can be sent again
  without duplicating or reordering context. Blank user input is rejected before any I/O.
- **Truncation.** `finish_reason = length` is a success. The reply is committed and marked
  `truncated = true`.
- **Empty replies.** Missing, null, empty or whitespace-only content is `empty_completion` and
  is not committed.
- **Context budget.** No tokenizer is used. Before sending, the total characters (Unicode scalar
  values) of all request messages must be at most `context_budget_chars`, otherwise the turn
  fails with `context_limit` and nothing is sent. This guard is not token-exact. The server's
  own limit is reported separately as `server_context_limit`.
- **Deadlines.** The session gives the transport a whole-turn deadline of `request_ms` from send,
  plus `connect_ms` for connecting. Transports report `timeout_connect` / `timeout_response`
  themselves. The session also discards any reply that arrives after the deadline as
  `timeout_response`.
- **Cancellation.** It is supported via `CancelToken`. If cancelled before sending, no request is
  made. If cancelled while in flight, the reply is discarded as `cancelled`. Prompt interruption
  of a blocking network call depends on the future transport.
- **Retries.** None. `retry.max_attempts` other than 0 is a configuration error.
  `ChatError::is_retryable()` classifies errors for callers: connect, timeout, rate-limited and
  server errors are retryable.
- **HTTP transport.** `POST` with `Content-Type: application/json`, `Accept: application/json`,
  `User-Agent: rose-chat/VERSION` and, if configured, `Authorization: Bearer ...`. TLS
  certificates are verified with the platform verifier; verification cannot be disabled.
  Redirects are never followed (3xx is `unexpected_status`). Loopback hosts never use a proxy.
  Other hosts use the standard `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY` environment and honour
  `NO_PROXY`. A response body larger than `limits.max_response_bytes` fails with
  `response_too_large`. No compression is requested.
- **Deadline coverage.** `connect_ms` bounds DNS, connect and TLS. The whole-turn `request_ms`
  deadline bounds everything, including a server that never sends headers, stalls mid-body or
  drips bytes. Loopback tests measure a 700 ms deadline ending at 0.69–1.1 s in each case.
- **Cancellation measurement.** The exchange runs on a worker thread. The caller polls for
  cancellation every 20 ms and returns `cancelled` at once; the loopback test requires less than
  150 ms after cancel. The abandoned socket is not interrupted; it closes no later than the same
  turn deadline.
- **Reasoning.** If the server returns separate `message.reasoning` or
  `message.reasoning_content`, it is never used as the answer, shown or replayed in history.
  `Reply::reasoning_chars` reports only its length. Content that is null but has reasoning is
  `empty_completion`.
- **Sampling.** `temperature` is omitted unless configured. Models Core's draft says the candidate
  model should use server defaults and never temperature 0; follow that in demo configs.
- **Credentials.** Configuration holds only where a token comes from. `Secret` prints as
  `<redacted>`. Errors name the variable or program, never its output. Response bodies are
  never copied into errors.

### Error kinds (`ChatError::kind()`)

| kind | Cause |
| --- | --- |
| `config` | Invalid or missing configuration; raised before any I/O |
| `invalid_input` | Blank user message |
| `context_limit` | Client character guard exceeded; nothing sent |
| `server_context_limit` | HTTP 400/413/422 whose error says context length or window |
| `auth` | Credential could not be resolved locally |
| `connect` | Endpoint unreachable |
| `timeout_connect`, `timeout_response` | Deadline phases; HTTP 408 maps to `timeout_response` |
| `cancelled` | Caller cancelled |
| `unauthorized`, `forbidden` | HTTP 401, 403 |
| `rate_limited` | HTTP 429; `retry_after` only when `Retry-After` is in whole seconds |
| `bad_request` | Other HTTP 400/413/422 |
| `server` | HTTP 5xx |
| `not_found` | HTTP 404: check the endpoint URL |
| `unexpected_status` | Any other status, including 3xx; redirects are not followed |
| `response_too_large` | Body exceeded `limits.max_response_bytes` |
| `tls` | TLS handshake or certificate verification failed |
| `malformed_response` | Success status whose body is not the expected shape, ended early or was cut off |
| `empty_completion` | No assistant text |
| `model_identity_mismatch` | Reported model differs from `model.id` |

### Candidate wire shape

`src/wire.rs` encodes and decodes an OpenAI-compatible non-streaming chat-completions body
(`model`, `messages`, `max_tokens`, `stream: false`, optional `temperature`). The response is read
from `choices[0].message.content`, `choices[0].finish_reason`, `model`, `id` and `usage`. This
shape is **a candidate for testing**, not the selected protocol. Models Core confirms the actual
serving runtime and Agent Platform invocation API, and only the adapter changes.

## CLI (`rose-chat`)

| Command | Behaviour | Exit |
| --- | --- | --- |
| `config-check --config FILE [overrides]` | Prints resolved settings and the exact request URL. For bearer-env, reports only whether the variable is set. Never runs the token command. No network. | 0 OK, 3 config error |
| `chat --config FILE [--script FILE] [--transcript FILE] [--label TEXT] [overrides]` | Sends each line as a turn over HTTP and prints `assistant> ...`. Failed turns are reported on stderr, history stays unchanged and the next line still runs. Resolves the credential once at start; bearer-command runs the configured program. | 0 all turns OK, 2 transcript cannot be written, 3 config or credential error, 4 one or more turns failed; killed by SIGINT/SIGTERM when interrupted (below) |
| `fixture-demo` | Two in-process scripted turns, labelled FIXTURE | 0 OK, 4 chat error |
| anything else | Usage (also unreadable script or transcript path) | 2 |

There is no exit code 5; it existed only before the HTTP transport.

**Interrupts.** `rose-chat` installs no signal handler. SIGINT (Ctrl-C) or SIGTERM ends the process
immediately by that signal: the wait status is "killed by signal 2/15", which shells report as exit
130/143. An in-flight request is abandoned and its socket closed by the OS. Output and transcript
records for turns that already finished are kept. Nothing is written for the interrupted turn,
and there is no `summary` record.

### Transcript (`--transcript`)

This is opt-in JSON Lines. There is one `run` record (client version, `source_rev` from build-time
`ROSE_CHAT_SOURCE_REV`, label, protocol, request URL without query, model id, expected revision,
auth mode, start time, `content_recorded: false`). Then there is one `turn` record per message
(outcome kind, HTTP status, elapsed ms, user/reply character counts, finish reason, truncated,
served model, request id, usage, reasoning character count) and a final `summary`. It never
contains message text, reasoning text, credentials or URL queries. Same script, config and server
give the same records apart from times, which a loopback test checks (C15).

**Durability.** Each record is one complete line written with a single unbuffered `write` before
the next turn starts. The `run` record is written before any request, so an unwritable transcript
(for example `/dev/full`) fails with exit 2 before anything is sent. A later write failure prints
`cannot write transcript record for turn N` and exits 2 before the next turn. A run interrupted by
SIGINT/SIGTERM keeps every finished turn's record, with no record for the interrupted turn and no
`summary`, so a missing `summary` marks an incomplete run. Records are not fsynced, so this is
not a power-loss or OS-crash guarantee.

## Test tiers

| Tier | Files | Proves |
| --- | --- | --- |
| Unit | `tests/config.rs`, `tests/wire.rs` (encode/decode), `tests/redaction.rs` | Parsing, validation, URL contract, status mapping, redaction |
| In-process FIXTURE | `tests/session.rs`, `tests/wire.rs` (session cases), `tests/cli.rs` (`fixture-demo`) | Client history and error handling against scripted responses |
| LOOPBACK FIXTURE | `tests/http_loopback.rs`, `tests/cli.rs` (`chat`), `tests/signals.rs` | The real HTTP transport and CLI over 127.0.0.1 sockets, against QA's `fixture_server.py` and small in-test servers, including transcript durability under SIGINT/SIGTERM |
| QA direct harness | `qa/models/rust_cli_checks.py` (QA-owned), run by CI on the built binary | QA's independent loopback cases for the binary |
| Live | none | Requires the selected model on an approved endpoint and an approved client identity. UNAVAILABLE; never in generic test runs |

Not implemented: streaming (`:streamRawPredict`), automatic retry, token-exact context
budgeting and interruption of the abandoned socket on cancel.
