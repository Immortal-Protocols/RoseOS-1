# Local composer (Project 2, LOCAL-COMPOSER-001)

Phone-local layout composition for the workspace page one swipe left of Home: a button press ("best guess") or
direct text becomes a validated layout **on the phone, offline**. No server generates layouts. Models owns this
directory only: context selection, the constrained model call, validation and benchmarking. OS Product owns the
catalog, snapshot, ID registry, renderer, persistence, pin/undo and last-usable layout. App Core owns request
routing. Native UI waits for Nathan's prototype approval.

Status (2026-11-13): **source and offline tests only.** No model has been run, nothing has been built for a
phone, nothing has been deployed, and nothing has been measured on a device.

| evidence level | state |
|---|---|
| source | this directory: contract proposal, composer, grammar, validator, bench harness, 34 offline tests |
| build (phone runtime + weights) | none: no on-device runtime is in the repo; installs and weight downloads are not authorized |
| deployment | none |
| phone proof | none: no admitted Pixel 10 install/measurement route |

## Layout

```
contract/rose-layout-v1.md     the request/snapshot/catalog/layout/result proposal for OS Product + App Core
contract/examples/             synthetic, public-safe snapshot, cases (with expected IDs) and an alias layout
composer/contract.py           constants, proposed catalog, request + snapshot checks, typed failures
composer/context.py            deterministic bounded context selection; real IDs -> short aliases
composer/grammar.py            per-request grammar (GBNF for llama.cpp, same tree as a regex for tests)
composer/prompt.py             the short instruction + items + request text
composer/validate.py           strict output validation and alias -> real ID mapping; stale-snapshot check
composer/compose.py            one request -> validated layout | typed failure, with timings
composer/backends.py           heuristic no-model baseline, scripted test backend, llama-server adapter (unexecuted)
composer/bench.py              JSONL benchmark records + p50/p95 summary
tests/test_composer.py         offline unit, negative, fuzz and harness tests
```

```bash
cd workspace/models/local-composer && python3 -m unittest discover -s tests -v
```

```bash
cd workspace/models/local-composer && python3 -m composer.bench --cases contract/examples/cases.json --repeats 3
```

These tests are not in `scripts/test-local.sh` or CI: shared CI belongs to others, so wiring this directory in is
a request to its owners, not part of this change.

## How it keeps output inside the contract

1. **Context selection** (`context.py`): pinned first, then prompt-word overlap (or time-of-day category hints for a
   button press), then recency, at most 3 entries per app, within a character budget (1600 by default, roughly
   400–550 tokens). Each entry gets an alias (`a3`, `n1`, `c2`, `x4`); the model never sees a raw ID.
2. **Grammar** (`grammar.py`): built per request from only the selected aliases and the catalog's components, with
   item counts, section count and title lengths bounded. A grammar-capable runtime cannot emit an unknown ID,
   component or over-long layout. The longest admitted output for the demo snapshot is 561 characters, so the
   output-token cap is about 290.
3. **Validation** (`validate.py`): checks everything again, plus what a grammar cannot (a card's actions must
   target its item, no duplicates, the total item cap), then maps aliases back to real IDs. Any failure is a fixed
   category from the contract; model text never appears in a result.

The GBNF text has not been loaded by a real llama.cpp parser here (no runtime is available); the tests prove the
same tree, rendered as a regular expression, accepts and rejects the right layouts.

## Candidate matrix

Metadata from the official Hugging Face model API on 2026-11-13 (revision = commit SHA at that time). Byte counts
are the published files; none were downloaded. **Size is not a performance result**: no candidate has been run.

| candidate | licence / access | pinned revision | runtime file (bytes) | KV cache f16 per token | at 1024 / 2048 ctx |
|---|---|---|---|---|---|
| Qwen3-0.6B | Apache-2.0, ungated | `Qwen/Qwen3-0.6B-GGUF@23749fef` | Q8_0 GGUF 639,446,688 | 112 KiB (28 L × 8 KV × 128) | 112 / 224 MiB |
| Qwen3-1.7B | Apache-2.0, ungated | `Qwen/Qwen3-1.7B-GGUF@90862c4b` | Q8_0 GGUF 1,834,426,016 | 112 KiB (28 L × 8 KV × 128) | 112 / 224 MiB |
| SmolLM2-1.7B-Instruct | Apache-2.0, ungated | `HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF@2d4a76a3` | Q4_K_M GGUF 1,055,609,536 | 192 KiB (24 L × 32 KV × 64) | 192 / 384 MiB |
| Gemma 3 1B IT | Gemma Terms of Use (not OSI); Google repos gated | `ggml-org/gemma-3-1b-it-GGUF@f9c28bcd` (ungated mirror) | Q4_K_M GGUF 806,058,240 | not verified (config gated) | not verified |
| Gemma 3 1B IT (LiteRT) | Gemma terms; gated (auto) | `litert-community/Gemma3-1B-IT@a9bd8564` | q4 ekv4096 .litertlm 584,417,280 | runtime-managed | n/a |
| Llama 3.2 1B Instruct | Llama 3.2 Community Licence; gated (manual) | `meta-llama/Llama-3.2-1B-Instruct@92131767` | safetensors 2,471,645,608 (no GGUF checked) | not verified (config gated) | not verified |

