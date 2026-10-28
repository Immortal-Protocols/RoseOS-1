# Muse Glimmer serving draft (MODELS-CORE-ONBOARD-001)

This is a source and configuration draft for serving the candidate model behind the spoken name
"Muse Glimmer" on Agent Platform. Nothing here is deployed or measured. Nathan still has to select the model.
Capacity figures are arithmetic from the pinned config. No weights, images, GPUs or cloud
resources were used to produce this draft.

- `muse-glimmer-30b.json`: model pin, license obligations, the two serving options, the Agent Platform
  `containerSpec`/`machineSpec` drafts, and the wire contract.
- `capacity.py`: derives per-GPU fit from that file (`python3 capacity.py --check`).
- `tests/test_serving_config.py`: keeps pins, flags, routes and arithmetic consistent. It is offline and part of
  `scripts/test-local.sh`.

## Model

| Item | Value | Evidence |
| --- | --- | --- |
| Repository | `meta-models/Muse-Glimmer-30B`, not gated | HF API, 2026-10-28 |
| Revision | `cec94fcfd6512f2cfe708138c32533b346ea4f69` (main, 2026-10-22). Weights landed in `46ac57a4502b`; later commits are titled README, generation_config or eval results | HF commit list (titles, not blob diffs) |
| Architecture | `MuseGlimmerForConditionalGeneration`: 52 text layers (13 global, 39 with a 2,048 sliding window), 32 Q / 2 KV heads, head_dim 128, vocab 202,048, ~1.8–2B vision tower | `config.json` at the revision |
| Size | 29,776,626,688 BF16 parameters; 59.58 GB repository | HF safetensors metadata |
| Context | 131,072 (`max_position_embeddings`) | `config.json` |
| Sampling | temperature 1.0, top_p 0.95, top_k 64. Stops at 200001/200008. Never greedy | `generation_config.json`, Meta guide |
| License | Apache-2.0 (LICENSE sha256 `cfc7749b…3d30`, 11,358 bytes) plus Meta `USAGE_POLICY.md` | Files at the revision |

The license obligations are: keep the LICENSE/NOTICE text with any redistributed weights; the Usage Policy's prohibited uses
apply to Rose; and the model is not intended for people under 18, so a deployer must assess and mitigate that.
Acceptance is an owner decision.

## Runtime

| Runtime | Support evidence | Gap |
| --- | --- | --- |
| vLLM | Registry at tags v0.28.0, v0.30.0 and v0.32.0 maps the architecture and the DFlash drafter. Meta's guide gives image `vllm/vllm-openai:muse-glimmer` (~10.5 GB, CUDA 13.0, `ENTRYPOINT ["vllm","serve"]`), parsers `muse_glimmer`, `--generation-config auto`, no `--chat-template`, and "about 72 GB of VRAM to serve" | The tag floats. No digest was read: vLLM-org artifact access is paused (see Access). The Agent Platform GPU driver must support CUDA 13.0; this is unverified. `--limit-mm-per-prompt` text-only behavior is not in Meta's guide |
| transformers | `models/muse_glimmer/` and `muse_glimmer_assistant/` are present in both the 5.20.0 and 5.21.0 PyPI wheels (sha256 `5f4afd20…685b`, `8803a871…1c79`). Meta asks for `transformers>=5.15` | The earlier 404 at GitHub tag `v5.21.0` was a raw-path lookup, not a removal. That item is closed |
| llama.cpp | Meta's guide requires build `b10353` or newer (support commit `62bf73d25`). Official GGUF files are in `meta-models/Muse-Glimmer-30B-GGUF@b44f1072…` | Image tag and digest unresolved |

## Serving options (us-central1)

Both options assume text only, one conversation and a 16,384-token context with no drafter. These are the manager's
comparison assumptions.

| | A: `a100x2-bf16-vllm` (primary) | B: `l4x1-q4km-llamacpp` (labeled alternative) |
| --- | --- | --- |
| Machine | `a2-highgpu-2g`: 2× A100 40GB, 24 vCPU, 170 GB RAM | `g2-standard-12`: 1× L4, 12 vCPU, 48 GB RAM |
| Zones | us-central1-a/b/c/f (A2 Standard) | us-central1-a/b/c (G2) |
| Weights | Official BF16, TP=2 | Official Q4_K_M, 16,756,683,904 bytes; card states 1.0% average degradation |
| Derived per-GPU fit | 38.65 GB usable at 0.90; weights 29.78–31.78 GB; worst-case KV 1.74 GB; 3.2 GB reserve; **~1.9 GB headroom** | 24.15 GB; weights 16.76 GB; KV 0.87 GB; 2.1 GB reserve; ~4.4 GB headroom |
| Decode ceiling (bandwidth ÷ weights, not measured) | ~52 tok/s | ~18 tok/s |
| Quota seen by Chief (not capacity) | Agent Platform custom-serving A100: 14 | L4: 28 |

