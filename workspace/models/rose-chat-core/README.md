# rose-chat-core

Rust chat core for the Rose Models demo (MODELS-PRODUCT-ONBOARD-001, Cycle A). It covers
configuration, ordered multi-turn history and an injected transport boundary.

**What this build proves:** client behaviour against an in-process FIXTURE transport only.
It has no network transport, no endpoint and no model. Nothing here is evidence about a
served model, a GPU deployment or the final wire protocol.

## Build and test

Requires Rust 1.97 or newer (`rust-version` in `Cargo.toml`; tested with rustc/cargo 1.97.0).
The first build fetches `serde`, `serde_json` and `toml` from crates.io, pinned by `Cargo.lock`.

```sh
cd workspace/models/rose-chat-core
cargo test --locked
cargo clippy --all-targets --locked -- -D warnings
cargo run --locked --bin rose-chat -- fixture-demo
```

This crate is not yet part of `scripts/test-local.sh` or CI; that is a later coordinated change.

## Configuration

TOML, with overrides. Precedence is command line > environment > file. Unknown keys are rejected,
so typos and token-looking fields fail rather than being ignored.

```toml
[endpoint]
base_url = "http://127.0.0.1:8080/v1"   # required; http(s) only; no user:password@
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

[retry]
max_attempts = 0                        # only 0 is supported: no automatic retry

[chat]
# system_prompt = "..."                 # optional; first message of every request
# temperature = 0.2                     # optional, 0..=2; omitted means server default
```

Environment overrides: `ROSE_CHAT_BASE_URL`, `ROSE_CHAT_MODEL_ID`.
CLI overrides (config-check): `--base-url`, `--model-id`.

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
| `unexpected_status` | Any other status, including 3xx; redirects are not followed |
| `malformed_response` | Success status whose body is not the expected shape |
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
| `config-check --config FILE [--base-url URL] [--model-id ID]` | Prints resolved settings. For bearer-env, reports only whether the variable is set. Never runs the token command. No network. | 0 OK, 3 config error |
| `fixture-demo` | Two scripted turns through the candidate wire shape, labelled FIXTURE | 0 OK, 4 chat error |
| `chat ...` | Live chat is not in this build | 5 |
| anything else | Usage | 2 |

## Test tiers

| Tier | Files | Proves |
| --- | --- | --- |
| Unit | `tests/config.rs`, `tests/wire.rs` (encode/decode), `tests/redaction.rs` | Parsing, validation, status mapping, redaction |
| FIXTURE | `tests/session.rs`, `tests/wire.rs` (session cases), `tests/cli.rs` | Client history and error handling against scripted in-process responses |
| Live | none yet | Requires the selected model on an approved endpoint; never in generic test runs |

Not yet implemented: an HTTP transport, a loopback socket fixture (QA owns `qa/models/`),
streaming, a transcript/receipt writer and interactive chat.
