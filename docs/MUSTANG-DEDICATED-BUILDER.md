# Dedicated Mustang builder: source preparation and resource proposal

The active [lane](../os/build-only/mustang-lane.json) is **identity-bound** to the
confirmed VM `roseos-mustang-builder-20261107-a` (`5585425566393384049`) and data
disk `roseos-mustang-data-20261107-a` (`4322966418460195960`), ext4 UUID
`4183bff8-6be4-46bb-a641-2193d19813ee`, mounted at `/mnt/disks/mustang-data`.
The [runtime](../os/build-only/mustang-runtime.json) stays **unbound**. That
binding does not admit source, tools, a runner, app inputs, the baseline
archive, allocation reconciliation, signing-route approval, or publication.
The builder job has to be a self-hosted runner on this VM. Guard, controller,
publisher and cleanup stay on GitHub-hosted runners. Runner group 5,
`roseos-mustang-builder`, allows only this public repository and
`mustang-ota-dedicated.yml` at `refs/heads/main`. It has zero runners.
Lifetime is not selected and no runner is enrolled. The VM's own safeguard is a four-hour `maxRunDuration` STOP.
Mustang must never use Frankel's `immortal-os-builder` again; Yogi also has its own VM.
The earlier [proposal](../os/build-only/mustang-builder-proposal.json) (c3-standard-44,
2 TiB balanced, 12-hour STOP) is not this machine and is not deployed.
The old host's data removal belongs to the Mac operator, separately from this
source work. No VM, access, cleanup, signing, build or publication action has
been performed by this source change.

## Resource recommendation

Use a new `c3-standard-44` in `us-west2-a`, 44 vCPUs and 176 GiB RAM, Debian 12,
with a new 100 GiB boot disk and 2,048 GiB build disk, both zonal `pd-balanced`.
Start at **24 Make jobs**, rather than treating all 44 vCPUs as a proven safe
parallelism. This is an engineering starting point, not a measured optimum.
Keep the runner executable and diagnostic logs on the boot disk; place the
source, attempt workspaces and runner work directories on the data disk. This
keeps a full build filesystem from consuming the runner's diagnostic filesystem.
Use the existing cleanup mechanisms only after their live behavior is approved.

Infra reported through Chief that C3 is offered in this zone, regional C3 quota
is 300 CPUs with zero used, SSD-family quota has headroom, and the default subnet
has Private Google Access. **Stock and a working GitHub egress route remain
unverified.** The original Mac operator's 17:27 UTC readback also found a default
internet-gateway route, no regional Cloud Routers/Cloud NAT and no established
approved proxy endpoint. Private Google Access and the default route alone do
not establish GitHub connectivity for a VM without an external IP. A targeted
Network Services gateway lookup returned `SERVICE_DISABLED`; do not retry it,
enable the API or use an alternate route to bypass that denial.
Do not silently add public SSH, NAT, project-wide permissions or another VM if
admission fails. Use the approved Infra access route and report the exact gap.

The two disks must be newly allocated for Mustang. No old-host clone, snapshot
or local source-copy dependency is proposed. Fetch the pinned manifest and
source on Mustang's own disk. Fresh app inputs arrive as an immutable artifact
from their independent producer, rather than a Mustang job on Frankel.

## What sizing evidence actually establishes

