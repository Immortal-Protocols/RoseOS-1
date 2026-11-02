# Muse Glimmer persistent service (MODELS-ALWAYSON-SERVICE-001)

**Owners.** Core owns this source. Infra runs every command below with its own authorized identity. Nothing here was
run against a live resource.

**Evidence for the shape** (Compute metadata read on 2026-10-30, about 17:07 UTC, project `protocols-465723`):
- **`a2-highgpu-2g`** (2× A100 40 GB, 24 vCPU, 170 GiB RAM) exists in us-central1-a, -b, -c and -f.
- **Quota fits:** `NVIDIA_A100_GPUS` is 16 with 1 used (the KEEP VM `models-a100-hold-20261028-01`, untouched), and
  `A2_CPUS` is 192.
- **`a2-ultragpu-1g`** (A100 80 GB) has `NVIDIA_A100_80GB_GPUS` quota **0**, so it is not usable without a quota grant.

**Shape:** STANDARD (not Spot), BF16, TP=2, 8,192 context, 1 sequence. The model and image are pinned unchanged in
`../muse-glimmer-30b.json` profile `a100x2-bf16-vllm-persistent`.
- **Fit:** 3.44 GB headroom per GPU, derived and not measured. The startup guard checks the device-reported memory.
- **Cost:** about US$7.35/h for the machine (about US$5.4k per 30 days), plus about US$100/month for disks. This is a
  public list price, **not read from billing**; Infra confirms it.
- **Data disk:** 500 GB pd-ssd, about 240 MB/s, so a restart reloads 60 GB in about 4 minutes. 200 GB would take about
  10 minutes.

## 1. Resources (Infra; spend approved by Nathan, 17:47)

```bash
B=/tmp/rose-models-bootstrap   # from step 2, built at a merged commit
P=protocols-465723; R=us-central1; Z=us-central1-a   # if capacity is refused: us-central1-c, -b, -f
gcloud compute addresses create models-glimmer-ip --project=$P --region=$R
gcloud compute disks create models-glimmer-data --project=$P --zone=$Z --type=pd-ssd --size=500GB
gcloud compute instances create models-glimmer-01 --project=$P --zone=$Z --machine-type=a2-highgpu-2g \
  --provisioning-model=STANDARD --maintenance-policy=TERMINATE --restart-on-failure \
  --image-project=ubuntu-os-cloud --image-family=ubuntu-2404-lts-amd64 --boot-disk-size=150GB --boot-disk-type=pd-balanced \
  --disk=name=models-glimmer-data,device-name=models-data,mode=rw,auto-delete=no \
  --address=models-glimmer-ip --tags=models-https --no-service-account --no-scopes \
  --metadata=enable-guest-attributes=TRUE \
  --metadata-from-file=startup-script=$B/startup-script.sh,rose-models-payload=$B/rose-models-payload.b64
gcloud compute firewall-rules create models-https --project=$P --network=default --direction=INGRESS \
  --action=ALLOW --rules=tcp:443,tcp:80 --target-tags=models-https --source-ranges=0.0.0.0/0
```

- **Port 80** serves only `/.well-known/acme-challenge/` and returns 404 for everything else. It exists for automatic
  renewal.
- **No SSH is needed.** The startup script installs everything (step 2). The instance needs no service account; it
  pulls the image from Docker Hub and packages from the Ubuntu and NVIDIA repositories over its external IP.
