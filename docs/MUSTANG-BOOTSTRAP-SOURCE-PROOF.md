# Mustang bootstrap source-proof checks

The source-only adapter in `scripts/mustang_bootstrap_proof.py` checks a
candidate source selection against pinned Git objects and prepared payload
receipts. Offline tests use synthetic local repositories. The bound full
producer calls the collector and copy verifier inside its existing native job,
after runtime admission. Native attempts and their actual refusals are recorded
below and in os/build-only failure evidence; a successful native build, signing
and publication remain unestablished. The proof CLI retains no execution switch:

```sh
python3 scripts/mustang_bootstrap_proof.py
```

That command prints an inactive plan with `execute`, `runtimeWired`,
`readyForControlledStart`, `fullBootstrapVerified`, `pristine` and
`otaAdmitted` all false. There is no controller, credential, provisioning,
allocation, signing, publication or phone action in the adapter.

## Candidate checks

`inspect_source` is the offline-testable core. It parses one bounded resolved
manifest with explicit SHA-1 project revisions and manifest link/copy files.
It verifies each project root, object format and HEAD. The local Git reader
uses fixed metadata commands only: `rev-parse`, raw `cat-file commit`, and
NUL-delimited recursive `ls-tree` including trees. It hashes the raw commit
back to the pin and reconstructs every tree's serialized bytes and object ID.
Missing or altered object data, unsupported modes, duplicate paths and
unresolved manifest directives refuse. The index is not trusted or read.

