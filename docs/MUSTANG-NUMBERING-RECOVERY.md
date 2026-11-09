# Mustang numbering recovery: bounded source evidence

This increment adds offline compatibility evidence and a migration proposal.
It does **not** change allocation, choose or reserve a real version, reconcile
the lost allocation, enable the dedicated lane, or authorize runtime actions.
The existing allocation guard still refuses an unresolved state. Target-files
and factory-image baseline alternatives from #593 remain intact. PR #592's
shared private-input verifier is a separate dependency owned by Release.

## Current first-OTA route: UTC date and hour

Nathan selected the existing local date/hour approach instead of a bucket.
`scripts/mustang_clock.py` is integrated with the dedicated guard, producer,
collection, hosted signing and public staging. The runtime remains unbound;
this source change performs no initialization, allocation, dispatch or IAM write.
The GCS and synthetic counter/Git pilots below remain inactive.

The ten-digit OS incremental ID is **YYMMDDHH00**, from the authenticated Actions
run's UTC creation hour. For example, `2611090400` denotes the 04:00 UTC dispatch
hour on November 9, 2026; this is an example, not an issued build. Exactly one
reservation is available per dispatch hour. An occupied hour refuses; another
fresh run in a later hour can allocate after a failed build. Reruns do not allocate.
2026..2099 is the explicit supported year range; century rollover refuses.

The suffix `00` is the collision boundary: the captured normal legacy allocator
produces Gregorian YYYYMMDD plus sequences **01..99**, so it cannot produce a
value ending in 00. The lost exact allocation stays unknown, and its record is
never edited into an empty consumed list or marked reconciled. This proves
exclusion from that captured generator, not arbitrary out-of-contract host edits.
The new value is not claimed larger than every possible unpublished unknown
legacy value. It must exceed every actually observed release/reservation/path
and every prior value in the new series, and its real timestamp must exceed
known public/installed and local timestamp floors.

The allocator reuses the existing dedicated read-only canonical lock, public
release listing and native persistent disk. It independently reads published
`mustang-stable` assets against their API digest to establish the current public
timestamp high-water mark. Actual build time remains a separate positive Unix
timestamp, checked again against the compiled and signed output; it is not the
incremental ID. Guest/source/copy checks still precede allocation and Make.
No cloud write credential or signer token reaches the native allocator.

`rose-actions/mustang-clock-hour00-v1` holds a write-once origin, immutable
reservations and an atomically replaced head. Files and the containing directory
are fsynced and read back before exposing an ID. A crash between reservation and
head update leaves consumption uncertain and refuses another allocation. A
consumed reservation stays consumed even when the build fails or nothing is
published. Unknown files, broken chains, missing heads and linked paths refuse.

One authenticated dedicated workflow number may initialize this new local series:
workflow ID 408460629, run number 32. Full runs42576627118 / number23 and
42581537518 / number24 failed before produce (missing gh and CLI HTTPError,
respectively). Run42584970482 / number25 passed host/baseline/fresh-app admission, then refused
bootstrap source observation before copy/allocation/Make. Run42588478372 /
number26 passed the corrected binding and refused unsupported-git-tree-mode at
the prepared-source stage. Run42592596300 / number27 then reached the source
entry budget at toolchain/pgo-profiles. Run42594685959 / number28 reached
a generic source helper failure after the larger entry budget. All six skipped
copy/allocation/Make. Run42600298676 / number29 then refused an unclassified
selected file; no copy/allocation/Make began. Run42604053622 / number30
identified that file as resolved-manifest.xml, again before copy/allocation/Make.
Run42605954394 / number31 then refused a pinned blob-or-mode mismatch,
also before copy/allocation/Make; its old diagnostic omitted the path.
Completed cleanup and direct numeric stopped-VM observations are recorded in their os/build-only failure
records. No on-disk journal absence is asserted and no journal is reset.
This is an initialization fence, not the OS ID or
version authority. Later runs cannot recreate an absent ledger; loss requires
owner-led reconciliation. Attempt >1 refuses. If a preparation-only run consumes
the initialization window first, update the source pin from its actual verified
scope before first initialization; never change an already initialized epoch to
hide missing state. An existing origin binds the policy hash, VM and disk UUID.
This adds no infrastructure or administrative approval operation.

