# Dedicated retained-output delivery

## Space protection and the expanded data disk

Run 44 reached Ninja task170295 of170307 before the runner reported ENOSPC while
writing its diagnostic log. This is not completed compilation. The data disk was
still1500GiB; Make/collection completion and cache-hit statistics are unknown.
Owned STOP and metadata/runner cleanup succeeded. Keep the partial output,
successful run41 output, existing compiler cache and consumed allocation journal.

Disk protection now independently honors `sprint.reserveChecks=true`. The existing
300GiB reserve and finite source/metadata/lunch/compile bounds are checked before
each stage and sampled while copying/compiling. Initial guest admission requires
844GiB free for source, vendor, metadata, lunch, compile and reserve before the
long source walk starts. Reduced
target validation remains disabled; no artifact-validation policy is reintroduced.

Nathan proposed increasing the same SSD data disk from1500GiB to3000GiB. Cloud
preflight verified its unchanged numeric disk/VM identities and stopped VM.
`scripts/mustang-grow-data-filesystem.sh` prepares the ext4 expansion: it refuses
wrong VM, mount, UUID, attached device, partitioned device or block-device size,
then runs only bounded `resize2fs` and emits actual filesystem/inode readback.
It never formats, deletes build files or changes ownership/ACLs. The second owned maintenance operation completed: the same numeric disk is3000GiB,
ext4 reports3169514168320 filesystem bytes,1586187993088 available bytes and
196608000 total inodes. Fresh cloud readback confirms the VM stopped and startup/JIT
metadata absent. Exact readback is in `os/build-only/mustang-data-growth-20261111.json`. The historical1500GiB infrastructure record remains historical; runtime
can pin a separately verified expanded inode total without relaxing UUID/device identity.

## Persistent compiler cache

The full automatic dedicated route now enables ccache 4.12.2, pinned by archive
and executable SHA-256, under the existing Mustang data disk at
`roseos/rose-actions/compiler-cache-v1`. Its local object cache is capped at 20 GiB and is
outside the disposable runner workspace. No new host, IAM, credentials, sudo,
remote cache or disk purchase is needed. Existing compile/disk/time bounds apply.

Run 43 stopped before source copy/allocation/Make because its cache path used the
administrator-owned `roseos` parent. The cache now uses the runner's existing
writable `rose-actions` storage, alongside its attempt directories. No ownership,
ACL, sudo or parent-directory permission change is performed.

Each attempt still gets a fresh source tree and fresh `out-mustang-cache` output
directory. The stable relative output name, per-source base directory and debug
prefix map let matching C/C++ compiler results be reused across attempts. Soong's
pinned `cc/config/global.go` accepts `CC_WRAPPER`; the wrapper establishes cache
settings after Ninja's environment filtering. Compiler content, arguments and
headers remain checked, with no sloppy checks or hard-linked outputs. Java,
Soong/Ninja intermediates and entire completed build archives are not cached by
this layer. Unsupported compilations pass through ccache to the compiler.

Run 41 predates ccache, so its object files cannot populate ccache after the fact.
Its complete unsigned outputs and original allocation/journal remain untouched.
Run 44 populated the cache before its disk-space failure. Run 45 then completed
Make with 70,859 hits (70,777 direct and 82 preprocessed), zero cacheable misses
and 5,800 unsupported-option calls passed through to the compiler.
`compiler-cache.json`, the candidate receipt and the job log record before/after
statistics and deltas, including hits, misses and stored size. A cold or changed
input is not called a cache hit. Make still runs and the collected target ZIP
must contain the current allocation's actual device/build/timestamp before the
same automatic signing and publication stages can proceed.

