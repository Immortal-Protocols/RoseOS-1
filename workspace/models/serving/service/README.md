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

## 1. Resources (Infra; each line needs its stated authority)

```bash
P=protocols-465723; R=us-central1; Z=us-central1-a   # if capacity is refused: us-central1-c, -b, -f
gcloud compute addresses create models-glimmer-ip --project=$P --region=$R
gcloud compute disks create models-glimmer-data --project=$P --zone=$Z --type=pd-ssd --size=500GB
gcloud compute instances create models-glimmer-01 --project=$P --zone=$Z --machine-type=a2-highgpu-2g \
  --provisioning-model=STANDARD --maintenance-policy=TERMINATE --restart-on-failure \
  --image-project=ubuntu-os-cloud --image-family=ubuntu-2404-lts-amd64 --boot-disk-size=150GB --boot-disk-type=pd-balanced \
  --disk=name=models-glimmer-data,device-name=models-data,mode=rw,auto-delete=no \
  --address=models-glimmer-ip --tags=models-https --no-service-account --no-scopes
gcloud compute firewall-rules create models-https --project=$P --network=default --direction=INGRESS \
  --action=ALLOW --rules=tcp:443,tcp:80 --target-tags=models-https --source-ranges=0.0.0.0/0
```

- **Port 80** serves only `/.well-known/acme-challenge/` and returns 404 for everything else. It exists for automatic
  renewal.
- **SSH** goes through Infra's existing administrative route.
- **DNS:** set the A record `models.a-list.com` to the reserved address (Infra's DNS custody).

## 2. Host setup (on the VM, as root)

```bash
mkfs.ext4 -L models-data /dev/disk/by-id/google-models-data   # first boot only
echo 'LABEL=models-data /mnt/models-data ext4 defaults,nofail 0 2' >> /etc/fstab && mkdir -p /mnt/models-data && mount -a
apt-get update && apt-get install -y nvidia-driver-580-server docker.io nginx=1.24.0-2ubuntu7.19 certbot python3-cryptography
systemctl disable --now nginx   # the distro unit would hold :80; models-edge runs the same binary
# NVIDIA container toolkit from NVIDIA's apt repository, then:
nvidia-ctk runtime configure --runtime=docker && systemctl restart docker && reboot
docker pull vllm/vllm-openai@sha256:dbb04f5c0c0a6356779cf3175381a0406584df01b0ad2f88eaadb46f7f2da08c
docker image inspect --format '{{.Id}}' vllm/vllm-openai@sha256:dbb04f5c0c0a6356779cf3175381a0406584df01b0ad2f88eaadb46f7f2da08c
#   must print sha256:e90b8320f680a6a7b8daff87ad08cdf063a68d869880e662fd6d2cebfef689dc
```

**Check the nginx binary.** Confirm that the installed `/usr/sbin/nginx` has sha256 `5bfff15e…756eed`; see
`../https/README.md`.

## 3. Install the service (repository checkout at a merged commit)

```bash
python3 workspace/models/serving/service/render_service.py --out /etc/rose-models   # units, scripts, manifest with hashes
install -m 644 /etc/rose-models/systemd/* /etc/systemd/system/ && systemctl daemon-reload
systemctl enable --now muse-glimmer.service models-health.timer models-verify-full.timer   # first start downloads once
cat /run/rose-models/health.json   # "starting", then "ready"
```

The first verified load writes a model receipt to `/mnt/models-data/receipt.json`. Every later start verifies the
cache against that receipt and refuses on any mismatch.

**Readiness** means a real `chat/completions` call to the loaded model returns content from `muse-glimmer`. The model
list alone does not count.

## 4. TLS and auth (needs Chief's exact authority first)

**Persistent certificate.** Create a new persistent ACME account (no email); this is a separate decision from the
temporary 24 h account. Then:

```bash
install -d -m 755 /var/lib/rose-models/acme      # nginx workers read the webroot unprivileged
install -d -m 700 /run/rose-edge                 # lets the hook's `nginx -t` run before the edge starts
certbot certonly --standalone -d models.a-list.com --register-unsafely-without-email --agree-tos \
  --deploy-hook /etc/rose-models/bin/tls-deploy   # edge not running yet: the hook checks and installs, no reload
# ...start auth and the edge (below), then switch renewals to the edge's webroot:
certbot reconfigure --cert-name models.a-list.com --webroot -w /var/lib/rose-models/acme
```

- `certbot.timer` renews the certificate.
- `tls-deploy` checks the new chain (`render_proxy.py check-tls`), switches atomically, runs `nginx -t`, and then
  reloads. On any failure it rolls back to the previous chain.

**Auth.** Follow `../../contracts/service-auth-v1.md`:
- install the Google hardware-attestation root certificates (public files) into `/etc/rose-models/attestation-roots/`
  and record their hashes;
- put the approved RoseOS `verifiedBootKey` (hex) in `/etc/rose-models/self-signed-boot-keys.txt`;
- `systemctl enable --now models-attestation-status.timer`. It refreshes Google's status list; enrollment refuses
  while the list is missing or stale;
- `python3 /etc/rose-models/lib/service/auth_service.py secret-init`;
- `systemctl enable --now models-auth models-edge`.

**Edge check:** with no token, `/v1/models` returns 401. With a token from the enrollment flow, it returns 200.

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
