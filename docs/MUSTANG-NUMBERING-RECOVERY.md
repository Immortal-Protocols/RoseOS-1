# Mustang numbering recovery: bounded source evidence

This increment adds offline compatibility evidence and a migration proposal.
It does **not** change allocation, choose or reserve a real version, reconcile
the lost allocation, enable the dedicated lane, or authorize runtime actions.
The existing allocation guard still refuses an unresolved state. Target-files
and factory-image baseline alternatives from #593 remain intact. PR #592's
shared private-input verifier is a separate dependency owned by Release.

## Failed run and a clock-independent legacy ceiling

The committed failure record identifies run `41884368820`, attempt 1, source
`6be302942f348b58f8a0ce2406c7202f2dd676bf`. It says the allocation and native
phase are unconfirmed; binding, signing and publication jobs were skipped. This
review did not retrieve logs, artifacts, guest files or installer `13280698927`.

The captured workflow checks out source and invokes `mustang-pipeline.py`.
That source loads `mustang-allocate.py` from its repository scripts directory.
The allocator supplies `DAILY_LIMIT = 99` to `release-allocate.next_build`:
UTC Gregorian `%Y%m%d`, followed by the day's sequence in 01..99. Python's
maximum representable year is 9999. Thus every successful output of **that
generator** is at most **9999123199**, without assuming anything about the lost
host's clock. The reviewed old generator function is identical to today's
function. Tests execute the frozen function through its final day and verify
exhaustion rather than allocating anything.

The old allocator writes its write-once reservation ledger **before returning**;
the pipeline writes the attempt's `allocation.json` only after return. A missing
attempt receipt therefore cannot prove nonconsumption. Exact historical file
blob/SHA-256 identities and excerpts are in
`os/tests/fixtures/mustang-numbering/sources.json`.

This proves the normal captured-source generator's range. It is not a guest
execution attestation or proof that nobody modified code outside that contract.
The lost exact value remains unknown. Retiring this entire namespace would be
a new, explicit recovery mode, never a fabricated `unknown: false` or an empty
replacement ledger.

## Actual consumer assumptions inspected

All upstream sources below use the current `os/pinned-manifest.xml` revisions.
The fixture inventory links each full source, records its hash, and checks the
pin and excerpt digests offline.

| Consumer | Observed assumption | Bounded evidence |
| --- | --- | --- |
| Soong `ui/build/config.go`, `build.go` | `BUILD_NUMBER` may be a filename-safe string; stored as a string and written to `build_number.txt`. Build time has a separate variable/file. | Pinned source inspection. |
| Make `core/sysprop.mk`, `version_util.mk`; framework `Build.java` | Incremental ID is a property string and `Build.VERSION.INCREMENTAL` is a Java `String`. `BUILD_DATETIME` and `Build.TIME` are separate timestamps. | Pinned source inspection; no complete Make/Soong execution. |
| Releasetools `ota_utils.py`, `common.py` | Legacy metadata copies `build_incremental` unchanged; fingerprints format it as a string. Timestamp handling uses separate numeric fields. | Execute the real pinned metadata-conversion function with controlled protocol-shaped inputs, then the current Mustang full-OTA metadata boundary. Generated protobuf/runtime dependencies are not tested. |
| `script/generate-metadata`, `generate-release.sh` | Channel metadata obtains the incremental ID from the fingerprint; release filenames/`BUILD`/`VERSION` keep the ID as text. | Execute the real channel generator on synthetic ZIP metadata; inspect factory/release script source. No factory packaging executed. |
| Patched `RoseReleases.java` | Exactly ten decimal digits; `Long.parseLong`, numeric comparison, exact same-device assets and complete listing. | Execute the actual selector against both synthetic namespace boundaries, equal/lower IDs, wrong devices, incomplete listings and invalid/eleven-digit IDs. |
| Pinned Updater `Service.java` | Target incremental is a string. Newness uses `Long.parseLong(metadata[1])` and `ro.build.date.utc`; target timestamp must be strictly later. | Execute its unchanged metadata-admission block in a controlled Java harness, including times above signed 32-bit range and invalid/overflowing time. No Android/network/UpdateEngine execution. |
| Recovery `install/install.cpp` | Source incremental uses exact string matching; target incremental is logged as a string. Downgrade comparison uses `int64_t` timestamps. | Compile and execute the unchanged functions in a controlled C++ harness. Test matching/mismatching incremental IDs and older/malformed/overflowing timestamps; no Android library/signature/payload execution. |
| UpdateEngine `hardware_android.cc`, `hardware_interface.h`, `delta_performer.cc` | Build timestamp is `int64_t` from `ro.build.date.utc`; payload maximum-timestamp check is separate. | Pinned source inspection only. |
| Common Make AVB wiring | Passes board-specific rollback-index variables to AVB; the inspected common rules do not establish those variables' Mustang-specific values. | **Incomplete**: generated Mustang board definitions, all partition rollback indices and bootloader behavior still need evidence. Common wiring alone cannot prove their independence from the ID. |

