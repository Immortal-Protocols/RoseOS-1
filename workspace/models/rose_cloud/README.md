# Rose on the cloud VM (MODELS-ROSE-CLOUD-001 to -004, CLOUD-VISION-001)

The goal is to run the same Rose system and client as PR288 on `models-glimmer-01`, which has two A100 40 GB GPUs.
PR288 is pinned at `e1b702ee3b8a6bf656b5cc59bd765787622ae543` and was merged into main as `39b57db` with these paths
unchanged.

This directory holds only what has to change for Linux:

- the model providers;
- an offline preflight;
- the pinned bring-up manifest.

The phone client, the v1 wire contract, and PR288's scheduler, conductor, task, speaker and transport code are
reused unchanged, never forked. Cloud success is not a prerequisite for the laptop path. **Nothing here has run on
any VM.**

## What is real, what is a fixture, what is missing

| Part | Status |
|---|---|
| Phone link (`rose/remote.py`) | **Reused unchanged.** It runs on Linux in `test_contract.py`: pair, signed `/hello`, replay refused. |
| Scheduler (`rose/runtime.py`) | **Reused unchanged** through `nomlx.py`. Network and synthesis waits run on workers (`pump.py`), and only strictly higher-priority jobs run while a job waits (`coop.py`). Tested in `test_coop.py`. |
| Brain (`vllm_brain.py`) | **Real adapter**, using token-id `/v1/completions` streaming for text. Sequences that hold a screenshot go to the chat route (next section). It has been tested against a stand-in server only. |
| Speaker (`phonon.py`, `speech.py`) | The `ptts` provider sits behind PR288's unchanged `Speaker` and `/ws/audio`. **FIXTURE:** a stand-in engine and PR288's `FakeTransport` player. No real synthesis has run. |
| Preflight (`preflight.py` + bootstrap `cloud-preflight-once`) | **Source and tests only**, covered in `test_preflight.py` and the bootstrap/render suites. |
| Listener (`listen.py`) | PR288's `Listener` runs **unchanged**, with its sample-clock endpointing, early transcript and its cancellation, partials and echo guard. `listen.bind` gives it Linux providers for the same four models (table below). **FIXTURE:** `test_listen.py` uses an energy VAD and scripted recognizers. No model file, onnxruntime or recognizer weights are present here, so no real recognition has run. |
| Phone endpoint, auth, network | **Not in scope yet.** Each needs explicit scope (see the last section). |

## Images: PR288's talker look and phone-agent screenshots (`vllm_brain.py`)

PR288 calls `Sequence.append_image(pil_image)` and reads `last_image`. The talker's look and the phone agent's
screenshots then go through these steps:

1. **Resize locally.** The image is resized exactly as PR288's `Brain.image_size` does: long side at most 768 px,
   both sides on the 32 px grid, bicubic. This reproduces PR288's measured table: 1248×1972 gives 360 tokens at 768.
2. **Encode** it as PNG.
3. **Append the image tokens.** The sequence gets `<|vision_start|>`, (h/32)·(w/32) `<|image_pad|>` tokens and
   `<|vision_end|>`, the same tokens PR288 appends.
4. **Send by the chat route.** `/v1/completions` cannot carry an image, so `generate` on a sequence holding images
   uses `/v1/chat/completions`. It sends one user message whose content parts are the decoded text between images
   and the images as `data:image/png;base64` URLs. `RAW_TEMPLATE` renders those parts back verbatim, with one
   `<|vision_start|><|image_pad|><|vision_end|>` per image and no roles or generation prompt. vLLM's processor then
   expands each pad.
5. **Check the prompt.** The server's `prompt_token_ids` (first stream chunk) are compared with the client's tokens.
   The result is recorded in `last_prompt_exact`; `strict_prompt=True` makes a mismatch an error.
6. **Keep text-only requests unchanged.** Sequences without images still use token-id completions.

**Preserved:**

- **Cancellation:** the same worker and socket shutdown as text. A look closed midway adds nothing.
- **Identity:** an image belongs to its sequence and fork. Rewinding past an image drops it, so it is never sent
  later. `session_id` is passed when the brain has one.
- **Bounds:** inputs over 50 Mpx and non-images are refused before any pixel work. PNGs are at most 8 MiB, and each
  sequence holds at most 16 images.
- **Priorities:** LISTEN still runs while a vision request waits.

**Backend differences** (stated, not hidden):

- **Not token-exact.** Text in an image request is decoded and re-tokenized by the server. Sampled tokens may not
  re-tokenize identically, so the request is not token-exact like the completions route. `last_prompt_exact` shows
  when that happened.
- **Image positions.** vLLM computes M-RoPE image positions itself, and `Sequence.pos` stays `len(tokens)`.
- **Latency.** The vision encode and prefill run on the server at the next `generate`, so `last_image.encode_ms` and
  `prefill_ms` are 0. Every image request resends every image, mitigated only by vLLM's caches. PR288's phone-agent
  `_Reply` makes one request per decoded token. Nothing here reproduces MLX's resident image cache, its rewind costs
  or its per-unit vision scheduling.

**Server requirements** (`contract.BRAIN_PREREQUISITES`). These are proposals: not verified on the VM's image and not
staged.

| Requirement | Notes |
|---|---|
| Vision tower loaded | The server must **not** use `--language-model-only`. That flag saves memory but makes the brain text-only: `VllmBrain(vision=False)` refuses images. |
| `--chat-template <file from write_raw_template>` | The template is set on the server, not sent per request. vLLM main refuses request templates unless `--trust-request-chat-template`, which stays off. |
| `--chat-template-content-format openai` | |
| `--limit-mm-per-prompt '{"image": 16, "video": 0}'` | |

**Not shown:** what the model sees, its answers about a screen, image-processor parity with mlx-vlm, and latency.
The tests (`tests/test_vision.py`) use synthetic images, the stand-in server and a jinja2 render of `RAW_TEMPLATE`.

## Listener providers (`rose_cloud/listen.py`; proposals, nothing staged)

