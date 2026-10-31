# Rose on the cloud VM (MODELS-ROSE-CLOUD-001, -002, -003)

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
| Brain (`vllm_brain.py`) | **Real adapter**, using token-id `/v1/completions` streaming. It has been tested against a stand-in server only. Vision is unsupported. |
| Speaker (`phonon.py`, `speech.py`) | The `ptts` provider sits behind PR288's unchanged `Speaker` and `/ws/audio`. **FIXTURE:** a stand-in engine and PR288's `FakeTransport` player. No real synthesis has run. |
| Preflight (`preflight.py` + bootstrap `cloud-preflight-once`) | **Source and tests only**, covered in `test_preflight.py` and the bootstrap/render suites. |
| Listener (Silero, smart-turn, Nemotron, Parakeet) | **Unsupported.** This is the next slice: reuse PR288's 16 kHz endpointing and the exact upstream model revisions. |
| Phone endpoint, auth, network, vision | **Not in scope yet.** Each needs explicit scope (see the last section). |

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

## Later, separately approved (not part of this packet)

1. **Staging.** Download the four groups (37.6 GB) to the staging root and verify every size and hash.
   - **Channel:** there is no guest shell, so this needs its own bootstrap mode or another approved channel.
   - **Network:** egress to `huggingface.co` and PyPI only; no credentials (all pins are public and not gated).
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
   - These each need explicit scope, and so do Linux ASR (next) and vision.

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
- **Fixtures:** the vLLM, `ptts` and file trees in those tests are fixtures, not the serving image.