References: [pinned Soong wrapper](https://github.com/GrapheneOS/platform_build_soong/blob/250710f7f87acfbb8e3c517d90abde8b4a08fe1e/cc/config/global.go),
[ccache 4.12.2](https://github.com/ccache/ccache/releases/tag/v4.12.2) and its bundled
manual, including compiler-content checks, path normalization and local size limits.

Run 45 (`43050571884`, source `902867effd4cd4705f3d74824b446a94b7a3cfc8`)
finished unsigned compilation for build `2611111500`, but collection still looked
under the legacy build-number output directory. No signing or publication ran.
Collection, compile binding and the signing adapters now select the same fixed
`out-mustang-cache` directory from the authenticated compile receipt, reject a
conflicting recorded directory, and retain the actual build/timestamp/hash checks.
The original output and allocation journal are preserved. The next attempt uses
the existing complete automatic path and persistent compiler cache.

The hosted signer supplies generated, hash-bound `UPDATE.md` bytes through the
existing host-sign release-notes contract. The notes identify the build and
separate compile and delivery sources/runs. Run 42937365659 compiled and passed
signer preflight, then stopped before key retrieval because the adapter supplied
the absolute release-script path as a candidate-commit notes path. That script
is executable input, not release notes; its existing pin remains unchanged.

Mustang build-stage stdout and stderr stream directly to the GitHub Actions
builder job log, which is retained on success and failure. Captured lunch output
and other devices keep their existing behavior. Make's exit code, process-group
cleanup and resource limits are unchanged. Run 42907732655 predates this fix;
its discarded compiler stdout cannot be recovered from the uploaded receipts.

Current policy: Nathan disabled the custom target-files archive validator. The
runtime passes authenticated, hash-checked target files unchanged to the pinned
Android signing tools and emits no custom target-validation proof. Executable
archive, provenance, capacity and signature checks remain. The validator sections
below describe the prior implementation, which remains unused library code.

For dedicated workflow run number 46 only, the runtime carries Nathan’s standing choice to use the verified
`frankel-2026111001` apps (voice 370) instead of refreshing the app export.
The runtime binds the exact record-byte hash to this one run number.
Later workflow runs do not inherit this choice. The candidate
app-admission receipt records `sourceChoice`; it does not claim these APKs contain
the newer Glacier app changes. The full compile/sign/publication path is used.

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

## First delivery and hosted image space

Delivery `42711277712` at `c1985730` successfully collected the retained output,
verified the unsigned proof and uploaded its signing inputs. The hosted signer
then refused at its metadata gate: its unchanged current peak budget required
98,069,563,537 bytes, with only 92,057,849,856 bytes free. No payload download,
OIDC login, secret access, signing or publication occurred. Owned cleanup passed;
the numeric dedicated VM was independently observed stopped without JIT metadata.

The signer removes only the unused preinstalled Android SDK at the documented
Ubuntu image path `/usr/local/lib/android/sdk` before metadata admission. This
step is restricted to the disposable GitHub-hosted signer, refuses a redirected
or mounted SDK root, and reports free bytes before and after. Signing uses the
qualified otatools and JDK bundle, not that image SDK. No native builder files,
compiled outputs, journal, qualified tools, disk budgets or capacity checks are
changed. Freed space is not assumed: the existing actual hardware/free-space,
peak and byte checks must pass before any signer login on the next full attempt.

## Resident signing inputs and the physical space budget

The complete peak budget includes the downloaded unsigned target ZIP, qualified
otatools ZIP and JDK ZIP. Those archives already consume space when full
preflight observes free bytes. Preflight also leaves its verified extractions
for the signing entry point, which performs the same checks again. Comparing
the complete footprint with only remaining free space charges these resident
archives twice. With the recorded run-38 sizes, the first check has a modeled
98.81 GB free and the second 97.53 GB, against an unchanged 98.07 GB peak. This
is a source-level projection, not a runtime failure observed after the link gate.

After hash and archive validation, capacity admission credits only the three
verified input archives already resident on the filesystem where free space was
measured. Credit is capped by declared size, actual size and allocated blocks;
hardlinked inodes count once and other filesystems receive no credit. The full
peak must still fit the selected disk. Output, extraction, scratch and reserve
budgets are not credited or reduced, and the metadata gate remains unchanged.
This correction does not change symlink policy or resolve the unidentified
target-files member. No runtime or publication follows from the source change.

## Android payload links and executable archives

The pinned `platform_build` revision
`b0f697df8cc9519408ca0a404e918cd51bde0735` explicitly preserves symlinks in
`sign_target_files_apks.WriteOutputData`, materializes them in
`common.UnzipSingleFile`, and repacks with `zip -y`. A target-files archive is
therefore validated separately from the JDK and otatools archives. Those
executable inputs still reject every symlink, including directory-shaped ones.

The target validator does not extract, follow on the host, rewrite or remove
payload links. It checks canonical member names, duplicate paths, file types,
encryption, expansion and bounded link metadata. Links are confined to Android
payload trees; signer control paths remain regular data. No member may have a
symlink or regular-file parent. The complete graph resolves components in
filesystem order, following a link before processing `..`; cycles, excessive
chains and paths leaving the payload or reaching signer metadata refuse.

Absolute links are not automatically accepted as Android data. They require a
recognized Android namespace and a fresh observation that the corresponding
host root is absent. Existing roots, broken host symlinks, unavailable facts,
root execution, a writable host root or effective capabilities refuse. This
keeps such targets inert on the signer. Absolute links into real host namespaces
remain refused even when they might be valid on an Android device.

These checks run after authenticated input-byte checks and before signer login;
signing repeats them before reading key payloads. Capacity and source/input
bindings remain mandatory. The hosted receipt records the unsigned ZIP identity,
link-inventory digest, counts and observed absent host roots. Expected source
does not prove that the retained ZIP's unknown member passes this policy.

Offline coverage includes relative and absolute chains, cycles, link parents,
component-order traversal, canonicalization, actual host-observation refusal,
malformed ZIP metadata, executable-role rejection and the pinned writer/repack
behavior on small controlled fixtures. It does not execute the qualified
otatools archive or establish actual retained-input admission, signing,
publication or device compatibility. App-source freshness and the retained
delivery-only source contract are unchanged.

## Source freshness after the compiled producer

PR #729 (`060f837aa11f47c435f7e107b7fcb8d8eeadaeaf`) changes the
`workspace/rose-agent/app` customization asset, Java implementation and tests.
That subtree is a declared source of `dev.pixel.voice` in the existing app
allocation contract. The accepted `frankel-2026110901` export predates those
changes, so current app freshness reports voice drift. The retained-delivery
source comparison independently refuses the app changes; its original compiled
app-admission proof also remains bound to the old export.

A new export cannot change the APK bytes already inside the retained OS.
Without reverting the app work or weakening freshness, delivery therefore needs
the existing Release owner to produce and commit a fresh common app export,
followed by consistent Mustang consumer pins in `mustang_route.py` and
`mustang-runtime.json`, then the normal full compile/sign/publication path with
`retained_compile_run` empty. That path issues a new clock allocation and build
identity; it must not reuse the original consumed number or reset its journal.
Existing preparation and qualified-tool inputs may be reused only through their
normal authenticated checks. This source assessment does not authorize an app
build, export, allocation or Mustang runtime dispatch.

The normal route now selects the committed `frankel-2026111001` export from
producer `42813894214`, source `6bab12cdf9a6ac2fa2267966447b9820297650ec`.
Export `42851073284`, attempt 1, artifact `13781175876` contains voice 370,
calculator 6 and background control 41. Its 1,875,279-byte archive, digest
`4cb8c4ff5ed801210f687071ea5e48442ee11712e7e4f3877495456ee07376ef`,
12-member inventory, receipt and checksums were independently checked against
the merged record. The allocation file-byte hash is
`32c59f8bd4a73d0ed8c2683d78ecb7a34308896a8bd0b7122c27c3d0d916e6a8`;
it differs from the allocation's canonical identity hash. Source freshness
passed at record merge `07c890df8462407bc6233f89791496102786773a`.

Release rechecked APK identities with aapt/apksigner; the consumer preparation
here rechecked record and archive bytes, with no local APK tools available.
The dedicated native consumer still reruns its own APK tools before use and
checks freshness against the actual captured run source. This selection does
not authorize dispatch or admit an old compiled target; a fresh full compile,
actual output/link validation, signing and publication remain unperformed.

## Observed root debug alias

Full run `42860845139` compiled build `2611101700`, collected its complete
outputs and passed the unsigned proof. Hosted byte preflight then refused the
actual entry `ROOT/d -> /sys/kernel/debug` before signer login or secret access.
The authenticated lifecycle receipt confirms the numeric dedicated VM stopped,
metadata was removed and the ephemeral runner cleanup succeeded. No OTA was
signed or published, and the output and consumed allocation remain preserved.

The validator permits only that exact member/target pair as terminal link
metadata on an unprivileged signer with a protected host root. It does not add
`sys` to the allowed host namespaces. No archive member may live beneath the
alias, and no other payload link may reference or traverse it, including through
chains, case aliases or `..`. Other links into `/sys` or existing host roots
remain refused, as do all executable-archive links. The hosted receipt explicitly
lists accepted terminal metadata links in addition to the inventory digest.

This follows the pinned Android writer's `os.symlink`, the pinned
[`mkbootfs` implementation](https://github.com/GrapheneOS/platform_system_core/blob/322ff5129d865a5e8d958667e6c63ac943449493/mkbootfs/mkbootfs.cpp)
using `lstat`/`readlink` for symlinks, and the pinned signer's `zip -y` repack.
It preserves the link text without reading host debug data. Controlled tests
exercise the exact observed entry, real `zip -y` preservation and adversarial
references/children/nearby host targets. No original archive is rewritten or
relabelled, and this source fix does not admit a future input without its normal
byte, provenance, capacity and freshness checks.