- **DNS:** set the A record `models.a-list.com` to the reserved address (Infra's DNS custody).

## 2. Build the bootstrap (offline, at a merged commit)

```bash
python3 workspace/models/serving/service/build_bootstrap.py --out /tmp/rose-models-bootstrap \
  --attestation-roots <dir with Google's hardware-attestation root certificates, .pem>
```

- **Output:** `startup-script.sh`, `rose-models-payload.b64` (about 53 KB; the limit is 256 KB) and
  `bootstrap-manifest.json`.
- **Integrity:** the script embeds the payload tar's sha256 and size, and refuses any other payload. Compare
  `payloadTarSha256` with the value Core reports for that commit; the build is deterministic.
- **Roots:** omit `--attestation-roots` to defer them. The VM then reports `auth: waiting attestation_roots_missing`.

## 2a. Model selection: Muse (default) or the staged Qwen (MODELS-QWEN-SERVE-001; source only, never run as the service)

**Default:** Muse Glimmer is the default. The payload built without `--model` is byte-identical to before (tested).

**Qwen:** only an explicit build selects it, and the same model slot then serves Qwen:

```bash
python3 workspace/models/serving/service/build_bootstrap.py --out /tmp/rose-models-qwen --model qwen \
  --qwen-settings proven --attestation-roots <deployed roots dir>
```

**What changes with `--model qwen`** (an overlay; every other file, including the edge, auth, TLS, the Muse launch
scripts and config, and every diagnostic mode, is unchanged and tested):

- **`bin/launch-qwen`:**
  - the same cached image and startup guard;
  - host networking bound to `127.0.0.1:8000` only, behind the existing edge;
  - `/mnt/models-data/rose-cloud/qwen3.6-35b-a3b-fp8-95a723d0` mounted read-only;
  - offline: `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, no usage stats;
  - `vllm serve <staged dir> --served-model-name rose-qwen`.
- **`bin/start-model`:** `verify_staged.py` checks the pinned rose_cloud manifest (sha256 `ebcfb082…`), every
  file's size, and the hash of every non-weight file, then launches. Anything else exits 4, which systemd does not
  restart. **There is no download branch.**
- **`models-health`:** probes `rose-qwen` (the same readiness rule: `/v1/models` lists it, and a real completion
  returns tokens). **`models-verify-full`** rehashes the staged weights daily.
- **Unchanged names:** the unit (`muse-glimmer.service`) and container (`muse-glimmer`) keep their names. They are the
  model slot that bootstrap's hold and stop logic, health and the edge already use; the unit description and the
  service manifest say Qwen.

**Settings:**
- **`--qwen-settings proven`** (default) renders exactly the `vllm serve` flags of the original Mac's PR349 adapter run
  and the PR350 warm runs (TP2, `--max-model-len 2048`, eager, one sequence, the vision limits). They are asserted
  equal to `rose_cloud.qwen_gpu.SERVER_ARGS`.
- **`--qwen-settings proposed`** adds `--max-model-len 8192` and `--reasoning-parser qwen3`. They are labelled **NOT
  runtime-proven** in `bin/launch-qwen` and as `notRuntimeProven` in the service manifest. The Rose Models client needs
  both (`rose-chat-core/tests/qwen_contract.rs`):
  - with the proven flags, a thinking Qwen reply carries `<think>` inside `content`, and `local-agent-v1` refuses it;
  - with the reasoning parser, the thinking goes to a separate field that the client never shows or replays.
  - The bootstrap diagnostic `cloud-qwen-contract-once` (MODELS-QWEN-CONTRACT-001, `rose_cloud/README.md`) is the
    isolated proof for exactly these proposed flags. Until the original Mac runs it at a merged payload, they stay
    unproven.

**`--qwen-settings nothink`** (MODELS-QWEN-SERVE-NOTHINK-001): the proposed settings plus
`--default-chat-template-kwargs '{"enable_thinking": false}'`, asserted equal to
`rose_cloud.qwen_gpu.CONTRACT_NOTHINK_SERVER_ARGS`.
- **Evidence:** those flags passed the isolated `cloud-qwen-contract-nothink-once` diagnostic once (PR368 payload,
  original Mac, 03:38:46 UTC 2026-11-02). Replies took 2.7–8.5 s on the four fixed cases, against 29.5–40.1 s with
  thinking on (PR360). One run per case; variance and wider quality are not shown.
- **Labels:** the manifest records `qwenSettings: "nothink"`, `notRuntimeProven` and an `evidence` line saying it is
  not proven as the persistent service. The launcher's header lists the flags beyond the proven set.
- **Payloads:** the Muse default payload is unchanged. The `proven` and `proposed` Qwen payloads differ only in the
  `lib/qwen-fp8.json` they ship (which now holds this tier) and its manifest hash.
- **How the service differs from the diagnostic:**
  - the service runs `--network host` bound to `127.0.0.1:8000` behind the Nginx edge (TLS, service-auth-v1, 6
    requests/min, one in flight, 120 s);
  - its container has no `--read-only`, `--cap-drop`, `no-new-privileges`, CPU or memory limit, and no tmpfs: caches
    go to the container's writable layer and are removed with it;
  - it runs as the systemd model slot, with a verified start, restart on failure, the health timer and the daily
    full re-hash;
  - the same image ID, `--gpus all`, read-only staged weights, offline environment and 4096 MiB shm as the diagnostic.

  None of that is runtime-proven for Qwen.
- **Activating it is a separate decision:** a normal boot with such a payload serves Qwen in place of Muse and exposes
  it through the edge to enrolled devices. It is the owner's call, run by the original Mac alone. It needs no new
  credential, firewall or IAM change in source.

**Separate owner dependency (Product, `dev.pixel.models`):**
- the app compiles `ModelsService.MODEL = "muse-glimmer"` (there are no settings since 0.2) and fails any reply from
  another model with `model_identity_mismatch` (tested);
- its default system prompt is Muse's `Reasoning strength: low`.

So the app reaches Qwen only after a Product change of that constant (and, preferably, of the prompt line). The
server never answers as `muse-glimmer`.

**Operator implications** (runtime only by the original Mac operator; nothing here is activated):
- a normal boot (no diagnostic id) with a `--model qwen` payload starts Qwen instead of Muse. That lifts the Muse hold
  in favour of Qwen, which is an owner decision. Both models do not fit on the two A100s;
- the edge, auth and TLS are unchanged, with no new port, firewall rule, IAM or credential;
- Qwen loads in about 350–465 s (measured in the diagnostics) before health reports ready;
- a diagnostic boot still holds the model exactly as today.

**Not shown:** the proposed settings, the app's real prompts and envelopes on Qwen, the latency through the edge, and
PR288's voice/conductor (not part of this profile).

## 3. What the VM does on every boot

These stages are rerun-safe and each command has a time bound.

| Stage | What it does |
| --- | --- |
| `disk` | formats the data disk only if it is provably blank (no signature, no wipefs entry, zero first MiB, at least 200 GB), then mounts it |
| `packages` | the pinned Ubuntu NVIDIA 580.178.04 server packages, docker.io, nginx, certbot, cryptography and qrencode, and NVIDIA's container toolkit 1.20.1-1 (repository key fingerprint `C95B321B…F796ECB0`), then holds them |
| `driver` | `nvidia-smi` must show 580.178.04 on 2 GPUs. Otherwise it **reboots once** for the kernel module, then fails |
| `container` | the NVIDIA runtime for Docker |
| `image` | pulls the pinned image digest and requires the pinned image ID |
| `nginx` | checks the binary's sha256 and disables the distro unit |
| `service` | installs the units and starts vLLM, health and verification. The first start downloads the model once and records the receipt |
| `diagnostic` | only when requested (below); otherwise `not requested` |
| `auth` | initializes the server secret once; starts the auth service when roots are present |
| `tls` | prepare, issue, install; see §4. Nothing listens on :80 until metadata `rose-models-acme=agree-tos-no-email` is set **and** `models.a-list.com` resolves to this VM |
| `edge` | starts Nginx (:443 and challenge-only :80); a lineage issued standalone is switched to the webroot |

**Status:** one nonsecret JSON line, `ROSE-MODELS-BOOTSTRAP {...}`:

```bash
gcloud compute instances get-serial-port-output models-glimmer-01 --project=$P --zone=$Z | grep ROSE-MODELS-BOOTSTRAP
gcloud compute instances get-guest-attributes models-glimmer-01 --project=$P --zone=$Z --query-path=rose-models/
```

- `result` is `ready`, `waiting` (it names the missing operator input), `rebooting` or `failed` (it names the stage
  and category). `bootId` names the boot it describes.
- **`rose-models/health`** (every minute, `health.py`): `bootId`, `checkedAt`, `ready`, `status`, `category`, the
  completion's HTTP status, token count and latency (never its text), `lastOkThisBoot`, restarts against the limit, and
  `modelReceipt`. In `modelReceipt`, `present` is only the file and `recordedAt` its first-load hashing;
  `startCheck` (blob ids and sizes, this boot; null until a model start under this payload) and `fullCheck` (last
  content rehash, daily) are separate results with their own times. `service` (the unit's state, result, main exit
  code, restarts), `startup` (start-model's last phase: `verifying`, `refused_storage`, `launching_offline` or
  `launching_online`, with the verify exit and whether the data disk is mounted and the image present) and
  `container` (docker State: status, exit code, OOM, start time) separate a refused start, a failed container and a
  running container that does not answer. `containerRun` is the startup guard's result for this attempt
  (`guard_refused` with failure codes, or `guard_passed`); the guard then execs vLLM, whose output goes only to the
  container log. `weightsCache` compares the Hugging Face cache's complete and partial blob bytes with the pinned
  repository size. Fresh means this boot's `bootId` and `checkedAt` within 3 minutes. A publication or query failure
  never changes recovery.
- **`rose-models/renewal`** (after the edge starts, then every 6 h, `renewal_status.py`): `certbotTimer` (enabled,
  active, `nextRun`, `schedule`, and `certbot.service`'s last result), `renewalConfig` (webroot, deploy hook, single account), `certificate` (`notAfter`, `daysLeft`,
  installed chain equals the lineage) and `edgeAcme`, a fresh challenge through the RUNNING edge's :80 (200, all else
  404) from its verified pid. `renewalReady` requires all of them.
- **Rerun** (for example after supplying an input) by resetting the VM or with `gcloud compute instances add-metadata`
  followed by a reboot. Completed stages are skipped after verification.

**Permissions this needs:**
- **Create the instance:** create the instance with metadata, plus the IP and firewall from step 1.
- **Read the status:** `compute.instances.getSerialPortOutput` or `getGuestAttributes`.
- **Authorize ACME:** set `rose-models-acme`. Setting it is the persistent-certificate authorization: it creates a
  no-email Let's Encrypt account on the VM.

## 4. TLS and auth

The bootstrap covers both.

**TLS stage, in order:**
1. **Installed certificate:** done.
2. **Issued-but-uninstalled lineage:** a lineage for `models.a-list.com` that was issued earlier is installed with
   `tls-deploy`. It is never reissued, so its account and key identity are kept. This needs no ACME proof, ACK or
   :80. The same install runs right after an issuance whose deploy hook failed (certbot still exits 0); a failed
   install reports `tls-deploy`'s exit code and result.
3. **Prepare:** start the ACME-only Nginx (`edge/acme-only.conf`, :80) and prove all of the following:
   - `nginx -t` passes;
   - the pid file names a running process of the pinned binary, loaded with that config, that holds the :80
     listener. An ACME-only Nginx left by an earlier attempt is replaced; a foreign pid or listener is refused, never
     killed. The start runs with all output discarded and reports only `exit`, `signal`, `deadline` or `not_started`;
   - a synthetic challenge returns 200 with its exact content;
   - `/`, `/v1/models`, `/v1/chat/completions` and `/v1/auth/*` return 404.
4. **Issue:** `certbot certonly --webroot`. It reuses the single existing ACME account (`--account`); with none, it
   registers the approved no-email account once. The deploy hook installs the result. The ACME-only Nginx is then
   stopped on success and on failure (quit, then TERM, waiting until it has exited), and the edge takes over :80
   and :443.
5. **Refusals, all without issuing:**
   - more than one account (`acme_account_ambiguous`);
   - a second or renamed lineage (`certificate_lineage_ambiguous`);
   - a lineage without its key (`certificate_lineage_incomplete`);
   - any failed proof (`acme_http_*`).
   A failed issuance reports the account count, lineages and ACME probe codes. It never reports keys.

**Operator (Mac).** Only the existing channels are used: instance metadata and one reset. No SSH, no new guest
channel.
1. With **:80 still closed**, confirm `rose-models-acme=agree-tos-no-email` and DNS, replace the payload metadata, and
   reset once.
2. Read the proof published **before** any issuance, from the serial line `ROSE-MODELS-ACME-PROOF {...}` or the guest
   attribute `rose-models/acme-proof`. It must show:
   - this payload's sha256 and the boot ID;
   - the `acme-only.conf` sha256;
   - `pidVerified` with `exe`, `config` and `listener` all true;
   - challenge 200;
   - 404 for `/`, inference and auth.
3. Restore the already-approved ACME :80 ingress.
4. Acknowledge exactly that payload and boot. The helper then issues once; the ACK is not a permission gate.

```bash
gcloud compute instances add-metadata models-glimmer-01 --zone us-central1-f --project protocols-465723 \
  --metadata=rose-models-acme-ack=<payloadSha256>:<bootId>
```

**ACK rules:**
- The wait is bounded to 30 minutes. A timeout issues nothing and stops the ACME-only Nginx; reset again to retry.
- A stale boot or another payload never matches.
- Issuance is attempted at most once per acknowledged boot.

**Impact of that one reset:** vLLM reloads from the verified data disk, a few minutes without inference. The auth
secret, device registry, image and packages are kept and verified, not regenerated.
- `certbot.timer` renews the certificate.
- `tls-deploy` checks the new chain (`render_proxy.py check-tls`), switches atomically, runs `nginx -t`, and then
  reloads. On any failure it rolls back to the previous chain.
- The attestation-status timer refreshes Google's revocation list; enrollment refuses while it is missing or stale.

**Edge check:** with no token, `/v1/models` returns 401. With a token from the enrollment flow, it returns 200.

**Renewal:** restore the approved :80 ingress only when the current `rose-models/renewal` shows `renewalReady: true`
for the current `bootId`. Nothing is issued until certbot renews within 30 days of `notAfter`.

**Add a phone (admin, on the VM through Infra's administrative route; `qrencode` is preinstalled):**

```bash
python3 /etc/rose-models/lib/service/auth_service.py invite --label nathan-pixel --uri-only | qrencode -t ansiutf8
```

Scan the code in the Models app. It is single use and valid for 24 h.
- `auth_service.py invites` lists codes (never the codes themselves).
- `auth_service.py revoke nathan-pixel` revokes that label's devices and its unused codes.

**Offline CLI check (diagnostic, explicit, once per id).** Set existing metadata
`rose-models-diagnostic=offline-validate:<payloadTarSha256>:<id>` (id: lower-case letters, digits, `-`) and reset.
That boot holds the model (`/run/rose-models/model-hold`, which `muse-glimmer.service`'s `ExecCondition` honours and
any reboot clears), stops it and waits until its container is gone, then runs `vllm serve` with the deployed arguments
in the pinned image already in the local store: `--pull=never`, `--network none`, no GPU, no mounts,
`HF_HUB_OFFLINE=1`, 120 s, at most 2 MiB of output inspected and never kept. The result, fixed categories only, is
the serial line `ROSE-MODELS-DIAGNOSTIC` and `rose-models/diagnostic`. It shows where the CLI stopped
(`argument_rejected`, `tool_parser_invalid`, `reasoning_parser_invalid`, `model_config_unavailable`,
`device_unavailable`, ...); without network, GPU or weights it cannot show that serving works. HTTPS and auth continue;
inference is off until the next reset without a new id. Another payload's digest, a malformed value or a used id
holds and runs nothing.

**One GPU start (diagnostic, explicit, once per id).** `rose-models-diagnostic=gpu-start-once:<payloadTarSha256>:<id>`
uses the same hold, stop, positive absence and read-only prerequisites. `verify_model check` picks the payload's
`launch-offline` (0) or `launch-online` (3: production's Hugging Face first load over its network and cache); any other
status, an unmounted data disk, a busy :8000 or an unreadable launcher is refused. That exact `docker run`, plus
`--pull=never`, runs once: at most 900 s, 2 MiB inspected for fixed categories, stopped as soon as :8000 listens. The
container is always removed and its absence verified; inference stays held for the boot. It shows how far a start gets,
not why. The same bounded bytes also yield fixed tokens only: `guard` (`passed`/`refused`), `tracebacks`, and for the
last and the first classified traceback `exceptionType`/`firstExceptionType`, `…Location` (innermost frame's component)
and `…Process`, each from a fixed allowlist in `startup_guard.py`, anything else `other`; never a message, path, line
or stack. vLLM's per-line log header (upstream `NewLineFormatter`) is skipped. They narrow where a start stopped; the
first exception is only the earliest classified (it may be the API server's wrapper), not a proven root cause.

**One loader probe (diagnostic, explicit, once per id).** `rose-models-diagnostic=hf-probe-once:<payloadTarSha256>:<id>`
uses the same hold, stop, positive absence and read-only prerequisites, and refuses an unmounted data disk
(`data_not_mounted`). It runs `lib/service/hf_probe.py` once in the pinned image already in the local store:
`--pull=never`, `--runtime=runc`, `--network none`, no GPU, only `/mnt/models-data/hf` mounted read-only at the serve
cache path, `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1`, no credentials, at most 120 s; the container is always removed
and its absence verified, and inference stays held for the boot. AutoConfig, AutoTokenizer, AutoProcessor and
GenerationConfig are each tried once at the pinned revision with `local_files_only` (and `trust_remote_code=False`).
`probe` holds fixed tokens only, re-checked by bootstrap: per loader `ok`/`value_error`/`os_error`/`key_error`/
`type_error`/`attribute_error`/`import_error`/`runtime_error`/`not_implemented`/`other`, `firstFailure` (fixed order)
and `allOk`, `transformersVersion` (a plain version or `other`), `registeredTransformers`/`registeredVllm` (is
`muse_glimmer` a known model type; `null` if not determinable) and, per small config file, `present`/`absent`/
`not_cached`/`unknown`; `null` on a timeout or without a valid line. It shows which loader fails, not why. Library
names were checked against upstream source and released transformers 4.57.1/5.22.1, never against the pinned image.

## 5. Operations

| Situation | What happens | Operator |
| --- | --- | --- |
| vLLM exits | systemd restarts it after 30 s, at most 5 times per hour | Status shows `restart_limit` → inspect the journal |
| Loaded but not answering | after 3 failed probes, the health timer restarts vLLM, at most 3 times per hour | `failed_needs_operator` in `/run/rose-models/health.json` |
| VM or host event | GCE restarts the VM (`--restart-on-failure`); the units are enabled | Check health after boot |
| Bad renewal | `tls-deploy` keeps the previous chain | Fix, then rerun the hook |
| Credential abuse | `auth_service.py revoke KEY_ID`, `close-enrollment` or `rotate-secret` | Rotation keeps the previous secret, so live tokens survive one rotation |
| Model files changed | `start-model` exits 4 and is not restarted; the daily full check fails | Restore the disk, or remove the receipt and cache to download again |

**Rollback:** `systemctl stop models-edge` takes the endpoint offline. The disk and VM are kept.
