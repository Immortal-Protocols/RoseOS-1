# Muse Glimmer serving draft (MODELS-CORE-ONBOARD-001)

This is a source and configuration draft for serving the candidate model behind the spoken name
"Muse Glimmer" on Agent Platform. Nothing here is deployed or measured. Nathan still has to select the model.
Each fact below is labeled **verified** (read from a pinned file or primary documentation), **derived**
(arithmetic from verified inputs) or **open** (unresolved, with the decision or evidence it needs).

- `muse-glimmer-30b.json`: model pin, served alias, weight files, license obligations, both serving options,
  Agent Platform `containerSpec`/`machineSpec` drafts, the test envelope, load-test thresholds and the wire contract.
- `capacity.py`: per-GPU fit and an 8K/16K × 1/4-sequence sensitivity matrix (`python3 capacity.py --check`).
- `tests/test_serving_config.py`: offline consistency checks, part of `scripts/test-local.sh`.

## Model (verified)

| Item | Value | Evidence |
| --- | --- | --- |
| Repository | `meta-models/Muse-Glimmer-30B`, not gated | HF API, 2026-10-28 |
| Revision | `cec94fcfd6512f2cfe708138c32533b346ea4f69` (main, 2026-10-22). Weights landed in `46ac57a4502b`; later commits are titled README, generation_config or eval results | HF commit list (titles, not blob diffs) |
| Served alias | API model id `muse-glimmer` = `meta-models/Muse-Glimmer-30B@cec94fcf…`. The alias hides the revision, so the deploy record must keep the container args, and the load test checks `/v1/models` | Config |
| Weight files | `model-00001-of-00002.safetensors` 49,950,112,952 B; `model-00002-of-00002.safetensors` 9,603,322,320 B; `tokenizer.json` 28,129,897 B; about 59.58 GB in total. No NOTICE file | HF file list at the revision |
| Architecture | `MuseGlimmerForConditionalGeneration`: 52 text layers (13 global, 39 with a 2,048 sliding window), 32 Q / 2 KV heads, head_dim 128, vocab 202,048, ~1.8–2B vision tower | `config.json` |
| Size | 29,776,626,688 BF16 parameters | HF safetensors metadata |
| Context | 131,072 maximum; the draft caps it at 16,384 | `config.json` |
| Sampling | temperature 1.0, top_p 0.95, top_k 64. Stops at 200001/200008. Never greedy | `generation_config.json`, Meta guide |

## Terms for Nathan's decision (verified text; the owner accepts)

- **Apache-2.0** (LICENSE sha256 `cfc7749b…3d30`):
  - Grants use, modification and distribution, including commercial use, with a patent license (§3). The patent license ends for anyone who files a patent suit over the work.
  - Redistribution conditions (§4) apply only if weights or derivatives are passed on: include the license, mark modified files, keep notices. There is no NOTICE file at this revision. Serving the model behind an endpoint is not redistribution.
  - No trademark rights (§6). No warranty (§7–8).
- **Meta `USAGE_POLICY.md`** says it "applies to your access or use". Its prohibited uses include:
  - illegal activity, harassment, unlicensed professional practice, and collecting or inferring sensitive personal information without the right to;
  - malware, and circumventing safety measures;
  - weapons, critical infrastructure and self-harm;
  - fraud, impersonation, and **representing outputs as human-generated**;
  - **failing to disclose known dangers to end users**.

  How this policy binds alongside the Apache grant is a legal judgment for the owner.
- **Under 18:** the policy says the model "is not intended for individuals under the age of 18". The model card adds that where a system may be used by under-18s, the deployer must assess and mitigate the risks and comply with the law.

## Runtime