Base-model revisions: `Qwen/Qwen3-0.6B@c1899de2`, `Qwen/Qwen3-1.7B@70d244cc`,
`HuggingFaceTB/SmolLM2-1.7B-Instruct@31b70e2e`, `google/gemma-3-1b-it@dcc83ea8`. The official Qwen GGUF repos
publish Q8_0 only; a 4-bit Qwen file would be a local requantization (or a third-party file) and needs its own pin.
Qwen3's thinking mode must be off (`enable_thinking=false`); the grammar forces JSON from the first output token.

Budgets for one request: about 150 tokens of instructions + up to about 550 tokens of context + at most about 290
output tokens, so a 1024-token context is enough. With Qwen3-0.6B Q8_0 that is about 610 MiB of weights plus 112
MiB of KV cache before runtime overhead; measured peak memory replaces this estimate.

**Proposed first pair to measure**, once a route exists: Qwen3-0.6B (smallest, Apache-2.0, ungated, GGUF + GBNF)
against Gemma 3 1B (strong small instruction model, if its licence is acceptable to Nathan) or Qwen3-1.7B. Both on
llama.cpp CPU with the same grammar and cases first, so the runtime is held constant. No fine-tuning is proposed
until at least two candidates have device numbers and validity/usefulness on the same cases.

## Runtimes

| runtime | format | constrained output | notes |
|---|---|---|---|
| llama.cpp (MIT) | GGUF | GBNF grammars, native | Android arm64 CPU build via NDK; GPU backends exist but Tensor G5 GPU support is unverified. Version not pinned: its GitHub release metadata is not reachable from this session. |
| LiteRT-LM (Apache-2.0) | .litertlm | not verified | Google's on-device LLM runtime; GPU/NPU paths on Pixel are plausible but unverified; model choice mostly Gemma. Version not pinned (same reason). |
| AICore / Gemini Nano | system service | n/a | needs Google's AICore; unlikely on RoseOS and not ours to bundle. Excluded. |

## Benchmark plan (bench.py records every field; unmeasured ones stay null)

- Per record: device, OS build, runtime + version, backend, model + revision + quantization, context tokens,
  threads; case, trigger, run index, phase (`first` in process / `warm`).
- **Generation-only** (`generationMs`, plus runtime prompt/output tokens and ms) separately from **composer**
  (`composeMs`: context + generation + validation). Neither is request-to-render.
- **Request-to-render** needs OS Product's renderer completion timestamp; `requestToRenderMs` stays null until then.
- **True cold start** (fresh process, model load) is a device measurement; `first` here is only the first call in
  a process.
- Validity rate and failure categories; usefulness as recall and precision of each case's expected IDs (the
  no-model baseline scores recall 1.0 and precision 0.27 because it shows everything), replaced by human review of
  real layouts.
- Peak memory (`peakRssMb`) and repeat-run energy (`energyMj`) only from an admitted on-device route.
- Target: warm composer time under one second. This is a target, not a result.

## Blockers (exact)

1. **No phone route.** No admitted Pixel 10 install or measurement route exists for Models; phone deployment and
   new device access are not authorized. Device, RAM and backend for measurement are to be named by whoever admits
   the route.
2. **No on-device runtime.** There is no llama.cpp or LiteRT-LM build in the repo; runtime installs and weight
   downloads are not authorized for this assignment.
3. **Runtime versions unpinned.** GitHub release metadata for ggml-org/llama.cpp and google-ai-edge/LiteRT-LM
   returns 403 in this session (repositories not enabled); not routed around.
4. **Gated weights.** Gemma (Google repos) and Llama 3.2 need licence acceptance with a Hugging Face account, which
   is a credential; their configs (and so KV budgets) are unverified. Gemma's licence also needs Nathan's call.
5. **Contract confirmation.** The real catalog, ID format and first-build item kinds are OS Product's; the request
   fields are App Core's. Both are proposals in `contract/rose-layout-v1.md`.
6. **Renderer timestamp.** Request-to-render latency needs OS Product's renderer completion time.
7. **CI.** This directory is not wired into shared CI; tests run locally.