The same allocation record is bound into the collected unsigned output, the
protected hosted signing bundle, authenticated Actions metadata and the staged
OTA receipt. Signer and publisher admission require its actual build/time/run/SHA.
Unbound app, capacity/tool, runtime and publication inputs are still enforced.
Fresh Frankel export versionCodes need no schema change; the Mustang owner repins
the truthful immutable export once its Release owner supplies it. Held app PRs
are not modified by this route.

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
| Mustang board AVB default | The pinned Mustang skeleton includes its device base, Laguna common and `BoardConfigMainlineCommon.mk`, which assigns `BOARD_AVB_ROLLBACK_INDEX := $(PLATFORM_SECURITY_PATCH_TIMESTAMP)`. | Eight complete, hash/pin-checked board-source files and include traversal; execute the exact assignment in a small Make probe. Changing only the synthetic ID does not change this default. Actual final board evaluation, image indices and device rollback floors remain unproved. |

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

### Implemented inactive recovery contract

`scripts/mustang_numbering.py` implements the proposal/commit/recovery record
contract without a network, storage or allocator adapter. Its policy in
`os/build-only/mustang-numbering.json` is **unbound**: epoch, authority and real
timestamp floor are null. No existing allocator, producer, workflow, publication
or runtime admission imports it. Direct CLI execution refuses. No live version
is assigned by this source increment.

The contract requires an independently established policy/epoch, an external
authority's **fresh authenticated head**, and its complete immutable journal.
The policy pins the legacy source proof and retains the failed allocation as
unknown. Records hash-bind policy, epoch, exact dedicated run/source, counter,
previous receipt, derived ten-digit identity and a distinct explicit timestamp.
Both counters and actual timestamps must increase; the counter never supplies
an epoch time. Namespace exhaustion, duplicate runs, reruns and mismatched
receipts refuse.

The future adapter's protocol is: read the authoritative head/journal, call
`propose()`, atomically compare-and-swap that exact head while durably recording
the reservation, freshly read it back, then call `confirm()` before exposing the
ID to a producer. A proposal is **not** consumption. An interrupted/uncertain
commit requires readback; a confirmed reservation stays consumed even if the
caller never received its response or no build happened. Confirming an already
committed proposal is idempotent; proposing another counter for the same run
refuses.

`recovery_suffix()` accepts only an exact local prefix of the complete authority
journal and returns the already consumed receipts missing from that replica.
It neither writes files nor allocates another counter. A stale local backup
can be replayed from a fresh independent head. Missing authority state,
wrong/lost epoch, forward/forked replicas and incomplete journals fail closed.
There is no initialization, epoch rollover, automatic empty-ledger replacement
or backup-as-authority fallback.

Checksums cannot establish authentication or freshness: a jointly replayed old
head and old journal remain internally consistent. A regression test explicitly
demonstrates this limit. **No production adapter exists**, and a caller cannot
treat a locally manufactured anchor or a `fresh: true` assertion as evidence.
The fixture authority models atomic CAS; replica tests use the existing
`release_receipts.write_once()` primitive, including concurrent creation and an
uncertain error after linking. They do not prove a deployed backend's atomicity,
durability, independent storage, power-loss behavior or disaster recovery.

The concrete remaining dependency is selection and verification of the actual
independent authority and authenticated CAS/readback adapter, including no-reuse
recovery after loss of the authority itself. Before integration it must reconcile
actual consumed IDs/public timestamps and bind an evidenced epoch and timestamp
floor; none is guessed here. Only then can the producer and receipt chain be
changed together under the original fail-closed migration proposal. This is a
technical dependency, not another approval gate for ordinary source work.

### AVB scope now established and still missing

