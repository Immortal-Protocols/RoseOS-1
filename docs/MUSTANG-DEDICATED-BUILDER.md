# Dedicated Mustang builder: source preparation and resource proposal

The [proposal](../os/build-only/mustang-builder-proposal.json) is not deployed.
The active [lane](../os/build-only/mustang-lane.json) remains **unbound**. Mustang
must never use Frankel's `immortal-os-builder` again; Yogi also has its own VM.
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
  permissions below, plus the isolated 12-hour automatic STOP fallback.
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
request reconciliation. They perform no gcloud process or VM operation; actual
dedicated resource readback and runtime admission remain unverified.

The Mustang adapter records exact VM identity plus workflow, source, run and
attempt in a write-once ownership file before the common controller can submit
start. A failed/uncertain start keeps that record and refuses another automatic
start. Cleanup refuses absent, malformed, foreign or stale ownership, and skips
STOP if this operation did not start the VM. It calls the same explicit-target
API for STOP. It performs no login or provisioning.

The exact future workflow file, `.github/workflows/mustang-ota-dedicated.yml`,
currently contains **only a manual hosted source/binding guard**. It has no
controller, cloud login or builder job. Its helper reports
`nativePipelineReady=false` and `runtimeAuthorized=false`; the committed
unbound lane refuses even that source check. This is source preparation, not a
build workflow or a separate VM warm-up/observation process. Do not dispatch it
as a build or count a future green source check as delivery readiness.

`scripts/mustang_paths.py` derives explicit dedicated attempt/storage/lock paths
and adapts the validated canonical compiler recipe's three host-path fields.
Pins, build goals, limits and sprint omissions remain unchanged. Allocation,
collection and signing/factory data APIs now accept an explicit validated lane;
legacy CLIs remain retired. Dedicated compile/collection/sign/package receipts
bind the exact VM/workflow, and dedicated collection refuses old/foreign builder
receipts. Their tests use synthetic archives and signer/generator stand-ins;
no real signing or build has run. Historical data APIs/receipts and installed
baseline acceptance are preserved.

After approved actual Infra binding and input evidence exist, complete the normal
dedicated build lifecycle with its own runner and ownership-aware cleanup.
The native producer, fresh source/admission and final OTA path wiring remain
unfinished; do not reactivate old entry points. Baseline target-files/trust
evidence, a fresh signed-app export with matching receipts/policy/allocation,
and the uncertain failed-run allocation must be reconciled before native
compilation. No old local path is a substitute.
The common capacity map keeps the retired jobs' host reference for source
auditing, explicitly marks them retired and excludes Mustang from parallel
capacity. The active binding remains the unbound dedicated profile. Editing a
capacity label or retirement marker cannot restore the retired entry points.

The fresh app artifact must carry its producer/export run and attempt, artifact
identity/digest, the three APKs and per-APK receipts, and matching background
policy. Verify it on Mustang's own builder before integration. Source classes
alone do not establish F08 reachability; the app microphone removal and OS chin
control must be included together. Preserve the unsigned/signed raised-edge
proof gate, existing signing identity, automatic Beta hold and disabled Mustang
automatic publication. The requested next build does not itself authorize
public publication or Latest promotion. Physical-phone tests are separate.
