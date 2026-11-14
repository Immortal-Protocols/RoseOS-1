# Project 2 local decision model

**Current architecture:** Nathan corrected the model location on 2026-11-14: inference runs locally in the
**Rose server on the Mac**, and the phone consumes its decisions through the existing native WorkspacePage.
This supersedes this directory's earlier phone-local/offline design. It reuses OS Product's client (#818) and the agreed envelope `{schemaVersion:1, requestId, request}`.
No renderer format changes, generated
UI code, phone model runtime, new service listener, cloud endpoint or VM are required.

The implemented path is:

```
WorkspaceHub -> WorkspaceDecisionClient -> paired/pinned TLS POST /workspace/v1/compose
 -> Rose server WorkspaceModel -> owned persistent Java/JNI model worker
 -> validated rose-layout-v1 -> WorkspaceComposer.finish -> existing native renderer and saved state
```

Text, transcribed conversational requests, Arrange and Regenerate share this path. Undo and pins remain on
the phone. Connection, model, validation or cancellation failures retain the last usable layout. Voice
transcription remains the existing Rose voice path; this change does not claim offline voice or offline layouts.

## Model and runtime

- Qwen/Qwen3-1.7B-GGUF, Q8_0, Apache-2.0, revision `90862c4b9d2787eaed51d12237eafdfe7c5f6077`.
- Weights: `Qwen3-1.7B-Q8_0.gguf`, 1,834,426,016 bytes,
  SHA-256 `061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a`.
- llama.cpp (MIT), revision `dca216ad63c586f0db465324f785c5a418faef58`, JNI in an owned server child,
  Metal on Apple hosts with CPU fallback. No network model fallback or extra model HTTP listener.
- The model loads once per worker, with serialized requests. The server bounds request/reply sizes, startup,
  IPC and cancellation. It terminates only its own model child when it cannot safely continue.

Selection work compared Qwen3 0.6B and 1.7B on the public fixture. Asking either to reproduce the full layout
schema produced duplicate/mismatched references. The implementation instead asks 1.7B for a short selection of
available item names and display styles. A per-request GBNF restricts choices to known names, each at most once.
The adapter maps them to context aliases, retains explicitly named available items, and produces the existing
layout schema. Both Models' Validator and the phone's WorkspaceLayout.check still check the final result.
Context selection, pins, avoid hints, real-ID mapping, limits and typed failures reuse the existing core.
No fine-tuning was needed. Selection remains probabilistic; these checks establish functioning examples, not
perfect relevance for arbitrary requests.

## Prepare without starting Rose

Requires CMake, a C++ toolchain, JDK 21 and the authenticated `hf` CLI. Existing Hugging Face authentication is
used by the CLI; no credential is read, copied or included in an artifact.

```sh
JAVA_HOME=/path/to/jdk21 python3 workspace/models/local-composer/runtime/prepare.py
```

Optional `--llama-source`, `--model`, `--java-home` and `--output` reuse existing local inputs. Source and weights
must match the pins. Default output is ignored `.tools/local-composer/`: `composer.jar`, a native library and
`worker.json` containing its exact command, source hashes and artifact hashes. Downloaded dependencies and
weights stay outside Git. This is preparation, not a deployment or server restart.

On the next authorized Rose start, a prepared default `.tools/local-composer/worker.json` is discovered.
`ROSE_WORKSPACE_MODEL_CONFIG=/absolute/path/worker.json` selects another prepared output. No configuration
means the compose endpoint is absent and the phone reports unavailable. Existing task gates are independent.

## Checks

```sh
cd workspace/models/local-composer
python3 -m unittest discover -s tests -v
bash java/run-tests.sh
```

```sh
PYTHONPATH=prototypes/voice-phone-demo /path/to/rose/venv/bin/python -m unittest rose.tests.test_workspace_model -v
```

The opt-in real-model check uses synthetic pairing keys, the production signed ASGI route and the public
snapshot. It binds no port, starts no conversation server and touches no existing pairing or worker:

```sh
/path/to/rose/venv/bin/python workspace/models/local-composer/runtime/smoke.py \
  --config .tools/local-composer/worker.json --output out/local-composer-smoke.jsonl
```

`WorkspaceRenderedModelTest` consumes those results through the phone's real validator and saved state. This
proves renderer data acceptance and persistence, not a phone draw. `scripts/test-local.sh` includes the native
workspace/client contract suite. The historical Python benchmark utility remains available but is not the
product path or a delivery requirement.

## Remaining source boundaries

WorkspaceSources currently supplies real apps, launcher shortcuts and intent templates. Its notifications
and content sources are still empty pending OS Product's registry work. The model/renderer support those
types when present; the smoke uses clearly synthetic notification/content items and never claims they exist
on a phone. This implementation adds no notification access permission or invented app capability.

The stopped conversation server was not restarted. Runtime activation and a signed client deployment remain
with the existing owners. No phone run, physical action acceptance or publication is implied by a host check.
The previous session's denied acquisition record is retained in `runtime/acquisition-manifest.json` as historical
evidence; its authorization/runtime claims are not the current Mac session's state.