Option A is tight. Its worst case assumes the vision tower is replicated and that KV for four 16K sequences is
allocated without sliding-window savings. vLLM preallocates the remaining memory as KV after profiling, so a
startup OOM is the failure to watch. If that happens, lower `--max-model-len` or `--max-num-seqs` first. A single 80 GB
A100 has zero observed quota and is not proposed.

Priceable quantities (manager/Chief):
- Option A: one `a2-highgpu-2g` node-hour per demo hour while deployed, plus the Agent Platform management fee.
- Option B: one `g2-standard-12` node-hour, plus the same fee.
- Both options: one Artifact Registry repository in us-central1 holding the mirrored image (~10.5 GB for vLLM).
- If weights are staged rather than pulled at startup: 59.6 GB (A) or 16.8 GB (B) of GCS storage.

## Agent Platform container mapping

- The image must be in a regional (not multi-region) Artifact Registry repository in the endpoint's region. That repository
  and the mirror are owner-approved resources and do not exist yet, so `imageUri` is `null`.
- `ports: [{containerPort: 8080}]`. vLLM and llama-server listen on `0.0.0.0:8080`.
- `healthRoute: /health`. Agent Platform marks a replica unhealthy after a non-200 or a 10-second timeout.
  Both servers return 200 only once the model is loaded.
- `predictRoute: /v1/chat/completions`. Callers use `:rawPredict` (non-streaming) or `:streamRawPredict` with the
  OpenAI body unchanged. `invokeRoutePrefix` is not set because it excludes `predictRoute` and requires a dedicated endpoint.
- `startupProbe` on `/health` with up to 60 min of loading time (A) or 30 min (B). `deploymentTimeout` is `7200s`, the documented maximum.
- `sharedMemorySizeMb: 16384` for TP=2 NCCL (option A only), in place of Docker's `--ipc=host`.
- Weights source is open. A pinned Hugging Face download at startup needs public egress and roughly 60 GB of local disk,
  and the node disk size is unverified. `artifactUri` gives a read-only `gs://` copy as `AIP_STORAGE_URI`, but neither
  server reads `gs://` natively, so staging would need a copy step.
- The Probe reference contradicts itself on `periodSeconds` versus `timeoutSeconds`. The draft uses equal values; check this at the first deploy.

## Wire contract for Product and QA

- **Local / fixture:** `POST /v1/chat/completions` with `"model": "muse-glimmer"` and OpenAI `messages`.
- **Agent Platform:** `POST https://us-central1-aiplatform.googleapis.com/v1/projects/{project}/locations/us-central1/endpoints/{endpoint}:rawPredict`
  - Header: `Authorization: Bearer <Google OAuth token>`.
  - Permission: the caller needs `aiplatform.endpoints.predict`.
  - Body: unchanged.
- **Streaming:** `:streamRawPredict` with `"stream": true`. Meta recommends streaming for long generations.
- **Response fields:** the answer is in `choices[0].message.content`. With vLLM's reasoning parser, thinking is in `choices[0].message.reasoning`, separate from the answer.
- **Reasoning strength:** a `Reasoning strength: low|medium|high|xhigh` system line, or `chat_template_kwargs.reasoning_strength` (template default `high`).
- **Do not send:** `temperature: 0`, or a custom chat template.
- **Not verified live:** status codes, error-body shape, and the 1.5 MB limit (which applies to shared public endpoints only).

## Access

Chief's boundary pauses access to vLLM-org artifacts. The record:
- **Before the denial:** raw GitHub reads of `vllm/model_executor/models/registry.py` at tags v0.28.0, v0.30.0 and v0.32.0.
- **The denial:** `api.github.com/repos/vllm-project/vllm/pulls/51655` was refused with "GitHub access to this repository is not enabled for this session."
- **After the denial:** one more raw read, `vllm/model_executor/models/muse_glimmer.py` at v0.32.0.

No further vLLM GitHub, recipe, PyPI or image-registry reads have been made since. The image digest and a first-hand read of the recipe stay open until that boundary is set.

## Not established

- Owner selection of the model.
- The image digest.
- Agent Platform driver support for CUDA 13.0.
- Model Garden catalog status.
- Physical capacity, prices and credits.
- Any live load, latency or request result.
