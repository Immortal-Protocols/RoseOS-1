# Inactive Mustang bootstrap collector integration

`scripts/mustang_bootstrap_collector.py` connects the selected-source candidate
adapter to guest/disk checks, checkout verification, shared time limits and an
immutable attempt receipt. **This change performs no live observation.** All
collector execution tests use synthetic Git repositories and a probe explicitly
labelled `offline-fixture`. The native probe implementation is exercised only
with local response/process stand-ins.

The CLI prints an inactive plan. It accepts no execution switch, root override,
scope override or external probe configuration:

```sh
python3 scripts/mustang_bootstrap_collector.py
```

Only the offline CI suite invokes the collector test file. No dedicated runtime
workflow calls the library, and the proposed `bootstrap_observe_only` input and
`bootstrap-observe` job do not exist in the live workflow. No environment flag
can activate the CLI. Runtime configuration and bootstrap admission stay unbound.

## Collector contract

The unwired `collect` library entry point requires the dedicated lane's manual
main workflow, repository name and numeric ID, attempt 1, exact job and explicit
exclusive scope. Every other preparation/publication scope must be false or
absent; unknown inputs refuse. Captured source and workflow SHAs must agree.
The caller must be the unprivileged `mustang-runner`, with bytecode writes
disabled (`python3 -B` or `PYTHONDONTWRITEBYTECODE=1`). Its supplied checkout must
equal `GITHUB_WORKSPACE` and must be separate from the native source and receipt
storage. Environment checks validate workflow context; they are **not independent
authentication of the Actions run**, so `runIdentityObserved` stays false.

One existing read-only canonical lock is held across guest checks, the fresh
attempt claim, checkout verification, prepared-source observation, readback and
receipt writing. The lock is never created or opened for writing. Its inode and
metadata are checked again before publishing the receipt. A missing, held,
linked, changed or nonempty lock refuses. The core's internal already-locked
reader avoids trying to reacquire the collector's own exclusive lock.

The native probe, when eventually called by an authorized integration, reads
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
It keeps actual guest/disk observation flags false. A future native probe can
set those two narrower identity flags only after both guest checks succeed.
All results retain `runIdentityObserved`, `fullBootstrapVerified`, `pristine`,
`pristineRuntimeAttestation`, `runtimeWired`, `readyForControlledStart`,
`otaAdmitted`, `signingStarted` and `publicationStarted` as false. The collector
does not own STOP; `authenticatedStop=false` and `hostedLifecyclePending=true`
make the missing outer lifecycle explicit. Its receipt alone is not a production
source proof or runtime-admission record.

## Remaining activation work

A separately authorized integration still needs the hosted manual scope and
restricted runner admission, captured checkout with bytecode writes disabled,
owned START/JIT/STOP and cleanup, independently correlated run/receipt evidence,
and an actual successful dedicated-guest observation. No such operation is
performed or enabled here. Full-bootstrap and runtime tool/source bindings
require their own reviewed evidence, including verification when source is
actually copied. The collector neither allocates a build number nor admits
compilation, signing or publication.

This source slice is disjoint from app-owner drafts and numbering/signing work.
It needs no historical installer or old target-files and does not remeasure
installed-device compatibility. The historical artifact-storage denial and
group-6 signer metadata 403 remain held; no alternate read or access change is
part of this implementation.
