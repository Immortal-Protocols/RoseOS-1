# Mustang tool qualification and remaining admission work

Current source selection binds the reviewed release script, canonical JDK and
actual qualified otatools hashes in `mustang-hosted-sign-script.json`. This does
not execute the package or enable signer capacity. The historical qualification
receipts retain their original `signerPinsAdmitted=false` observation.

Runtime bindings select the measured host-distribution aapt2/apksigner, the pinned
JIT Node installation, jobs=24 from the completed native qualification, the
full-run source/copy contract, historical same-VM JIT egress and the independently
read existing signer policies. Native APK verification follows source/tool byte
observation. Its Java path uses the pinned source JDK; cloud signing CLI presence
is not required on the guest.

The accepted common input is the Release-verified Frankel2026110901 export from
42561803703 attempt1, artifact13649846651, archive digest
`e14607d7385952a19c9b78977d0c3dd6f84f1e8d76cc31bb7ae35bf09c4bc3d0`.
Build SDK37 and actual targetSdk35 are distinct. The old export and held app
stack are untouched. Existing manual full-delivery authority is reconciled to
the dedicated workflow; publish still requires its explicit input and all proofs.

Nathan's current administrator report is recorded in
`os/build-only/mustang-signer-runner-admin-20261109.json`: existing group6,
hosted runner3, Ready, `mustang-signer-4core`, Ubuntu24.04 Linux x64,4CPU,
16GB RAM,150GB disk, maximum1runner and no public IP. The active profile now
selects those reported facts. This is administrator evidence, not an independent
worker API read or measurement of actual hardware/free bytes. The denied group6
administrative GET remains untouched.

Explicit output budgets are selected: signed target16GiB plus copy16GiB,
OTA8GiB plus copy8GiB, scratch16GiB and reserve16GiB. Their80GiB sum excludes
separately accounted input copies and expansion. They are limits, not measured
output maxima or evidence that80GiB is free. Same-run hardware/filesystem and
actual bundle/expansion/free-space checks still precede signer login; actual
outputs must remain within their selected limits.

Nathan separately confirmed that group6 allows the public repository
`Immortal-Protocols/RoseOS-1` and its dedicated workflow on main. The exact
question and Yes response are preserved in the same administrator record.
Repository/workflow scheduling fields and runtime/delivery source admission
are now selected. No group read was retried and no permission was changed.
Source admission does not establish a successful native build, live capacity,
allocation, signing, publication or physical-device acceptance. Every full run
still supplies its native source/copy and reservation proofs, measured signer
hardware/filesystem/free space, qualified bundle, signatures/output bounds and
owned lifecycle/STOP receipts. Dispatch remains a separate action within the
current authorized scope; this source change dispatches nothing.

Full attempt42576627118 stopped because the native guest lacked gh. The next
attempt42581537518 stopped while downloading the pinned public CLI, reporting
HTTPError without status. Both attempts stopped before allocation or Make; their
cleanup jobs succeeded and direct numeric VM reads confirmed TERMINATED.
The native app consumer now uses Python's standard HTTP client for the same
committed artifact metadata/download endpoints. It checks the run, ID, name,
expiry, archive size/digest and exact safe ZIP inventory before APK admission.
API authorization is never forwarded to signed storage, HTTP failures stop
without retry, and errors expose phase/status rather than signed URLs. No CLI
resource or previously denied artifact is retried through this change.
Failure records are under os/build-only/mustang-full-run-*.json. The first clock
initialization fence is30, without observing or resetting an existing journal.

Run42584970482 passed the native host, baseline and fresh-app byte/signature
checks, then bootstrap source observation refused before copy/allocation/Make.
The pipeline now exposes only sanitized collector phase/failure codes and its
reporter exports the exact same-run bounded collector receipt even before the
prep directory exists. Source review found that guard added the legacy consumedBuilds projection
to clock runtime JSON, which deterministically violates the collector exact
binding. The clock guard now returns the captured runtime unchanged; legacy
allocation behavior and strict binding remain intact. An offline full collector
regression reproduces the old mismatch. The exact live code was not logged; the
next native result must establish the corrected handoff.

The sections below retain the historical qualification review and its original
unbound-state observations; they are not a second claim about current pin values.

The read-only `scripts/mustang_otatools_qualification.py` bridges the successful
dedicated tool build to the held hosted-tool closure. It does not enable a
signer, execute a package, allocate a build number or start a VM.

With no arguments it checks the committed evidence for run 42531345133:

```sh
python3 scripts/mustang_otatools_qualification.py
```

The verifier requires a successful tool-only Make, the exact dedicated VM and
disk, `mustang-cur-user`, both project checks, the pinned Soong module and the
same-run independent hosted readback. It reconstructs the exact native and
hosted receipt encodings and checks both hashes. Package size/hash, source,
run/attempt, artifact identities and STOP evidence must agree. This mode reads
no package bytes and says so in its result. Qualification is not a pristine
bootstrap or production signing pin.