| Item | Status |
| --- | --- |
| vLLM support | **Verified:** registry at tags v0.28.0, v0.30.0 and v0.32.0 maps the architecture and the DFlash drafter. Meta's guide gives `vllm/vllm-openai:muse-glimmer` (~10.5 GB, amd64/arm64, CUDA 13.0, `ENTRYPOINT ["vllm","serve"]`), parsers `muse_glimmer`, `--generation-config auto`, no `--chat-template`, and "about 72 GB of VRAM to serve". |
| Image digest | **Open:** the tag floats. A digest read is held behind the access boundary (see Access). |
| CUDA 13.0 on Agent Platform | **Open:** no Google page states the serving-node driver. Google's prebuilt prediction containers are labeled CUDA 12.x. CUDA 13.0 needs an R580-or-newer driver (NVIDIA's rule, not checked against a node). The first deploy must show `nvidia-smi` or the engine start, or fail fast. |
| transformers | **Verified:** `models/muse_glimmer/` is present in both the 5.20.0 and 5.21.0 PyPI wheels. The earlier 5.21 "gap" was a GitHub raw-path 404 and is closed. |
| llama.cpp (option B) | **Verified (Meta guide):** build `b10353` or newer. **Open:** image tag and digest. |

## Fit (derived, not measured)

Option A: `a2-highgpu-2g`, 2× A100 40GB, BF16 vLLM, TP=2, `--gpu-memory-utilization 0.90`, i.e. 38.65 GB usable per GPU.
Worst-case assumptions:
- the vision tower is replicated on each GPU, giving 31.78 GB of weights per GPU (29.78 GB if it shards);
- KV is allocated for all 52 layers with no sliding-window savings.

| Context × sequences | Headroom at 3 GiB runtime reserve | Headroom at 5 GiB reserve |
| --- | --- | --- |
| 8K × 1 | 3.44 GB | 1.29 GB |
| 8K × 4 | 2.78 GB | 0.64 GB |
| **16K × 1 (proposed)** | **3.22 GB** | **1.07 GB** |
| 16K × 4 | 1.91 GB | −0.24 GB (does not fit) |

