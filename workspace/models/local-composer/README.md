# Local composer (Project 2, LOCAL-COMPOSER-001/002)

Phone-local layout composition for the workspace page one swipe left of Home: a button press ("best guess") or
direct text becomes a validated layout **on the phone, offline**. No server generates layouts. Models owns this
directory only: context selection, the constrained model call, validation and benchmarking. OS Product owns the
catalog, snapshot, ID registry, renderer, persistence, pin/undo and last-usable layout. App Core owns request
routing. Nathan approved the Project 2 HiFi direction (r2, `prototypes/rose-hifi`, source `6fcf8c9b`) on
2026-11-14; native layout, state and rendering are OS Product's. Models does not build UI, an app module or a
service: it provides the Python reference and a Java core the app can call.

Status (2026-11-14): **source and offline tests only.** No model has been run, nothing has been built for a
phone, nothing has been deployed, and nothing has been measured on a device. Runtime access limits are
unchanged by the design approval.

| evidence level | state |
|---|---|
| source | this directory: contract proposal, Python reference, Java core, 55 shared vectors, 38 Python tests (one of which runs the 1,318 Java checks) |
| compilation | Java compiled with `javac --release 8` on a host JDK (Rose Agent's source level); not compiled against `android.jar` or dexed here (no Android SDK in this workspace) |
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
contract/vectors/v1.json       shared vectors, generated from the Python reference (tests/make_vectors.py)
java/src/rose/models/composer/ Java 8 core for the app: Contract, ContextSelector, Grammar, Validator, Composer,
                               LocalInference (the runtime seam), ScriptedInference (labelled, not a model), Json
java/test/.../ComposerTests    replays every vector plus cancel/deadline/crash/goal-boundary/JSON checks
java/run-tests.sh              compiles at --release 8 into java/out/ (ignored) and runs the Java tests
tests/test_composer.py         offline unit, negative, fuzz and harness tests
tests/test_parity.py           vectors are current; Java reproduces them (skips with a reason if no JDK)
```

```bash
cd workspace/models/local-composer && python3 -m unittest discover -s tests -v
```

```bash
cd workspace/models/local-composer && python3 -m composer.bench --cases contract/examples/cases.json --repeats 3
```

```bash
cd workspace/models/local-composer && python3 tests/make_vectors.py && bash java/run-tests.sh
```

## Java core (for the app)

`rose.models.composer` is plain Java 8 with no Android or `org.json` types, so it compiles inside Rose Agent
(`-source 8`, `minSdkVersion 29`) and on a host JDK. The caller passes the request as JSON-shaped maps (or parses
a string with `Json.parse`) and an on-device `LocalInference`:

```java
Composer composer = new Composer(Catalog.fromJson(osProductCatalog), localInference);
ComposeResult r = composer.compose(request, cancellation);        // ok + layout of real IDs, or failed + category
ComposeResult shown = Validator.checkFresh(r, currentSnapshotVersion); // stale_snapshot just before rendering
```

- `LocalInference.generate(Request)` receives the system and user text, the per-request GBNF, an output-token
  cap, the time left and a cancellation token; it returns untrusted text. Any exception other than `Refused`
  (`timeout`, `cancelled`) becomes `model_unavailable`. No binding to llama.cpp or LiteRT-LM is included: none is
  admitted.
- Every behaviour is checked against the Python reference through `contract/vectors/v1.json`: the selected
  aliases and exact model-visible lines, the GBNF text (SHA-256), the output-token cap and the result. Lengths
  are counted in code points on both sides, and Python's whitespace rules are mirrored.
- Where this core and OS Product's native composer meet (who calls it, on which thread, with which catalog) is
  OS Product's design; nothing here registers a provider, service or UI.

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

## Waiting on OS Product (the dependent pieces, not implemented)

1. **Catalog.** HiFi r2's card set is `apps`, `actions`, `carousel`, `list`/checklist and `notif`; the code reads
   all rules from catalog data, but `cat-1` is a proposal until OS Product names the native catalog and version.
2. **Pins.** HiFi pins whole cards at fixed positions. If native does the same, the request needs the pinned cards
   as fixed input (slots plus ids the composer must not repeat or move); today `pinned` only gives items priority.
3. **Regenerate.** Same context plus deterministic decoding gives the same layout. App Core recommends a fresh
   snapshot plus the previous layout's ids as a soft avoid hint; OS Product decides the input.
4. **Real IDs and action shapes** for the first build.

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
5. **Contract confirmation.** App Core confirmed its side (PR 782 §4.4). The real catalog, ID format, first-build
   item kinds, pin semantics and regenerate input are OS Product's (see above).
6. **Renderer timestamp.** Request-to-render latency needs OS Product's renderer completion time.
7. **CI.** This directory is not wired into shared CI; tests run locally.
8. **Android compile.** No Android SDK is present, and downloading one is outside this source-only scope, so the
   Java core has not been compiled against `android.jar` or dexed. It uses only `java.lang`, `java.util`,
   `java.util.regex` and `java.math` APIs available at Rose Agent's `minSdkVersion` 29.