A separately available matching ZIP can be inspected without extraction:

```sh
python3 scripts/mustang_otatools_qualification.py --archive /absolute/path/otatools.zip
```

This mode requires the exact qualified compressed bytes before reading ZIP
members. It bounds expansion to 4 GiB, entries to 20,000 and elapsed inspection
to 900 seconds. It refuses noncanonical or duplicate paths, parent traversal,
symlinks, special/privileged modes, encrypted/unsupported entries, file/directory
prefix conflicts and bad member CRCs. Every regular member is streamed and
hashed. `bin/ota_from_target_files` and `bin/sign_target_files_apks` must be
nonempty executable regular entries. The result records a sorted inventory
hash, expanded size and those entries' hashes; neither executable runs.
Inspection describes the bytes read through one open descriptor, not a later
path or execution. A later signer must perform its own admitted input checks.

`mustang_tool_closure.verify_closure` validates this qualification reference
while continuing to require null production hashes/executable, held trust,
unbound output bounds and closed signer admission. An executable inventory is
provenance for review, not authority to fill those fields.

The [actual read-only inspection](../os/build-only/mustang-otatools-inspection-42531345133.json)
now records all **500 entries** and **927,318,429 expanded bytes**, inventory
SHA-256 `7e69d987fd0384e169467f1f9ded8c4986d6e9f8e0538f7aa6b4402808dd9912`.
Supported range reads from the new qualified artifact's returned file
reference completed the interrupted local transfer. The complete outer
artifact matched GitHub's `8ace301a...9bbb4d` digest; its embedded native
receipt and inner package also matched their exact recorded hashes. The full
inventory and inspector source/hash are retained. `inspection_record` checks
the committed inventory hash, counts, byte totals, receipt encoding, required
entries and package/run/artifact binding. This metadata check does not reread
a ZIP or admit execution. No package member was extracted or executed, and
the historical installer-storage Forbidden was not retried.

## Reconciliation at main 48c08db2

This is a source/evidence checkpoint, not an owner acknowledgment or live
admission. The visible open PRs and recent dedicated workflow runs were read
on November 8, 2026. No competing tool-closure PR or newer dedicated run was
visible. Completed preparation is not repeated.

| Prerequisite | Current evidence | Outstanding work or owner boundary |
| --- | --- | --- |
| Dedicated inputs and tools | Factory 42510878271, host dependencies 42512135815, vendor 42522784617 and tools 42531345133 succeeded. PR #701's verified vendor consumer is merged. | Preserve existing inputs and output retention. Read-only qualification/ZIP inspection does not admit a full build. |
| Full runtime source/tools | Both tool-build project checks cover all 1,057 projects; ignored content remains unchecked. Runtime status and executable bindings remain unbound. | Obtain truthful pristine-bootstrap evidence and measured full-runtime tool/space bindings through a separately authorized dedicated operation. Do not relabel the sync or tool receipt as pristine. |
| Fresh common apps | The bound producer is `f5b1210c358a31f305b004d8b9dc98a3906f398f`. Current freshness check reports `dev.pixel.voice`, `dev.pixel.calculator.agent` and `dev.pixel.backgroundcontrol` changed. | Coordinate a fresh immutable export with the app/release owner. PRs #685/#686 remain drafts with explicit Chief holds; do not merge or duplicate that owner's version-ledger work. |
| Numbering | Failed run 41884368820's allocation remains unknown. The production epoch/authority are null; provision/network/issue admission remain false. | Agree and establish the durable monotonic authority and compatibility proof before any production issue. No synthetic observation, proposed range or unproved consumed list becomes an issued number. |
| Continuity | The committed candidate-continuity contract passes its existing validator. It is candidate-only, not a current phone observation or target-files archive. | Select the appropriate admitted continuity input and satisfy the existing staging checks. The full-payload route uses the new target-files; old target-files are not required merely to generate that payload. The installer-storage Forbidden remains untouched. |
| Hosted signer | Reviewed helpers and the canonical public JDK have provenance; successful tool production/transfer now has bound evidence. Production pins and output bounds remain null. | Complete actual ZIP/closure review, measured signer capacity/output bounds and admission of the existing v1 signing identity. Current source work performs no signing, IAM, secret or credential operation. |

Runner group 5's idle zero-runner count is expected for the working JIT route.
Historical readiness prose about absent runners, factory inputs or vendor
generation is not a reason to repeat those completed operations. Current
guard fields, receipt limits and owner holds still apply. Public publication
stays separate and disabled.

Run42604053622 stopped before copy/allocation/Make on the root source-sync
manifest, `resolved-manifest.xml`. Its failure record includes successful
cleanup and direct stopped-VM evidence. The correction binds the file to the
exact captured pinned manifest and retains copy readback; this source change
is not evidence that native compilation, signing or publication succeeded.