- **Proposal:** 16,384-token context, `--max-num-seqs 1`. That matches the one-conversation demo and stays positive under the pessimistic reserve.
- **Runtime reserve:** this covers CUDA context, CUDA graphs, activations, NCCL buffers and allocator fragmentation. It is an assumption, not a measurement.
- **Low-risk fallbacks,** in order: 8K context, then `--enforce-eager` (Meta's tight-memory variant).

Option B (labeled alternative, official Q4_K_M, about 1.0% average degradation per the card): `g2-standard-12`, 1× L4, about 4.4 GB of
headroom at 16K × 1. Decode ceilings from bandwidth ÷ weights: A ~52 tok/s, B ~18 tok/s. Neither is measured.

### Load test: abort and accept (proposal)

**Abort and tear down if any of these happens:**
- an OOM or engine exit at startup;
- the replica is not healthy within 3600 s;
- the engine reports less than 1.0 GiB of KV cache per GPU, or concurrency below 1.0 at 16,384 tokens;
- peak GPU memory goes above 95% on either GPU;
- spend reaches the approved cap.

**Accept when all of these pass:**
- `/health` returns 200;
- `/v1/models` lists `muse-glimmer` with root `meta-models/Muse-Glimmer-30B`;
- a two-turn non-streaming chat returns non-empty `choices[0].message.content`;
- one ~16K-token prompt completes.

The exact wording of vLLM's startup log messages is unverified.

## Time and cost envelope

The manager's proposal is one replica for a four-hour test, with a six-hour teardown allowance.
- `deploymentTimeout` is 3600 s, matching the startup probe (30 s × 120). A failed load therefore aborts within the first hour, leaving at least three hours of test time. The documented maximum of 7200 s would consume half the window.
- **Pricing:** the published on-demand `a2-highgpu-2g` inference rate the manager supplied (**$8.4489898/h**) already covers the machine. **No separate management fee is added for on-demand.** Compute reservations bill separate infrastructure and management SKUs.
- **Derived cost:** about $33.80 for 4 h and about $50.69 for 6 h for option A, before storage.

## Resources and quantities (nothing created)

| Resource | Quantity / path (proposal) | Operations needed by an existing approved principal |
| --- | --- | --- |
| Image mirror | `us-central1-docker.pkg.dev/{project}/models-serving/vllm-openai@sha256:{digest}`, ~10.5 GB compressed. It must be a regional repo in the endpoint's region | `artifactregistry.repositories.create` (once), `artifactregistry.repositories.uploadArtifacts`. The Agent Platform service agent pulls with `downloadArtifacts` (same-project default unverified) |
| Weights | Pinned Hugging Face download at container start: 59.58 GB per replica start, inbound | Needs public egress from the endpoint, plus node disk of at least ~60 GB of weights plus the unpacked image. Node disk size is **open** |
| Optional GCS staging | 59.58 GB (A) or 16.76 GB (B) | `storage.buckets.create`, `storage.objects.create/get/delete`. Neither server reads `gs://` from `AIP_STORAGE_URI` natively, so this would need a copy step and a custom image. Not proposed |
| Model and endpoint | 1 Model, 1 Endpoint, 1 replica of `a2-highgpu-2g` in us-central1 | `aiplatform.models.upload`, `aiplatform.endpoints.create`, `aiplatform.endpoints.deploy`; clients need `aiplatform.endpoints.predict`; cleanup needs `aiplatform.endpoints.undeploy`, `aiplatform.endpoints.delete`, `aiplatform.models.delete` and the Artifact Registry delete |

Container: port 8080, `healthRoute /health`, `predictRoute /v1/chat/completions`, `sharedMemorySizeMb 16384` (TP=2),
startupProbe `httpGet /health` every 30 s for up to 120 failures. `invokeRoutePrefix` is not set. The Probe reference contradicts itself on
`periodSeconds` versus `timeoutSeconds`; the draft uses equal values.

## Wire contract for Product and QA

- **Request:** `POST {base}/v1/chat/completions` with `"model": "muse-glimmer"`.
- **Agent Platform:** `POST https://us-central1-aiplatform.googleapis.com/v1/projects/{project}/locations/us-central1/endpoints/{endpoint}:rawPredict`.
  - Header: `Authorization: Bearer` with a Google OAuth token.
  - Permission: `aiplatform.endpoints.predict`.
  - Body: unchanged.
  - Streaming: `:streamRawPredict`.
- **Response:** the answer is `choices[0].message.content`. vLLM puts thinking separately in `choices[0].message.reasoning`.
- **Sampling:** omit the sampling fields; never send `temperature: 0`.
- **Body size:** the 1.5 MB request/response limit applies to shared public endpoints only.
- **Not verified live:** error bodies and status codes.

## Access

**Record:**
- **Before the denial:** raw GitHub reads of vLLM `registry.py` at tags v0.28.0, v0.30.0 and v0.32.0.
- **The denial:** `GET api.github.com/repos/vllm-project/vllm/pulls/51655` returned "GitHub access to this repository is not enabled for this session."
- **After the denial:** one raw read, `muse_glimmer.py` at v0.32.0, disclosed. No vLLM access of any kind since.

**Smallest owner clarification to close the digest:**
- **Who and which provider:** one named actor reads Docker Hub (`registry-1.docker.io`), image `vllm/vllm-openai:muse-glimmer`.
- **Operation:** a single manifest/index `HEAD`/`GET`, which returns the index digest and the linux/amd64 manifest digest. A few kilobytes; no layers, no image pull, no weights.
- **Optional:** one `GET https://recipes.vllm.ai/meta-models/Muse-Glimmer-30B`, which Meta names as the source of truth.
- **Why current permission doesn't settle it:** the hold covers vLLM-org artifacts after the GitHub denial. Docker Hub is a different provider, but it is still a vLLM-org channel, so it needs an explicit yes or no.

## Owner decisions and open evidence

**Owner decisions:**
1. Select the model and pin.
2. Accept Apache-2.0 plus the Usage Policy, including the under-18 statement.
3. Confirm option A (or B), us-central1, a four-hour window and the spend cap.
4. Approve the image mirror and the Hugging Face startup download.
5. Decide the digest-read clarification.

**Open evidence:**
- The image digest.
- Whether the Agent Platform serving nodes support CUDA 13.0.
- Node disk size.
- Every live load, latency and error result.
