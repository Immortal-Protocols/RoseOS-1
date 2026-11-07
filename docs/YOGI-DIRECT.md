# Yogi direct operator route

Source: `scripts/yogi_direct.py`, contract in `os/ota/yogi.json` `builder.direct`, tests in
`scripts/tests/test_yogi_direct.py`. **Status: `unbound`.** Nothing here has run. The route stays refused until every
dependency below is recorded from an operator receipt with provenance, and its first use is separately approved.
Replacement-builder recovery (unit mode, host-key rotation, temporary-access cleanup, and STOP) is a different tool:
[YOGI-REPLACEMENT-RECOVER.md](YOGI-REPLACEMENT-RECOVER.md). It does not satisfy this route's host-key pin.

## Why a separate route

GitHub self-hosted scheduling routes a job to any eligible runner carrying its labels. Organization runner eligibility
for this repository is unknown: the org runner-group read was denied (HTTP 403, 2026-11-06T01:24:17Z), and the org-admin
readback route was rejected. Build, key-bootstrap and signing jobs therefore cannot be shown to land only on the Yogi
builder, and they stay refused on the runner route (`runner.organizationEligibility`, `builder.status`), unchanged.

The direct route removes GitHub scheduling from the path instead of weakening that check. The operator connects to the
exact replacement VM, and SSH authenticates the VM against a committed host-key pin. No runner, label or workflow takes
part, so runner eligibility is not a question this route has to answer. It does not exempt the runner route: those
gates, the readiness-only exception and `builder.status` are untouched. Instead, this route requires the runner to be
**quiesced** on every connection and before every step: no `Runner.Listener` or `Runner.Worker` process, and the runner
unit `inactive` and `disabled`. So the runner cannot accept other jobs while a direct operation runs.

## Scope

| Stage | Direct route |
| --- | --- |
| `check` | identity and quiesced runner, then the existing readiness checks (`yogi_ota.readiness` with the direct identity) |
| `compile` | bootstrap A up to an unsigned compile: preflight, allocate (records the operation id), prepare from the retained lab tree, integrate the hash-verified Release app inputs, compile |
| `key-bootstrap`, `sign` | **refused**: they need a signer credential on the guest. GitHub OIDC is never forwarded, and no direct credential path is decided |
| `install-image`, `stage`, `ota` | **refused**: they follow signing |
| `publish` | **refused**: not part of this route; `publicationEnabled` stays false |

Signing-profile, signing-window, app-input, publication and phone-install requirements all stay where they are.
`compile` still needs Release's committed app-inputs record.

## Threat model