`os/tests/fixtures/mustang-numbering/avb/` contains the reviewed complete board
sources. The include graph reaches the security-patch timestamp default; the
symbolic Make probe shows that fixed default stays fixed across both synthetic
ID boundaries and changes when its timestamp input changes. This narrows the
earlier missing board-source evidence; it does not establish every final
partition's rollback value or all subsequent build-variable overrides.

Still required from an admitted actual candidate/baseline: authenticated vbmeta
and chained partition indices/locations, actual security-patch and payload
timestamps, firmware versions/rollback constraints, and comparison with trusted
installed or relevant previously published state. Device rollback floors and
bootloader behavior are not observable from these templates. Neither this
audit nor its Make probe verifies AVB cryptography, images or a phone. No held
installer or image was retrieved. #593's alternative baseline paths and #592's
separately owned shared input-verifier work remain unchanged.

Primary GitHub references: [default variables](https://docs.github.com/en/actions/reference/workflows-and-actions/variables#default-environment-variables),
[workflow-run metadata](https://docs.github.com/en/rest/actions/workflow-runs#get-a-workflow-run).
No workflow metadata access, new token scope, storage or access operation is
added by this proposal.

## Reproduction and limits

### Inactive raw Git-object boundary

`scripts/mustang_git_objects.py` adds a pure, read-only codec. It has no transport,
ref writer, checkpoint provider, credentials, initialization, allocation or
production integration. The committed numbering policy stays unbound and CLI
execution refuses. This is source preparation for the Git authority candidate,
not evidence that such an authority or trustworthy provider exists.

The codec accepts only SHA-1 Git object IDs, raw `(kind, bytes)` objects from an
injected bounded getter, and explicit commit/total-byte limits. It checks Git's
`kind length NUL bytes` identity and a deliberately narrow journal format:

- Each tree contains exactly one regular `100644 journal.json` entry. Symlinks,
  executable files, extra entries, paths and unsupported object kinds refuse.
- Each blob is canonical JSON with schema `roseos.mustang-git-event.v1`, the
  exact policy SHA-256 and one event. Genesis alone has a null event.
- Genesis is a root commit; every later commit has exactly one parent. The
  supported headers are tree, optional parent, author and committer, followed
  by the fixed `Mustang numbering journal` message and newline. Extra headers,
  merges and partial history refuse. Author/committer metadata is syntax only,
  not authenticated identity or signature evidence.

Complete traversal reuses `mustang_numbering.expected_anchor()` to validate
counter, run/source, policy, epoch, duplicate-event and timestamp invariants.
The Git commit OIDs and SHA-256 journal digest remain separate. `History` is
decoded data, with immutable bytes and copy-returning accessors; it is not a
committed-consumption or trusted-freshness receipt. Unsupported Git formats fail
closed rather than falling back to an ordinary source tree.

`Checkpoint` is also only data: a Git OID and canonical anchor bytes. Its type,
hashes and position in valid ancestry cannot establish provenance or freshness.
The `CheckpointVerifier` interface requires separately trusted code to establish
those properties, including authority deletion/force-push uncertainty. There is
**no implementation or selected trust source**. `confirm_from_objects()` refuses
when no verifier is supplied, verifies the checkpoint's exact ancestor/prefix,
invokes that independent verifier, then reuses the existing exact-event
confirmation. Unknown provenance/freshness must raise; transport-supplied flags
are not accepted as a result. An arbitrary callback cannot become trusted merely
by implementing this interface; provider selection and verification remain absent.

Tests supply only in-memory raw-object getters and independently controlled
fixture verifiers. Native `git hash-object` cross-checks run without `-w` or ref
operations. Cases cover object corruption and bounds, missing/genesis/merge/tree
errors, stale or forked checkpoints, jointly stale snapshots, changed policy,
duplicate events, exact confirmation after a lost reply and advanced head, and
verifier/getter failure. They prove codec/protocol mechanics, not external
authentication, freshness, protected refs or operational recovery guarantees.

A future reader must enforce byte limits while obtaining objects; checking bytes
after return cannot prove its transport's memory bound. A future CAS writer
remains unimplemented. Production
binding still requires actual authority identity, permitted writer/reader scope,
ref protection evidence and a verified independent recovery checkpoint source.

### Completed synthetic pilot and ambiguous reply handling

The authorized `20261106-01` pilot used the existing `nskpaun` CLI route in
RoseOS-1. It staged four synthetic commits with no build/allocation records and
left the approved pilot branch/checkpoint tags in place. Successful atomic
head/checkpoint advance, matching no-op, later advance and exact-event ancestry
reconciliation after an application-level lost-reply simulation passed.

The stale-beforeOid, checkpoint-collision and mismatched-no-op requests each
returned a **generic GitHub query-execution error**, not a precise conditional
conflict code. Origin ref readback was unchanged after each, and exact synthetic
event bytes were checked before continuing with distinct tests. No mutation was
resubmitted. The saved replies are replayed by
`os/tests/fixtures/mustang-numbering/pilot-error-replies.json`; this source work
does not query or mutate the completed pilot again.
Replay tests pair those reply bytes with controlled synthetic histories,
including a committed event, to exercise ambiguity. They do not recast the
actual negative pilot outcomes as committed writes.

Observed final pilot refs (mechanics evidence, not production bindings):

- `refs/heads/mustang-checkpoint-pilot-20261106-01` and checkpoint tag `-2`:
  `06085984fd1e11edec8a3a8a09777d481c25f8a2` (`event-2`).
- `refs/tags/mustang-checkpoint-pilot-20261106-01-1`:
  `c2981579ce2c7ee099ce648a67d1f54e1853227d` (`event-1`).
- Verified final ancestry: event-2, event-1, then genesis
  `f1b983dcc6aa994d053a05afb40ec04cf0e90858`.

No Actions runs were observed through 2026-11-06T16:50:22Z; main and source files
were unchanged. External webhooks and billing were not verified. The pilot did
not establish ref protections, independent freshness, disaster recovery or
AVB/device compatibility. Its write capability was observed only within the
approved synthetic scope; production authorization was not inferred.

`reconcile_ref_reply()` now handles the resulting source-level uncertainty. It
reuses the raw-object codec, independent checkpoint-verifier requirement and
existing numbering invariants. It confirms only when the **exact attempted Git
OID** and the exact proposal event occupy the expected slot in verified complete
history, including when a later head has advanced. A transport success shape,
generic error, lost reply, unchanged head, competing event or logically identical
event under a different Git OID cannot alone confirm the attempted write.

Absent exact confirmation, `UnresolvedRefWrite` explicitly leaves consumption
unresolved and permits neither automatic retry nor a new allocation/reuse. It
does not assert nonconsumption. `validate_proposal()` factors the existing prefix
and proposal checks without asserting a reservation happened. Response categories
are diagnostics only; no error is promoted to a known conditional-conflict code.
This is an inactive read-only step, with no transport/writer or production caller.
Historical consumption confirmation never authorizes a downstream action or
continuing an access-denied operation.

Actual authority/protection/recovery choices are still required for a real
checkpoint provider. The existing unbound policy, allocation refusal and
publication controls remain unchanged; no new approval gate is added for source
work.

Run `python3 scripts/tests/test_mustang_edge_gate.py NumberingCompatibilityTest CounterRecoveryContractTest -v`
for the focused evidence/contract, or the already registered complete Mustang edge suite.
Run the same script with `GitJournalCodecTest -v` for the raw-object boundary.
It invokes the available C++ compiler and Java compiler module in temporary
directories, with controlled platform stand-ins. Missing compilers produce an
explicit skip. No reservation, real build ID, native OS/app build, signing,
publication, cloud operation, held artifact retrieval or phone change occurs.

Source and protocol tests are evidence for the listed boundaries. They do not
prove a full Android build, every native/vendor consumer, real image contents,
AVB/bootloader compatibility, installation or phone acceptance.
