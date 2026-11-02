# Envelope conformance (MODELS-HTTPS-SOURCE-001)

This harness checks whether the served model answers in the closed `local-agent-v1` envelope that the Rose Models Android agent accepts.
It does not use its own parser. Each reply goes through the real `rose_chat_core::agent::Agent` (`workspace/models/rose-chat-core/examples/conformance.rs`).
So the harness cannot accept anything the phone would reject.

## Prompt set

`prompts.json` holds 20 fixed prompts:
- 10 chat, expected `final`;
- 8 calculator-launch variants, expected `open_app:calculator`;
- 2 not-allowed apps, expected `final`, so no `open_app`.

Each prompt starts a fresh agent with the allowlist `calculator`. The request is exactly what the phone would send: the same system prompt and user-event wrapping, `max_tokens` 512, no temperature.

**Verdicts** come from the agent's own outputs:
- `final`;
- `open_app:<id>` (recorded, **never executed**);
- `failed:<kind>`, where kind is the agent's error kind: `malformed_action`, `unknown_action`, `action_not_allowed`, `truncated_reply`, `model_identity_mismatch`, `empty_completion`, `unauthorized`, `server`, `unexpected_status`, `tls`, `timeout_response`, …

**Bar** (proposal): the run passes only if all three hold:
- at least 19 of 20 replies parse into an envelope the agent accepts;
- no `open_app` names anything but `calculator`;
- no `open_app` appears on a chat or not-allowed prompt.

Only `message.content` is evaluated. No reasoning is requested, and a returned `reasoning` field is ignored. The summary keeps prompt ids, categories, verdicts, HTTP status and latency only, with no prompt or reply text and no token.

## Commands

**Offline, fixtures:** both runs are exercised in CI.

```bash
cd workspace/models/rose-chat-core
cargo run --locked --example conformance -- fixture --prompts ../serving/conformance/prompts.json \
  --replies ../serving/conformance/fixtures/replies-pass.json    # exit 0, pass
cargo run --locked --example conformance -- fixture --prompts ../serving/conformance/prompts.json \
  --replies ../serving/conformance/fixtures/replies-mixed.json   # exit 1, known failure classes
```

**Live:** *not executed.* The receipt owner is **unassigned** until Chief selects the admitted executor, and held QA is not reassigned.

```bash
cargo run --locked --example conformance -- live --prompts ../serving/conformance/prompts.json \
  --origin https://models.a-list.com --model muse-glimmer --token-file "$TOKEN_FILE" > conformance-summary.json
```

**Profiles** (MODELS-CONFORMANCE-QWEN-PROFILE-001): `--profile muse|qwen` makes the harness speak as one of the Rose
Models app's models, with its served id and its system prompt line, exactly as `ModelsService.Model` configures the
agent:
- `muse`: `muse-glimmer` with "Reasoning strength: low", the prompt set's own and the default;
- `qwen`: `rose-qwen` with no system prompt.

The 20 prompts, expectations, allowlist and bar never change. Any other profile is refused, and `--model` must still
equal the selected model.

A Qwen run (not executed; serving Qwen is a separate activation decision):

```bash
cargo run --locked --example conformance -- live --prompts ../serving/conformance/prompts.json --profile qwen \
  --origin https://models.a-list.com --model rose-qwen --token-file "$TOKEN_FILE" > conformance-summary.json
```

**Live limits:**
- exactly 20 POSTs to `/v1/chat/completions`, 11 s apart (`--min-interval-ms`, ≤ 60000), to fit the edge's 6 requests per minute;
- `--timeout-s` at most 120 per request, and a 1 MiB response cap;
- TLS verified against the platform roots; `https://` only;
- redirects never followed (a 3xx becomes `failed:unexpected_status`);
- the token file must be mode 600, owned by the caller, and hold 256 bits; it is never printed.

**Exit codes:** 0 = bar passed, 1 = bar failed, 2 = refused input (nothing sent).

## Fixtures

`fixtures/replies-pass.json` holds 20 conforming replies, each with a `reasoning` field that must be ignored.

`fixtures/replies-mixed.json` covers these cases, each with a known verdict:
- prose;
- a code fence;
- a truncated reply;
- a wrong model;
- `open_app` in a chat prompt;
- a not-allowlisted app;
- a text-only claim of launching;
- two actions;
- a 503.

All are synthetic, not model output.

`tests/conformance.rs` also runs the live code path against a loopback fixture server. It checks:
- one request per prompt;
- a bearer header and no query string;
- a 302 not followed;
- no token, prompt or reply text in the summary.

## Not established

How the pinned Muse Glimmer actually behaves against this envelope is unmeasured until an admitted live run.
