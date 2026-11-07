# Independent Launcher3 source handoff

This is an integration handoff, not runtime authorization or a compile result.
The active binding is `os/build-only/launcher3-compile-lane.json` (schema 4),
source-bound for `verifyIntegrated`. It does not gate any OTA or release workflow.
The compile contracts and the #536 interruption, process containment, reports
and cleanup rules are retained.

## Source boundary

The harness owner supplies `launcher3_lane.py`, `launcher3_controller.py`, the
compile adapter, the new binding and tests in the existing adapter suite. Infra
owns `Production.control_builder` in `release-pipeline.py`; this patch does not
change that API. Release owns workflow, routing, capacity-map and reclaim policy
integration. The source binding does not change workflow jobs, reclaim policy,
CI registration, or os-release/OTA routing.

`launcher3-compile.py` now reads the schema-4 binding by default. A schema-3
`launcher3-compile-host.json` is rejected for execution. The old file remains
unchanged solely as the historical root used by Release's old-run reclaim test
and policy. `WORK_DIRS` and `task_root()` are unchanged. Do not point Frankel's
reclaim policy at the new disk or remove its coverage of old Frankel runs.

The adapter rejects any GitHub workflow context except
`Immortal-Protocols/RoseOS-1/.github/workflows/launcher3-compile.yml@refs/heads/main`.
Consequently the old `os-release.yml` hosted guard cannot start Frankel even
with this binding populated. An old run checked out at an older commit
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

The committed binding selects `verifyIntegrated`, so `bundleReport` and
`reconstructSource` stay null. Lunch is `frankel-cur-user`. `adevtoolGit` is
`public-fetch` because that is the only class `check` accepts for this lunch;
the controller check still reports `runtimeAuthorized` false. `reconstruct`
remains refused. No production-tree preparation is included in this handoff.

Source completion is this reviewed, offline-tested migration and its coordinated
workflow integration. Runtime completion additionally needs authorized execution
on the verified independent capacity and a successful compile/classpath/flag
report plus cleanup evidence. A setup refusal is a blocker, not completion.
Physical-phone testing is not a completion gate.

## Fresh export bound for verifyIntegrated (2026-11-07)

Schema 4 has no fields for the commit, tree pins, resolved manifest, export
ids, or the boot and source disks. Those values are recorded here and in the
binding `basis`. `policyReceipt` holds only the path and sha256. `lane.disk` is
the data disk mounted at `/srv/rose-launcher3`: the controller starts and stops
the VM, and that disk is the storage root holding `tasks/`, `locks/`, and
`inputs/`.

| Field | Value |
| --- | --- |
| policyReceipt path | `/srv/rose-launcher3/inputs/export-20261107T204240Z/background-policy.json` |
| policyReceipt sha256 (`--background-policy`) | `4b3faede5e309ac71cd00b9a642f84e244eee3186f3c355bf26aea3f5226446c` |
| app-inputs export run / artifact | `42408244069` / `13588167823` |
| producer | `frankel-2026110704` |
| integrated RoseOS-1 commit | `5454c2f1f0797fd69452da613cfd1934c0808fb0` |
| runner branch | `rose-integrated-5454c2f1` |
| resolved manifest sha256 | `43dc8a6b0689f85599a4cd1dd82e54816e3b18ceec3930c3db9afc208f9b69ed` |
| frameworks/base | `6fb4b6fad176c50025fa18e11b4615e69de41d56` |
| build/make | `6d3e9fe61a63b14d99ec240a1328199879cae6d2` |
| build/release | `82869bb9b22785588dc9501055d31362dc14198c` |
| Launcher3 | `5bcda53c843e355376b8f7a4a7cdf5fa8f446102` |
| Updater | `6ee911e5ecfdf7957d57c6f8c595a4b78ea63a71` |
| Permission | `f1b2ad113f43609d839e7d897892ff59398cf7c5` |
| VM instanceId | `8380049696571473017` |
| lane.disk | `roseos-launcher3-compile-data-01` / `263837443130866785` |
| boot disk | `roseos-launcher3-compile-01` / `5299228702637505657` (100 GB) |
| source disk | `roseos-launcher3-src-02` / `7922451502759869665` at `/srv/rose-launcher3/src` |
| taskParent | `/srv/rose-launcher3/tasks` |
| lockPath | `/srv/rose-launcher3/locks/launcher3.lock` |