[GrapheneOS](https://grapheneos.org/build) documents at least 32 GiB RAM and a
typical full-build storage increment above 100 GiB.
[AOSP](https://source.android.com/docs/setup/start/requirements) documents at
least 64 GB RAM and 400 GB free disk for source and build. The proposed machine
exceeds these minimums; neither guide proves this Mustang recipe at 44 jobs.

The committed successful [2026110401 compile receipt](../os/mustang-ota/41761758623/producer/compile-completed.json)
records `measure=filesystem-available-drop`, consumption 119,999,254,528 bytes
(about 111.8 GiB) and sampled peak 121,686,892,544 bytes (about 113.3 GiB).
**Those are disk measurements, not RSS or peak RAM.** No Mustang RAM high-water
measurement is available in the reviewed evidence. The 176 GiB choice is
reasonable for initial admission, with 24 jobs pending actual observations;
it does not establish a compile-duration promise or an absence of OOM risk.

Two TiB has ample capacity for the recorded single-attempt footprint, source,
vendor extraction, packaging and the existing proposed reserve. It is not an
unbounded-retention policy. Confirm free bytes and inodes on the mounted data
filesystem before the first build, and keep active and retained material
protected by the existing lifecycle contracts. The earlier sprint's omitted
checks are not silently restored or claimed passed by this proposal.
For scale, rounding the initial source checkout up to 250 GiB, then adding the
existing 150 GiB source-copy, 10.6 GiB factory download, 128 GiB vendor and
250 GiB compile proposals plus 300 GiB proposed reserve totals about 1,089 GiB.
This conservative planning sum leaves room for tools and packaging on 2 TiB;
it is not a measurement or an additional admission gate. Multiple retained
attempts need a measured retention budget rather than this single-attempt sum.

[Google's disk performance documentation](https://docs.cloud.google.com/compute/docs/disks/performance)
gives zonal balanced disks a shared baseline of 3,000 IOPS and 140 MiB/s, plus
6 IOPS and 0.28 MiB/s per provisioned GiB. For 2,148 GiB combined boot/data size,
this yields theoretical maxima of **15,888 IOPS and 741.44 MiB/s**, below C3's
44-vCPU VM limits of 50,000 IOPS and 1,200 MiB/s. These are not measured or
guaranteed application rates; boot traffic shares the limits. `pd-balanced`
is a supported starting choice, not evidence it matches the previous host's
build speed. A move to `pd-ssd` needs a measured bottleneck and a priced change.

## Exact approval package for Infra

The original Mac operator verified Los Angeles on-demand list rates at
**2026-11-05 17:27 UTC**, relayed by Chief: **$2.665883 per running hour** for
44 C3 vCPUs/176 GiB RAM, and **$257.76 per month** for 2,148 GiB of zonal
`pd-balanced`. Sources: [C3 CPU SKU](https://cloud.google.com/skus?currency=USD&filter=49B7-2000-7A99),
[C3 memory SKU](https://cloud.google.com/skus?currency=USD&filter=72AB-B51D-7D57),
[balanced disk SKU](https://cloud.google.com/skus?currency=USD&filter=DF86-0465-7120).
Mustang did not query billing/cloud APIs; its public text fetch did not render
the dynamic SKU rows. This record consumes the verified Mac handoff.

Twelve running hours cost **$31.99 compute**. Using a 730-hour monthly proration,
the disks add **$4.24** for those hours: **$36.23 before network/tax**, discounts
or credits. This is a list-rate example, **not an enforced spending cap or an
approved disk teardown time**. Disks remain billable while stopped. Disk
retention/teardown is an explicit unresolved owner decision.

The network choice also remains unresolved. If separately selected and approved,
one-VM/one-IP [Public NAT](https://cloud.google.com/nat/pricing) adds about
**$0.0064/hour plus $0.045/GiB processed**, with internet egress extra. Before
approving NAT, specify its actual router/subnet/address scope; a NAT covering
the shared default subnet must not silently expand other machines' access.
No public IP, NAT, proxy or new network resource is selected by this proposal.

The runner needs a **separate organization group**, restricted to repository
`1428996648`, explicitly admitting that public repository and only
`Immortal-Protocols/RoseOS-1/.github/workflows/mustang-ota-dedicated.yml@refs/heads/main`.
Its label is `roseos-mustang-builder`. The group identity/creation approval,
protected registration route and persistent-versus-ephemeral lifetime are not
established. Do not reuse or widen Frankel's group or issue credentials here.

Before creating anything, the exact approval package must resolve:

- The named VM and two disks at these rates, including the retention/teardown
  decision and ongoing disk charges.
- The exact GitHub egress choice and its network resource/access scope and cost.
- The separate runner group, protected registration route and runner lifetime.
- The dedicated controller/provider with the restricted trust and instance-only
  permissions below. The earlier proposal asked for a 12-hour STOP. The VM that
  was created uses a four-hour maxRunDuration STOP, which the lane records.
- Approved host setup and actual resource readback before binding the lane.
  Build, signing, live reclaim and publication remain separately held.

The controller proposal is an instance-scoped identity with only get/start/stop
on the dedicated VM. Its OIDC admission must bind numeric repository ID
`1428996648`, the exact dedicated workflow and main. This repository is public;
do not reuse an obsolete private-repository condition. No attached VM cloud
service account, new key set, signing bootstrap, signing grant or IAM expansion
to Frankel/Yogi is proposed. Infra must verify the actual trust configuration
and required setup permissions as part of its approval package.
Preserve the existing `openssh-client` hold and access restrictions; bootstrap
must not silently unhold or upgrade it to make an access path work.

## Source integration and owner boundary

Mustang owns its dedicated adapter, workflow, path/receipt integration and the
capacity-map change as its single writer. Infra owns the explicit-builder API
in `scripts/release-pipeline.py` and its focused common-controller tests,
preserving Frankel defaults. Common workflow routing remains Release-owned;
this split does not authorize overlapping edits there.
`scripts/mustang_controller.py` requires an explicit target and never replaces
the shared `BUILDER` global or falls back to Frankel. Infra's
[PR #586](https://github.com/Immortal-Protocols/RoseOS-1/pull/586), merged as
`b6b7ac3fb72379bab19daaa8d48f5fd221a45701`, supplies the agreed API:
`Production.control_builder(action, ..., builder={project, zone, instance, instanceId})`.
It verifies numeric ID/name on each describe, retains bounded polling and
no-resubmit behavior, and calls the ownership claim before start. Frankel's
default is preserved. Mustang integration tests exercise those actual merged
bytes with mocked process responses for start/STOP, wrong IDs and uncertain
request reconciliation. They perform no gcloud process or VM operation. The
2026-11-06 VM and disk identity is recorded; runtime admission remains unverified.

The Mustang adapter records exact VM identity plus workflow, source, run and
attempt in a write-once ownership file before the common controller can submit
start. A failed/uncertain start keeps that record and refuses another automatic
start. Cleanup refuses absent, malformed, foreign or stale ownership, and skips
STOP if this operation did not start the VM. It calls the same explicit-target
API for STOP. It performs no login or provisioning.

The manual `.github/workflows/mustang-ota-dedicated.yml` source now wires hosted
admission, the dedicated controller, one builder job, optional publication and
ownership cleanup. **`mustang-lane.json` is identity-bound and
`mustang-runtime.json` remains unbound. No runner, source, signing route, or
publication is activated.** The old source-only inspector is
superseded by `scripts/mustang-dedicated.py guard`. No workflow dispatch or real
candidate has been performed to prove this implementation.

The hosted guard checks the exact repository ID, main workflow, attempt 1,
actual binding evidence and app-source freshness before any controller login.
The controller validates the exact VM through Infra's API, records ownership
before start and refuses to adopt an already-running/unowned builder. One
builder job works with either a persistent runner or an approved protected
registration route that prepares an ephemeral runner on **every** VM start;
the lifetime remains an unselected binding, not a registration operation here.

The builder checks its non-secret guest instance ID and mounted filesystem UUID,
then available bytes/inodes and tool paths. It observes the committed baseline
archive's updater/trust before starting a compile. It downloads the exact app
artifact by ID, verifies its producer-run metadata and ZIP digest, and runs the
existing per-file/APK/receipt/policy admission. The app producer's source must
match the current three-app source paths in the existing allocation contract;
later OS-only source changes do not require rebuilding unchanged apps.

The native producer copies only its own admitted pristine pinned base, fetches
the existing pinned metadata, prepares vendor inputs, allocates under its own
lock, assembles the admitted app bundle and integrates the current source.
Host-reconcile's existing pristine-HEAD path performs first integration; it
never imports a Frankel reconcile seed. Carried-forward consumed build numbers
are included in allocation. Job/OIDC/GitHub credential environment variables
are removed from the producer process before Make/vendor tools run. The source
copy retains its honest byte-snapshot verification limits.
Structured phase-entry/completion/failure events go to stdout and an allowlisted
`progress.jsonl`; stdout still reports the phase if the data disk cannot write.
An entered compile phase includes its preflight and does not alone prove Make
has begun. Native completion still requires its actual successful receipt.

Actual unsigned outputs and raised-edge proof are collected **before signer
login**. Signing rechecks that same-run proof, uses the established version-1
identity and requires the signed proof. After logout, OTA staging verifies the
baseline trust, updater, versions, whole-package/payload signatures and both
proofs. The default result is an authenticated Actions artifact, not a public
release or channel update. This source does not claim privacy beyond the
repository's artifact-access controls.

`publish=false` is the default. Public publication additionally requires the
new runtime publication flag and a committed explicit authority record; the
old activation record alone cannot activate this route. The hosted publisher
rechecks same-run archive hashes and both proofs before minting its existing
repository-scoped App token. It preserves Latest and publishes OTA before
channel metadata. Automatic dispatch/publication remains off.

Failure receipts use fixed allowlisted JSON names, never signing configs,
key material or raw logs. Reporting verifies guest/disk identity even on failure.
Cleanup validates the exact ownership before login and does not depend on
signing, app or baseline readiness. It stops only this run's owned VM; absent
or foreign ownership refuses, with this VM's four-hour `maxRunDuration` STOP
(14400 seconds) as the safeguard when cleanup does not stop it. No global policy
reclaim/deletion is called. The builder job's outer limit is 200 minutes, inside
that four-hour window, so cleanup is requested before the VM stops itself.
Individual historical stage ceilings remain unchanged and
are not a promise they can all consume their maxima within that job.

`scripts/mustang_paths.py` derives explicit dedicated attempt/storage/lock paths
and adapts the validated canonical compiler recipe's three host-path fields.
Pins, build goals, limits and sprint omissions remain unchanged. Allocation,
collection and signing/factory data APIs now accept an explicit validated lane;
legacy CLIs remain retired. Dedicated compile/collection/sign/package receipts
bind the exact VM/workflow, and dedicated collection refuses old/foreign builder
receipts. Their tests use synthetic archives and signer/generator stand-ins;
no real signing or build has run. Historical data APIs/receipts and installed
baseline acceptance are preserved.

### Actual binding handoff still required

The following are input contracts for Infra/Release to supply after applicable
approval, not claims that these resources or records exist. Populate actual
evidence under `os/`, then bind the lane/runtime together; never use a proposed
name, a copied device label or a guessed value as readback.

| Runtime field | Required actual content |
| --- | --- |
| `jobs`, `tools`, `dataFilesystemUuid`, `automaticStopHours` | Approved Make parallelism, executable aapt/apksigner/Node paths, the dedicated data UUID and the confirmed four-hour STOP. The UUID and four-hour STOP are already recorded; jobs and tools are not. |
| `networkEvidence` | JSON with matching `builder`, `githubEgressVerified: true` and the actual `approvedRoute`; no denied lookup retry or implicit NAT/proxy. |
| `sourceEvidence` | Matching `builder`/`sourceRoot`, pinned manifest SHA-256, pinned `adevtoolRevision` and `pristine: true` bootstrap readback. This attestation is not a full copy-byte revision verification. |
| `runner` | Actual group name/ID and chosen persistent/ephemeral lifetime; registration evidence matching VM, workflow and repository `1428996648`, with `publicRepositoryAdmitted: true` and `readyOnEveryStart: true`. The protected registration implementation remains Infra-owned. |
| `signingRouteEvidence` | Actual approved admission of this workflow to the **existing** signer, matching `builder`/workflow, `identityVariable: GCP_SIGNER_SERVICE_ACCOUNT`, `providerVariable: MUSTANG_SIGNER_WORKLOAD_IDENTITY_PROVIDER`, `approved: true`, `establishedMustangIdentityPreserved: true`. No new IAM/key/credential operation is performed by source or inferred from these field names. |
| `appInputsRecord` | An actual `roseos.app-inputs-record.v1` Actions export, or the explicit `roseos.app-inputs-private-record.v1` private archive contract below, with the exact committed producer allocation and APK/receipt/policy identities. |
| `baselineTargetFiles` | Actual own-disk path with the **same** SHA-256/size as the committed Mustang baseline signing receipt; the archive is rechecked on the guest. |
| `baselineFactoryEvidence` | Optional explicitly bound [factory-image recovery contract](MUSTANG-BASELINE-RECOVERY.md), only with `baselineTargetFiles: null`. Default null; no fallback, retrieval or transfer is activated. |
| `allocationState` | Reconciliation of failed run `41884368820`, `unknown: false`, all `consumedBuilds`, and the destination `builder`/`dataFilesystemUuid`. Deleting old data is not evidence that uncertainty disappeared. Replacing the VM/disk requires a matching updated reconciliation record. |
| `publication` | Default disabled; a later actual authority record must name device/workflow, an `approvalRef`, `approved: true` and `automatic: false`. |

The new signer-provider **variable** does not create a provider or extend trust.
Infra must resolve and verify the established signing route for this workflow
under applicable authorization before binding it. No bootstrap or new key set
is supported by this workflow. The legacy Mustang entry points remain retired.
The common capacity map keeps the retired jobs' host reference for source
auditing, explicitly marks them retired and excludes Mustang from parallel
capacity. The lane is identity-bound and the runtime remains unbound. Editing a
capacity label or retirement marker cannot restore the retired entry points.

An Actions app export must carry its actual producer/export run and attempt,
artifact identity/digest, the three APKs and per-APK receipts, and matching
background policy. A private export carries the same payload identities and
the explicit archive/transfer evidence below instead of invented Actions IDs.
Verify either export on Mustang's own builder before integration. Source classes
alone do not establish F08 reachability; the app microphone removal and OS chin
control must be included together. The unsigned/signed raised-edge proof gate,
existing signing identity, automatic Beta hold and disabled Mustang automatic
publication remain. The requested next build does not itself authorize public
publication or Latest promotion. Physical-phone tests are separate.

### Private app export transport (source only)

The dedicated `fetch-inputs` step also supports one prepositioned **plain tar**
under the actual bound Mustang data home:
`<home>/inputs/frankel-<build>/export.tar`. It performs no download or transfer,
and never reads a Frankel host path. This does not authorize positioning that
file, approving a destination or spending on capacity. The lane is identity-bound
and the runtime remains unbound. No archive or real private-transfer record accompanies this
source change. The Yogi budget is not Mustang approval.

Release owns the shared input-record contract and verifier. Its private schema
is `roseos.app-inputs-private-record.v1`; `producer`, `signerSha256`, `sdk`,
`apps` and `files` retain the canonical export receipt's exact values. `export`
has **only** `receiptSha256` and `privateArchive`. The latter has `path`, lowercase
`sha256`, integer `sizeBytes`, and `provenance: {path, sha256}`. It has no Actions
artifact, export run or export attempt. The producer's original Frankel run and
attempt still come from the real committed allocation; a Mac export is not a new
Actions run. Actions records and their existing creation/download path remain
unchanged.

`provenance.path` names an actual committed JSON under `os/`, pinned by its own
SHA-256. Its schema is `roseos.app-inputs-private-provenance.v1`, with:

- `producer` and `receiptSha256` identical to the private record, and
  `archive: {sha256, sizeBytes}` identical to the tar's descriptor.
- `source` containing `exporter: scripts/host-app-inputs-export.py`, the actual
  full `exporterSource` commit, `originalAttemptChainVerified: true` and a
  nonempty `evidenceRef` for the performed exporter/chain verification.
- `transfer` containing actual `approved: true`, `completed: true`, `approvalRef`
  and receiver `evidenceRef`, with `received: {sha256, sizeBytes}` matching the
  archive and `destination: {builder, dataFilesystemUuid, path}` matching this
  exact bound VM, data filesystem and private archive path.

These fields must record actual authorization and observations. Filling them
does not itself grant authority or perform a copy. The hosted guard validates
their binding before controller login, including all producer fields against
the hash-bound committed allocation. On the guest, the existing host check
verifies VM identity and the data UUID. The importer refuses linked paths,
special files or a file on another filesystem, then copies the archive once to
the fresh run's private input directory and checks its size and hash again.
It leaves the original unchanged and retains failed copies for diagnosis.

No tar metadata is extracted. Only the canonical twelve regular files are
copied; common `./` prefixes and the root/signed-apps directory entries are
allowed. Extra, missing, duplicate, linked, sparse, special, absolute/traversing
or oversized members refuse admission. The canonical shared verifier then
checks every file's hash/size, export receipt, sums, APK receipt/signature,
package/version/signer and policy with the destination's own tools. The native
producer repeats that same admission before integration. Importing a tar alone
is not an admission or a new build.

The reported Mac export of Frankel `2026110501` (apps 365/5/39) has not been
transferred or admitted here. Its exact tar hash/size, receipt and provenance,
the baseline archive's availability and the failed Mustang allocation state
remain operational inputs, not inferred facts.