| Threat | Control | Test |
| --- | --- | --- |
| Wrong host answers SSH (address reuse, DNS, MITM) | Only the committed ed25519 pin is trusted: `StrictHostKeyChecking=yes`, a one-line known_hosts under `HostKeyAlias=yogi-<numeric id>`, no global known_hosts, `-F /dev/null`, no `UpdateHostKeys`. Never first use or keyscan | `SshTest` |
| Pin taken from an untrusted source | `observedVia` must be serial-console output or guest-attribute host keys, both read through the authenticated Compute API for the numeric instance. Never first use, keyscan or a guest name | `ContractTest` |
| Replacement and original VM share a host key (the replacement disk was **restored**) | The pin needs `distinctFromOriginal: true`. The original VM (numeric `8812446529823929967`) must be TERMINATED before every operation and connection | `ContractTest`, `ControlTest`, `SshTest` |
| Original zone-a VM, foreign builder or wrong numeric ID | `describe` checks project, zone, name, numeric ID and selfLink on every call. After authentication the guest's own metadata tuple must match. `yogi_ota.config()` still refuses legacy and foreign tuples | `ControlTest`, `SshTest` |
| Runner accepts an unrelated job during the operation | Quiesced check on every connection and before every step | `SshTest`, `AgentTest` |
| Unreviewed or modified source executed | `plan` needs a clean checkout at a commit already on `origin/main`. The `git archive` hash goes in the manifest; the guest verifies the bundle, manifest and archive hashes before unpacking or executing, and runs the agent from that archive | `PlanTest`, `InstallScriptTest`, `AgentTest` |
| Substituted app inputs | The zip must equal the committed record's artifact digest; it is hashed again on the guest and admitted by Release's verifier inside `integrate` | `PlanTest`, `UnzipTest` |
| Replay, stale or duplicate execution | Random 128-bit operation id. Exclusive `mkdir` per operation on the guest; a replay or interrupted earlier delivery refuses. `launched.json` is written before sending, so nothing is re-sent. 12-hour expiry for planning, starting, sending and each step. A direct lock serializes direct operations; the existing build lock serializes steps | `InstallScriptTest`, `SshTest`, `AgentTest` |
| Fabricated GitHub identity or leaked job/OIDC credentials | Every entry point, the guest agent included, refuses inside a GitHub Actions job (`GITHUB_ACTIONS`, `ACTIONS_*`, `GITHUB_TOKEN`). The operation records a declared operator, never a run id. ssh runs with only `PATH` and a temporary `HOME`, no agent or X11 forwarding and no SendEnv. Nothing secret is sent | `ManualOnlyTest`, `SshTest` |
| Operator's SSH key or cloud credential reaches the guest | The key file is passed with `-i` and `IdentitiesOnly`, with no agent forwarding. gcloud runs only on the operator machine | `SshTest` |
| IAP tunnel loses, or over-receives, the operator's gcloud setup | ssh runs `ProxyCommand` with its own minimal environment, so the tunnel's gcloud is handed only `CLOUDSDK_CONFIG` (the operator's config directory, as a path) and an explicit `CLOUDSDK_PYTHON`, through `env(1)` inside the ProxyCommand. ssh's environment stays `PATH` plus a temporary `HOME`. ADC, token-file, account and proxy variables are not passed. `%` is escaped for ssh, and the location must be an absolute path | `SshTest` |
| Lost connection | The agent runs detached (`setsid`, redirected I/O) and persists `status.json` after every step. `status` reports `lost` when the recorded process is gone. Nothing resumes: a new operation is planned | `StatusCancelCleanupTest` |
| Cancellation | `cancel` sends SIGTERM to the agent's process group, only when it is live and running. The agent records `cancelled` | `StatusCancelCleanupTest`, `AgentTest` |
| Unsafe or unowned cleanup | STOP only with this operation's own record (exact tuple, operation id, source, manifest hash) that started the VM from TERMINATED. Never while the agent runs. An unverifiable state needs an explicit `--abandon`, which is recorded. A VM found RUNNING is never adopted | `ControlTest`, `StatusCancelCleanupTest` |

Residual risks, stated rather than claimed away:
- A compromised guest root could lie in its metadata answer or start the runner between checks. The host-key pin, the
  control-plane identity and the per-step re-check bound this, but do not remove it; the same guest is already trusted
  to build.
- The guest clock is used for the per-step expiry.
- If `systemd-logind` `KillUserProcesses` is enabled for the SSH user, the agent would be killed at logout. That shows
  as `lost`, never as success.

## Strict possession check

This is not the direct route and it does not record `builder.direct.hostKey`. `guestAuthenticated` stays
false until the strict handshake and the fresh inspect both succeed. `hostKeyPinned` stays false even after
that success: this check does not record a config pin. The host-key input is the bare `gcloud --format=json`
list from `get-guest-attributes --query-path=hostkeys/`, three `{namespace, key, value}` objects. A wrapper
with provenance is not that response. The fresh terminated read at 2026-11-07T15:01:24Z matches the `ce79`
fingerprints. Preflight refuses unless that read is still the instance: fingerprint `pSAPI4eI6q4=`,
only the two base metadata keys, status `TERMINATED`, and instance id `2720796557371812468`. Serial output, trust on first use, and `gcloud compute ssh` are not used.

The inspect recorded no authorized-key line and no temporary account. OS Login stays false and project SSH keys stay
blocked, so there is no existing login to reuse. The check appends one expiring instance `ssh-keys` line for the
existing `pixelbuild` account and leaves every other metadata key and every other `ssh-keys` line in place. One
metadata write uses the current fingerprint and is not retried. The login private key stays a local mode `0600` file
and is not forwarded. The one remote command is the existing read-only quiesced probe: metadata, `/proc`, and
`systemctl is-active`/`is-enabled`. It does not use sudo and does not change the account. After it, the same
fingerprint rule removes only that exact line. A 412 keeps the intended map and is not written again.
Start and stop are submitted with `gcloud --async` and then polled. One describe that is not `RUNNING` is not a start timeout, and a stop is confirmed only when a later describe is `TERMINATED`. The stop budget is 240 seconds. The same removal and STOP run when SSH fails or the start stays unresolved. The stopped read shows nic0 `accessConfigs` `[{name: External NAT, type: ONE_TO_ONE_NAT, networkTier: PREMIUM}]` with `natIP` absent. That config is kept for outbound egress. It is not removed and not treated as an allocated address. `natIP` may appear while `RUNNING` and is not an identity change. Inbound is a separate check: `gcloud compute instances network-interfaces get-effective-firewalls` for `nic0` (`compute.instances.getEffectiveFirewalls`). The SDK 592 response is an object whose only key is `firewalls`. Preflight, and the describe taken before the login line is inserted, require the tag set `roseos-yogi-b-20261105` and `roseos-yogi-builder`, nic0 on `default` / `us-west2`, the single External NAT, IAP TCP/22 from `35.235.240.0/20` at priority 900, and deny-all from `0.0.0.0/0` at priority 901 on that tag. Lower-priority IPv4 allows, including the two priority-1000 connector rules and `default-allow-ssh` at 65534, are shadowed by that deny. A higher-priority or tied rule, an IPv6 source, a nonempty or unexpected policy field, a tag or nic change, or an unreadable firewall read fails closed before start. SSH remains the IAP tunnel, not the NAT address. No firewall, route, or NAT resource is created or deleted. The outcome records the operation status, target id, instance status, and the describe result (`describe-exit` plus the process exit code, `describe-not-json`, `describe-identity`, `describe-metadata`, `describe-fingerprint`, `describe-external-address`, `describe-external-nat`, or `describe-external-ipv6`). It does not record the response body, the address, or an access token. A stable describe refusal is not retried for the whole start budget. After a removal
that was accepted, the guest agent is given one bounded refresh, the VM is stopped, and a fresh inspect boot
must read both `/root/.ssh/authorized_keys` and `/home/pixelbuild/.ssh/authorized_keys`. The receipt has to
name this operation's exact `roseos-yogi-possess-<id>` comment, `authPathsRead` 2, `accessCleanup` ok, and
`possessMarkerLines` 0. A missing count is not zero. An unreadable path is not proof. Metadata removal alone
is not that proof. The runner is not enabled. IAP only selects the instance. OpenSSH checks the ed25519 host
key against the saved API blob. `hostKeyPinned` stays false.

`handshake-plan` writes that plan and does not call GCP or SSH. `handshake --execute` is the whole operator run, including the fresh inspect. There is no second command. After the probe it removes only that line, waits one bounded guest-agent refresh, stops the VM, and then plans a new inspect with a new operation id and nonce. That inspect is `yogi_replacement_recover.py plan`, `preflight --execute`, and `apply --execute` in the same process: the apply boots the guest, reads the receipt, removes the startup script, and stops again. It does not start that inspect when the metadata removal was not accepted, the refresh failed, or the stop is unconfirmed. Those outcomes leave cleanup instructions and leave `guestAuthenticated` false. The guest agent installs the metadata text after the first colon and uses `expireOn` only to drop an already-expired line; it does not delete the JSON comment. The inspect counts a line that still contains that comment or the login public blob, including a rebuilt `google-ssh` line that dropped the comment. `possessMarkerLines` 0 means neither remained. A missing count is not zero.

```sh
env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT \
  python3 -B yogi_possession.py handshake-plan \
  --hostkeys HOSTKEYS.json --client-public CLIENT.pub --out ~/yogi-ops/possess-LABEL
env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT \
  python3 -B yogi_possession.py handshake --execute \
  --hostkeys HOSTKEYS.json --client-public CLIENT.pub --identity ~/yogi-ops/possess-LABEL/id_ed25519 \
  --out ~/yogi-ops/possess-LABEL
```

## Dependencies (all unverified today, all fail-closed)

| `builder.direct` field | What must be recorded | Owner |
| --- | --- | --- |
| `hostKey` | Replacement ed25519 public host key and fingerprint, bound to instance `2720796557371812468` through serial-console output or guest-attribute host keys, `distinctFromOriginal: true` (rotated after the restore, or compared with the original's), provenance | Operator via Chief; a host-key rotation is a separate guest change needing approval |
| `route` | `external-ip` or `iap`, whichever the network and firewall actually allow. Not assumed. For `iap`, the operator's gcloud must already be authenticated through its configuration directory (`CLOUDSDK_CONFIG`, or `~/.config/gcloud` under the operator's real `HOME`), with any proxy set in that configuration. Environment-only auth (ADC file, access-token file, account override) is not passed to the tunnel and would fail closed | Operator / Dev Infra |
| `sshUser` | The guest account the operator logs in as | Operator |
| `operatorAccess` | Readback that the operator's own identity can describe/start/stop the exact VM, describe the original VM, log in over the chosen route, and write `/srv/rose-yogi` (direct root, lock, attempts) | Operator via Chief; any IAM, firewall or SSH change needs its own approval |
| `runnerService` | The runner's systemd unit name (it must stay inactive and disabled). The last guest observation before the VM STOP was active/running but disabled (Chief receipt correction, 2026-11-06). That is historical, not the stopped VM's state, and that shape is refused: an operation runs only once the runner is observed inactive with no process. This source does not stop or alter the runner | Operator via Chief |
| `status: ready` | Set only after the rows above, with approval | Nathan via Chief |

The existing controller service account's get/start/stop grant does not authorize remote commands and is not used. If
the operator's own access proves insufficient, the route stays refused; no grant is assumed.

`compile` additionally needs Release's `os/app-inputs/frankel-2026110403.json` on main, and the operator's own copy of
that artifact zip, matching its digest.

## Operator runbook (only after the dependencies are recorded and use is approved)

On the operator's Mac, outside any GitHub job, in a clean checkout at the reviewed commit:

```sh
git fetch origin main && git checkout --detach <reviewed-sha>
python3 scripts/yogi_direct.py describe                        # every stage's blockers; must be empty for the stage
python3 scripts/yogi_direct.py plan --stage check --source <reviewed-sha> --operator "<name>" --out ~/yogi-ops/<label>
python3 scripts/yogi_direct.py start  --op ~/yogi-ops/<label>   # record fsynced, then START from TERMINATED only
python3 scripts/yogi_direct.py run    --op ~/yogi-ops/<label> --identity ~/.ssh/<key>
python3 scripts/yogi_direct.py status --op ~/yogi-ops/<label> --identity ~/.ssh/<key>   # repeat until terminal
python3 scripts/yogi_direct.py cleanup --op ~/yogi-ops/<label> --identity ~/.ssh/<key>  # STOP, receipt in cleanup.json
```

For `compile`, add `--inputs <artifact.zip>` to `plan`; the zip must match the committed record's digest. To stop
early, run `cancel`, wait for `cancelled`, then run `cleanup`. If the VM cannot be reached, `cleanup --abandon` stops it
anyway and records that the remote state was unverified. Keep the operation directory: it holds the manifest, the
ownership record, `launched.json` and `cleanup.json`. The guest keeps its copy under
`/srv/rose-yogi/direct/<operation-id>/`.

Never reuse an operation directory or id, edit its files, or run any entry point inside a GitHub Actions job.

## Later automation

A hosted controller is not part of this change. The current 660-minute build allowance is longer than a GitHub-hosted
job's six-hour limit, so a later automated design cannot hold one SSH session for a build. The detached agent and its
persisted status are the shape such a design would poll.
