# Muse Glimmer serving draft (MODELS-CORE-ONBOARD-001)

This is a source and configuration draft for serving the candidate model behind the spoken name
"Muse Glimmer" on Agent Platform. Nothing here is deployed or measured, and no image or weights were pulled.
Nathan selected Muse Glimmer for the Rust chat prototype (authenticated PM transcript relayed by Chief at 21:50 UTC; Core did not see the transcript itself). The first run is 8,192 tokens of context, one sequence, BF16, TP=2. The 16K figures below are comparison only.
Each fact below is labeled **verified** (read from a pinned file, registry manifest or primary documentation),
**derived** (arithmetic from verified inputs) or **open** (unresolved, with the decision or evidence it needs).

- `muse-glimmer-30b.json`: model pin, served alias, weight files, license obligations, both serving options,
  image digests, Agent Platform `containerSpec`/`machineSpec` drafts, the prompt envelope, the one-attempt test envelope,
  load-test thresholds, cleanup, and the wire contract.
- `capacity.py`: per-GPU fit and an 8K/16K × 1/4-sequence sensitivity matrix (`python3 capacity.py --check`).
- `startup_guard.py`: the pre-download node check run inside the container before `vllm serve`. Runtime-only; it imports nothing from this repository.
- `render_upload.py`: renders the Model upload request body offline (see Upload renderer).
- `tests/`: offline checks for the config and guard, part of `scripts/test-local.sh`.

## Model (verified)

| Item | Value | Evidence |
| --- | --- | --- |
| Repository | `meta-models/Muse-Glimmer-30B`, not gated | HF API, 2026-10-28 |
| Revision | `cec94fcfd6512f2cfe708138c32533b346ea4f69` (main, 2026-10-22). Weights landed in `46ac57a4502b`; later commits are titled README, generation_config or eval results | HF commit list (titles, not blob diffs) |
| Served alias | API model id `muse-glimmer` = `meta-models/Muse-Glimmer-30B@cec94fcf…`. The deploy record keeps the container args; the load test checks `/v1/models` | Config |
| Weight files | `model-00001-of-00002.safetensors` 49,950,112,952 B; `model-00002-of-00002.safetensors` 9,603,322,320 B; `tokenizer.json` 28,129,897 B; ~59.58 GB total. No NOTICE file | HF file list at the revision |
| Architecture | `MuseGlimmerForConditionalGeneration`: 52 text layers (13 global, 39 with a 2,048 sliding window), 32 Q / 2 KV heads, head_dim 128, vocab 202,048, ~1.8–2B vision tower | `config.json` |
| Size | 29,776,626,688 BF16 parameters | HF safetensors metadata |
| Sampling | temperature 1.0, top_p 0.95, top_k 64. Stops at 200001/200008. Never greedy | `generation_config.json`, Meta guide |

## Terms for Nathan's decision (verified text; the owner accepts)

- **Apache-2.0** (LICENSE sha256 `cfc7749b…3d30`):
  - Grants use, modification and distribution, including commercial use, with a patent license (§3). The patent license ends for anyone who files a patent suit over the work.
  - Redistribution conditions (§4) apply only if weights or derivatives are passed on: include the license, mark modified files, keep notices. There is no NOTICE file at this revision. Serving behind an endpoint is not redistribution.
  - No trademark rights (§6). No warranty (§7–8).
- **Meta `USAGE_POLICY.md`** says it "applies to your access or use". Its prohibited uses include:
  - illegal activity, harassment, unlicensed professional practice, and collecting or inferring sensitive personal information without the right to;
  - malware, and circumventing safety measures;
  - weapons, critical infrastructure and self-harm;
  - fraud, impersonation, and **representing outputs as human-generated**;
  - **failing to disclose known dangers to end users**.

  How it binds beside the Apache grant is a legal judgment for the owner.
- **Under 18:** the policy says the model "is not intended for individuals under the age of 18". The model card adds that a deployer whose system may be used by under-18s must assess and mitigate the risks and comply with the law.

## Runtime and image

| Item | Status |
| --- | --- |
| vLLM support | **Verified:** registry at tags v0.28.0, v0.30.0 and v0.32.0 maps the architecture. Meta's guide gives `vllm/vllm-openai:muse-glimmer` (CUDA 13.0, `ENTRYPOINT ["vllm","serve"]`), parsers `muse_glimmer`, `--generation-config auto`, no `--chat-template`, and "about 72 GB of VRAM to serve". |
| Image digest | **Verified 2026-10-28 21:43 UTC** by an official Registry V2 manifest read: 4 requests, 13,422 bytes, anonymous pull token (not recorded); index and platform manifest only, no layers. The tag pointed to the manifest-list index `sha256:413c8fbecb1204a218117c77a4ea4b3a211d5686ff99c31d82ba6dd0cec8c5a6`. Its **linux/amd64 manifest is `sha256:dbb04f5c0c0a6356779cf3175381a0406584df01b0ad2f88eaadb46f7f2da08c`**: 32 layers, 8,619,692,312 compressed bytes, config `sha256:e90b8320…`. Body hashes equal the registry headers. The tag floats; deploy by digest. |
| Driver | **Verified rule:** CUDA 13.x needs driver 580 or newer (NVIDIA minor-version compatibility). **Open:** no Google page states the serving-node driver. The startup guard checks it on the node. |
| transformers | **Verified:** `models/muse_glimmer/` is in both the 5.20.0 and 5.21.0 PyPI wheels. The earlier 5.21 "gap" was a GitHub raw-path 404. |
| llama.cpp (option B) | **Verified (Meta guide):** build `b10353` or newer. **Open:** image tag and digest. |

