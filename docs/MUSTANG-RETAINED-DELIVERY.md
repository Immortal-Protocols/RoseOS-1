# Dedicated retained-output delivery

This is source integration for one producer, not a dispatch or a new OTA claim.
PR #725 fixes the canonical lock selection. The dedicated workflow's optional
`retained_compile_run` accepts only `42664204892`; its empty default preserves
the existing full preparation/allocation/compile/sign/publication path.

Producer `42664204892`, attempt 1, captured source
`4e6d35cd4ecc249a0c133c3bf5a7a536dd3be49b`, completed unsigned compilation on
the dedicated Mustang VM. Collection then failed while opening the canonical
lock with the legacy write-open helper. Signing and publication were skipped.
Cleanup succeeded and the numeric VM was independently observed stopped with
no JIT metadata. The original workflow remains failed.

The retained contract pins receipt artifact `13708411788`, 30,778 bytes,
SHA-256 `0a35e679c31667151814357b9e1ba5a11c84b33035ac178aaa1159dba355570a`.
The archive was subsequently downloaded through the authorized GitHub connector;
its byte count, SHA-256, actual receipt cohort and authentication contract with
observed producer metadata passed. It confirms Make=0, build `2611091700`,
compiled timestamp `1794248529`, and complete declared output identities. The
actual source-bound evidence is recorded in
`os/build-only/mustang-full-run-42664204892.json`. This is receipt-byte evidence,
not a new observation or rehash of the retained native files/journal. Future
execution must reauthenticate the archive and rehash the native bytes;
unavailable, denied or differing evidence refuses without retry or an alternate
route. No held resource was retried; historical denials stay held.

## Producer and delivery remain distinct

Before a host start, the hosted guard requires the exact completed original
run/source/workflow/repository/attempt, successful compilation step, failed
collection step, successful owned cleanup, skipped signer/publisher and the
exact live artifact metadata and archive digest. Its complete receipt cohort
must show successful integration/Make, complete actual device/build/time,
the recorded collection-lock fault, the original allocation and the currently
selected immutable signed-app export. A cancelled/partial/arbitrary failed
producer cannot be selected.

The new source must descend from the producer and change only the reviewed
delivery implementation, its tests/docs and the observed failure record.
Changes to OS/app source, manifest, runtime/input records, clock policy,
tool pins or allocation authority refuse. This complements the existing
current app-source freshness and baseline admission.

The new run uses the same exclusive dedicated queue, controller, group-5
ephemeral runner, numeric VM/disk and mounted-filesystem checks, source-checkout
binding, nonroot identity, capacity floors and four-hour STOP fallback.
It authenticates and copies only the small original public receipts into a
new attempt directory. Under the existing read-only canonical flock, it must
match the genuine original receipt bytes, retained output hashes/identities
and the existing current journal head. Missing/corrupt/superseded journal or
changed output refuses without repair. No Make, reservation, journal reset,
original-receipt edit, permission change or prior failed-directory resume exists.

New collection computes the unsigned raised-edge proof with the **original
compile source/run** and the **new delivery source/run**. The bundle preserves
both identities, original native observation/copy proofs, original allocation,
new observed guest/ownership proof and exact original archive receipt hashes.
The new guest proof explicitly does not claim a fresh pristine source walk or
another compile.

The hosted signer independently authenticates the pinned old artifact/run and
the new in-progress main/manual run and completed guard/controller/enrollment/
builder jobs. It matches old output/source/allocation proofs to the original
archive and new guest ownership to the current controller. Normal same-run
admission is unchanged. Qualified tools, actual hosted capacity/free-space,
byte preflight and unsigned raised-edge checks still precede OIDC/secret access.

Signed staging and publication keep the original compile build/timestamp,
candidate source and compile run separate from the new recipe/delivery source
and run. Both raised-edge proofs and the original clock proof survive the public
receipt. Existing signature/updater checks, current publication ordering,
metadata-last behavior, rollback preservation and owned STOP/metadata/runner
cleanup remain. No new IAM, credential, provisioning or signing-key generation
is part of this source change. Physical-device compatibility is not claimed.

## Verification and admission limits

Offline tests use a real local two-reservation journal, real small ZIP/hash/DEX
collection and controlled API/archive, guest, signer/verifier stand-ins. They
exercise distinct producer/delivery identities through collection, bundle,
hosted admission and public staging, plus wrong source/outcome/cleanup, archive
corruption, app drift, missing/corrupt/superseded state, changed output, foreign
owner/guest and denied-read refusal. They are not live artifact/signing proof.

No runtime was dispatched to implement these tests. Before any future execution,
the merged source, artifact availability, unchanged input/source compatibility,
exclusive queue/ownership, stopped dedicated VM/disk, current journal and
publication floor still need actual admission within the applicable authority.
The source-only preparation instruction is not runtime dispatch authorization.
