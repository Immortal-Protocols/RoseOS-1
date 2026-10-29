# Rose Models Android: inference prerequisites (MODELS-ANDROID-CORE-001)

This is a finite prerequisite note, not a request. Nothing here was created, allocated, deployed or
accessed. Prices are historical figures relayed by the Models Manager, not fresh quotes.

## What the phone needs from the server

- **Endpoint:** one OpenAI-compatible `POST /v1/chat/completions`, non-streaming, model id `muse-glimmer`.
- **Model and runtime:** the same pins as `muse-glimmer-30b.json`:
  - `meta-models/Muse-Glimmer-30B@cec94fcfd6512f2cfe708138c32533b346ea4f69`, BF16, for model and tokenizer;
  - image `vllm/vllm-openai@sha256:dbb04f5c0c0a6356779cf3175381a0406584df01b0ad2f88eaadb46f7f2da08c`;
  - 8,192 context, `max_tokens` 512, one sequence.
- **Request body:** `local-agent-v1` builds the whole body (`need_inference.body`). The server only generates.
  It runs no tools, keeps no phone task state and does no Pixel Voice orchestration.

## Structured-output conformance: unproved

- **Envelope:** the agent asks for one closed JSON envelope in ordinary `message.content`, and parses it strictly. A malformed answer fails the turn with zero execution, so non-conformance is safe but unusable.
- **Not established:**
  - **Envelope conformance:** whether Muse Glimmer at the pinned revision emits the envelope reliably. Never measured. The H100 run proved plain text chat only, not the envelope or tools.
  - **Constrained decoding:** whether the pinned vLLM image's constrained JSON output (`response_format` with a JSON schema) works with this model and the `muse_glimmer` reasoning parser. Not verified: the vLLM source hold stands and there has been no live run. The first adapter therefore does not depend on it.
- **Reasoning:** with the reasoning parser on, thinking arrives in `message.reasoning`. The agent ignores it, so only `content` must hold the envelope.
- **Measurement for the first admitted window.** The receipt owner is **unassigned**: the QA session is held, and no conformance receipt or live run is assigned to it. Until an admitted route exists, engineers own only their fresh source tests (for the agent: `rose-chat-core/tests/agent.rs`, fixtures only). Proposed method:
  - 20 fixed prompts: 10 plain chat, 8 "open Calculator" variants, 2 not-allowed apps.
  - Record the parse result of every reply.
  - Proposed bar: at least 19 of 20 parse; every `open_app` names `calculator` only when asked; the not-allowed cases produce no `open_app`.
  - If the bar fails, the next step is a separately scoped constrained-decoding trial, not prose extraction.

## Executable preparation (MODELS-HTTPS-SOURCE-001)

- **HTTPS edge:** `https/` holds the renderer, Nginx and Caddy configs, and real-Nginx synthetic tests, plus the operator sequence.
- **Conformance:** `conformance/` holds the 20-prompt harness built on the real agent, with fixtures and the bounded live command.

Neither has run against a live endpoint. Caddy was not obtainable from this session, so its configuration is unvalidated by a real binary; see `https/README.md`.

## Phone-reachable HTTPS route

**Required properties:**
- the phone must reach an **authenticated HTTPS** origin whose certificate Android trusts by default (no user CA, no trust-all, no raw model port). It need not be public: an already-authorized private route would also satisfy this, if one exists. None is known to Core today;
- an inference-only credential scoped to this endpoint, revocable, and delivered to the device out of band, never embedded in source or the APK;
- only `/v1/chat/completions` and `/v1/models` exposed;
- rate limiting;
- logs that keep request metadata only, not prompts.

**One candidate shape: a public IP, DNS name and TLS proxy.** It is not the only permissible architecture, and it is not an agent server:
- vLLM stays bound to `127.0.0.1:8000`, exactly as in `render_local.py`.
- A TLS reverse proxy on the same VM terminates HTTPS on :443, checks the bearer token and forwards only those two paths.

**Missing operations for that candidate.** Nothing is done. Each needs the owner's exact approval. A private route would replace rows 2–5 with its own operations:

| # | Operation | Creates |
| --- | --- | --- |
| 1 | Allocate the one-H100 VM for the window (as in the retained 23:01 run) | GPU VM, boot disk; Local SSD is bundled |
| 2 | Give the VM a reachable address: an ephemeral external IP, or a reserved one (`compute.addresses.create`) | Public address |
| 3 | Firewall rule allowing tcp:443 to that VM only (`compute.firewalls.create`) | Ingress rule |
| 4 | A DNS name pointing at the address. Which domain and zone is unknown | DNS record |
| 5 | Issue a publicly trusted certificate for that name (for example ACME, which needs 80 or 443 reachable) | Certificate |
| 6 | Generate one inference bearer token and install it in the proxy, plus a copy for the test phone | **New credential** |
| 7 | Install and start the proxy and vLLM using `render_local.py` | Guest software |
| 8 | Teardown: revoke the token, delete the VM and boot disk, release the address, delete the firewall rule and DNS record | — |

None of these is available to Models Core. Operations 2–6 are new network, credential and security scope.
Existing GCP project facts (protocols-465723, us-central1) come from Chief's discovery and do not grant them.

## Cost envelope (draft only: not an all-in quote and not an approval request)

| Item | Basis | Two-hour figure |
| --- | --- | --- |
| a3-highgpu-1g, one H100, Spot | Last observed rate $6.587557589/h (manager-relayed, historical) | ≈ $13.18 |
| Boot disk, external IP, egress, DNS, certificate, any proxy or route cost | **Not quoted.** Expected to be small for 2 h, but neither measured nor priced | Unknown |
| **Tentative reserve** | Plan envelope | **$20** (not an approved allocation) |

- **Spot interruption:** the VM may be preempted at any time. The app then shows a connect or timeout failure (`inference_failed`), never a fake reply.
- **Persistence:** there is no promise of a persistent service.
- **What makes this incomplete:** the machine figure is a historical relayed rate, not a fresh quote, and every other line is unpriced. An all-in request needs a fresh price for each item (machine, disk, address, egress, DNS, certificate, chosen route), its source and date, Spot versus on-demand, and the teardown cost before any approval is asked.

## Out of scope here

The A100 hold is unchanged and not a candidate: one 40 GB card cannot hold the BF16 weights, and CPU offload is unvalidated.
No model or quantization change. No access to the held vLLM source route.