For prepared-source projects, authenticated mode160000 gitlinks are treated as
boundaries, consistent with the [Git format](https://git-scm.com/docs/git-fast-import#_filemodify).
A populated child must be explicitly pinned by the resolved manifest to that
same commit and independently pass the ordinary project/byte checks. Otherwise
a selected gitlink directory must be empty (or absent); hidden metadata and
unpinned contents refuse. Parent tree reconstruction still binds the gitlink
commit ID. The generic reader and workflow-checkout observer continue refusing
gitlinks by default. No submodule is initialized or fetched.

Full run42588478372 passed runtime binding, host/baseline/app checks, then
reported unsupported-git-tree-mode in source observation before copy/allocation/
Make. Cleanup succeeded and the exact VM was independently observed stopped.
This change adds the missing standard gitlink policy; the next native run must
establish its actual source result. The old error did not include mode/path, so
it is not represented as a recovered per-project receipt.

Git runs with an explicit environment that excludes inherited credentials,
injected Git paths, shell initialization and library overrides. System/global
configuration, replacement objects, optional locks and lazy fetching are
disabled. No transport is allowed. The reader never invokes status, diff,
checkout, filters, source hooks or source programs. Each Git read has byte and
time limits, suppressed stderr and its own process group; that group is ended
on completion or refusal. See Git's documentation for the
[environment controls](https://git-scm.com/docs/git#_environment_variables) and
[tree output format](https://git-scm.com/docs/git-ls-tree#_output_format).

The adapter calls the existing shared `source_selection` mechanism with a
bounded descriptor-based planner. It retains the committed copy exclusions
and explicit dependency includes. Resource paths and the supported exclusion
contract are checked before Git/source reads; the policy is captured once from
a bounded checkout file, with no fallback pointer. Unknown selected files or directories refuse
before their payloads are opened. It handles manifest-created links and copies,
checks raw link text and file bytes against Git blob IDs, and verifies Git's
owner-executable bit. Link components are resolved in filesystem order within
the selection boundary; intermediate escapes, cycles and links into existing
excluded paths refuse. Excluded caches require their empty tracked prune marker
and the existing build-definition reference checks. Excluded content is not
read or claimed verified.

Prepared Mustang vendor, dependency modules, TypeScript source/loader, product
makefile, revision marker and the complete host distribution are checked against
their recorded catalogs using the measured selection. Other device output is
not introduced as a generated input. Project HEADs and selected namespace
metadata are checked again at the end. A changed inode, size, mode, timestamp,
or directory namespace refuses. This is a bounded observation, not an atomic
filesystem snapshot or a guarantee about a later source copy.

`observe_prepared` is an **unwired library adapter**. It takes paths from the
dedicated lane, holds the existing read-only canonical lock, validates the
committed successful vendor/host bindings, and matches their actual on-disk
receipt bytes before calling the core. It records the builder/run as declared
metadata. It does not establish the guest, filesystem UUID or run identity.
The [full-run collector](MUSTANG-BOOTSTRAP-COLLECTOR.md) integrates the candidate
with guarded guest checks, immutable receipts and actual copied-byte verification.
Its tests use offline fixtures; no live run has been dispatched by this change. The collector calls the
internal already-locked reader so the same lock spans the entire observation.

## Limits and remaining proof

The ceilings are 7,200 seconds, 4,000,000 selected/expected entries and 150 GiB
of source-data reads. Git output is bounded to 128 MiB per command and 512 MiB
in total, commit data to 4 MiB, each Git command to 60 seconds, and manifest,
recipe and policy inputs to 4 MiB.
Build-definition scans have a 16 MiB per-file limit. Smaller bounds may be used.
Symlink text reads, including intermediate link resolution, count toward the
source-data byte limit. A collector can pass an earlier overall deadline;
the source reader cannot restart that time budget.
Run42592596300 reached the original2,000,000-entry ceiling at
toolchain/pgo-profiles before copy/allocation/Make. The entry ceiling alone is
now4,000,000; Compute metadata independently confirms the existing n2-highmem-32
profile has32vCPU and262144MiB RAM. This is configured capacity, not measured
process memory. No VM/resource or other reader budget is changed. Tree-bound
refusals now include observed/allowed counts, and successful receipts include
expectedEntryCount.
Raising these ceilings requires a source change. These are proposal ceilings,
not measurements of Mustang's actual tree. Deadlines are checked between reads;
they do not make a blocked kernel filesystem operation cancellable.

A successful candidate reports selected-byte/catalog hashes and counts. It
still sets `guestIdentityObserved`, `dataFilesystemIdentityObserved`,
`fullBootstrapVerified`, `pristine`, `pristineRuntimeAttestation`,
`readyForControlledStart` and `otaAdmitted` to **false**. The core also leaves
prepared-receipt provenance false; only the library adapter's successful
on-disk receipt binding sets that narrower field true. Neither outcome admits
a source pin to the live full-build guard.

A separately authorized integration must establish the actual dedicated guest,
mount/UUID, clean captured workflow checkout, run identity, tool bindings,
space/reserve and lifecycle bounds, then perform the actual source observation.
Any resulting full-bootstrap contract needs its own review. No such evidence
is fabricated here. Source-copy identity must also be checked when that copy
is made; a prior observation is not evidence for changed or later bytes.

This work does not need the historical installer or old target-files. It does
not remeasure the old Updater, its APK signer or installed-device compatibility.
The existing candidate-continuity validator remains a separate source contract.
App-owner drafts, numbering/signing operations, the historical installer-storage
denial and the group-6 signer metadata 403 remain untouched.

Run42594685959 returned collector-check-failed in source observation, before
copy/allocation/Make; the old helper omitted the exception category/location.
Full-run failures now emit only type, numeric errno and bounded repository code
frames, with no exception text, locals, source text or URLs. Pinned project
directory failures also identify the committed manifest path.
The source planner now permits an absent excluded unpacked-cache directory; its
parent and an existing directory must remain plain paths, and the pinned empty
prune marker remains mandatory. Offline absence/symlink/marker regressions
establish this correction; the exact old native exception is not inferred.

The shared copier uses the same absent-cache rule, so source observation and
construction cannot disagree on this optional directory. An integrated fixture
runs observation, actual shared copy and copied-byte readback with the cache
absent. Observation errors remain refusals; only FileNotFoundError means absent.

Run42600298676 exposed unclassified-selected-file before source copy/allocation/
Make. The scanner now records up to32 bounded sibling metadata paths on that
refusal. Unknown payloads are never opened and unknown directories are not
traversed; the same refusal remains mandatory. Paths are canonical relative
ASCII metadata, with URLs/absolute/escape paths rejected. No unknown file is
excluded, moved, deleted, trusted or compiled by this diagnostic change.

## Resolved source-sync manifest

Run42604053622 identified `resolved-manifest.xml` as the refused root file
before copy, allocation or Make. The existing source-sync recipe writes this
file with `repo manifest -r`; its committed receipt records the same SHA-256 as
`os/pinned-manifest.xml`. The source observer now classifies only that exact
root name by the captured manifest's exact blob bytes. The file is required,
regular and nonexecutable; modified bytes, links or absence refuse. Ordinary
namespace readback and the shared copier's actual inventory verification cover
it. Parsed project equivalence alone cannot admit different manifest bytes.
Other unclassified names still refuse before their contents are opened.

Run42605954394 reached the pinned-file comparison after its source walk and
refused `selected-pinned-blob-or-mode-differs`, before copy/allocation/Make.
Its old diagnostic omitted the affected path and subtype. The same comparison
now also runs immediately after each pinned file/link is measured, reports a
bounded relative path and one of `type`, `blob` or `mode`, and retains the final
comparison and namespace readback. It does not print payloads, raw exceptions
or source text. The affected path and correction remain unclaimed until actual
native evidence is available.

## Pinned ANGLE checkout line endings

Run42611455205 identified a blob mismatch at
`external/angle/scripts/trigger.bat`, before copy/allocation/Make. The
[pinned ANGLE attributes](https://android.googlesource.com/platform/external/angle/+/1f4500d3906c24de59eec5b9cda17a2ad2a8ba2d/.gitattributes)
use `text=auto` and explicit `eol=crlf` rules. Git documents that checkout
[converts LF to CRLF](https://git-scm.com/docs/gitattributes#_eol). This is a
source-model correction, not a claim that native line endings were separately
read or that the next observation has passed.

The initial #721 model recognized only ANGLE's exact root attribute blob.
Run42614456651 then identified `external/bc/vs/bc.sln`, whose
[pinned attributes](https://android.googlesource.com/platform/external/bc/+/683fc6fd48adab655b30f96ffdb931ba192047cb/.gitattributes)
also declare CRLF. Both native refusals preceded copy/allocation/Make; no
separate native line-ending dump or complete candidate is claimed.

The current model evaluates each pinned project's applicable attribute hierarchy
with Git's [check-attr](https://git-scm.com/docs/git-check-attr), in private
exclusive scratch metadata. Only fixed HEAD/config and independently hash-verified
raw pinned .gitattributes blobs are written there. Source config, info attributes,
index, hooks, filters, global/system attributes, credentials and transport are
not used. Nested rules and macros are evaluated by Git rather than a custom
pattern matcher. Source files are never rewritten. Owned scratch is removed
on success or refusal; no existing directory is adopted or deleted.

Only explicit eol=crlf with supported text semantics can derive checkout bytes;
filter, ident, encoding and legacy crlf transforms remain unsupported. A raw blob
is read by ID and hashed back to its pin. Explicit text converts naked LF while
preserving existing CRLF. text=auto additionally retains existing CR/CRLF and
binary blobs using Git's printable/nonprintable/NUL/EOF heuristic. Only the
exact derived blob identity can match the measured worktree. Changed, mixed or unattributed
bytes still refuse; raw pinned identity remains valid. No Git --filters,
--textconv, checkout or source program is invoked.

Raw blobs retain the8MiB per-object and existing aggregate/time limits. Attribute
blobs are at most1MiB; private scratch is capped at1024projects,4096files,
8192directories and16MiB of file payload, including fixed metadata. Attribute
query output is capped at128KiB within the existing Git budget; each NUL-delimited
path input is at most4096bytes. These are engineering limits, not capacity
measurements. Receipts report limits and charged usage.

Conversion count/inventory digest bind original and checkout blob identities plus
the pinned attribute binding. Actual checkout bytes stay in the selected SHA-256
inventory and pass namespace and copied-byte readback. Offline tests use real Git
checkout for ANGLE, BC and macro/override fixtures, then exercise the full collector
and shared copier. Negative cases cover changed/mixed/unattributed bytes, pinned
filter refusal, corrupted objects, bounds, ignored source info/config overrides,
non-execution of configured filters and scratch cleanup on success/refusal.

Run42618172377 refused `external/pigweed/bootstrap.bat` under the initial
conservative text subset, before copy/allocation/Make. Its pinned blob contains
two ESC bytes and its
[pinned rule](https://android.googlesource.com/platform/external/pigweed/+/db5cb0db8513b2598ae7c1fa8a6441ff7f8808b5/.gitattributes)
is explicit `text eol=crlf`. The refusal is reproducible: the old subset rejected
ESC even though Git permits it, and explicit text should not use the auto binary
heuristic. The corrected model follows Git's
[conversion implementation](https://github.com/git/git/blob/master/convert.c)
(gather_stats, convert_is_binary and will_convert_lf_to_crlf). Real Git checkouts
from pinned raw objects independently verify explicit/auto behavior with ESC,
EOF, NUL/binary data, mixed endings, bare CR and binary-threshold cases. The
full collector/copier/readback fixture includes the explicit-text ESC case.
This changes only the authenticated blob-to-checkout model, never normalizes
arbitrary worktree bytes, and does not claim a successful native candidate yet.
