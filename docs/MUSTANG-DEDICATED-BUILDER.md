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
has Private Google Access. **Stock, GitHub egress and actual regional prices are
unverified.** Private Google Access alone does not establish GitHub connectivity.
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

Before creating anything, obtain approval for the named VM, two disks and their
actual regional quote; the dedicated controller/provider and runner; and the
isolated 12-hour automatic STOP fallback. The planning allowances are $3 per
running hour and $300 per month for persistent disks, **not a verified regional
tariff**. Twelve running hours plus half a day of disks is about $41 before
network/tax, within the proposed $50 initial cap. Four hours daily is about
$660 monthly. Disks remain billable while the VM is stopped.

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

Mustang owns its dedicated adapter and workflow integration. Release owns
`scripts/release-pipeline.py`, common workflow routing and the capacity map.
The new `scripts/mustang_controller.py` deliberately refuses the current common
controller, which has an implicit Frankel `BUILDER` global. It never changes
that global or falls back to it.

Required Release handoff: support
`Production.control_builder(action, ..., builder={project, zone, instance, instanceId})`.
For explicit targets, verify numeric ID on every describe before a start or
STOP request, keep existing bounded polling/no-resubmit behavior, and invoke
the ownership claim before submitting start. Preserve Frankel's default API.
Unknown/mismatched IDs must fail without a mutation. Final integration requires
tests against the actual reviewed common-controller bytes, not only a stand-in.

The Mustang adapter records exact VM identity plus workflow, source, run and
attempt in a write-once ownership file before the common controller can submit
start. A failed/uncertain start keeps that record and refuses another automatic
start. Cleanup refuses absent, malformed, foreign or stale ownership, and skips
STOP if this operation did not start the VM. It calls the same explicit-target
API for STOP. It performs no login or provisioning.

The dedicated build workflow is **not yet installed or runtime-ready**. Do not
add a controller-only warm-up run. After the common API and actual Infra binding
exist, integrate it directly into the normal guarded build lifecycle, with its
own concurrency group and runner and ownership-aware cleanup. Port the legacy
Mustang adapter paths to the dedicated profile; do not reactivate old entry
points. Baseline target-files/trust evidence, a fresh signed-app export with
matching receipts/policy/allocation, and the uncertain failed-run allocation
must be reconciled before native compilation. No old local path is a substitute.

The fresh app artifact must carry its producer/export run and attempt, artifact
identity/digest, the three APKs and per-APK receipts, and matching background
policy. Verify it on Mustang's own builder before integration. Source classes
alone do not establish F08 reachability; the app microphone removal and OS chin
control must be included together. Preserve the unsigned/signed raised-edge
proof gate, existing signing identity, automatic Beta hold and disabled Mustang
automatic publication. The requested next build does not itself authorize
public publication or Latest promotion. Physical-phone tests are separate.
