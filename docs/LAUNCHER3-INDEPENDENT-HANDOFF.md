# Independent Launcher3 source handoff

This is an integration handoff, not runtime authorization or a compile result.
The active binding is `os/build-only/launcher3-compile-lane.json` (schema 4).
Every real capacity and source value remains unbound. The compile contracts and
the #536 interruption, process containment, reports and cleanup rules are retained.

## Source boundary

The harness owner supplies `launcher3_lane.py`, `launcher3_controller.py`, the
compile adapter, the new binding and tests in the existing adapter suite. Infra
owns `Production.control_builder` in `release-pipeline.py`; this patch does not
change that API. Release owns workflow, routing, capacity-map and reclaim policy
integration. No workflow, capacity map, CI registration or reclaim policy is
changed by this patch.

`launcher3-compile.py` now reads the schema-4 binding by default. A schema-3
`launcher3-compile-host.json` is rejected for execution. The old file remains
unchanged solely as the historical root used by Release's old-run reclaim test
and policy. `WORK_DIRS` and `task_root()` are unchanged. Do not point Frankel's
reclaim policy at the new disk or remove its coverage of old Frankel runs.

The adapter rejects any GitHub workflow context except
`Immortal-Protocols/RoseOS-1/.github/workflows/launcher3-compile.yml@refs/heads/main`.
Consequently the old `os-release.yml` hosted guard cannot start Frankel even if
the new binding is later populated. An old run checked out at an older commit
retains its old finish implementation; this patch performs no live cleanup.

## Release integration, coordinated before runtime

1. Add the dedicated manual-only `.github/workflows/launcher3-compile.yml` on
   main. Its concurrency queue must be the independently verified binding's
   `lane.queue`, with cancellation disabled. Do not reuse `os-release-frankel`
   or any release/Agent queue. Check out the same `GITHUB_SHA` in every job.
2. On a hosted guard, run `python3 scripts/launcher3-compile.py check` for the
   existing outer/finish/job bounds, then
   `python3 scripts/launcher3_controller.py check`. The latter returns JSON
   with the validated workflow, runner name/label, queue, VM, controller variable
   names and canonical `bindingSha256`; it performs no cloud operation.
   An unbound input must fail before authentication, start or self-hosted routing.
3. A hosted controller job authenticates only through the verified Launcher3
   identity/provider variables. Run `launcher3_controller.py start` with an
   absolute `--ownership-record` in that job's temporary directory. The wrapper
   uses exactly `control_builder('start', builder=lane.vm, claim=...)`. It writes
   and fsyncs a fresh ownership record before the common API may submit start.
   Preserve that record with an always-run artifact step even if start fails or
   its result is uncertain. Do not retry an existing ownership record.
4. Route the worker exclusively to the verified dedicated runner label. The
   adapter also checks `RUNNER_NAME`, the exact main workflow/source and run
   identity. Only `workflow_dispatch`, attempt 1 is admitted. VM RUNNING is not
   runner readiness: Infra must independently verify the runner's registration,
   service, labels and exclusivity before activating this route.
5. Preserve the existing bounded `run`, always-run `finish`, allowlisted
   `report.json`, `classification.json`, `cleanup.json`, and seven-day artifact
   retention. Pass the current `GITHUB_RUN_ID`/`GITHUB_RUN_ATTEMPT`; keep the
   exec'd outer timeout and its 30-second kill-after. The worker needs only
   `contents: read`, no controller token, signer, secret or publication authority.
6. A hosted always-run cleanup job retrieves only this run/attempt's controller
   ownership artifact and checks out this same source. Call
   `launcher3_controller.py stop --ownership-record ...`. Missing, foreign,
   modified or malformed ownership refuses STOP; a VM already running when
   start was requested is left running. Never substitute the old plain
   `stop-builder` or `--only-if-started` boolean. The wrapper binds ownership to
   the full capacity/source binding digest, workflow, commit, run, attempt and
   exact VM including numeric ID. The common API rechecks numeric identity.
7. Remove/explicitly retire the old `launcher3-compile` mode's worker and
   controller/STOP routing in `os-release.yml`. Update the capacity map and its
   routing tests for the dedicated workflow in the same integration change.
   Update the harness workflow tests from the legacy-route snapshot to the new
   route. Keep the old-run reclaim policy on Frankel; any new backstop must be
   independently bound to the dedicated storage and use the same `WORK_DIRS`
   allowlist under its own lock. No live reclamation is authorized here.

The controller API is already present. No common-controller implementation,
global `BUILDER` override, fallback host or new cross-lane service is needed.
Shared Frankel recipe inputs remain *compile provenance*, not capacity routing.

## Evidence needed to bind the lane

Infra supplies reviewed readbacks for the exclusive runner name/label (including
all other eligible runner labels/groups), VM project/zone/name/numeric ID,
dedicated disk resource/numeric ID, disk/mount lineage, storage root, task parent,
separate lock, queue and narrowly scoped Launcher3 controller identity/provider
variables. Store the evidence reference in `lane.infraEvidence`. A name or label
alone is insufficient. The committed capacity-map hosts, Mustang binding and
Yogi profile are checked for collisions, including Yogi's retired VM and runner
label aliases; separation from unbound/unrecorded capacity still needs Infra's
evidence. `status: bound` means reviewed source configuration, not runtime
approval. It must never be filled using proposed names or synthetic test values.

Source, policy and bundle report must be staged on the independent storage, not
read through a live Frankel mount or symlink. `taskParent` and `lockPath` are
separate descendants of `storageRoot`; the source lies outside the task parent.
The copy remains capped at 150 GiB with at least 300 GiB free after its projected
size. The existing `-j8`, 120-minute compile ceiling and sampled 64 GiB RSS /
150 GiB output stops are first-measurement ceilings, not measured capacity or
hard isolation guarantees.

The selected `verifyBaseApplyCandidate` branch still needs a qualified base tree,
`frankel-cur-user` lunch inputs, the integrating policy receipt and its SHA-256,
and a producer-generated bundle report with its SHA-256. The report requires
reviewed run ID/attempt/commit/label/timestamps in
`os/build-only/launcher3-bundle-run.json`, matching receipt, patcher and recipe
evidence. These remain unbound. The public adevtool fetch needs its separate
runtime authorization before `adevtoolGit` is bound. If no qualified base tree
exists, Product must supply a contract-backed preparation route; `reconstruct`
remains refused. No production-tree preparation is included in this handoff.

Source completion is this reviewed, offline-tested migration and its coordinated
workflow integration. Runtime completion additionally needs authorized execution
on the verified independent capacity and a successful compile/classpath/flag
report plus cleanup evidence. A setup refusal is a blocker, not completion.
Physical-phone testing is not a completion gate.
