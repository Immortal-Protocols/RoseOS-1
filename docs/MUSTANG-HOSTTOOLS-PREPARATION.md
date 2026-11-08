# Mustang host-tools preparation handoff

Checked on 2026-11-08 from the RoseOS environment at main
`577cb73915947642512dad2a63ee9082f41ce414` (merged #682). This is a
preparation design and fresh readback, not an execution receipt or a runtime
binding. Saba is unavailable; continued engineering does not depend on an
acknowledgment from Saba.

## First live preparation result: 2026-11-08

After the user's explicit authorization, [PR #687](https://github.com/Immortal-Protocols/RoseOS-1/pull/687)
merged as `27c72f08032d0d23e6163d30cfb18fcec8a381f1` with Mustang OTA
checks, Release verification and App offline tests passing. The manual
`hostdeps_only=true`, `source_only=false`, `dry_run=false`, `publish=false`
[run 42492662025](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/42492662025)
used that exact main revision and attempt 1.

Admission, controller START, group-5 JIT enrollment, queue watch and owned
cleanup succeeded. The guest build step and its later receipt-copy step each
exited 4. Artifact metadata reports zero artifacts. The GitHub job-log download
returned HTTP 403 from its log-storage endpoint. After the run completed (a new
availability state), one retry of the same endpoint also returned Forbidden;
no alternative access path was used. Check annotations contain only the step exit code and missing
receipt warning. The exact guest refusal and whether Make began are therefore
**unknown**. A missing lock is a possible preflight failure, not an observation.

A fresh Compute API read after cleanup confirmed VM ID `5585425566393384049`
`TERMINATED`, last stop `2026-11-08T07:03:55.483-08:00`, with the unchanged
14,400-second STOP safeguard. No host-tool distribution, vendor generation,
otatools proof, new OTA, signing or publication is claimed. No second run was
dispatched. Obtain the failed step's actual error through authorized access
before choosing a correction or retry.

The follow-up source retains a separate, write-once `hostdeps-failure.json`
from the admitted job's runner temporary directory if execution fails before
the full receipt can be copied. It contains fixed failure categories and
**expected** identities only. It explicitly attests neither guest identity,
Make success, distribution nor STOP; it never serializes exception text or raw
logs. This is prospective failure reporting, not recovery of this run's missing
receipt or an explanation of its failure.

## Verified starting point

| Item | Evidence and limit |
| --- | --- |
| Dedicated VM | A fresh `gcloud compute instances describe` returned `roseos-mustang-builder-20261107-a`, numeric ID `5585425566393384049`, `TERMINATED`, `n2-highmem-32`, last stop `2026-11-07T13:19:09.988-08:00`. `maxRunDuration.seconds` is `14400`, termination action `STOP`. This read did not start the VM or inspect its filesystem. |
| Source sync | [Run 42409634329](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/42409634329), attempt 1, is still completed/successful at `c89d530cda33354ec593c1fb1338aa50e5dc7484`. The [bound receipt](../os/build-only/mustang-source-sync-42409634329.json) records all 1,057 pinned project identities. It explicitly has `pristine: false` and is not a host-tool attestation. |
| App export | #680 binds the completed independent export. The runtime points to its record; host preparation must preserve that binding and recheck source freshness when a build is actually admitted. |
| Runner | Existing group 5 uses JIT enrollment on every start. `mustang_ephemeral.require_group5` accepts null or `ephemeral` lifetime and requires null registration evidence. Idle/zero runners is expected. Do not fill those fields or pre-register a persistent runner. |
| Recent activity | The recent Actions page and open-PR search showed no newer Mustang execution/preparation deliverable than #682. The existing owner branch still points to `937a5b9`. These reads do not establish whether another engineer has uncommitted work. |
| Factory | The [committed record](../os/build-only/mustang-factory-box-20261107.json) is still box-only, not transferred or extracted. Its old `/workspace/mustang-ota/` path is absent in this new environment. The record is not proof the bytes are available here. |

The checkout and GCP read work in this environment. Its name/configuration does
not make it the remote Android builder. Source compilation belongs on the
dedicated Mustang VM and its own data disk, never on Frankel or Yogi.

## Preparation order

There are two distinct compilations: adevtool's generic host dependencies,
then the Mustang `otatools-package` proof. Requiring the generated Mustang
product before the generic dependency build would make the preparation order
circular.

1. **Observe the existing source and host.** Through the established dedicated
   controller/JIT route, recheck the guest instance ID, mounted UUID
   `4183bff8-6be4-46bb-a641-2193d19813ee`, source path, current free bytes/inodes,
   tool versions and any running job or held lock. Compare project HEADs and
   worktree state to the pinned manifest before calling the tree pristine;
   `repo manifest -r` alone does not check dirty files. Record actual Node,
   aapt and apksigner paths/versions as `mustang-runner`. The bootstrap script
   contains expected paths but is not fresh executable-path evidence. Preserve
   existing output directories and any interrupted attempt.
2. **Build adevtool dependencies with explicit bounds.** The pinned
   [paths.ts](https://github.com/GrapheneOS/adevtool/blob/17df3b79a73e67ad68387b35c7d41c85e1d8103c/src/config/paths.ts)
   invokes `vendor/adevtool/scripts/run-build.sh` with product
   `sdk_phone64_x86_64`, output `out_adevtool_deps`, and the fourteen names in
   `mustang.json` `adevtool.depsTools`. The pinned
   [run-build.sh](https://github.com/GrapheneOS/adevtool/blob/17df3b79a73e67ad68387b35c7d41c85e1d8103c/scripts/run-build.sh)
   sets OUT_DIR, sources envsetup, lunches that product, and calls `m` with the
   remaining arguments. The future wrapper must supply an explicit reviewed
   job count (24 is the existing starting proposal, not a measurement), a
   stage timeout and storage allowance. It must record the exact invocation,
   source/project identities, Make result, elapsed time, disk delta and tool
   output identities. Directly running run-build.sh does **not** write
   `adevtool_revision`: paths.ts writes it only after its build succeeds.
   A wrapper must verify the successful build and actual pinned adevtool HEAD
   before atomically writing that marker as the pin plus one newline. Preserve
   the complete host distribution, including script/JAR/library dependencies;
   fourteen existing filenames or their hashes alone are not provenance.
3. **Transfer and verify factory inputs, then generate vendor.** Resolve the
   existing archive's availability with its data owner before choosing any new
   download. Put approved bytes on Mustang's data disk and verify
   11,366,092,596 bytes and SHA-256
   `099acc44e2f87fe2735d92952081d64550ff08b4a6390bb6b001c8451695657e`.
   Record transfer provenance. Refuse collisions at the committed vendor/unpack
   paths; do not remove or adopt them silently. Use pinned adevtool and
   `ADEVTOOL_SKIP_DEP_BUILD=1` only after step 2 is actually attested. Record the
   generated tree and factory identity; seeing an include in `mustang.mk` is
   not vendor provenance.
4. **Produce the bounded otatools proof.** Only after the dependency and vendor
   receipts exist, lunch `mustang-cur-user` and request `otatools-package` with
   an explicit separate OUT_DIR. Bind the precise source, product, job count,
   output path, Make result, output size/hash and complete execution closure to
   that attempt. Read the archive only from its exact Soong module location,
   as the existing collector does. This is not a full OTA compile, allocation,
   signing, baseline recovery or publication.

The first live preparation need not combine all four steps. Bound each
attempt so receipt/reporting and hosted cleanup fit inside the four-hour STOP
window. Existing source/vendor/compile recipe ceilings cannot simply be added
and claimed to fit that window. Measure time and space before planning the
following attempt; retained partial output is not a successful receipt.

## Required execution adapter

This branch adds `scripts/mustang_hostdeps.py` and a separate `hostdeps_only`
Boolean selection on the **existing** dedicated workflow. Its guard accepts
only that selection, the exact manual main workflow, numeric repository ID and
attempt 1; combining it with source, dry-run or publication is refused before
the controller. Only the `hostdeps` guest job running as `mustang-runner` can
execute. The ordinary OTA runtime gate stays unbound.

The stage reuses the controller ownership file, group-5 JIT enrollment, queue
serialization, existing dedicated lock and hosted cleanup. The guest checks
instance ID, disk UUID/mount, filesystem device, clean recipe checkout and the
HEAD/worktree of every pinned project. Git status leaves ignored content
unchecked; the receipt explicitly does not claim a pristine-runtime
attestation. The lock must already exist as a regular no-link file. Missing or
busy locks refuse before output changes; the adapter does not repair parent
permissions or create/chown a lock in the protected data home.

`recipe()` makes the proposed bounds explicit: 24 Make jobs, 9,000 seconds
including source inspection/build/output observation, 128 GiB of watched
output/attempt files, 300 GiB free reserve, a 16 MiB raw-log ceiling and
100,000 entries. The workflow step is 155 minutes and its job is 165 minutes.
Checks run every five seconds; this is sampled process-group supervision,
not a filesystem quota or containment of descendants that detach into a new
session. Bounds are ceilings, not measured costs. Inodes and free space are
rechecked during work. Timeout, cancellation or monitor failure kills the
build process group; guest code never claims an authenticated VM STOP.
The structured receipt records the first, last and minimum available bytes,
minimum available inodes, peak watched bytes and sample count. Its filesystem
available-byte drop is a disk observation, not RSS or a unique-process cost.
Live samples tolerate entries being renamed or removed while Make runs and
walk from directory descriptors without following links. Final inventory
requires readable supporting directories; an incomplete walk fails.

The output directory must be absent. Existing or partial output is preserved
for explicit reconciliation. The child receives an environment allowlist and
its own temporary directory, without inherited job tokens, injected GCP
credentials, shell startup variables or workflow output channels. After a
successful Make, the adapter hashes the complete host distribution, records
contained links and requires fourteen real executable entrypoints. Only then
does it atomically create the pinned revision marker. The write-once attempt
receipt binds source/run/builder/disk, command, bounds, manifest, wrapper,
distribution, marker and result. Failure retains its own files and a failed
receipt. The report step rechecks guest identity and copies only that run's
structured receipt; raw logs are not uploaded.

Queue-watch recognizes `hostdeps`, cleanup waits for it, and the ordinary
builder and signer are excluded. This implements preparation step 2's source
adapter; it is not a live run, a successful tool build, vendor generation or
the later Mustang otatools proof. `source_only` still runs the pinned sync
recipe and `dry_run` still performs host checks. Review and merge of this
source, followed by an appropriately scoped manual dispatch and actual guest
readback, remain necessary before claiming live preparation success.

Keep the current `mustang_hosttools.py` proposal fail-closed when integrating
and running this adapter. Do not weaken `guard()` or change `status` to `bound`
to reach a prep stage. Do not rerun the guest bootstrap as a diagnostic: it
installs packages, replaces runner files and changes system configuration.

## Remaining build admission work

Host preparation will supply evidence for only part of the runtime gate.
`jobs` and executable tool paths, pristine source attestation, network evidence,
signing-route evidence, unresolved old allocation, baseline continuity and
hosted signer output/tool bounds remain separate checks. Group-5 null lifetime
and registration evidence stay valid. The production tool pins and
`readyForControlledStart` stay unchanged until actual proofs and their required
review exist. Source-sync success does not supply those proofs.

The previous Codex thread ID is
`01a1b953-0aed-77fc-9e3f-95500e4be0fa`. This environment exposes no thread-read
tool, so its complete transcript was not independently retrieved here. This
handoff uses the delegated summary, current repository, recent room receipts
and fresh GitHub/GCP reads; it does not claim unseen owner acknowledgment.

## Local review results

The self-review tightened the execution boundary to the exact fourteen pinned
host goals; another syntactically valid Make goal such as `droid` is refused
before controller START. The recipe's adevtool pin must match the manifest.
Live disk sampling now tolerates disappearing temporary entries while using
descriptor-based walks. Final distribution inventory refuses unreadable
supporting directories. The receipt includes measured disk/inode observations,
and revision-marker observation shares the original stage deadline. The
existing Mustang signer label was added to Actionlint's label metadata; no
runner or access binding was created.

Verification on this environment:

- **205 affected tests passed**, covering the adapter, source-sync, available
  ephemeral-runner cases, edge/runtime admission, hosted signing, app binding
  and test-path coverage. The source-sync tests used an official Repo checkout
  installed in `/tmp`, with its config also under `/tmp`.
- **Two additional ephemeral-runner permission fixtures are unavailable**:
  `test_health_check_uses_the_runner_when_parents_are_not_searchable` and
  `test_source_dirs_are_runner_writable_under_a_root_owned_parent` require
  `sudo`, which is absent. They were attempted and their missing prerequisite
  was recorded; they are not counted as passes.
- **Actionlint 1.7.12 passed all repository workflows**; its official release
  archive was checksum-verified before local use. Both Mustang YAML files parse,
  the changed Python files compile, and `git diff --check` passes.
- The ordinary full offline suite was attempted. Java's real compiler module
  was exposed through a temporary `javac` launcher; fixture package construction
  required a process-local umask of 022. The suite then stopped on missing
  `mke2fs`. A diagnostic copy of the suite continued its original commands to
  record every failing unit, without changing the repository's test script.
  It finished with four failing units: `test_release_pipeline.py`,
  `test_permission_apex.py` and `test_emulator_alpha.py` require missing
  `mke2fs`; `test_yogi_direct.py` has an OpenSSH-9.2/OpenSSL-3.5 qualification
  failure, four read-only-home fixture errors and two unresolved process-death
  assertions. The latter assertions are not declared environment-only without
  further evidence. The full suite is therefore **not green**.

The detailed local logs are `/tmp/mustang-review-final-affected.log`,
`/tmp/mustang-review-actionlint.log`, `/tmp/mustang-review-suite-all.log` and
`/tmp/mustang-review-suite-failures.log`. These are local check records, not
live compilation, cryptographic signing or device acceptance evidence. The
remaining prerequisite/fixture results must be resolved on a suitable check
environment before claiming full-suite validation for integration.

## Integration and first preparation run

The steps below preserve the original integration/run plan. The user's later
explicit authorization resolved the earlier PR-creation approval rejection;
PR #687 is merged and the first preparation run is recorded above. These
commands are not instructions to redispatch that failed attempt blindly.

1. **Reconcile and review the complete branch.** After the local changes are
   committed, use the following read/reconciliation commands. The branch
   includes the initial documentation commit and the adapter commit; review
   the full diff rather than only its latest commit. If main has advanced,
   reconcile on an isolated branch and rerun the affected checks.

   ```sh
   git status --short
   git fetch origin main
   git log --oneline origin/main..codex/mustang-hostdeps-adapter
   git diff --stat origin/main...codex/mustang-hostdeps-adapter
   git diff origin/main...codex/mustang-hostdeps-adapter
   ```

2. **Publish for review only after the external action is authorized.** The
   exact branch publication would be:

   ```sh
   git push --set-upstream origin codex/mustang-hostdeps-adapter
   ```

   Proposed draft PR title: **Add bounded Mustang adevtool host-dependency
   preparation**. Proposed description: “The dedicated Mustang workflow has
   source-sync and OTA paths but no stage for preparing adevtool's generic
   host dependencies. Add an exclusive manual `hostdeps_only` job using the
   existing owned controller, group-5 JIT route, dedicated lock and hosted
   cleanup. Check pinned projects and the exact fourteen goals before Make;
   enforce explicit job/time/disk/log bounds and retain run-bound distribution
   and space observations. Vendor generation, OTA allocation, signing and
   publication remain outside this stage. Validation and remaining local
   environment gaps are recorded in this handoff.” Add the actual final check
   results to that description. Normal review/CI and merge must finish before
   a main-only dispatch; a feature-branch dispatch is refused by the adapter.

3. **Before any authorized dispatch, recheck ownership and exact resources.**
   Record the integrated main SHA and check that there is no queued or active
   dedicated Mustang run:

   ```sh
   git ls-remote origin refs/heads/main
   gh run list --repo Immortal-Protocols/RoseOS-1 \
     --workflow mustang-ota-dedicated.yml --branch main --limit 20 \
     --json databaseId,event,headSha,status,conclusion,createdAt
   gcloud compute instances describe roseos-mustang-builder-20261107-a \
     --project protocols-465723 --zone us-west2-a \
     --format='json(name,id,status,scheduling,disks)'
   gcloud compute disks describe roseos-mustang-data-20261107-a \
     --project protocols-465723 --zone us-west2-a \
     --format='json(name,id,sizeGb,type,users)'
   ```

   Require VM ID `5585425566393384049`, disk ID `4322966418460195960`, the
   recorded attachment, VM `TERMINATED`, and `maxRunDuration.seconds=14400`
   with termination action `STOP`. An active or foreign-owned VM/run is a
   reconciliation blocker, not a reason to adopt or cancel healthy work.
   These control-plane reads do not inspect the stopped filesystem. Existing
   lock presence/permissions, source state and output absence are still guest
   preflight checks; no successful readback of those paths is claimed here.

4. **If the one preparation run is authorized, dispatch only its scope.**

   ```sh
   gh workflow run mustang-ota-dedicated.yml \
     --repo Immortal-Protocols/RoseOS-1 --ref main \
     -f hostdeps_only=true -f source_only=false \
     -f dry_run=false -f publish=false
   ```

   Capture the actual new run ID from GitHub, verify its head SHA equals the
   reviewed integrated main SHA, and require attempt 1. Do not rerun an old
   attempt. Monitor that exact run's controller, enroll, queue-watch, hostdeps
   and cleanup jobs with `gh run view RUN_ID --repo
   Immortal-Protocols/RoseOS-1 --json url,headSha,status,conclusion,jobs`.
   `RUN_ID` here means the observed numeric ID, not a preselected value.
   Controller ownership must precede guest work. Queue timeout or a refused
   preflight must retain the existing cleanup path; do not route through SSH
   or the guest bootstrap to get past the refusal.

5. **Collect evidence and verify STOP before planning another action.** The
   only uploaded preparation artifact is `mustang-hostdeps-RUN_ID-1`; its
   file is `hostdeps-receipt.json`. Download that exact artifact into a fresh
   directory and record its digest. Require the receipt's run/source/attempt,
   builder and disk identities to match GitHub and the lane; Make exit 0;
   `success: true`; the exact fourteen real executable entrypoints and their
   supporting distribution; the pinned wrapper/manifest/revision marker;
   and measured headroom within the recorded bounds. The ignored-content and
   detached-descendant limitations remain part of that evidence. A successful
   dependency receipt does not set the OTA runtime or otatools pins.

   Require cleanup's owned STOP and group-5 sweep results, then make a fresh
   control-plane read of the same numeric VM ID and observe `TERMINATED`.
   The four-hour safeguard is a backstop, not evidence that cleanup succeeded.
   If a lock is missing/busy or output already exists, Make must not start.
   A refusal before attempt creation may have no receipt artifact; retain the
   fixed step error and cleanup result instead of fabricating a receipt.
   Existing or partial output stays in place for explicit reconciliation.
   Root-owned lock preparation, input deletion, permission changes and any
   retry are separate actions with their own applicable scope. No blind
   redispatch, automatic deletion, credential creation or access workaround
   is part of this plan.