## Fit (derived, not measured)

Option A: `a2-highgpu-2g`, 2× A100, BF16 vLLM, TP=2, `--gpu-memory-utilization 0.90`.
- The 42,949,672,960 bytes per GPU (40 GiB) is an **assumption** from the nominal size. Each 1 GB less of device-reported total
  memory removes 0.9 GB of headroom. The startup guard therefore checks device-reported total and free memory, not the nominal size.
- Worst-case assumptions: the vision tower is replicated on each GPU (31.78 GB of weights per GPU), and KV is allocated for all 52 layers.

| Context × sequences | Headroom at 3 GiB runtime reserve | Headroom at 5 GiB reserve |
| --- | --- | --- |
| **8K × 1 (first run)** | **3.44 GB** | **1.29 GB** |
| 8K × 4 | 2.78 GB | 0.64 GB |
| 16K × 1 (comparison) | 3.22 GB | 1.07 GB |
| 16K × 4 | 1.91 GB | −0.24 GB (does not fit) |

The margin at the pessimistic reserve is only about 1 GB. It is not a validated fit, and only the live startup report settles it.
Option B (labeled alternative: official Q4_K_M; the card states about 1.0% average degradation): `g2-standard-12`, 1× L4, ~4.4 GB headroom at 16K × 1.

## Prompt envelope (first run)

`--max-model-len 8192` covers **prompt plus output**. The prompt count includes the chat template, system lines and
the reasoning-strength line; the output count includes reasoning tokens. Clients send `max_tokens` ≤ 512.
The server counts both with the pinned tokenizer revision and reports `usage.prompt_tokens` and `usage.completion_tokens`.

The acceptance request is `usage.prompt_tokens` between 6,500 and 7,168, with `max_tokens` 512. That leaves 512 tokens of slack inside 8,192.

## Startup guard (`startup_guard.py`)

`render_upload.py` puts the guard source inline in `containerSpec.command`:
`python3 -c <guard> '<limits json>' -- vllm serve`, followed by `containerSpec.args`.
This is a documented command override on the pinned upstream image. **No derived image and no image build are needed.**

Before any weights download, the guard reads:
- `/proc/driver/nvidia/version`;
- the GPUs torch sees, with `mem_get_info` total and free per device;
- free disk at `~/.cache/huggingface`;
- `/dev/shm` size.

It prints one JSON evidence line to the container log, then either `exec`s `vllm serve` or exits 3. On exit 3 the container stops,
the replica never turns healthy, and the deploy fails: the operator sees the abort in the deploy status, and the reason in the evidence line.

The first-run (8K) limits, rendered from the pinned config:

| Check | Limit |
| --- | --- |
| Driver major version | ≥ 580 |
| GPUs visible | exactly 2 |
| Device-reported total per GPU | ≥ 39,128,839,964 B (worst-case weights + 8K all-layer KV + 3 GiB reserve, ÷ 0.90). The 16K figure was 39,371,177,528 |
| Free per GPU | ≥ 0.90 × total |
| Free cache disk | ≥ 63,634,841,824 B (weights ×1.05 + 1 GiB) |
| `/dev/shm` | ≥ 15,461,882,265 B (0.9 × 16,384 MiB) |

The guard does not prove that managed disk is large enough; it refuses when it isn't.
The image's own unpack space (8.62 GB compressed) is the platform's pull, outside the guard.

## Upload renderer (`render_upload.py`)

Offline. It makes no network call and handles no credentials or tokens. The Mac executor runs it after staging the image:

```bash
python3 workspace/models/serving/render_upload.py \
  --image-uri us-central1-docker.pkg.dev/PROJECT/REPO/vllm-openai@sha256:dbb04f5c0c0a6356779cf3175381a0406584df01b0ad2f88eaadb46f7f2da08c \
  --out upload-request.json
```

**Output contract.**
- On success: exit 0. `--out` holds the complete JSON body for
  `POST https://us-central1-aiplatform.googleapis.com/v1/projects/PROJECT/locations/us-central1/models:upload`: `model.displayName`, `model.description` and `model.containerSpec`.
- stdout carries one JSON line with `out`, `sha256`, `bytes`, `imageUri` and `guard_limits`.
- On rejection: exit 2, the reason on stderr, and no file written.

