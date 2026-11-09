# Mustang full-run source and cleanup integration

`scripts/mustang_bootstrap_collector.py` connects the selected-source candidate
adapter to guest/disk checks, checkout verification, shared time limits and an
immutable attempt receipt. **This change performs no live observation.** Collector tests use synthetic Git
repositories and local response/process stand-ins. Full-path tests model native responses explicitly; these fixtures
are not live observations.

The default CLI prints a plan and cannot start a native observation:

```sh
python3 scripts/mustang_bootstrap_collector.py
```

The existing full producer in `mustang-pipeline.py` calls the collector directly
inside the `builder` job. There is no separate observation-only dispatch, input
or guest job. The CLI's explicit `lifecycle --execute` action runs only in the
GitHub-hosted full-run cleanup job and summarizes completed cleanup results.
It performs no cloud operation itself.

`os/build-only/mustang-full-run-source.json` defines the typed per-run source
contract. It explicitly records `liveEvidencePresent=false`; it is a plan,
not observed evidence. The runtime still points to the old sync record and
remains unbound. Selecting this contract in an eventual admitted runtime does
not bypass fresh observation: the producer must verify actual native bytes and
its isolated copy before lunch, allocation or Make. Other runtime/app/numbering,
signer and publication gates remain required before the workflow can start.

## Full-run context and source copy

The full producer requires this repository's exact manual main workflow,
attempt 1, the actual `builder` job, and every preparation-only input disabled.
The publication input keeps its existing meaning. It validates the hosted
controller's existing run-bound owned-start record without changing job identity
or minting another runner. The caller must be the unprivileged `mustang-runner`,
with bytecode writes disabled for the whole job. The supplied captured checkout
must equal `GITHUB_WORKSPACE` and be separate from native source and receipts.

The fresh collector receipt is reread against its hash before proceeding. An
incomplete, fixture, failed or foreign receipt stops the producer before source
copy or allocation. Its portable inventory normalizes only absolute in-tree
links that the existing copier legitimately rebases.

After the existing source copy and Git-metadata stage, `verify_copy` validates
the copier's retained selection record against the observed inventory, then
rehashes the actual copied files, modes and link text. It rejects missing,
unknown, changed or special entries; Git metadata is excluded. Its bounded
record parser handles the existing escaped tab/newline/backslash format and
non-UTF-8 filesystem names. Changed input between observation and copy, or a
changed destination after the copy's hash, refuses before lunch/allocation/Make.
The existing vendor consumer still checks both original and copied prepared
payloads. No source reset, old attempt adoption or other-device seed is added.

One existing read-only canonical lock is held across guest checks, the fresh
attempt claim, checkout verification, prepared-source observation, readback and
receipt writing. The lock is never created or opened for writing. Its inode and
metadata are checked again before publishing the receipt. A missing, held,
linked, changed or nonempty lock refuses. The core's internal already-locked
reader avoids trying to reacquire the collector's own exclusive lock.

The native probe used by the admitted full producer reads
only the fixed numeric link-local instance-ID endpoint and one fixed `findmnt`
command. Metadata requests disable proxies and redirects, require the Google
response header and bound the response. The mount command uses a clean
environment, bounded streamed output, a deadline and an owned process group.
Only that child group is ended on completion or refusal. Neither probe performs
login, cloud lifecycle operations, source execution or credential handling.

Guest checks compare the numeric instance ID and mounted UUID to the supplied
lane/runtime binding. They require the exact mount, ext4, a writable root of that
filesystem, matching major/minor device numbers and plain paths on the same
device for home, source, storage and captured checkout. Directory identities
and guest/disk identity are checked again after observation. Available space
must retain 64 MiB and eight inodes for the bounded receipt; this is **not** a
native-build reserve qualification. The passed lane/runtime documents must equal
their captured checkout bytes before the source candidate is read.

The checkout reader verifies the pinned raw commit and reconstructed Git trees,
then reads every tracked regular file and raw symlink text against its Git blob
and owner-executable bit. It rejects missing, additional and ignored paths,
special files, privileged modes and device crossings. Only the root `.git`
metadata entry is excluded. Checkout symlinks are hashed without following
their targets. Git status, index flags, filters, hooks and source programs are
not used. HEAD and namespace metadata are read back after source observation.

The prepared-source reader matches actual producer receipt bytes, selected
pinned source and prepared vendor/dependency distributions as described in
[the source-proof contract](MUSTANG-BOOTSTRAP-SOURCE-PROOF.md). Failed or
unexpected candidate contracts refuse. This observation is not an atomic
filesystem snapshot or proof about a later source copy.

## Limits and receipt lifecycle

Collector ceilings are 7,200 seconds overall, 100,000 checkout entries, 1 GiB
of checkout data and a 1 MiB receipt. Checkout Git output uses the existing
128 MiB per-command / 512 MiB aggregate ceilings. Native-source ceilings remain
2,000,000 entries, 150 GiB of data reads and their separately counted bounded
Git output. All source/checkout operations inherit the overall deadline; a
smaller source time budget can shorten it. Limits may be lowered, never raised
without a source change. They are proposal ceilings, not measured host usage.
Checks occur between reads; a blocked kernel filesystem operation is not made
cancellable, and a metadata socket read may finish after the overall deadline.
An expired operation cannot produce a successful collector result.