| PR288 role (MLX on the Mac) | Linux provider | Pinned upstream input (the MLX file's base) |
|---|---|---|
| VAD: `mlx-community/silero-vad`, PR288's `SileroCPU` in NumPy | **PR288's `SileroCPU` unchanged**, on the same weights file read with NumPy (`read_safetensors`): the same arithmetic | `mlx-community/silero-vad@7bc17f22…`: `config.json`, `model.safetensors` (2.18 MB, sha256 `185e0bc3…`) |
| End of turn: `mlx-community/smart-turn-v3` | `SmartTurn`: onnxruntime, with the official preprocessing from `pipecat-ai/smart-turn` (`inference.py`, `audio_utils.py`): last 8 s, left zero pad, `WhisperFeatureExtractor(chunk_length=8)`, sigmoid output | `pipecat-ai/smart-turn-v3@f766f81d…/smart-turn-v3.2-gpu.onnx` (32.4 MB, sha256 `ab8dc64b…`). The MLX config names this file as its `source_onnx`. |
| Final ASR: `mlx-community/parakeet-tdt-0.6b-v3` | `Parakeet`: transformers `AutoModelForTDT`, `processor.decode(..., skip_special_tokens=True)` (model card) | `nvidia/parakeet-tdt-0.6b-v3@541d1f99…`: 6 files, `model.safetensors` 2.51 GB (sha256 `3a202636…`) |
| Streaming ASR: `mlx-community/nemotron-3.5-asr-streaming-0.6b`, `att_context_size [56, 0]` | `NemotronStreaming`: transformers `AutoModelForRNNT` cache-aware streaming (model card), with `set_num_lookahead_tokens(0)` | `nvidia/nemotron-3.5-asr-streaming-0.6b@ea30d66d…`: 6 files, `model.safetensors` 2.55 GB (sha256 `9eebdd65…`) |

**Libraries** (`listen.LIBRARIES`):

- transformers ≥ 5.13.0. Nemotron needs that version per its card. `AutoModelForTDT` and `AutoModelForRNNT` are both
  present in the released 5.22.1, checked locally by import only.
- onnxruntime 1.31.1, which needs Python ≥ 3.11 (cp312 manylinux wheel sha256 `1f3dea47…`).
- torch, matching the host (CUDA for GPU).
- numpy.

None of these is installed or pinned in the cached serving image. The image has transformers 5.15.0, per the PR289
probe receipt. Every file is checked against its pin by `listen.check_inputs` before anything loads, and nothing is
downloaded.

**Scheduling:**

- **Smart-turn and Parakeet:** these run inside PR288's LISTEN jobs. Their work happens on a worker thread while the
  job waits through `coop.Waiter`. Nothing outranks LISTEN, so the job holds the runtime thread for the model's
  duration, as GPU work does on the Mac.
- **Cancellation:** cancelling the job ends the wait at once, and the result is dropped. PR288 does this when speech
  resumes during a dip.
- **Streaming session:** decoding runs on the session's own thread, and `step` never waits. `_queued` and `_encoded`
  report only text ready to collect. Otherwise PR288's loop would spin at LISTEN priority and starve the talker; an
  earlier draft did exactly that, and `test_listen` caught it.

**Mute semantics** (PR288 and its client):

- **Rose's output:** while her audio is audible, PR288's echo guard raises the speech-start bar
  (`set_rose_audible`, driven by `Speaker.on_audible`).
- **Microphone mute on the phone:** the phone sends zero frames (`LocalRoseLink`), so the sample clock keeps running
  and a pending turn still commits on time. Both are tested.

**Not proven (needs real files, libraries and hardware, and a separate approval):**

- recognition accuracy;
- latency on the VM's CPU or GPU;
- whether the transformers streaming loop matches mlx-audio's chunking for `[56, 0]`;
- smart-turn numerics through onnxruntime;
- phone audio over the signed `/ws/audio` link.

The listener files are not yet in `manifest.json`, which is unchanged because it ships inside the CLOUD-003 payload
digest. Adding them is a later manifest and payload update.

## Pinned bring-up manifest: `rose_cloud/manifest.json` (a proposal; nothing is staged or approved)

**Target:**

| | |
|---|---|
| Project | `protocols-465723` |
| Zone | `us-central1-f` |
| Instance | `models-glimmer-01` |
| Instance id | `2705251739288295377` |
| Machine | `a2-highgpu-2g` (2× A100 40 GB, 24 vCPU, 170 GiB RAM) |
| Data disk | `/mnt/models-data`, 500 GB pd-ssd |

**Image:** the exact cached serving image, ID `sha256:e90b8320…689dc` and manifest `sha256:dbb04f5c…8c`. Its vLLM
version is **unknown** until the preflight runs.

**Staging root:** `/mnt/models-data/rose-cloud/`

| Group | Pin | Files | Bytes |
|---|---|---|---|
| `qwen` | `Qwen/Qwen3.6-35B-A3B-FP8@95a723d08a9490559dae23d0cff1d9466213d989` | 54 (size + sha256/git sha1 each) | 37,492,949,243 |
| `phonon` | `gradium/phonon-31m-en@92741c5b07d085ff840e26fea8ce43110b87852f` | `config.json`, `tokenizer.json`, `model.safetensors` (sha256 `8a04cf83…`) | 103,106,521 |
| `voices` | `gradium/phonon-voices@0ee17205f254b5abc43b266c8a0f215565c352f7` | `voices/NbpkqMVS3CJeq2j8.safetensors` (zoey, sha256 `97c7f8c2…`) | 256,136 |
| `ptts` | PyPI `ptts==0.6.0` | `ptts-0.6.0-cp39-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl` (sha256 `6efb197e…a60c`) | 7,483,513 |

**`ptts` choice, resolved from the 0.6.0 tag source** (`ptts-pyo3/src/lib.rs`):

- `quant=None` is `Quant::F32`, which loads `model.safetensors`. That is the same full-precision file mlx-audio uses.
  The pin is therefore `quant='f32'`, `device='cpu'`, `lang='none'`.
- `available_quants()` is `f32, q8_0, q8_1, q8k, q6k, q5_0, q5_1, q5k, q4_0, q4_1, q4k`.
- **Requirements:** Python ≥ 3.9, `numpy`, glibc ≥ 2.17, and an x86-64-v3 CPU (AVX2, FMA, F16C).
- **Version caveat:** 0.6.0 was uploaded on 2026-10-30, so it is very new.

**Assumptions** (`manifest.assumptions`):

- **Disk:** staging needs 37.6 GB plus a 10% margin beside the existing Muse cache (59.58 GB). The preflight
  measures free space; it is not assumed.
- **GPU memory:** about 18.75 GB of FP8 weights per GPU at tensor parallel 2. KV cache, vision tower and CUDA graphs
  are not measured. **No claim is made either way about whether BF16 fits.**
- **Phonon:** runs on CPU.

**Brain prerequisites** (`contract.BRAIN_PREREQUISITES`), all unverified for this image:

- vLLM ≥ 0.19.0;
- `Qwen3_5MoeForConditionalGeneration` registered;
- block-128 FP8 through weight-only Marlin MoE on A100;
- the completions fields listed in the contract.

The preflight reports which of these the image's source contains. That shows the code is present, not that the
kernels run.

## cloud-preflight-once

`serving/service/bootstrap.py` recognizes the new value
`rose-models-diagnostic=cloud-preflight-once:<payloadTarSha256>:<id>`. It runs once per id, through the existing
metadata + payload-digest + one-reset channel.

**Before the probe**, it applies the same controls as `hf-probe-once`:

- the model is held (`/run/rose-models/model-hold`), stopped, and its container positively absent;
- docker, runc and the exact image ID are present;
- additionally, the image's repo digest is `vllm/vllm-openai@sha256:dbb04f5c…` and its platform is `linux/amd64`;
- the data disk is mounted.

**Then one container:**

```
docker run --rm --pull=never --runtime=runc --network none --name rose-cloud-preflight \
  -v /mnt/models-data:/mnt/models-data:ro --entrypoint python3 sha256:e90b8320… \
  -c <lib/rose_cloud/preflight.py> <lib/rose_cloud/manifest.json>
```

- **Limits:** no GPU, no credentials, no `-e`, at most 120 s, and stdout kept to 1 MiB.
- **Cleanup:** the container is then removed, and its absence is checked separately with `docker ps --all`.
  `cleanup` reads `removed`, `failed` or `unknown`.
- **Receipt:** at most 4,096 bytes, in guest attribute `rose-models/diagnostic` and on the serial line
  `ROSE-MODELS-DIAGNOSTIC`. It contains fixed tokens only, re-checked by `preflight.sanitize`:
  - python and glibc versions;
  - 12 package versions from metadata;
  - vLLM source facts: registry entry, model file, FP8 Marlin MoE, `--language-model-only`, completions fields;
  - `ptts`: importable, API, `f32` (from the install, or the staged wheel's stub);
  - CPU flags and an `x8664v3` boolean;
  - staging-root presence and free bytes;
  - per group: expected, present, missing, size and hash mismatch counts, and whether the group is complete.
- **Not done:** nothing is loaded, installed, downloaded or written. vLLM is never imported. Files of 1 MiB or less
  are hashed; larger ones are checked by size only.

## Owner action packet (NOT authorized; for a separate runtime approval)

**Read these consequences before approving:**

- **TLS and auth interruption:** the reset reboots `models-glimmer-01`. HTTPS (443) and ACME (:80) are down from the
  reset until boot starts the edge again. The last reset took about 23 s at the API (19:04:43–19:05:06); the boot
  itself takes longer and has not been measured for this payload. The certificate, auth secret, device registry,
  tokens and renewal timers are kept, not regenerated. The firewall and TLS configuration are unchanged.
- **Held state:** that boot holds Muse inference, as now. Afterwards the id is used, so the **next** reset without a
  new diagnostic id boots normally and starts Muse, whose start is still unresolved. Do not reset again without a
  separate decision.
- **Payload:** the new payload replaces the startup script and payload metadata. A diagnostic boot runs only the
  diagnostic stage; nothing is installed or reconfigured. The payload must be built **with the same
  `--attestation-roots` as the deployed one**: without roots, auth reports `waiting attestation_roots_missing`.
- **Cost:** no added GPU time (the VM is always on). Nothing downloads, and nothing is written to the data disk.

**Steps, run by the original Mac operator only:**

1. Build at the merge commit with the deployed roots. Record `payloadTarSha256` (P) and confirm that `files` lists
   `etc-rose-models/lib/rose_cloud/preflight.py` and `etc-rose-models/lib/rose_cloud/manifest.json` with the hashes
   Core reports.

   ```bash
   python3 workspace/models/serving/service/build_bootstrap.py --out /tmp/rose-cloud-preflight --attestation-roots <deployed roots dir>
   ```

2. Install the payload and set a fresh id (lower-case letters, digits and `-`, never used before):

   ```bash
   gcloud compute instances add-metadata models-glimmer-01 --project protocols-465723 --zone us-central1-f --metadata-from-file=startup-script=/tmp/rose-cloud-preflight/startup-script.sh,rose-models-payload=/tmp/rose-cloud-preflight/rose-models-payload.b64 --metadata=rose-models-diagnostic=cloud-preflight-once:<P>:<fresh id>
   ```

3. Do one reset:

   ```bash
   gcloud compute instances reset models-glimmer-01 --project protocols-465723 --zone us-central1-f
   ```

4. Read the receipt:

   ```bash
   gcloud compute instances get-guest-attributes models-glimmer-01 --project protocols-465723 --zone us-central1-f --query-path=rose-models/diagnostic
   ```

**Stop conditions:**

- a `prerequisite` refusal;
- `cleanup` other than `removed`;
- `probe: null` (timed out or no result).

None of these is retried without a new decision.

## cloud-stage-once (CLOUD-STAGING-SOURCE-001; source only, has not run: running it is a separate runtime decision)

**What it would stage:** exactly the pinned manifest, sha256 `ebcfb082a94bcae9c950eefab4b34a8fc736bfeb934c84a96360398afd09f3c3`
(bootstrap refuses any other with `manifest_mismatch`). That is 59 files, 37,603,795,413 bytes:

| Group | Files | Bytes |
|---|---|---|
| Qwen FP8 (`95a723d0…`) | 54 | 37,492,949,243 |
| Phonon (`92741c5b…`) | 3 | 103,106,521 |
| Zoey voice (`0ee17205…`) | 1 | 256,136 |
| `ptts` 0.6.0 wheel | 1 | 7,483,513 |

**How bootstrap runs it** (`serving/service/bootstrap.py`, `cloud_stage_once`):

1. **Existing controls:** the same once-per-id metadata, payload digest, Muse hold, stop and positive container
   absence as `cloud-preflight-once`. It also checks docker, runc, the exact image ID, repo digest `dbb04f5c…` and
   `linux/amd64`, and that the data disk is mounted.
2. **Staging directory:** it creates `/mnt/models-data/rose-cloud` if missing.
3. **One container:**

   ```
   docker run --rm --pull=never --runtime=runc --network bridge --name rose-cloud-stage --cap-drop ALL \
     --security-opt no-new-privileges -v /mnt/models-data/rose-cloud:/mnt/models-data/rose-cloud:rw \
     --entrypoint python3 sha256:e90b8320… -c <lib/rose_cloud/stage.py> <manifest.json> \
     '{"deadlineS": 3600, "maxBytes": 45000000000, "readTimeoutS": 30}'
   ```

   - **Isolation:** no GPU, no credentials or environment variables, and only the staging directory mounted.
   - **Network:** the default bridge, outbound only.
   - **Time limit:** Host.run allows 3600 + 120 s, then kills the container.
4. **Cleanup and receipt:** `docker rm -f` plus a separate absence check (`removed`, `failed` or `unknown`). The
   receipt is at most 4 KiB, in `rose-models/diagnostic`, and inference stays held.

**What `stage.py` does** (stdlib only; `tests/test_stage.py` runs it against a local fixture server):

- **Sources:** each file's one source is the Hub resolve URL at its pinned revision, or PyPI's exact URL for the
  wheel (its sha256 matches the manifest).
- **Allowlist:** https only, to exactly `huggingface.co`, `us.aws.cdn.hf.co`, `us.gcp.cdn.hf.co` and
  `files.pythonhosted.org` (no wildcards or subdomains), for the first request and every redirect (at most 5). The
  first, second and fourth are the hosts a HEAD request showed from a development sandbox. `us.gcp.cdn.hf.co` was
  added alone by CLOUD-STAGING-ALLOW-GCP-CDN-001, after the approved `cloud-redirect-probe-once` run on the VM checked
  all 59 sources: 14 were reachable and 45 were refused solely at that host. Any other host is still refused with
  `redirect_refused`, and the allowlist needs a source change. A successful GET through it is still unproved.
- **No credentials:** none are ever sent. A 401 or 403 stops that file with `auth_required`.
- **Existing files:** each one is verified. Exact files are kept. **Any differing file refuses the whole run before
  any request**, and nothing changes.
- **Free space:** the run needs remaining bytes plus 10% free, or it stops with `insufficient_space`.
- **Fetching:** each file goes into `<name>.partial`, resuming an existing partial with a `Range` request. A server
  that ignores `Range` stops that file with `range_refused`, and the partial is not touched.
- **Size checks:** a Content-Length or body past the pinned size stops that file with `size_mismatch`.
- **Hash check:** complete files are hashed (sha256, or git blob sha1 for the small Hub files). Only an exact match
  is renamed into place; a mismatch stays `.partial` with `hash_mismatch`.
- **Stops and partials:** the deadline and byte cap stop the run (`deadline`, `byte_cap`). **Partials are never
  deleted**, so the next run resumes them.
- **Receipt:** fixed tokens only, re-checked by `stage.sanitize`. It holds the stop reason, bytes in, elapsed
  seconds, free and needed bytes, per-group counts (expected, present, downloaded, resumed, failed, bytes in) and
  the first failure token per group. It never contains a URL, query, host, header or path.

**Refused destinations** (CLOUD-STAGING-REDIRECT-DIAG-001):

- **The gap:** the one approved run (payload `6a99cc0a…`, merge `e192912`) verified 14 of 59 files and refused 45
  redirects (`redirect_refused`), but its receipt could not say where they went.
- **What the receipt now adds:** `refusedHosts`, up to 4 entries of `{host, reason, count}`, plus a `refusedOther`
  count.
- **`host`:** the lower-cased hostname (no trailing dot, DNS syntax, at most 253 characters) or a normalized IP
  literal without an IPv6 zone id (a zone id is free text, so such a URL counts as `malformed`). It is `null` when
  the URL is malformed. The scheme, userinfo, port, path, query (signed CDN URLs carry
  their signature there), fragment and headers are never kept.
- **`reason`:** one of `host_not_allowed`, `scheme_not_https`, `userinfo` or `malformed`.
- **Bounds:** URLs over 8192 characters count as `malformed`.
- **Bootstrap:** `bootstrap.py` passes this through unchanged via `stage.sanitize`, which drops any entry that is
  not a hostname.
- **What did not change:** the allowlist, the refusal itself and what follows it (nothing). There are no retries.

**Smallest safe next diagnostic** (proposal only; not executed or authorized). Superseded by
`cloud-redirect-probe-once` (next section), which names the refused destination with HEAD requests only and no
staging attempt; this stage-based variant remains only for reference:

- **Who:** the original Mac operator, once.
- **Steps:**
  1. Build at the merge commit of this change with the deployed attestation roots.
  2. Set the payload metadata and `rose-models-diagnostic=cloud-stage-once:<new payloadTarSha256>:<fresh id>`.
  3. Do one reset.
  4. Read `rose-models/diagnostic`.
- **Same bounds:** no allowlist change and the same 60 minute / 45 GB limits. Nothing is deleted.
- **Expected behaviour:**
  - the 14 verified files are re-hashed locally and kept (no transfer);
  - the 45 files whose redirects were refused are refused again at their first redirect, before any body bytes;
  - existing partials are untouched.
- **What it reveals:** `refusedHosts` names the concrete host or hosts and reasons, and `bytesIn` should stay near 0.
- **After that:** adding the named host to `ALLOW_HOSTS` is a separate source change and its own decision; it should
  never be guessed.
- **Same reset effects:** the TLS interruption, inference held for that boot, and the next reset without a new id
  starts Muse.

**Unverified** (needs the separately approved run):

- the image's Python ssl and CA store;
- the Hub's redirect hosts from us-central1;
- Range support on the CDN;
- whether 37.6 GB arrives within 60 minutes (that needs about 10.5 MB/s sustained);
- docker bridge egress from the VM.

**Proposed owner action** (NOT authorized; a separate runtime decision):

- **Who:** only the original Mac operator, on `models-glimmer-01` (instance 2705251739288295377, us-central1-f).
- **Steps:**
  1. Build the payload at the merge commit with the deployed attestation roots, and record
     `payloadTarSha256`.
  2. Set the startup-script and payload metadata plus `rose-models-diagnostic=cloud-stage-once:<payloadTarSha256>:<fresh id>`.
  3. Do one reset.
  4. Read `rose-models/diagnostic`.
- **What the reset costs:** the same TLS interruption as any reset, inference held for that boot, and the next reset
  without a new id starts Muse.
- **Limits:** at most 60 minutes, at most 45 GB inbound, and about 37.6 GB written under
  `/mnt/models-data/rose-cloud`.
- **Retries:** a `deadline` or `byte_cap` stop leaves partials for one later, separately approved rerun with a new
  id.
- **Disk:** nothing else on the disk changes. The staged files stay until their removal is separately approved.

## cloud-redirect-probe-once (CLOUD-REDIRECT-HEAD-001; source only, has not run: running it is a separate runtime decision)

**Why:** `cloud-stage-once` still permits 45 GB and 3600 s (3720 s for its container), so a near-zero download is not
guaranteed. This mode names the refused redirect destination without any staging attempt.

**What it runs:** the same `stage.py`, in HEAD-only mode (`head_probe`), over the same pinned manifest (sha256
`ebcfb082…f3c3`, 59 files):

- one `HEAD` per file at its one pinned source; every redirect hop stays `HEAD`;
- every hop is checked by the same `Fetcher.check`: https only, no userinfo, `ALLOW_HOSTS` (the same list
  `cloud-stage-once` uses), at most `MAX_REDIRECTS` (5) hops;
- a refused destination is recorded (sanitized host, fixed reason, count, as in PR329) and **never requested**; the
  refused redirect response is closed unread;
- **no response body is read** (http.client reads none for `HEAD`; responses are closed unread), even when a server
  sends one;
- **nothing is written**: the staging root, existing files and partials are never opened, and the container has a
  read-only root filesystem with nothing mounted;
- no retries, no credentials, no new hosts, dependencies, permissions, model configuration or network policy.

**How bootstrap runs it** (`serving/service/bootstrap.py`, `cloud_redirect_probe_once`): the same hold, model stop,
positive container absence, read-only prerequisites, image digest and pinned-manifest checks as `cloud-stage-once`
(no data-disk mount is needed). Then one container:

```
docker run --rm --pull=never --runtime=runc --network bridge --name rose-cloud-redirect-probe --cap-drop ALL
  --security-opt no-new-privileges --read-only --entrypoint python3 <pinned image id>
  -c <stage.py> <manifest.json> {"deadlineS":100,"maxBytes":0,"mode":"head","readTimeoutS":10}
```

**Time budget:**

- **The diagnostic cap is 120 s:** the container is killed at `PROBE_SECONDS` = 120 (no grace).
  - Inside it, the helper stops itself at 100 s through a real-time alarm, so it still prints a receipt.
  - Each socket operation is bounded by min(10 s, time left), including on redirect hops.
- **Not counted in the 120 s** (boot and cleanup overhead, each step already bounded):
  - **The reset and boot** up to the bootstrap.
  - **Before the container:**
    - stopping the model (`systemctl stop` ≤ 180 s);
    - the positive-absence wait (≤ 120 s);
    - `docker info` and `docker image inspect` (≤ 60 s each) and the image identity inspect (≤ 60 s).
  - **After the container:**
    - `docker rm -f` (≤ 30 s) and the `docker ps` absence check (≤ 30 s);
    - the guest-attribute write (≤ 10 s).
  - Typically these take seconds, but they have not been measured for this payload.

**Receipt** (`rose-models/diagnostic` and the serial line `ROSE-MODELS-DIAGNOSTIC`, at most 4096 bytes):

```
{"schema":"rose-models-diagnostic/1","check":"cloud-redirect-probe-once","payloadSha256":…,"bootId":…,
 "attemptId":…,"modelHeld":true,"prerequisite":null|<category>,"exitCode":…,"timedOut":…,
 "cleanup":"removed"|"failed"|"unknown","cleanupRemoveExit":…,
 "probe":{"mode":"head","stop":"done"|"deadline"|"bad_manifest","elapsedS":…,
          "groups":{<group>:{"expected","reachable","refused","failed"}},
          "firstFailure":{<group>:redirect_refused|redirect_limit|auth_required|http_error|size_mismatch|network|null},
          "refusedHosts":[{"host":<hostname>|null,"reason":host_not_allowed|scheme_not_https|userinfo|malformed,
                           "count":…}] (at most 4),"refusedOther":…}|null}
```

`sanitize_head` (the allowlist bootstrap re-applies) keeps only these fixed tokens, bounded counts and hostnames. It
never keeps a URL, scheme, port, path, query, fragment, userinfo, header, body or exception text.

**Unverified** (needs the separately approved run):

- whether the Hub answers `HEAD` on `resolve` with the same redirect target as `GET` from the VM's region;
- whether an allowed CDN that signs per method answers `HEAD` (that would show as `auth_required` on an allowed host,
  not as a refusal);
- the image's ssl/CA store;
- docker bridge egress.

**Proposed owner action** (NOT authorized; a separate runtime decision):

- **Who:** only the original Mac operator, on `models-glimmer-01`.
- **Payload contract:**
  1. Build at the merge commit with the deployed attestation roots (`build_bootstrap.py --attestation-roots
     <deployed roots dir>`). Record `payloadTarSha256` (P). Confirm the payload lists
     `etc-rose-models/lib/rose_cloud/stage.py` and `manifest.json` with the hashes Core reports.
  2. Set the metadata `startup-script` and `rose-models-payload` from that build, plus
     `rose-models-diagnostic=cloud-redirect-probe-once:<P>:<fresh id>`. The id is lower-case letters, digits and `-`,
     at most 32 characters, and must never have been used before.
  3. Do one reset.
  4. Read `rose-models/diagnostic`.
- **Consequences:**
  - the same TLS and auth interruption as any reset;
  - Muse inference is held for that boot;
  - the id is then used, so the **next** reset without a new diagnostic id boots normally and starts Muse;
  - nothing is staged, downloaded or written to the data disk.
- **Stop conditions** (none is retried without a new decision):
  - a `prerequisite` refusal;
  - `cleanup` other than `removed`;
  - `probe: null`;
  - `stop` other than `done`.
- **After that:** adding a named host to `ALLOW_HOSTS` is a separate source change and its own decision.
- **Result** (approved run): all 59 sources were checked; 14 were reachable and 45 were refused solely at
  `us.gcp.cdn.hf.co`. Container cleanup was verified. That host alone was then added to `ALLOW_HOSTS` by owner
  approval (CLOUD-STAGING-ALLOW-GCP-CDN-001). HEAD only: GET success remains unproved.

## cloud-validate-once (CLOUD-DEPS-VALIDATE-001; source only, has not run: running it is a separate runtime decision)

**Why:** the approved staging attempt verified all 59 files (37,579,550,198 bytes downloaded in 305 s, zero
failures, cleanup verified). Staging does not show dependency or model-config compatibility. `preflight.py` reads
metadata, source text and the wheel's type stub; it imports nothing that is not already installed.

**What `validate.py` does** (stdlib only at module level; `tests/test_validate.py` runs it against a synthetic
wheel, a real `pip --no-index --no-deps --target` install into a temp dir, and stand-in `ptts`/`transformers`):

1. **Isolation, checked from inside before anything else** (otherwise `stop: not_isolated` and nothing is
   installed, imported or loaded):
   - `/tmp` is a tmpfs of at most `tmpMaxBytes` (512 MiB), measured with `statvfs`, and is not `noexec`;
   - `/` and the staging root are mounted read-only (`/proc/self/mounts`);
   - the only network interface is `lo`;
   - no `/dev/nvidia*` device exists.
2. **Inputs** (otherwise `stop: bad_inputs`):
   - the wheel's sha256 equals the pin `6efb197e73af258e278e90fcc771f36022c5123e9ecca2fca50ae90472caa60c`, which is
     also the manifest's;
   - the 11 non-weight Qwen files (config, tokenizer, processor, template, licence; under 24 MiB) match their
     manifest size and hash;
   - safetensors weights and their index are never opened.
3. **ptts:**
   - `python -I -B -m pip install --isolated --no-index --no-deps --no-cache-dir --no-compile --target
     /tmp/rose-validate/site <wheel>`;
   - the wheel's `Requires-Dist` names are checked against the image's installed distributions (`numpy`, the only
     pinned one; any other name is counted, never fetched);
   - in a child process: `import ptts`, `TTS.stream` and `TTS.add_voice` are callable, and `available_quants()`
     lists `f32`. No `TTS(...)` is constructed, so no model loads and nothing is synthesized.
4. **Qwen,** in a child process with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`:
   `AutoConfig`/`AutoTokenizer`/`AutoProcessor.from_pretrained(<staged dir>, local_files_only=True,
   trust_remote_code=False)`. It checks that `model_type` is `qwen3_5_moe` and the architecture is
   `Qwen3_5MoeForConditionalGeneration`, then records the tokenizer fast/chat-template flags and the processor
   class. No model class, weights or inference.

**Child processes:**
- they run with `python -I -B`, with HOME, TMPDIR, HF_HOME and XDG_CACHE_HOME under `/tmp/rose-validate`;
- their time limits are 120 s for pip, 60 s for ptts and 150 s for Qwen, each cut to the time left of the helper's
  270 s;
- a crash (for example SIGILL without AVX2) is the category `crashed`, and a hang is `timeout`.

**How bootstrap runs it** (`cloud_validate_once`):
- **Checks before the container:** the same hold, model stop, positive container absence, read-only prerequisites,
  image digest, data-disk and pinned-manifest checks as `cloud-stage-once`. The staging root must already exist,
  otherwise the run is refused as `staging_missing`; Docker would otherwise create the bind source.
- **The container:**

  ```
  docker run --rm --pull=never --runtime=runc --network none --name rose-cloud-validate --cap-drop ALL
    --security-opt no-new-privileges --read-only --tmpfs /tmp:rw,exec,nosuid,nodev,size=536870912,mode=1777
    --cpus 2 --memory 4294967296 --memory-swap 4294967296
    -v /mnt/models-data/rose-cloud:/mnt/models-data/rose-cloud:ro --entrypoint python3 <pinned image id>
    -c <validate.py> <manifest.json> {"deadlineS":270,"tmpMaxBytes":536870912}
  ```

**Time and storage bounds:**
- **The diagnostic cap is 300 s:** the container is killed at 300 s, and the helper stops itself at 270 s.
- **Outside the cap, each step already bounded:**
  - the reset and boot;
  - the model stop (≤ 180 s) and absence wait (≤ 120 s);
  - `docker info` and `docker image inspect` (≤ 60 s each), the image identity inspect (≤ 60 s) and `mountpoint`
    (≤ 30 s);
  - `docker rm -f` (≤ 30 s) and `docker ps` (≤ 30 s);
  - the guest attribute (≤ 10 s).
- **Writable storage** is the 512 MiB tmpfs only, which counts against the 4 GiB memory limit. It goes when the
  container is removed, and the staged files cannot be written.

**Receipt** (`rose-models/diagnostic`, at most 4096 bytes), `validate`, re-checked by `validate.sanitize`:
- **Run:** `stop` (`done`, `not_isolated`, `bad_inputs`, `deadline` or `bad_manifest`), `elapsedS` and
  `tmpUsedBytes`.
- **Isolation flags** and `tmpBytes`.
- **Inputs:** `wheelSha256`, and the Qwen file counts.
- **ptts:**
  - `install` and `import` status, each `ok`, `failed`, `crashed`, `timeout`, `skipped` or `pip_unavailable`;
  - `version`;
  - the `stream`, `addVoice` and `f32` flags;
  - `requires.numpy`, `otherRequires` and `siteBytes`;
  - an exception class name and a missing module's import name.
- **Qwen:** `run`, the `transformers` version, each step's status, the `modelType`, `architecture`,
  `tokenizerFast` and `chatTemplate` flags, `processorClass`, and per-step exception class and missing-module names.
- Never a message, path, file content or environment value.

**Unresolved assumptions** (shown only by the approved run):
- the image ships `pip`; otherwise `install: pip_unavailable`, and the Qwen check still runs;
- pip accepts the `manylinux2014_x86_64`/`abi3` wheel on the image's Python and glibc;
- the image's `numpy` satisfies ptts at import time;
- the ptts extension loads from an exec tmpfs under `--cap-drop ALL`;
- the VM's CPU passes ptts's x86-64-v3 requirement inside the container;
- the image's `transformers` knows `qwen3_5_moe` without remote code, and its Qwen processor's own imports (for
  example `torchvision` or `PIL`) are present;
- 4 GiB holds `import transformers` plus a processor;
- Docker accepts `exec` in `--tmpfs` options on the VM's version;
- whether `TTS.stream`/`add_voice` are visible on the class (pyo3 methods) rather than only on instances. If not,
  the receipt shows `false`, and that is a check finding, not proof of absence.

**Not shown even by success:** that weights load, that vLLM serves the model, that ptts synthesizes, or any audio
or latency.

**Proposed operator packet** (NOT authorized; a separate runtime decision):
- **Who:** only the original Mac operator, on `models-glimmer-01`.
- **Steps:**
  1. Build at the merge commit with the deployed attestation roots, and record `payloadTarSha256` (P). Confirm the
     payload lists `etc-rose-models/lib/rose_cloud/validate.py` and `manifest.json` with the hashes Core reports.
  2. Set `startup-script` and `rose-models-payload` from that build, plus
     `rose-models-diagnostic=cloud-validate-once:<P>:<fresh id>`.
  3. Do one reset.
  4. Read `rose-models/diagnostic`.
- **Consequences:**
  - the usual TLS and auth interruption;
  - Muse inference is held for that boot, and the **next** reset without a new id starts Muse;
  - both A100s stay running and unused;
  - no download, no data-disk write and no model load.
- **Stop conditions** (none is retried without a new decision):
  - a `prerequisite` refusal;
  - `cleanup` other than `removed`;
  - `validate: null`;
  - `stop` other than `done`.

## cloud-qwen-gpu-once (CLOUD-QWEN-GPU-001; source only, has not run: running it is a separate runtime decision)

**Why:** the approved `cloud-validate-once` run passed: the ptts import and API, and all 11 staged Qwen config,
tokenizer and processor checks. Cleanup and the hold were verified at 20:22:39. That does not show that the weights
load or that inference works. `gpu-start-once` runs Muse's production launch script (Muse model, Muse cache, port 8000,
container `muse-glimmer`), so it is not reused.

**What `qwen_gpu.py` does** (stdlib only at module level; `tests/test_qwen_gpu.py` runs it against a stand-in `vllm`
package), stopping at the first refusal:

1. **Manifest:** the `qwen` group must be exactly `Qwen/Qwen3.6-35B-A3B-FP8` at
   `95a723d08a9490559dae23d0cff1d9466213d989`. A Muse manifest or any other model is `bad_manifest`.
2. **Isolation, from inside before vLLM is imported** (otherwise `not_isolated`):
   - `/tmp` is a tmpfs of at most 8 GiB that is not `noexec` (Triton and vLLM kernel caches);
   - `/dev/shm` is a tmpfs of at most 4 GiB;
   - `/` and the model directory are read-only;
   - the only network interface is `lo`;
   - exactly two `/dev/nvidiaN` devices exist.
3. **Inputs** (otherwise `bad_inputs`):
   - the 11 non-weight files match their manifest hashes;
   - the 43 weight files (42 shards and the index) match their manifest sizes. They are not re-hashed: staging
     verified them, and hashing 37.5 GB would use much of the bound.
4. **One child process** in its own session, its process group killed at the deadline (engine workers included):
   - it imports vLLM and refuses **before loading** if the image's `EngineArgs` lacks any requested option
     (`engine_arg_unsupported`). For example, there is no silent fallback to loading the vision tower;
   - it builds `vllm.LLM(model=<staged dir>, tokenizer=<staged dir>, tensor_parallel_size=2, max_model_len=2048,
     max_num_seqs=1, gpu_memory_utilization=0.90, enforce_eager=True, language_model_only=True,
     trust_remote_code=False, seed=0, distributed_executor_backend="mp", disable_log_stats=True)`;
   - it sends ONE `generate` of the fixed synthetic prompt `Count from one to ten in English words, separated by
     commas:` as token ids, with `SamplingParams(max_tokens=32, temperature=0.0, seed=0)`, `use_tqdm=False`.
   - **Offline API only:** no HTTP server and no service port.

   The child gets the image's own environment (bootstrap passes no `-e`) with these changes:
   - proxies removed;
   - HOME and the HF, vLLM, Triton, Inductor and CUDA caches on the tmpfs;
   - `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `VLLM_NO_USAGE_STATS` and `DO_NOT_TRACK` set;
   - `VLLM_HOST_IP=127.0.0.1` and `VLLM_WORKER_MULTIPROC_METHOD=spawn`.

   Its stderr is discarded unread, and only the tail of its stdout is read.

**Receipt** (`rose-models/diagnostic`, at most 4096 bytes), `qwen`, re-checked by `qwen_gpu.sanitize`:
- `stop`: `done`, `bad_manifest`, `not_isolated`, `bad_inputs`, `engine_arg_unsupported`, `engine_failed`,
  `generate_failed`, `crashed` or `deadline`.
- **Isolation flags** with the tmpfs, shm and GPU-device counts, and the **input counts**.
- **`engine`:**
  - `run` and the step reached;
  - the vLLM version, and any unsupported option names (from the fixed list);
  - `loadS` and `generateS`;
  - prompt and generated token counts, `finishReason`, and the sha256 of the generated token ids;
  - whether every id is inside the vocabulary;
  - the quantization name, and an exception class or missing module name.
- **Never** the generated text, the prompt, a log line, a path or an environment value.

**How bootstrap runs it** (`cloud_qwen_gpu_once`):
- **Checks first:** the same hold, model stop, positive absence, read-only prerequisites, image digest, data-disk and
  pinned-manifest checks as `cloud-validate-once`. The staged Qwen directory must exist (`staging_missing`
  otherwise; Docker would create the bind source).
- **The container:**

  ```
  docker run --rm --pull=never --runtime=runc --gpus all --network none --name rose-cloud-qwen-gpu --cap-drop ALL
    --security-opt no-new-privileges --read-only --tmpfs /tmp:rw,exec,nosuid,nodev,size=8589934592,mode=1777
    --shm-size 4294967296 --cpus 16 --memory 68719476736 --memory-swap 68719476736
    -v /mnt/models-data/rose-cloud/qwen3.6-35b-a3b-fp8-95a723d0:/mnt/models-data/rose-cloud/qwen3.6-35b-a3b-fp8-95a723d0:ro
    --entrypoint python3 <pinned image id> -c <qwen_gpu.py> <manifest.json>
    {"deadlineS":870,"shmMaxBytes":4294967296,"tmpMaxBytes":8589934592}
  ```

- **GPUs** are requested exactly as the Muse launch requests them (`--gpus all` under `runc`; bootstrap registers
  the `nvidia` runtime but does not make it the default).
- **Cleanup:** the container is always removed and its absence verified. No retry.

**Corrections to the proposed bounds** (with evidence):

- **Shared memory added: `--shm-size 4 GiB`.** Docker's default `/dev/shm` is 64 MiB, and vLLM's TP2
  multiprocessing workers and NCCL use `/dev/shm`. The existing Muse TP2 launch uses 16 GiB (`shm_size_mb` in
  `serving/muse-glimmer-30b.json`).
- **Memory accounting:** the 8 GiB cache tmpfs and the 4 GiB `/dev/shm` are tmpfs, charged to the container's memory
  cgroup. They fit inside the 64 GiB limit, which is not widened, so processes keep at least 52 GiB when both are full.
- **Ports:** no service port is listened on or published.
  - **Worker rendezvous inside the container:** at vLLM v0.19.0, workers meet over TCP on 127.0.0.1 (`multiproc_executor.py`:
    `get_distributed_init_method(get_loopback_ip(), get_open_port())`). At v0.32.0 they use a file store under
    `tempfile.gettempdir()`.
  - **Unreachable from outside:** with `--network none` neither is reachable from outside the container.
- **900 s split:** the container is killed at 900 s, and the helper stops at 870 s so a receipt is still printed.
  `enforce_eager` skips CUDA-graph capture and `torch.compile`, which keeps the load inside the bound and the caches
  inside 8 GiB. The trade-off is that timings are not representative of serving.
- **Weights size-checked, not re-hashed** (see step 3).

**Checked against upstream vLLM source at v0.19.0** (the model card's minimum) **and v0.32.0** (the latest release):
- every `ENGINE` name is an `EngineArgs` field (`language_model_only: bool = MultiModalConfig.language_model_only`);
- `LLM.__init__(**kwargs)` forwards them to `EngineArgs`;
- `Qwen3_5MoeForConditionalGeneration` is registered (`qwen3_5`);
- `generate(prompts, sampling_params)` accepts a `TokensPrompt` (`prompt_token_ids`) and returns `RequestOutput`, whose
  `outputs[0]` has `token_ids` and `finish_reason`;
- `SamplingParams` has `max_tokens`, `temperature` and `seed`;
- `VLLM_NO_USAGE_STATS` and `DO_NOT_TRACK` are honoured.

**Exact-image-unverified** (the cached image is `vllm/vllm-openai:muse-glimmer`; its vLLM version is not recorded in
this repository, and the helper reports it):
- **vLLM version and options:** whether that version has these `EngineArgs` fields (checked before loading) and the
  `qwen3_5` model.
- **A100 FP8 path:** whether it runs the FP8 block-quantized MoE weight-only on the A100 (sm80, Marlin W8A16) for both
  dense and expert layers.
- **Memory:** whether TP2 with 0.90 GPU memory fits about 18.75 GB of weights per 40 GB GPU with a 2048 context and
  eager mode, and whether 64 GiB of RAM covers the loader's host memory.
- **Load time:** whether the load from the pd-ssd finishes inside 870 s.
- **GPU access:** whether `--gpus all` works with `--cap-drop ALL`, `no-new-privileges` and a read-only root.
- **Interpreter:** whether `python3 -I` finds vLLM in the image. The approved validation found `transformers` this way
  in the same image, which is evidence but not proof for vLLM.
- **Tokenizer:** whether the tokenizer adds special tokens to the fixed prompt as expected.

**Time outside the 900 s bound,** each step already bounded:
- the reset and boot;
- the model stop (≤ 180 s) and absence wait (≤ 120 s);
- `docker info` and image inspect (≤ 60 s each), the identity inspect (≤ 60 s) and `mountpoint` (≤ 30 s);
- `docker rm -f` (≤ 30 s, after a kill at 900 s at most) and `docker ps` (≤ 30 s);
- the guest attribute (≤ 10 s).

**Not shown even by `done`:** serving through the HTTP API, vision (the vision tower is not loaded), speech, ASR,
throughput or latency at serving settings, longer contexts, or output quality. The token hash is for comparison
across runs, not a correctness check.

**Proposed operator packet** (NOT authorized; a separate runtime decision):
- **Who:** only the original Mac operator, on `models-glimmer-01`.
- **Steps:**
  1. Build at the merge commit with the deployed attestation roots, and record `payloadTarSha256` (P). Confirm the
     payload lists `etc-rose-models/lib/rose_cloud/qwen_gpu.py` and `manifest.json` with the hashes Core reports.
  2. Set `startup-script` and `rose-models-payload` from that build, plus
     `rose-models-diagnostic=cloud-qwen-gpu-once:<P>:<fresh id>`.
  3. Do one reset.
  4. Read `rose-models/diagnostic`.
- **Consequences:**
  - the usual TLS and auth interruption;
  - Muse inference is held for that boot (the model service is stopped first, and its container is positively
    absent before the GPUs are used);
  - **both A100s are used for up to 900 s** and are released when the container is removed;
  - the staged data is read-only;
  - the **next** reset without a new id boots normally and starts Muse;
  - no download, install, data-disk write or retry.
- **Stop conditions:**
  - a `prerequisite` refusal;
  - `cleanup` other than `removed`;
  - `qwen: null`;
  - `stop` other than `done`.

## cloud-qwen-vision-once (CLOUD-QWEN-VISION-001; source only, has not run in this mode)

**Why:** the original Mac operator's PR345 text run succeeded at 21:07: TP2 FP8 text loaded in 348.9 s, 32 valid
tokens generated in 5.9 s, no unsupported option or runtime error, isolation and positive container absence verified,
both A100s RUNNING and Muse held. It used payload `f5bde894…dfda` and attempt `qwen-6baf1303bb984347b4ce`. That proves
the bounded text path only, not vision or serving.

**What changes:** `qwen_gpu.py` gains a vision mode, and bootstrap gains `cloud-qwen-vision-once`, which runs it in
its own container (`rose-cloud-qwen-vision`).
- **Unchanged:** the container controls, bounds, hold, isolation and input checks, and cleanup are identical to
  `cloud-qwen-gpu-once`. The argv differs **only** in the container name and in `"mode": "vision"` in the limits.
- **Text mode is the PR345 configuration, byte for byte:** the same `ENGINE`, `SAMPLING`, prompt, limits string,
  container and messages. Its receipt only gains `mode: "text"`.

**The vision request:**
- **Engine:** `VISION_ENGINE` is `ENGINE` without `language_model_only` (so the vision tower loads), plus:
  - `limit_mm_per_prompt={"image": {"count": 1, "width": 256, "height": 256}, "video": 0}`, which allows one image
    and no video and makes the profiling image 256 × 256;
  - `mm_processor_kwargs={"max_pixels": 65536}`.
- **The image:** one synthetic 256 × 256 solid red (255, 0, 0) image, made in the child with Pillow. No file and no
  download.
- **The prompt:** raw Qwen chat format, with thinking turned off by an empty think block:
  `<|im_start|>user\n<|vision_start|><|image_pad|><|vision_end|>What is the colour of this image? Answer with one
  word.<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n`. It is sent as
  `{"prompt": ..., "multi_modal_data": {"image": <PIL image>}}`, with the same greedy 32-token sampling.
- **`done`** requires the prompt to have expanded the placeholder to 1–256 `<|image_pad|>` tokens. 64 are expected;
  otherwise the stop is `vision_not_applied`.
- **Receipt additions:** `imageTokens`, and `expectedWordSeen` (whether the decoded answer contains "red"). It is a
  weak signal only and never gates `done`. The answer text never leaves the child.

**Checked against upstream vLLM source** (v0.19.0 and v0.32.0):
- `TextPrompt` with `multi_modal_data` is an accepted `LLM.generate` prompt;
- `limit_mm_per_prompt: dict[str, int | dict[str, int]]` accepts both the count form and per-item dummy options
  (`ImageDummyOptions` with `width`/`height`, `extra="forbid"`);
- `mm_processor_kwargs` is an `EngineArgs` field;
- `Qwen3_5MoeForConditionalGeneration` uses `Qwen3VLMultiModalProcessor`, whose image placeholder is
  `<|vision_start|><|image_pad|><|vision_end|>`;
- `Qwen3VLProcessingInfo._get_vision_info` applies `mm_kwargs["max_pixels"]` as the `longest_edge` override, and
  counts vision tokens as `num_patches // merge_size**2`.
- With 64 tokens per image, vLLM's "max_tokens_per_mm_item … is larger than max_num_batched_tokens" refusal and the
  max_model_len check cannot trigger.

**Feasibility corrections:**
- The profiling image is bounded with the per-item dummy options. A count-only limit would profile the processor's
  default largest image, whose token count can exceed a 2048-token context.
- `min_pixels` is not passed: there is no source evidence that it is honoured.
- `video: 0` keeps the video dummy, which uses 2048 frames by default, out of profiling.
- Memory and time bounds are unchanged (64 GiB, 16 CPUs, 8 GiB tmpfs, 4 GiB shm, 900 s). The vision tower adds a
  small amount of GPU memory over the measured text run.

**Exact-image-unverified:**
- whether the image's vLLM has these options (the helper refuses before loading if `EngineArgs` lacks any);
- whether Pillow is importable (a missing one is reported as `ModuleNotFoundError` `PIL`);
- whether `max_pixels` reaches the processor in that version (it is reported by `imageTokens`);
- that `RequestOutput.prompt_token_ids` carries the expanded pads. Source shows positions in that list, not the
  output construction. Without them the stop is `vision_not_applied`, never a false `done`;
- the vision-tower load time and memory on the A100s;
- whether the answer mentions the colour.

**Not shown even by `done`:** vision quality, real screenshots, PR288's image resizing or chat-route template, more
than one image, video, HTTP serving, or latency at serving settings.

**Proposed operator packet** (within Nathan's expanded Qwen authority; runtime only by the original Mac operator):
1. Build at the merge commit with the deployed attestation roots, and record P. Confirm `qwen_gpu.py` and
   `manifest.json` have the hashes Core reports.
2. Set `startup-script` and `rose-models-payload` from that build, plus
   `rose-models-diagnostic=cloud-qwen-vision-once:<P>:<fresh id>`.
3. Do one reset.
4. Read `rose-models/diagnostic`.

- **Consequences:** the same as `cloud-qwen-gpu-once`:
  - the TLS interruption;
  - Muse held, stopped and absent first;
  - both A100s used for up to 900 s;
  - staged data read-only;
  - the next reset without a new id starts Muse.
- **Stop conditions:**
  - a `prerequisite` refusal;
  - `cleanup` other than `removed`;
  - `qwen: null`;
  - `stop` other than `done`.

## cloud-qwen-adapter-once (CLOUD-QWEN-ADAPTER-001; source only, has not run in this mode)

**Why:** PR348's vision run succeeded at 21:40: load in 400.1 s, 64 image tokens, 2 valid tokens, the colour flag
passed, cleanup verified, Muse held. It used vLLM's offline API, not the adapter Rose actually uses.
`rose_cloud/vllm_brain.py` (`VllmBrain`/`VllmSequence`) is an **HTTP client** of vLLM's OpenAI server:
- it uses `/v1/completions` for text, and `/v1/chat/completions` with `RAW_TEMPLATE` once a sequence holds an image;
- cancellation is client-side: the `cancel` callable or closing the generator stops reading and shuts the socket
  down, and nothing produced afterwards is appended;
- whether vLLM then aborts the request is server behaviour the adapter cannot prove by itself.

**What the mode does:** `qwen_gpu.py` adapter mode, run by bootstrap's `cloud-qwen-adapter-once` in its own
container `rose-cloud-qwen-adapter`.
- **Unchanged:** the same container controls and bounds as the text and vision modes. The argv equals theirs
  except for the container name, `"mode": "adapter"`, and one more argument: the payload's `vllm_brain.py`, `coop.py`,
  `pump.py` and `adapter_session.py` as a JSON bundle. That bundle is written to the tmpfs and imported from there.
- **Steps inside the container:**
  1. it refuses if `EngineArgs` lacks any vision option;
  2. it starts `vllm serve <qwen dir> --served-model-name rose-brain --host 127.0.0.1 --port 18000 --chat-template
     <RAW_TEMPLATE on the tmpfs>` with the vision mode's bounds:
     - `--tensor-parallel-size 2 --max-model-len 2048 --max-num-seqs 1 --gpu-memory-utilization 0.9`;
     - `--enforce-eager --seed 0 --distributed-executor-backend mp`;
     - `--limit-mm-per-prompt {"image": {"count": 1, "height": 256, "width": 256}, "video": 0}`;
     - `--mm-processor-kwargs {"max_pixels": 65536}` and `--chat-template-content-format openai`;
     - no `--disable-log-stats`, so `/metrics` stays on;
  3. it waits for `/health`, loads the tokenizer from the staged files, and runs `adapter_session.run_session`
     once with a synthetic 256 × 256 red image;
  4. the server's process group is killed at the end, and the container is removed and confirmed absent.
- **The server port:** it listens only on the container's loopback; `--network none` means nothing is published
  and nothing outside can reach it. This is the one change from the offline modes, and it is required because the
  adapter is an HTTP client.

**The session** (`rose_cloud/adapter_session.py`; synthetic text and image only; every request greedy and bounded):
1. **Turn 1, text:** the model's own system prefix and a user turn, at most 16 tokens, on the completions route.
2. **Turn 2, image:** a user turn holding the image, on the chat route. The server's `prompt_token_ids` are
   compared with the client's (`promptExact`).
3. **Turn 3:** text with the image still in history, still on the chat route and still compared.
4. **History:**
   - fork after turn 3, then rewind the original to before turn 3 and generate; the fork must be untouched and the
     rewound request exact;
   - then rewind to before the image: the image must be dropped (`staleImageDropped`) and the next request must
     use the text route again.
5. **Cancellation in flight:** a request of up to 256 tokens, cancelled through `cancel` after 3 tokens.
   - **Client side** (measured directly): no token appended after the cancel (`noLateAppend`), the worker thread
     joined, and `clientStopS`.
   - **Server side** (evidence, never assumed):
     - the `vllm:num_requests_running` gauge returning to 0 within 20 s;
     - a 1-token probe. With `--max-num-seqs 1`, a request still generating would hold the probe for the remaining
       ~250 tokens.
   - **`semantics`:**
     - `inflight_abort`: the gauge reads 0 and the probe is under 15 s;
     - `inflight_abort_probe_only`: no gauge, and the probe is fast;
     - `client_suppressed_only`: the gauge is above 0 or the probe was slow. The client dropped the tokens but the
       server kept generating;
     - `completed_before_cancel`: the request finished before the cancel fired;
     - `unknown` otherwise.
6. **Cancel after completion:** a 2-token request that finishes, then the cancel. The sequence and the request
   count must not change (`lateCancelNoop`). That is a no-op, not an abort.

**Receipt** (re-checked by `qwen_gpu.sanitize`; the worst case is tested at ≤ 4096 bytes): `serverReadyS`,
`serverExit`, and the session's step, `imageTokens` and `imagesInHistory`.
- **Per turn:** token count, `endedWithImEnd`, first-token and total seconds, `promptExact`, and the sha256 of the
  token ids.
- **History:** the history flags.
- **Cancellation:** the cancel counts, flags, seconds, gauge reading and probe, and `semantics`.
- **Late cancel:** the late-cancel flags.
- **Never** prompt, reply or image content.

**Contract findings:**
- `VllmBrain` has no server-side cancel call. Its cancel ends the HTTP stream, and an abort depends on vLLM
  noticing the disconnect, which is what this session measures.
- A cancel that arrives after the last token is a no-op by design.
- Nothing in the adapter retries.

**Exact-image-unverified:**
- the image's `vllm serve` CLI accepts these flags, and its `/health` and `/metrics` paths are as above;
- the chat route accepts `return_token_ids` and `continue_final_message`, and reports `prompt_token_ids` in the first
  chunk (if not, `promptExact` stays `null`);
- whether vLLM aborts on a client disconnect at that version (reported, not assumed);
- that the server load (one more process than the offline modes) fits in 870 s and 64 GiB;
- the `return_token_ids` support that `contract.BRAIN_PREREQUISITES` lists.

**Not shown even by `done`:** PR288's conductor, runtime scheduling or audio, real screenshots, more than one image,
long contexts, throughput, or the 443 edge.

**Proposed operator packet** (within Nathan's expanded Qwen authority; runtime only by the original Mac operator):
1. Build at the merge commit with the deployed attestation roots, and record P. Confirm the payload lists
   `etc-rose-models/lib/rose_cloud/qwen_gpu.py`, `vllm_brain.py`, `coop.py`, `pump.py`, `adapter_session.py` and
   `manifest.json` with the hashes Core reports.
2. Set `startup-script` and `rose-models-payload` from that build, plus
   `rose-models-diagnostic=cloud-qwen-adapter-once:<P>:<fresh id>`.
3. Do one reset.
4. Read `rose-models/diagnostic`.

- **Consequences:**
  - the TLS interruption;
  - Muse held, stopped and absent first;
  - both A100s used for up to 900 s;
  - staged data read-only;
  - the next reset without a new id starts Muse.
- **Stop conditions:**
  - a `prerequisite` refusal;
  - `cleanup` other than `removed`;
  - `qwen: null`;
  - `stop` other than `done`.

  Read `semantics` as the cancellation finding. It is not a pass or fail gate.

## cloud-qwen-warm-once (CLOUD-QWEN-WARM-001; source only, has not run): one load, many sessions

**Why:** each diagnostic so far cost a reset, the Muse stop and a ~400 s load (PR345: 348.9 s; PR348: 400.1 s). Nathan
(21:58): "remember speed". `cloud-qwen-adapter-once` (PR349) already runs every test kind on one loaded engine. This
mode keeps that engine loaded so later sessions cost seconds, without a reset.

**How it runs** (bootstrap `cloud_qwen_warm_once`):
1. **Prerequisites and controls:** the same prerequisites as the adapter mode (shared `qwen_prepare`) and the same
   container controls (shared `qwen_argv`). The container is started **detached** (`-d`) as `rose-cloud-qwen-warm`,
   with `"mode": "warm"` and the helper's deadline set to the load plus the window (870 + 7200 s). The container ends
   itself at that deadline even if bootstrap does not.
2. **Startup:** the helper checks isolation and inputs, writes the adapter bundle, and starts `vllm serve` on the
   container's loopback. Once `/health` answers, it writes `/tmp/rose-qwen-gpu/state.json` and keeps the server up.
   Bootstrap waits for that file (at most 900 s).
3. **First session:** bootstrap runs the `full` case (the PR349 session) with `docker exec` and publishes it to
   `rose-models/qwen-session` (`requestId: "first"`, `index: 0`). The `rose-models/diagnostic` receipt then shows
   `phase: "serving"`, `readyAfterS`, and a compact first-session summary.
4. **Request loop:** bootstrap polls ONE metadata key every 10 s:
   `rose-models-qwen-session=<payload sha256>:<fresh id>:<case>`.
   - A value counts only when it changes.
   - It runs only if it names **this payload**, an id not used in this window (not the attempt id, not `first`), and
     one of the payload's cases.
   - Otherwise it is published as `ignored`: `malformed`, `other_payload` or `id_already_used`.
   - Each accepted request runs `qwen_gpu.RUNNER` inside the container (`docker exec … python3 -I -B -c <RUNNER from
     the payload> /tmp/rose-qwen-gpu <qwen dir> <case>`, at most 300 s). That is a new adapter session against the
     loaded server: the tokenizer loads in the runner, with no model reload. Its receipt goes to
     `rose-models/qwen-session`.
5. **End:** the window (7200 s after the first session), 1800 s without a request, 24 sessions, or the container
   disappearing. Then `docker rm -f`, a verified absence, and the final `rose-models/diagnostic` receipt
   (`phase: "final"`, `sessions`, `ignored`, `endedBy`, `cleanup`). Inference stays held for the boot.

**Cases** (`adapter_session.CASES`, in the attested payload; a request carries only the name):
- `full`: the PR349 session, covering text, image, image history, fork, rewind, the stale image, the in-flight cancel
  and the late cancel;
- `text`: three text turns, for warm first-token and total timings;
- `image`: an image turn and a follow-up, with the prompts compared;
- `cancel`: the in-flight cancel with its gauge and probe evidence, and the late cancel.

**What does not change:**
- No code comes from metadata. A new or changed test still needs a new payload, built and attested, and one reset:
  executing code delivered outside the payload would change the security model.
- The same `--network none`, loopback-only server, CPU, RAM, tmpfs and shm limits, and the same single original Mac
  operator.
- `cloud-qwen-adapter-once` stays as the one-shot proof path.

**Proposed operator packet** (within the expanded Qwen authority; runtime only by the original Mac operator):
1. Build at the merge commit with the deployed roots, and record P. Confirm `qwen_gpu.py` and `adapter_session.py`
   have the hashes Core reports.
2. Set `rose-models-diagnostic=cloud-qwen-warm-once:<P>:<fresh id>`, and do one reset.
3. Read `rose-models/diagnostic` until it shows `phase: "serving"`, then read `rose-models/qwen-session` for the first
   session.
4. For each further test, set the request key, then read `rose-models/qwen-session`; no reset:

   ```bash
   gcloud compute instances add-metadata models-glimmer-01 --project protocols-465723 --zone us-central1-f --metadata=rose-models-qwen-session=<P>:<fresh id>:<full|text|image|cancel>
   ```

5. The window ends by itself. The final `rose-models/diagnostic` shows `endedBy` and `cleanup`. The next reset without
   a new diagnostic id starts Muse.

**Exact-image-unverified, beyond the adapter mode's:**
- that `docker exec` into the `--read-only`, `--cap-drop ALL` container runs `python3` as expected;
- that metadata polling every 10 s from bootstrap is acceptable for the window;
- that the startup script may stay in bootstrap for up to about 2.5 h on a diagnostic boot. Bootstrap returns only
  when the window ends; the model stays held meanwhile.

## cloud-qwen-contract-once (MODELS-QWEN-CONTRACT-001; source only, has not run): the proposed serving contract

**Why:** the original Mac's local preparation found that every existing diagnostic serves `rose-brain` with a 2048-token
context, the adapter's raw template and no reasoning parser. None of them can show whether the PROPOSED serving
profile of `serving/qwen-fp8.json` (`--qwen-settings proposed`) meets the Rose Models client's contract. This mode
closes that gap. It is one fixed profile, not a new kind of test.

**Profile** (`qwen_gpu.CONTRACT_SERVER_ARGS`, asserted equal to `render_local.qwen_args(qwen, 'proposed')`):
- `vllm serve <staged dir> --served-model-name rose-qwen --host 127.0.0.1 --port 18000`;
- the model's own chat template: no `--chat-template`, nothing from the adapter;
- `--reasoning-parser qwen3` and `--max-model-len 8192`;
- every other flag is the proven `SERVER_ARGS`.

Before anything loads, the helper refuses (`engine_arg_unsupported`) if the image's EngineArgs lacks
`reasoning_parser`. `vllm serve` itself rejects an unknown parser name before loading, and that shows as
`server_failed` with its exit code.

**How it runs:** exactly `cloud-qwen-warm-once` (previous section), with these differences:
- the container is `rose-cloud-qwen-contract`, with `"mode": "contract"`;
- the bundle is `QWEN_CONTRACT_FILES` (`contract_session.py`, `qwen_contract_requests.json`) instead of the adapter
  sources;
- the runner is `qwen_gpu.CONTRACT_RUNNER`.

Everything else is shared code: the prerequisites, the container controls (`--network none`, no published port, the
staged directory read-only, 16 CPUs, 64 GiB with no swap, the 8 GiB exec tmpfs, the 4 GiB shm), the 900 s startup
bound, the 300 s per run, the 7200 s window, 1800 s idle, 24 runs, and the removal with verified absence.

**Cases** (`contract_session.CASES`; a request names one, as `rose-models-qwen-session=<P>:<fresh id>:<case>`):
- `contract` (the first run): all of the following on the one loaded server;
- `chat`: one plain turn, expected `final`;
- `tool`: an `open_app` proposal, expected `open_app:calculator`, then the request after its `launch_requested` tool
  result, expected `final`. The follow-up is sent only when the first reply was that proposal;
- `long`: a request whose history (a code word, then 26 journal turns, 14,091 characters, inside the client's 20,000)
  is far past the proven 2048 tokens. It asks for the code word: expected `final`, `recalled`, and `beyondProven`
  (reported `prompt_tokens` > 2048).

Every run also checks `/v1/models`: exactly `["rose-qwen"]`, with `max_model_len` 8192.

**What a reply is judged on:**
- **The requests** are the client's own bodies, byte for byte: `rose-chat-core/tests/qwen_contract.rs` builds them
  with the real `rose_chat_core::agent::Agent` (model `rose-qwen`, allowlist `calculator`, no system-prompt prefix,
  default limits: `max_tokens` 512, no temperature) and fails if `qwen_contract_requests.json` differs.
- **The verdict** is the one that agent reaches, in its order: identity, truncation, empty content, length, then the
  closed envelope. `contract_session.verdict` is checked against the agent itself on 34 shared fixtures
  (`tests/fixtures/qwen_contract_verdicts.json`), in both languages.
- **Per reply:** `identityExact` (the response `model` is exactly `rose-qwen`), `reasoningField` and `reasoningChars`,
  `thinkInContent`, `finish`, `contentChars`, token usage and seconds.
- **`allExpected`** holds only when all of these hold:
  - the models check;
  - every expected verdict, with the exact identity;
  - reasoning separated: some reply had a reasoning field, and no reply had `<think>` in its content;
  - the long case recalled the code word beyond 2048 tokens.
- **Never exported:** prompt, reply, reasoning text and the code word.

**Not shown even when it passes:**
- the app's real system prompt for Qwen (a Product decision: `ModelsService` still compiles `muse-glimmer` and Muse's
  `Reasoning strength: low`);
- latency through the edge;
- behaviour under the persistent service (`render_service --model qwen`);
- any PR288 voice or conductor path.

A reply that thinks past `max_tokens` 512 is reported as `failed:truncated_reply`, which is exactly what the client
would do.

**PR353 runtime record** (original Mac, payload `34397ad1…`, boot `a667c989…`). This run did NOT pass the contract:
- **Configured as intended:** the exact `rose-qwen` identity, the qwen3 parser and the 8192-token context; the
  server was ready in 464 s.
- **Initial full run: failed.** Chat and the calculator tool loop passed, with reasoning returned separately, valid
  envelopes and no `<think>` in the content. The `long` request then hit the driver's 55 s bound (`TimeoutError`,
  `allExpected: null`).
- **Standalone `long` repeat, unchanged, on the same engine: passed once.** 3451 prompt tokens, 405 completion
  tokens, 53.1 s, correct recall. One pass, not evidence of reliable latency.
- **Full repeat, unchanged: failed.** Chat and the calculator proposal passed, then the request after the tool result
  hit the 55 s bound (`TimeoutError`, `allExpected: null`).

**Diagnosis (MODELS-QWEN-CONTRACT-002):**
- The 55 s was this driver's own bound, chosen so five requests fit bootstrap's 300 s. It was not the product's.
- The client waits 120 s per request:
  - `inference_timeout_ms` 120000 in rose-chat-core `Limits::default()`;
  - Android sends no limits, so those defaults apply, and passes the 120 s to its read timeout;
  - the edge's upstream timeout is also 120 s.
- The client allows 512 completion tokens, thinking included. At the roughly 7.6 tokens/s of the lone pass (an
  inference), that is about 66 s.
- So the driver gave up on requests the phone would still have waited for. Because the `TimeoutError` escaped, the
  timed-out request left no time or token counts.

**Correction (this driver only; product settings unchanged):**
- **Client limits from the client.** rose-chat-core's test writes `Limits::default()` into the fixture's `client`,
  and each request waits exactly that `inference_timeout_ms`.
- **A timeout is a reply.** A request still unanswered then is recorded with the client's own verdict,
  `failed:timeout_response`, plus `timedOut` and its elapsed seconds. Later requests still run.
- **Nothing is hidden.** A case stays inside bootstrap's 300 s per run (`CASE_BUDGET_S` 285). A request that could not
  get the full client wait in what is left is not sent and is listed in `notRun` as `budget`. A tool follow-up whose
  proposal did not come is listed as `no_proposal`.
- **`allExpected` is now always `true` or `false`,** never null. It is false whenever anything timed out or did not run.
- **The `/v1/models` check** has its own 10 s bound and records `timedOut`.
- **Old code fails the new tests:** run against the PR353 driver, they fail with `TimeoutError`.

**Still open (deadline matching does not fix it):** a 30–70 s wait for a simple answer is a separate usability
concern. It is mostly thinking length at the measured rate. Answering it needs per-reply token and time evidence from
a run with this driver, not a longer deadline.

**Proposed operator packet** (runtime only by the original Mac operator):
1. Build at the merge commit with the deployed roots, and record P. Confirm the hashes Core reports for `qwen_gpu.py`,
   `contract_session.py` and `qwen_contract_requests.json`.
2. Set `rose-models-diagnostic=cloud-qwen-contract-once:<P>:<fresh id>`, and do one reset.
3. Read `rose-models/diagnostic` until it shows `phase: "serving"` (`first.allExpected`), then read
   `rose-models/qwen-session` for the whole contract receipt.
4. If the first run lists a request in `notRun` (`budget`), request that case on its own (`long`, or `tool`); each
   single case fits a full client wait per request. Optionally repeat a case on the same engine (the client sends no
   temperature, so the server's default sampling applies, and repeats show stability):

   ```bash
   gcloud compute instances add-metadata models-glimmer-01 --project protocols-465723 --zone us-central1-f --metadata=rose-models-qwen-session=<P>:<fresh id>:<contract|chat|tool|long>
   ```

5. The window ends by itself, and the final receipt shows `endedBy` and `cleanup`. The next reset without a new
   diagnostic id starts Muse.

## cloud-qwen-contract-nothink-once (MODELS-QWEN-CONTRACT-NOTHINK-001; source only, has not run): thinking off

**Why** (from the PR360 receipt, original Mac):
- The one full pass of `cloud-qwen-contract-once` took 29.5–40.1 s per reply for 224–301 completion tokens, about
  7.5 tokens/s.
- Per reply, `reasoningChars`/`contentChars` were: chat 751/107, tool proposal 1144/74, tool follow-up 878/106,
  long 1056/70.
- Those are measured character shares, not measured token or time attribution.
- PR288's own Qwen3.6 path runs with thinking off (`demo/brain.py` `NO_THINK`, `rose/brain.py` `GEN_FAST`).

**Profile** (`qwen_gpu.CONTRACT_NOTHINK_SERVER_ARGS`): exactly the contract profile, plus
`--default-chat-template-kwargs '{"enable_thinking": false}'`. A server-wide default; the client and its request
bodies are unchanged.

**Source support, at vLLM commit `99a10304d`** (the version PR349 recorded for the cached image, `0.26.1rc1.dev608`):
- `FrontendArgs.default_chat_template_kwargs` exists (`vllm/entrypoints/openai/cli_args.py`).
- The chat server merges it into each request's template kwargs
  (`chat_completion/serving.py` `_effective_chat_template_kwargs`).
- The `qwen3` parser is built with those merged kwargs. With `enable_thinking` false, `extract_reasoning` returns the
  whole output as content (`vllm/parser/qwen3.py`).

This is source for that commit, not proof about the image. So, inside the image:
- before loading, the helper refuses (`engine_arg_unsupported: default_chat_template_kwargs`) if `FrontendArgs` lacks
  the field;
- `vllm serve` rejects a flag it does not know (`server_failed`).

Nothing falls back silently.

**Rule** (`contract_session.run(..., thinking=False)`, from the `thinking` flag the helper writes into `state.json`):
- **Unchanged:** every other check (exact identity, `/v1/models`, envelope verdicts, the tool loop, long recall
  beyond 2048 tokens, the client's 120 s and 512 tokens, timeouts, `notRun`, the 300 s run budget).
- **Replaced:** instead of reasoning in its own field (`reasoningSeparated`), `noThinking` requires that no reply
  carries any reasoning and none has thinking in its content.
- **Never a pass:** an answer a parser moved into the reasoning field leaves the content empty, which is
  `failed:empty_completion`.

**Running it:** same machinery, bounds, cases and request key as `cloud-qwen-contract-once`, in its own container
`rose-cloud-qwen-contract-nothink`. The thinking profile and its mode are unchanged.

**Conformance case** (MODELS-QWEN-SERVE-NOTHINK-001; a request `<P>:<fresh id>:conformance` in either contract
mode):
- **The prompts:** the existing 20-prompt envelope set (`serving/conformance/prompts.json`: 10 chat, 8 calculator
  launches, 2 not-allowed apps). Each prompt is the first turn of a fresh client agent, built by rose-chat-core's test
  with the Qwen config (no system prompt). One request each, nothing executed.
- **Limits:** the client's 120 s per request and the 300 s run budget. A prompt that could not get a full wait is
  listed as `{"notRun": "budget"}` and counts as neither parsed nor conforming.
- **Per prompt:** verdict, `ok`, seconds, completion tokens, identity, timeout and thinking flags.
- **Summary:**
  - `existingBarPass` is the harness's own bar: at least 19 parsed, no `open_app` naming another app, and no `open_app`
    outside launch prompts;
  - `pass` additionally needs at least 19 replies meeting their expectation, so a parsed envelope never stands in for
    the right action;
  - `allExpected` also needs every prompt sent, exact identity and the profile's thinking rule.
- **Not a guarantee:** at the measured 2.7–8.5 s per reply the 20 prompts should fit, but each request needs a full
  client wait left to be sent, so slower replies end in `notRun`.

**Operator packet** (original Mac only, after merge):
1. Build the payload at the merge commit.
2. Set `rose-models-diagnostic=cloud-qwen-contract-nothink-once:<P>:<fresh id>` and do one reset.
3. Read `rose-models/qwen-session` for the contract receipt.
4. Compare per-reply `seconds`/`completionTokens` with the PR360 thinking run. Latency stays unproven until then.

## Later, separately approved (not part of this packet)

1. **Staging.** Download the four groups (37.6 GB) to the staging root and verify every size and hash, through
   `cloud-stage-once` (previous section). The source is in place; running it is a separate runtime decision.
2. **Loopback text and speech trial**, about 30 minutes, about US$3.68 at US$7.35/h, excluding disk and network.
   - **vLLM:** in an image with vLLM ≥ 0.19.0 that the preflight shows registers `qwen3_5_moe` (offline,
     `--pull=never`), run `vllm serve /mnt/models-data/rose-cloud/qwen3.6-35b-a3b-fp8-95a723d0
     --served-model-name rose-brain --host 127.0.0.1 --port 8000 --tensor-parallel-size 2 --max-model-len 8192`,
     with no `--trust-remote-code`, then `rose_cloud.smoke`.
   - **Speech:** run `rose_cloud.speech` with `ptts` installed from the staged wheel (`pip install --no-index`)
     inside a throwaway container.
   - **Cleanup:** remove the trial containers. Staged files stay unless their removal is approved. The Muse hold
     does not change.
3. **Phone endpoint.** PR288's signed remote listener (TLS pin on 8443) or the existing 443 edge.
   - **Network:** a firewall rule or edge route.
   - **Pairing:** QR pairing from the phone, which is a phone action.
   - **Auth:** the phone's device key and the server pin. No new IAM.
   - These each need explicit scope.

## Tests

```bash
python3 -I tests/test_vllm_brain.py
```

```bash
python3 -I tests/test_contract.py
```

```bash
python3 -I tests/test_preflight.py
```

```bash
python3 -I tests/test_stage.py
```

```bash
python3 -I tests/test_head_probe.py
```

```bash
python3 -I tests/test_listen.py
```

```bash
python3 -I tests/test_vision.py
```

```bash
python3 -I tests/test_stage.py
```

```bash
python3 -I tests/test_pr288_tasks.py
```

```bash
python3 -I tests/test_coop.py
```

```bash
python3 -I tests/test_speech_wire.py
```

```bash
python3 -I ../serving/tests/test_service_bootstrap.py
```

```bash
python3 -I ../serving/tests/test_service_render.py
```

- **PR288 dependencies:** the PR288 parts need `ROSE_PR288_DIR=<absolute path to prototypes/voice-phone-demo>` and
  PR288's dependencies. Without them they are skipped, with the reason printed.
- **Preflight tests:** `test_preflight.py` runs the real script as a subprocess through bootstrap's own `Host.run`,
  covering a hung probe killed at the deadline, noisy stdout, and staged files present, wrong or missing.
- **Fixtures:** the vLLM, `ptts` and file trees in those tests are fixtures, not the serving image. The VAD,
  recognizers and decoder in `test_listen.py` are fixtures too, not recognition.