**What it rejects:**
- an image that is not a `us-central1-docker.pkg.dev` reference pinned to exactly the linux/amd64 digest (this excludes tags, the index digest, other regions, Docker Hub and `us-docker`);
- a context that disagrees between the args, the demo settings and the envelope, or that exceeds the model maximum;
- prompt + output + slack above the context;
- `--max-num-seqs` or the TP size disagreeing with the machine.

`containerSpec` carries only supported ModelContainerSpec fields. Nothing here creates the endpoint or deploys.

## One attempt, timeline and cleanup

- **Attempts:** one deploy attempt. 16K context, `--enforce-eager`, and any restart, retry or second deploy each need a separately approved scope.
  The platform may restart a replica on its own, and a restart may fetch the weights again, so one attempt does not guarantee a single weight download.
- **Timeline:** T0 is the **first cost-bearing operation**: image staging into Artifact Registry if it comes first, otherwise the deploy request.
  - The replica must be healthy within 3600 s of the deploy request, and in any case before T0+4h.
  - Testing stops by T0+4h.
  - Teardown must be **verified** by T0+6h, after success, failure or timeout alike. It covers the endpoint, the model and the staged image.
- **Cleanup:** `aiplatform.endpoints.undeploy`, then `aiplatform.endpoints.delete` and `aiplatform.models.delete`. Verify with get/list. A deploy timeout alone is not cleanup.
- **Cost:** the published on-demand `a2-highgpu-2g` rate of $8.4489898/h covers the machine. The working reserve is USD 150 within the aggregate budget (Chief's 21:51 packet).

### Load test

**Abort and tear down if any of these happens:**
- the guard exits 3;
- an OOM or engine exit at startup;
- the replica is not healthy within 3600 s;
- the engine reports less than 1.0 GiB of KV per GPU, or concurrency below 1.0 at 8,192 tokens;
- peak GPU memory goes above 95%;
- spend reaches the cap.

**Accept when all of these pass:**
- `/health` returns 200;
- `/v1/models` lists `muse-glimmer` with root `meta-models/Muse-Glimmer-30B`;
- a two-turn non-streaming chat returns non-empty content;
- the 6,500–7,168-token prompt with `max_tokens` 512 completes.

The exact wording of vLLM's startup log messages is unverified.

## Resources and operations (nothing created; permission availability unverified, no grants invented)

| Resource | Quantity / path | Operations |
| --- | --- | --- |
| Image mirror | `us-central1-docker.pkg.dev/{project}/models-serving/vllm-openai@sha256:dbb04f5c…` (8.62 GB compressed). Must be a regional repo in the endpoint region | `artifactregistry.repositories.create` (once), `uploadArtifacts`. Service-agent `downloadArtifacts` unverified |
| Weights | Pinned HF download at start: 59.58 GB per replica start; needs public egress | The guard enforces the free-disk floor |
| Model and endpoint | 1 Model, 1 Endpoint, 1 replica `a2-highgpu-2g`, us-central1 | `aiplatform.models.upload`, `endpoints.create`, `endpoints.deploy`; clients need `endpoints.predict`; cleanup as above |

Container: port 8080, `healthRoute /health`, `predictRoute /v1/chat/completions`, `sharedMemorySizeMb 16384`, startupProbe `httpGet /health`.
The Probe reference contradicts itself on `periodSeconds` versus `timeoutSeconds`; the draft uses equal values.

## Wire contract for Product and QA

- **Request:** `POST {base}/v1/chat/completions` with `"model": "muse-glimmer"`.
- **Agent Platform:** `:rawPredict`, with the body unchanged and a Google OAuth bearer token; the caller needs `aiplatform.endpoints.predict`. Streaming uses `:streamRawPredict`.
- **Response:** the answer is `choices[0].message.content`. Thinking comes separately in `message.reasoning`.
- **Limits:** one in-flight request (`--max-num-seqs 1`); 8,192 tokens of prompt plus output; `max_tokens` ≤ 512; never `temperature: 0`.

## Access

- The GitHub hold is unchanged: `GET api.github.com/repos/vllm-project/vllm/pulls/51655` was denied. One raw source read after that denial was disclosed. No vLLM source has been read since.
- The registry manifest read above was a distinct read, explicitly bounded and authorized by Chief at 21:31 UTC.

## Final list

| Category | Items |
| --- | --- |
| Closed facts | Model pin and files; arithmetic; terms text; vLLM tags; image index and amd64 digests; transformers 5.21; NVIDIA 580 rule; route and probe fields; 8K envelope; inline-guard upload body; timeline; cleanup operations |
| Settled per Chief's 21:50/21:51 relay | Muse Glimmer selection; a2-highgpu-2g, us-central1, one attempt, 4 h / 6 h, USD 150 reserve. Core records these as relayed; it did not independently see them |
| Escalate only if hit | A new grant or persistent credential actually required; a concrete conflict with the license or Usage Policy (including the under-18 statement). Whether existing principals hold the listed operations is unverified |
| Measured only in the approved run | Node driver; device-reported memory; managed disk; pull and start time; KV and peak memory; latency; error bodies; `--limit-mm-per-prompt` effect |
