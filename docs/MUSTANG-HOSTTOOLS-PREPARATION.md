# Mustang host-tools preparation handoff

Checked on 2026-11-08 from the RoseOS environment at main
`577cb73915947642512dad2a63ee9082f41ce414` (merged #682). This is a
preparation design and fresh readback, not an execution receipt or a runtime
binding. Saba is unavailable; continued engineering does not depend on an
acknowledgment from Saba.

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

There is currently **no host-dependency preparation mode** in
`mustang-ota-dedicated.yml`. `source_only` runs the exact pinned sync recipe;
`dry_run` performs host checks; the normal builder path requires the complete
runtime binding and enters baseline/allocation/OTA production. None can be
repurposed as an arbitrary command carrier.

The next source implementation is a separately guarded preparation stage on
that same workflow. Reuse the controller ownership file, group-5 JIT enrollment,
queue serialization and dedicated lock, guest instance/mount checks,
credential-stripped child environment, process-group supervision, allowlisted
receipts and hosted cleanup. Its guard must admit only its exact preparation
scope and reject mixed source/dry-run/OTA/publication selections. Its Make and
storage bounds must be explicit inputs from a reviewed preparation contract,
not inherited defaults. Queue-watch and cleanup must include the new job and
handle queue timeout, cancellation and partial failure before any dispatch.
No guest-side local command is an authenticated VM STOP.

Keep the current `mustang_hosttools.py` proposal fail-closed while implementing
and testing that adapter. Do not weaken `guard()` or change `status` to `bound`
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
