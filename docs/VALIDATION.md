# Verification record — 2026-10-26

This records the first Rose Agent import and app signing in the new Dev Infra
agent. It does not claim a released or device-tested RoseOS build.

## Source and environment

- Starting RoseOS-1 main: `c85722ffe9b73037cc61b36ffe1a54e8e15278d7`.
- Imported prior art: `pixel-workspace@9f1261dd5aac9227ea4b16d3817f394bffa1cf5c`.
  Per-file original blob IDs are in `scripts/rose-import.json`.
- Correct repository origin verified; empty initial checkout populated without
  overwriting user files. No applicable AGENTS.md existed in either source tree.
- GitHub repository and Notion reads succeeded. Expected platform configuration
  IDs were supplied but cannot be attested from this shell.
- Java 21.0.12.1, Node 24.19.0, Google Cloud CLI 541.0.0 executed.
- Pinned Android SDK archives downloaded and checksum verification passed.
- Locked npm install succeeded using `.tools/npm-cache`. The default home cache
  was unavailable; no package integrity checks were disabled.

## Fresh app evidence

Rose Agent, PiP background controller and Calculator compiled on this Linux
agent. Each signed APK passed `apksigner verify`, with the same full public
certificate SHA-256:

`650d47f91b7f5af3c530fef53fe92018aa4521efba065bcb1d6fc9b699060bfb`

That matches Nathan's supplied `650d47` signer prefix. APK receipts record full
artifact hashes. Rose Agent has the Rose Agent application label and retains
`dev.pixel.voice`, version code 297, min SDK 29, target SDK 35. The integration
build used newly generated loopback TLS assets, so it is a signing/build proof,
not a preconfigured phone connection to a live backend. A user must configure a
verified real backend endpoint before end-to-end phone acceptance.

The first integration build artifacts (before the documentation commit) were:

| Artifact | SHA-256 |
| --- | --- |
| Rose Agent signed APK | `64f77112fda318f6d9e718b9ce91b1526cead4ad79f5db5fc435e1ea33ea7b8c` |
| PiP controller signed APK | `937d63125a1604633e87149886bd191d7b7d49868100819dd2ef1c49ae33a9be` |
| Calculator signed APK | `315f561bd74d5d85f7ac32e206ff9d7f0edf0e938853006fbe6d3acbb546e2bc` |

Builds after this commit may differ; use the accompanying receipt/checksums for
the delivered artifact. Byte-for-byte reproducibility has not been established.
No APK was installed, no live backend was started, and no end-user task was run.

Offline checks: **44 JavaScript tests and 3 Python OS-policy tests passed**.
The import exposed a CommonJS/browser-module boundary in the command-delivery
suite; an explicit local CommonJS package boundary fixed the constructor loading
without changing the browser implementation. The stale generated Android
Manifest.java was excluded from the build stage to avoid a duplicate class.

A restricted candidate policy was generated and verified from the signed
controller, launcher and Calculator at Android 17 SDK 37. It permits only the
explicit Calculator target and does not enable prototype/locked control. It has
not been applied to an OS tree or accepted as the final product allowlist.

## Cloud and signing checks

Service-account type, expected identity/project and private-key parsing passed
without printing values. Nathan authorized activation; OAuth/token acquisition
and active identity checks succeeded. CLI cache is isolated outside Git and
owner-only. Temporary activation input was deleted.

The four explicitly named app keystore/config/password resources were accessible.
Signing used owner-only files outside Git and password-file inputs, with no
passwords on command lines. OS signing secrets have not been retrieved while the
OS build is blocked. Existing signing identities were reused; no production keys
were generated or rotated.

Compute metadata verified `immortal-os-builder`, `us-west2-a`, `n2-standard-64`,
initially `TERMINATED`. Nathan authorized starting it and the start operation
succeeded. Gcloud registered a dedicated SSH public key in instance metadata
while preparing authorized builder access. The SSH private key remains outside
Git. No remote shell command ran successfully, and no builder source, job,
disk content or OS output was inspected or modified through SSH.

## Remaining blockers and limits

| Action | Status and reason |
| --- | --- |
| Builder SSH / OS build | Denied by environment proxy: CONNECT 403 to `tunnel.cloudproxy.app`; network-policy update requested, no bypass or retry after denial |
| Builder disk/source/job inventory | Pending SSH; reported 1.5 TB is unverified |
| Project metadata | Destination HTTP 403 from `cloudresourcemanager.googleapis.com`, API disabled; no API enablement attempted |
| Current public release inventory | `gh api repos/Immortal-Protocols/pixel-ota/releases` returned Forbidden; proxy versus destination was not established, no retry |
| OS release | Pending inspected Frankel source/vendor inputs, current release/build number, update endpoint, signing-chain checks, full compilation and acceptance |
| Factory packaging | Need to verify whether a separate factory-image SSH signing key is required; the described OS bundle lists AVB/APK keys |
| Runtime/device acceptance | Not performed; no claim of phone update compatibility, verified boot, flashing or Cuttlefish acceptance |

The public `pixel-ota` repository and its README were readable through the GitHub
connector, confirming Frankel channel naming. That does not prove release-list
or asset-upload access. No public release, updater metadata, IAM grant, API,
firewall rule or network policy was changed by this integration.

Historical prior-art validation is preserved under `docs/upstream/`. It describes
other machines/devices and must not be presented as fresh RoseOS-1 evidence.