After the first guest checks, the collector exclusively creates
`rose-actions/mustang-bootstrap-observation-RUN_ID-1` with mode 0700. Any existing
entry refuses, including an empty directory or symlink. It never adopts,
resumes, deletes or resets an earlier attempt or native output.

Once claimed, ordinary check failures produce a sanitized structured failure
receipt. Arbitrary exceptions, stderr, input bytes and provider URLs are not
serialized. Failure before a safe claim returns no receipt. Interruption leaves
the claim intact and releases owned file descriptors and the canonical lock.
It does not fabricate a successful receipt or clean up retained data.

`bootstrap-observation-receipt.json` is written to an exclusively created mode
0600 temporary file, flushed, atomically linked without replacement, and read
back byte-for-byte. The returned descriptor includes its SHA-256 and byte count.
Directory descriptors and inode readbacks prevent adopting a replaced path.
Failure to write or verify a receipt returns `receiptWritten=false` and cannot
claim collector success; any partial or uncertain output is retained.

A successful fixture receipt says `observationKind=offline-fixture`, with
`guestChecksPassed` and `checkoutBytesVerified` describing those fixture checks.
It keeps actual guest/disk observation flags false. The native full-run probe sets those two narrower identity flags only after
both guest checks succeed.
All results retain `runIdentityObserved`, `fullBootstrapVerified`, `pristine`,
`pristineRuntimeAttestation`, `runtimeWired`, `readyForControlledStart`,
`otaAdmitted`, `signingStarted` and `publicationStarted` as false. The collector
does not own STOP; `authenticatedStop=false` and `hostedLifecyclePending=true`
make the missing outer lifecycle explicit. Its receipt alone is not a production
source proof or runtime-admission record.

## Signing bundle, run correlation and lifecycle

The dedicated signing bundle uses the already qualified retained otatools ZIP,
rehashing it against its committed qualification and full inventory. It packs
the pinned JDK tree from the verified source copy into the existing deterministic
canonical ZIP and checks that exact hash, size and member count. The release
script comes from the reviewed checkout. No archive member is executed by
bundling. Both expansion sizes are populated before capacity admission.

The bundle carries the native source receipt and copy-verification receipt. Its
SHA-256 is passed as a protected native-job output. The hosted signer matches
the downloaded metadata bytes with that output and correlates repository, main
workflow/source, attempt, owned controller record and completed native producer
through the fixed Actions run and attempt-jobs API reads. It requires the exact
group-5 producer and refuses another preparation scope. The actual signer job
must be in progress; the verifier does not pretend the overall run has finished.
Reads are bounded and a denial stops without an alternate endpoint or retry.
Source/tool pins, archive checks and measured capacity still precede signer login.

The hosted signer stages the verified full OTA itself, using the qualified
signature checker and APK tools, the existing v1 public identity, both real
raised-edge proofs and the explicitly selected candidate continuity contract.
It creates the four-file public asset set under `staged/`. The publisher verifies
that asset set before obtaining publisher credentials and retains the existing
metadata-last/Latest-preserving publication routine. Raw signed target-files
remain private in the workflow artifact. Same-filesystem links avoid extra
physical unsigned/OTA copies; the capacity model counts both tool extractions.
No successful sign or stage is inferred from fixture execution.

The existing cleanup retains independent owned START/STOP semantics. For full
runs it captures a structured STOP receipt with a fresh numeric-ID TERMINATED
readback, the metadata key's verified absence/removal and this run's group-5
cleanup result. It uploads only those public structured receipts. It never waits
for a new verifier job, so no cleanup dependency cycle is introduced. Native
collection continues to report `authenticatedStop=false`; completed-run/STOP
reconciliation is an outer evidence step after the workflow ends.

This source change performs no dispatch, credential creation, IAM mutation,
allocation, signing or publication. Runtime bindings and signing admission remain
unbound. An actual successful full run and its completed-run reconciliation are
still needed. The signer provider URI in source names the verified existing
`mustang-actions/providers/mustang-main`; its missing signer IAM binding requires
separate action-time approval. No existing legacy trust binding is removed.

The app owner must provide a fresh immutable export of backgroundcontrol, voice
and calculator with their existing signer, SDK 37, APK/idsig/receipt files,
background policy, allocation proof and producer-source freshness. A hosted
producer schema change must be coordinated with the consumer, which currently
requires a Frankel producer. Held app-owner drafts are untouched.

Numbering still needs an independently durable authority/epoch and authenticated
atomic issue/recovery semantics. The live producer still requires reconciled
allocation state; the lost legacy allocation is never guessed or changed to
known. No production allocation or obsolete pilot activation is performed here.

Neither the source gate nor creation of a new full payload needs the historical
installer or old target-files. Installed-device compatibility remains a separate
claim. The historical storage denial and GitHub group-6 administration-read
denial remain closed. Nathan's new read-only WIF viewer grant was used only for
permitted provider metadata inspection and is owned by Nathan, without expiration;
it is not removed by this implementation.