Synthetic probe IDs cover the first integer above the legacy ceiling and the
largest ten-digit integer. These are fixtures, not release assignments. There
are **876800** ten-digit integers above the legacy range. Eleven-digit IDs are
incompatible with the current selector and receipt/path contracts.

A higher ID does not override timestamp rollback protection. Both Updater and
recovery probes reject an older timestamp even with a high ID. Current Mustang
`ota_metadata()` and `verify_staged()` compare the epoch against the configured
baseline. `publish()` also checks all existing Mustang tags/drafts for newer or
equal IDs, but does **not** establish a timestamp high-water mark for every
previous public release. A migration must preserve explicit timestamp ordering
against trusted public/installed evidence and partition/AVB rules. Calling the
ID scheme clock-independent does not make OTA timestamps clock-independent.

## Minimal implementation proposal and unresolved dependency

Do not substitute one high ID into the current date allocator: regression tests
confirm it subsequently refuses that namespace with `clock_behind`.

The smallest defensible change is an explicit Mustang-only numbering policy,
with these linked changes reviewed together:

1. Bind a durable counter authority/epoch outside disposable build output. One
   candidate is the dedicated workflow's server-assigned `run_number`, mapped
   above the fixed legacy ceiling. GitHub documents it as a counter for one
   workflow, with reruns retaining the value. It is **not** a repository-wide
   counter. Pin repository and workflow identities/path, verify actual current
   run/source/event/attempt metadata, refuse attempt >1, bound the counter and
   reject a changed/reset epoch. A display name or unchecked environment string
   is insufficient. Workflow replacement/reset and disaster recovery require
   an explicit no-reuse handover; no claim that an ID alone prevents all resets.
2. Under the existing **dedicated Mustang lock**, reobserve known releases,
   reservations and live paths. Refuse a collision, non-increasing identity,
   exhausted namespace, missing durable state, wrong counter identity, or
   unreconciled consumption outside the proven retired namespace. Persist the
   reservation and its counter/source/run evidence write-once and durably
   before exposing paths; interrupted attempts consume their counter value.
   Never backfill a gap or recreate an empty ledger as recovery.
3. Version the Mustang allocation/recovery record explicitly: preserve the
   legacy failed run and exact value as unknown, with the proven retired range
   and source evidence. Admit that mode only with positive counter/epoch and
   retirement evidence. Keep v1 historical receipts readable and unchanged;
   require the new proof at `mustang-dedicated.py` admission and through the
   producer/signing/staging receipt chain. Do not replace the existing unknown
   check with a generic bypass.
4. Keep build epoch distinct. Establish a trusted timestamp floor from the
   relevant installed/public release evidence, validate generated board AVB
   definitions, and verify actual candidate timestamps/rollback indices at the
   existing artifact boundaries. Do not use the synthetic counter as epoch,
   lower rollback indices, request downgrade/wipe or reuse unsigned failed-run
   outputs. Carry source pins/checker identity into the migration evidence.

The exact remaining source dependency is the durable counter/epoch and recovery
record contract, together with the missing device-specific rollback-definition
audit. No active allocator switch is safe to claim from the current bounded
probes alone. No counter binding or migration record has been invented here.
These can be prepared independently of #592; activation still needs the real
dedicated capacity, admitted inputs/baseline, and applicable runtime authority.

Primary GitHub references: [default variables](https://docs.github.com/en/actions/reference/workflows-and-actions/variables#default-environment-variables),
[workflow-run metadata](https://docs.github.com/en/rest/actions/workflow-runs#get-a-workflow-run).
No workflow metadata access, new token scope, storage or access operation is
added by this proposal.

## Reproduction and limits

Run `python3 scripts/tests/test_mustang_edge_gate.py NumberingCompatibilityTest -v`
for the focused evidence, or the already registered complete Mustang edge suite.
It invokes the available C++ compiler and Java compiler module in temporary
directories, with controlled platform stand-ins. Missing compilers produce an
explicit skip. No reservation, real build ID, native OS/app build, signing,
publication, cloud operation, held artifact retrieval or phone change occurs.

Source and protocol tests are evidence for the listed boundaries. They do not
prove a full Android build, every native/vendor consumer, real image contents,
AVB/bootloader compatibility, installation or phone acceptance.
