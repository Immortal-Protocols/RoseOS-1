# Mustang tool qualification and remaining admission work

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
provenance for review, not authority to fill those fields. The optional local
package transfer is still incomplete; synthetic ZIP tests do not replace an
actual inspection result. The existing same-run hosted byte readback remains
successful independently of that local transfer.

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
