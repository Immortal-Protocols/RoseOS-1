# Inactive Mustang bootstrap source-proof adapter

The source-only adapter in `scripts/mustang_bootstrap_proof.py` checks a
candidate source selection against pinned Git objects and prepared payload
receipts. Its implementation and tests use synthetic local repositories.
**No observation of the dedicated VM has been performed by this change.**
The existing runtime remains unbound and still references the source-only
sync evidence. No workflow invokes the adapter, and the CLI has no execution
switch:

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
Missing or altered object data, unsupported gitlinks, duplicate paths and
unresolved manifest directives refuse. The index is not trusted or read.

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
and explicit dependency includes. Unknown selected files or directories refuse
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
There is no receipt writer or live dispatch path.

## Limits and remaining proof

The ceilings are 7,200 seconds, 2,000,000 selected/expected entries and 150 GiB
of source-data reads. Git output is bounded to 128 MiB per command and 512 MiB
in total, commit data to 4 MiB, each Git command to 60 seconds, and manifest/recipe inputs to 4 MiB.
Build-definition scans have a 16 MiB per-file limit. Smaller bounds may be used;
raising these ceilings requires a source change. These are proposal ceilings,
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
