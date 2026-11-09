# Mustang bootstrap source-proof checks

The source-only adapter in `scripts/mustang_bootstrap_proof.py` checks a
candidate source selection against pinned Git objects and prepared payload
receipts. Its implementation and tests use synthetic local repositories.
**No observation of the dedicated VM has been performed by this change.**
The existing runtime remains unbound and still references the source-only
sync evidence. The full producer now calls the collector and copy verifier
inside its existing native job, after runtime admission. The proof CLI retains
no execution switch:

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
