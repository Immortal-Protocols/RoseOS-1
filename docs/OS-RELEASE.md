# RoseOS build and public release

Target channel: **Pixel 10 / frankel**, public repository
[Immortal-Protocols/pixel-ota](https://github.com/Immortal-Protocols/pixel-ota).
Its README specifies `frankel-stable` metadata and the matching
`frankel-ota_update-BUILD.zip`. Factory images and checksums may accompany it.
Do not use the imported Mustang (Pixel 10 Pro XL) scripts as Frankel instructions.

This is the release procedure and its remaining gates, not a claim that a new
OS release has already been built. Refer to [VALIDATION.md](VALIDATION.md).

## Build & publish latest Pixel 10 (the normal button)

OCT30-INFRA-S014. Source and tests are in place; **no run of this button has
happened yet**, so its first real run is still an acceptance step (see the end
of this section).

**Click sequence:** GitHub → Actions → **Build & publish latest Pixel 10** →
**Run workflow** → branch `main` → leave every field at its default (mode
`latest-beta`) → **Run workflow**.

What one run does, in the existing `os-release.yml` path. The workflow
identity, controller, builder, signer, publisher, lock, logout and always-STOP
cleanup are unchanged, and the button adds no permission, credential, runner,
trigger or storage service.

1. **Guard.** Refuses any explicit pin, a channel other than `beta`, a device
   other than `frankel`, a resume or a promote input. It also requires the
   dispatch commit (`GITHUB_SHA`) to be on main. That commit is the captured
   source; later main changes never reach the run.
2. **Builder allocation, before any identity is used.**
   `scripts/release-allocate.py latest` runs under the shared builder lock. It
   reads every identity already consumed:
   - the public pixel-ota releases, read anonymously;
   - this builder's `frankel-<build>[-resume-<run>]` attempt directories;
   - earlier reservations in `$HOME/rose-actions/allocations`;
   - the committed retained baselines and app reservations in
     `os/release-pipeline/frankel.json`.

   From these it allocates:
   - **Build number** `YYYYMMDDNN`: the UTC day of the run, above every known
     build. It refuses past `NN=99` and when a known build is dated after
     today.
   - **`BUILD_DATETIME`**: above every known timestamp.
   - **App versions**, per package in `os/latest-beta/allocation.json`:
     - a package whose source paths did not change since the current Beta
       Latest keeps that release's version (Calculator and controller are not
       bumped needlessly);
     - a changed package gets a fresh versionCode above every version ever
       consumed (published, built, reserved, retained, earlier allocations).

   Only the manifests that need it change. The result is a deterministic
   local commit: the source plus that version-only delta and the generated
   `os/release-notes/frankel-{build}.md` the sign stage reads, with a fixed
   identity and dates. Unchanged main still gets this commit. It is never
   pushed, and the compiled source really carries the allocated versions.

   The allocation (source, run, clock, hashes of the observation, delta and
   allocation) is written with `O_EXCL` before the build uses it. A failed or
   cancelled attempt therefore stays consumed, and the next click allocates
   afresh.

   Missing or unreadable state refuses; it is never guessed. This covers the
   storage, the release listing (or one longer than scanned), a stray file in
   the reservation directory, an unrecorded previous release source and a
   malformed manifest.
3. **Build, sign and verify:** the existing stages and checks, unchanged.
4. **Publisher**, only after the builder succeeded:
   - It re-derives the candidate from the captured source and the allocation,
     and requires exactly the allocated apps in the verified image.
   - With its pixel-ota token it lists every release, drafts included, and
     refuses if the tag already exists. An older release or draft is never
     reconciled or overwritten.
   - It uploads a draft with truthful notes: the captured source, commits
     since the previous Beta, bundled app versions, the delta, and the
     evidence limits (no phone runtime acceptance is claimed).
   - It download-checks the draft, then publishes it as a **public**,
     non-prerelease release and makes it the **Beta Latest**.

   This is the Beta channel, not production; Alpha and Prod are not offered.
   Every earlier release stays for rollback.

Clicks are serialized by the existing non-cancelling `os-release-frankel`
concurrency group. GitHub keeps one pending run per group, so a newer click
replaces an older *pending* one before it starts. A replaced run has consumed
nothing. Each run that starts allocates afresh past every reservation.

A latest-beta attempt is not resumed. Click again, which allocates a fresh
candidate. The advanced explicit `os-release` mode and its resume inputs are
unchanged.

The app-only route also refuses a Rose Agent version at or below any
latest-beta reservation on the builder (`scripts/host-app-release.py`).

**Real acceptance so far:** runs 41095801733 (frankel-2026103002) and
41103495650 (frankel-2026103003) completed allocation, build, signing,
verification, publication, Latest promotion and cleanup with no
intervention. Phone update acceptance is still open.

### Back-to-back campaign (RELEASE020)

Set `campaign` to `start` on a latest-beta run to begin a 24-hour campaign.
The final job `next-release` (`scripts/release-chain.py next`) is the only
job with `actions: write`, owner-approved. It uses the job's short-lived
`GITHUB_TOKEN` to dispatch one successor latest-beta run on main. The
successor pins fresh main and allocates fresh versions at its own start,
unchanged main included.

- **Successor input:** `<id>.<deadline>.<generation>`. The deadline is fixed
  at the first run's start plus 24 hours and is carried unchanged, so a
  campaign cannot extend itself. Run names carry the campaign.
- **When it chains:** only if the builder, publisher and cleanup all
  succeeded, on run attempt 1, before the deadline, and while the repository
  variable `RELEASE_CAMPAIGN_STOP` is neither `all` nor the campaign id.
- **Duplicates and uncertainty:** a run that already carries the next
  generation is refused as a duplicate. If the dispatch outcome is
  uncertain, the job looks for the successor once and otherwise fails
  visibly; it never retries.
- **Stopping:** set the variable, or cancel the running run.
- **The plain button:** an empty `campaign` is a single run, as before.
- **Not used:** no schedule, no new token and no WIF change.

A queued successor waits in the same concurrency group and is not evidence
that a build started. A manual click while a successor is pending replaces
it; that ends the campaign, which is visible as a missing generation.

Allocations are not written back to main; they live on the builder and in
pixel-ota. Drafts made outside this workflow must stay recorded as retained
baselines in `frankel.json`. The publisher's authenticated tag check is the
final guard.

## Retained Rango signing and separate release (source only, runtime unbound)

This route was added in OCT30-INFRA-S015 as source and tests only. Nothing
has been bootstrapped, transferred, signed or published.

The mode `rango-retained` connects these steps:

1. The guard runs `python3 scripts/rango-retained.py preflight --job guard`.
   It exits 4 and lists every unbound value in
   `os/retained-rango/release.json`, before the controller starts.
2. Only when every value is bound, the job `rango-builder` runs on the
   original builder:
   - `preflight --job builder`, then `stage --consumer`, both before login;
   - signer login, then `sign`, which runs host-sign's retained route under
     the shared lock;
   - the logout step, which always runs;
   - `verify`, which runs release-pipeline `verify_signed` with the Rango
     expectations;
   - `stage-artifact`, which runs release-pipeline `stage_artifact`.
3. The job `rango-publisher` then runs `publish`:
   - it revalidates the bindings;
   - release-pipeline `publish` uploads the draft and download-checks it;
   - the adapter's `finalize_public` makes it public with
     `make_latest=false`, and Latest must be unchanged before and after.
4. Cleanup always stops the builder.

The `latest-beta` button and the Frankel modes are unchanged.
`SUPPORTED_DEVICES` and `LATEST_DEVICES` stay `('frankel',)`.

**Signing-only by default; tools proven per run (OCT30-INFRA-S017).**
- `rango_publish` is a boolean dispatch input, default `false`. With the default, the run stops at the builder's
  private artifact. The `rango-publisher` job runs only when `rango_publish` is true, the publisher token is never
  minted, and `rango-retained.py publish` itself refuses without `ROSE_RANGO_PUBLISH=true`.
- The four Fold tools come from the admitted S016 helper. `prove --proof-dir P` runs after staging and before signer
  login, under the shared lock. `sign ... --proof-file P/fold-tool-proof.json` rechecks that same-run proof under the
  lock before any secret, and refuses with `tool_proof_missing`, `tool_proof_binding` or `tool_proof_drift`.
  No installed hash is trusted from source.
- `feedChannel` and `notes` stay signing prerequisites, because they produce the signed public assets `rango-stable`
  and `UPDATE.md` that `verify_signed` checks. The repository, tag and Latest settings are publication-only.
- The identity authority (`signing.identityPolicy.mode`) is `first-use-containers`, selected by the owner at 13:07 UTC.
  The bootstrap-receipt route stays available; its receipt (artifact 12961047183) is held, never retrieved another way,
  so `signing.bootstrapReceipt` stays null. The bootstrap itself completed (run 41019390144) and is never repeated.
- The first-use container contract (`identityPolicy.firstUse`, `selected: true`):
  - exactly one ENABLED version in each fixed container, created inside the bootstrap window;
  - numeric versions pinned before access;
  - a write-once identity binding that refuses a Frankel collision and must match on every later run.

  It refuses while unselected, before any metadata or secret call. Selected (OCT30-INFRA-S017-FIX001 connected it;
  the public identity and numeric versions are runtime outputs, never prefilled):
  - the hosted guard checks the bootstrap run (`check-bootstrap-run`) and hands a same-run receipt to the builder,
    because the builder has no Actions read access; bootstrap-receipt mode records `checked: false` without a call;
  - `sign` pins both numeric versions and rechecks them under the shared lock; inside host-sign's retained seam the
    extracted inventory and key pairs are validated with OpenSSL, the OTA/AVB identity is derived, a Frankel
    collision is refused, the version metadata is rechecked again, and only then is the durable record written;
  - the record (`firstUse.recordDir/first-use.json`) is written through an exclusive partial file and a hard link,
    never followed through a symlink, never overwritten. Only `rango_first_use_enroll=true` (default false, refused
    outside `rango-retained`) creates it. Every other run must match its identity fields. Missing, partial or
    conflicting state refuses. Per-run audit fields (`enrolledBy`) are excluded from the comparison;
  - `verify` and the staged `firstUseBinding` use that same record. No bootstrap receipt is fabricated or claimed.

**Upstream Updater verification (OCT30-INFRA-S018).** The retained Rango build was compiled with
`--keep-upstream-updater`, so it has no Rose channel. The owner approved its first Beta for manual installation
without Rose on-device discovery (13:32:59 UTC); Rose Updater integration follows separately.
- `release-pipeline.py` has an allowlisted `updater.mode`. `rose`, the default and every Frankel build, is unchanged:
  the Rose channel Updater from `--updater-url`, with RoseChannels code and signer. A Rose config without
  `--updater-url` now refuses instead of crashing.
- `upstream` is refused for a Latest device. It requires the `--keep-upstream-updater` recipe with no Rose channel
  argument, and refuses Rose-only keys (a hybrid). Its contract is explicit and evidence-bound:
  - `included: false`: no Updater APK anywhere and none of its supporting XML files;
  - `included: true`: exactly the expected privileged APK, package and XML; readable dex without the Rose channel
    code; every expected compiled value (`resources`, `string/url` required); one exact signer on signed files.
- `verification.json` records `updater` with `mode` and `roseDiscovery` (true only for `rose`). A public GitHub Beta
  is not on-device discovery.
- `os/retained-rango/release.json` `verification.updater` binds the recipe as the pinned `rango-reconcile.json`
  path and sha256, with `included: false`. The source expectation was absence (upstream `media_system.mk` adds it only
  with `OFFICIAL_BUILD=true`, the patcher only with `--updater-url`), and the Mac's inspection of the retained
  target-files confirmed it: all 217 APK identities decoded without `app.seamlessupdate.client` (receipt 9a134b49), and
  none of the three supporting XML paths in the 10221-member index (7cce414e). The signed target-files is rechecked.

**Bound retained recipe (FINAL static data handoff, 14:37 UTC).** `os/retained-rango/release.json` now binds the
transport (the transferred `2026103001-transfer007/input` directory, input receipt 54d4cf66), first-use selected,
otatools provenance (product, resolved manifest, compile and collection receipts, archive bytes), release-script
provenance (GrapheneOS/script 09d65184 `generate-release.sh` and `common.sh` hashes), `feedChannel: beta` (a
classification; nothing on the phone reads `rango-stable`), the notes pin, the Updater absence and
`verification.expected` (shared-source framework, Permission APEX and SystemUI expectations, the retained apps and
signer, the builder's tool paths). The hosted guard passes with nothing missing. Still runtime-only, inside the one
protected run: the builder's real input files and hashes, the host tool and release-script executable comparisons,
the local tool proof, two-container version admission, key pairs and the Frankel collision check, the durable
first-use record, and verification of the actual signed bytes.

**Retained release notes pin.** The immutable product `02b21bc6` holds notes only for frankel builds, so
`publication.notes` pins UPDATE.md separately: `{commit, path, sha256, build, device}` for exactly rango 2026103001
(`os/release-notes/rango-2026103001.md`, manual installation only, no RoseOS on-device discovery). The bytes are read
with `git show` from that commit, never the working tree. They are re-hashed before any login, again by host-sign
(`release_notes`), at staging and by the hosted publisher, and recorded as `notesSource` in `rango-bindings.json`. The
Rango builder checks out full history for that read. Frankel still reads `os/release-notes/{device}-{build}.md` from
its candidate commit, unchanged.

**Version names by project number.** Secret Manager may name a version's parent project by its number. First-use
admission accepts only the fixed project's id (`protocols-465723`) or its pinned number (`firstUse.projectNumber`,
1072799993443, supplied by the authorized 15:13 UTC packet and the prior Oct 28 IAM receipt). Any other number or id, a location scope, an alias or a
nonpositive version refuses, with a closed diagnostic (shape, project, container and version classes, never the raw
name). Run 41077961699 refused at this point, before any payload or enrolment.

**Guard versus builder preflight.** The hosted guard (`preflight --job guard`) checks the complete static config,
provenance selection, pinned notes and the declared `input.transport`, but never reads the builder-local input
directory, which the hosted runner cannot see; `--input-dir` is refused there. `preflight --job builder` checks the
exact five files, sizes and hashes before signer login, then `stage` and `recheck` revalidate the consumer copy.

What the source already does:
- **Input.** The input receipt schema is `roseos.rango-retained-input.v1`. It
  binds:
  - device `rango`, product `02b21bc6…`, build `2026103001` and its
    timestamp;
  - the source manifest, overlays, config and compile receipt, with compile
    exit 0;
  - target-files and otatools, each by name, size and sha256;
  - the signed Rose Agent and its policy;
  - the `rango-build-only` producer.

  A missing value stays explicitly unbound. Wrong devices, products, builds or
  producers are refused, as are traversal, links, extra or missing files, and
  size or hash drift. The only input interface is a local directory, named by
  the bound `input.transport`, holding exactly the receipt, target-files,
  otatools, resolved manifest and background policy.
  `rango-retained.py stage --input-dir D --consumer C` copies it into a fresh
  owner-only consumer directory. `recheck --consumer C` revalidates that
  copy, and host-sign runs the recheck again before any secret.
- **Signing.** `scripts/host-sign.py` signs with `ROSE_RETAINED_INPUT` (the
  consumer directory) and `ROSE_RETAINED_BOOTSTRAP_RECEIPT` set. Everything
  else is the same Frankel primitive. Only
  `PIXEL_OS_SIGNING_KEYS_RANGO_V1` and `PIXEL_OS_SIGNING_PASSPHRASE_RANGO_V1`
  are used. Their exact per-secret versions, and the OTA certificate and AVB
  identities, come from a genuine completed `fold-rango-v1` bootstrap
  receipt, which must differ from the Frankel identity. The four installed
  tool identities, and the otatools and release-script provenance, must be
  bound; while any is null, signing refuses before any secret fetch.
- **Publication.** `rango-retained.py` publishes to tag `rango-{build}` with
  rango-prefixed assets.
  - It refuses an existing tag.
  - It uploads a draft and download-checks it. Only then does it make the
    release public with `make_latest=false`, using
    `GitHubReleases.finalize_public`.
  - It checks that Latest is the same release before and after.
  - An explicit promote or a config with `latest: true` refuses.
  - Earlier releases are never touched.

  A GitHub tag does not establish automatic Rango OTA discovery, and no
  channel routing is configured.

Unbound, in the order the guard lists them:

| Value | What it needs |
|---|---|
| `input.transport` | A supported transport from the Mac operator to the original builder |
| `input.directory` | The retained input itself |
| `signing.bootstrapReceipt` | A genuine `fold-rango-v1` bootstrap |
| Four `signing.tools.*.installed` | Observed installed tool identities |
| `signing.otatoolsProvenance`, `signing.releaseScriptProvenance` | Approved source bindings |
| `publication.feedChannel`, `publication.notes` | A selected channel and reviewed notes |
| `verification.expected` | Rango-specific release-pipeline expectations |

## 1. Inspect the existing builder

Project `protocols-465723`, instance `immortal-os-builder`, zone `us-west2-a`.
Compute metadata verified `n2-standard-64`; the previously reported 1.5 TB disk
and its contents need independent inspection. Nathan authorized starting and
using this builder. An early instruction said there was no spending limit; that
has been superseded by an aggregate USD 5000 project cap (Wave 0–2 plan,
2026-10-27). Dev Infra keeps the cost ledger. Preserve existing sources, caches,
disks, keys and outputs. Do not assume no jobs run after boot.

Use the authenticated Dev Infra account and supported IAP SSH. Before building,
record OS/tool versions, free storage/memory, mounts, current jobs, source
locations/revisions, local changes, vendor inputs, signing boundaries and output
locations. Acquire an exclusive build lock. Do not reset or clean existing
trees to make a bootstrap script succeed.

An existing `OUT_DIR` may be reused only for the same lunch target and variant,
and only under the exclusive lock. The 2026102701 release reused one; never
reuse the output directory of a different release. Reuse is safe only when all of
the following hold:

- inputs replaced in the source tree carry fresh modification times
  (`scripts/apply-os-changes.py` now guarantees this);
- version metadata is regenerated for the new build number;
- the injected prebuilts in the built output are checked byte for byte against
  their inputs, for example with `qa/first-update/runtime/verify-target-files.py`.

A fresh mtime does not prove the image holds the right bytes.

Historical (2026-10-26): IAP was then blocked by proxy CONNECT 403 at
`tunnel.cloudproxy.app`. Dev Infra later restored supported IAP access and used
it for the 2026102701 build (INFRA-WAVE-048). If a connection is denied again, do
not replace IAP with direct network access to route around the denial.

## 2. Reconstruct and verify source

`os/pinned-manifest.xml` contains exact project commits captured in the prior-art
handoff, based on GrapheneOS `2026100600` / Android 17. Verify the corresponding
upstream manifest tag with GrapheneOS's published signer and compare project
identities before accepting source. Review current security updates before
choosing a public stable baseline; the imported pin is a reconstruction baseline.
If the builder has newer Frankel fixes, inventory and reconcile them before
changing its source. Never silently replace a known-good live build with this
older source snapshot.

For a new source directory on a provisioned build host:

```sh
# Set ROSE_REVISION to the reviewed, pushed RoseOS-1 commit.
repo init -u https://github.com/Immortal-Protocols/RoseOS-1.git \
  -b "$ROSE_REVISION" -m os/pinned-manifest.xml
repo sync -c -j4 --fail-fast
repo manifest -r -o resolved-manifest.xml
```

These are large downloads requiring the matching dependencies, storage and
allowed source hosts. The small app container is not sized for a complete OS
checkout. Follow the [upstream build guide](https://grapheneos.org/build) for the
selected release's host requirements and Frankel vendor inputs. Match `adevtool`,
Node/Yarn and device packages to that source; do not mix modern prebuilts into an
older release without validation. The manifest and local changes, rather than
an unreviewed moving branch, define the build.

## 3. Prepare RoseOS integration

Run the source patcher's preflight against the exact pinned checkout:

```sh
python3 /path/to/RoseOS-1/scripts/apply-os-changes.py /path/to/os-source --check
```

The script checks the relevant Git revisions, clean trees, ordered patch
application and overlay conflicts. Its `--check` uses a temporary copy and does
not modify the OS source. Applying without `--check` writes the reviewed changes;
it does not build, sign or flash.

Background control additionally requires a signed controller APK and a generated
policy tied to its exact bytes and the full app signing certificates. Build the
controller with the same SDK and app identity as Rose Agent:

```sh
python3 scripts/build-background-agent.py --viewer pip \
  --android-jar .tools/android-sdk/platforms/android-35/android.jar \
  --build-tools .tools/android-sdk/build-tools/android-15 \
  --out out/rose-controller
# Sign Agent-unsigned.apk using scripts/sign-apk.py and the approved app keys.
```

Generate the restricted policy from signed controller, launcher and the explicitly
allowed target APKs (repeat `--target-apk` as required):

```sh
python3 scripts/prepare-background-policy.py \
  --agent-apk out/rose-controller/Agent.apk \
  --voice-apk out/rose-agent.RUN/rose-agent.apk \
  --target-apk out/calculator.RUN/calculator.apk \
  --sdk TARGET_OS_SDK_NUMBER \
  --aapt .tools/android-sdk/build-tools/android-15/aapt \
  --apksigner .tools/android-sdk/build-tools/android-15/apksigner \
  --output out/background-policy.json
python3 scripts/apply-os-changes.py /path/to/os-source \
  --background-policy out/background-policy.json \
  --background-agent out/rose-controller/Agent.apk --agent-tray \
  --preinstall-apk out/rose-agent.RUN/rose-agent.apk \
  --preinstall-apk out/calculator.RUN/calculator.apk \
  --updater-url INSTALLED_CHANNEL_BASE_URL/ --check
```

Any product option (`--background-policy`, `--preinstall-apk`, `--agent-tray` or
`--locked-control`) now requires `--updater-url`; without it the pinned Updater
would poll GrapheneOS and strand the installed phone. Pass
`--keep-upstream-updater` only for a build that will never update an existing
RoseOS phone.

Add `--installed-identities FILE` (the `os/release-evidence/*/expected-identities.json`
shape, describing what the phone has installed) to catch bundled apps the phone
would ignore. Android keeps an installed app unless the image's copy has the
same signer and a higher versionCode. A different signer or a lower version is
refused. An equal version is normal for an unchanged app and is only reported as
a note. It is refused only when the installed record also pins an `apkSha256`
that differs. This needs a policy receipt that records `versionCode` (receipts
from `prepare-background-policy.py` now do).

Substitute actual artifact paths and the target OS's SDK number. Confirm the
policy's final allowlist before applying. RoseOS's product baseline calls for a
pinned app allowlist and explicit owner approval for installs, messages,
purchases and account changes. The imported `--prototype-control` and
`--locked-control` switches are historical capabilities, not release defaults.
Every build with a background policy still applies
`locked-control-frameworks-base.patch`. It adds the locked-session resources,
their symbols and the owner-gated guards, with
`config_pixelComputerControlLockedSessions=false`, so locked creation stays off.
The retained tree and the release verifier both expect that state.
`--locked-control` only switches the resource on.
Their inclusion in source does not establish that broad control meets RoseOS's
requirements. The current patcher installs the controller when policy is supplied;
`--preinstall-apk` adds the policy-pinned Rose Agent and Calculator APKs to the
product partition. `--agent-tray` enables the
status tray, which the patches otherwise leave off. See
[OS integration](OS-INTEGRATION.md) for the tray, the Stop contract, the
preinstall and default HOME behaviour and the facts an existing-phone update needs.

## 4. Build a release candidate

Use a new, monotonic build number greater than the channel's current release and
retain the resolved manifest and all local changes. Select the pinned source's
Frankel **user** target (the imported Android 17 configuration uses `cur`).
Build target-files and OTA tools, then stage with the matching source scripts.
Typical commands, to be confirmed against the inspected source:

```sh
source build/envsetup.sh
lunch frankel-cur-user
export BUILD_NUMBER=YOUR_NEW_BUILD_NUMBER
m target-files-package otatools-package
script/finalize.sh
```

Use an isolated output path and matching vendor inputs. Any Updater-enabled build
must use the verified RoseOS update endpoint; never direct a differently signed
build at GrapheneOS's upstream update server. Public GitHub assets alone do not
prove the installed Updater knows how to consume that channel. Inspect the existing
Frankel Updater configuration and release workflow before publishing metadata.

## 5. Sign and verify

Nathan identified the existing Frankel V5 resources:

- `projects/protocols-465723/secrets/PIXEL_OS_SIGNING_KEYS_FRANKEL_V5`
- `projects/protocols-465723/secrets/PIXEL_OS_SIGNING_PASSPHRASE_FRANKEL_V5`

The first is a gzip tar of the existing key set, not permission to generate a new
update identity. Retrieve only when ready to sign, using owner-only temporary
storage outside Git; pin the secret version. Validate archive members before
extraction (no absolute paths, parent traversal, links or unexpected files).
Compare public certificates and `avb_pkmd.bin` with the established channel/device
trust chain. Check encryption, swap and temporary-key handling before signing.
Never print a passphrase or put it in shell arguments/logs.

Use the **inspected source's** release script with `frankel` and the exact build
number. The historical bundle described by Nathan does not list a factory-image
SSH signing key; check whether the selected packaging workflow needs a separate
one. Do not generate or substitute any missing production signing key.

Independently verify APK/APEX and AVB identities, OTA signatures and metadata,
consistent device/build numbers, the `user` variant, archive integrity and public
artifact hashes. Test the candidate on the appropriate approved runner/device;
Cuttlefish can prove virtual boot/UI behavior but not Pixel flashing, firmware,
radio behavior or hardware-backed verified boot. Keep signed target-files for
future delta updates. Compilation/signing alone is not a stable-release gate.

## 6. Publish to the existing public channel

Read the current `pixel-ota` releases and existing metadata before allocating the
build number. Stage a draft release with verified OTA/factory artifacts, checksums
and a concise validation report identifying source, device, signer, version and
known limits. Verify uploaded bytes and metadata reference the same candidate.
Never upload keys, private caches, config, source dumps or unsanitized logs.

Current publication route, as of 2026-10-27: frankel-2026102701 was uploaded and
promoted manually by Nathan from his Mac. Dev Infra then downloaded all seven
public assets and checked them against the verified originals (INFRA-DELIVERY-047).
From the cloud agent, release API reads and draft creation work, but asset
upload to `uploads.github.com` returns 401 Bad credentials. The cause is
unresolved: the successful manual upload does not show the cloud path works, so
do not blind-retry it. No unattended cloud publication route exists yet.

`scripts/release-manifest.py` records and checks the candidate offline. It never
signs, uploads or downloads. `create` writes an immutable pre-publication manifest
from explicit asset paths. It records the source revision, the resolved-manifest
hash, the target device/variant, the semantic channel, the build number/timestamp,
asset sizes/hashes and the claimed public app/OTA/AVB identities. The feed keeps
its `DEVICE-stable` filename whatever the semantic channel is. `verify` checks a
local directory against the exact asset allowlist, the expected public pins (a JSON
file in the same shape as `--identities`) and the currently published feed line;
use `--no-baseline` only for a channel with no release yet. `verify-published` compares a
separately downloaded directory with the manifest and writes a delivery receipt.
Matching identity fields against pins is not cryptographic signature verification.
Record apksigner/OTA verifier results separately.

`scripts/release-stage.py` records the release lifecycle offline as a chain of
immutable receipts. Each receipt is bound to one candidate (source revision,
device and build number). The stage order is build, bytes-verify, sign, ota-verify,
transfer, publish and publication-verify. `run` executes only the next legal stage,
under a non-blocking lock that the stage command inherits. It records start and
completion separately, and binds declared input and output file hashes. `status` reports:

- `RUNNING` only when the lock is held and the owner's boot id and process are live;
- `INTERRUPTED` when a started stage has no live owner;
- `AWAITING_EXTERNAL` for publication, which this tool never performs, with builder
  compute not needed at that point;
- `TERMINAL` only after publication-verify succeeds, or an explicit `fail`/`abandon`.

`.github/workflows/release-verification.yml` runs the manifest and stage tests on
relevant pull requests and pushes. On manual dispatch, for an allowlisted
already-public release, it fetches exactly the assets listed in
`os/release-evidence/RELEASE/manifest.json` from pixel-ota's public download URLs,
with no token. It then compares them with `verify-published` and `verify`, and
derives the OTA certificate and AVB key pins from the published files. It keeps
only small receipts, for 7 days. It never writes releases. The RoseOS-1 token cannot
write to pixel-ota.

Interface for a future destination-side publication workflow in pixel-ota. This is
not deployed: artifact ingress from the builder is unresolved, and the cloud upload
host still returns 401.

1. Dispatch inputs: the exact tag, the candidate manifest SHA-256 and an ingress
   reference for the artifact bytes. No free-form URLs or commands.
2. Use pixel-ota's own `GITHUB_TOKEN` with `contents: write`. No cross-repository
   credential.
3. Create or reuse only a draft for that tag. Upload exactly the manifest's
   assets, then download the draft assets and pass `verify-published` before
   any promotion.
4. Promotion to Latest is a separate, explicit dispatch. Record the draft ID and
   the receipts. On failure, leave the draft for cleanup, and never reuse a tag
   with different bytes.

`.github/workflows/os-release.yml` is the unattended path. It is implemented, but
its setup is missing and it has not been proven by a real release run. It runs
only on a manual dispatch from main, for an exact candidate SHA, build number and
timestamp, channel and expected app versions. The jobs are:

1. **guard:** validates the inputs and the candidate's ancestry, and checks every
   bootstrap input.
2. **controller:** starts `immortal-os-builder` using GitHub OIDC (no key files).
3. **builder:** runs on a repository-scoped self-hosted runner, under the
   existing lock and `release-stage.py` receipts.
   - `scripts/host-integrate.py` checks out the exact candidate, builds the
     apps, checks their versionCodes, and signs them *before* integration with
     the pinned `PIXEL_APP_SIGNING_*` versions. It then writes the SDK 37 policy,
     reconciles the candidate into the retained tree and records the resolved
     manifest.
   - The job runs `installclean` plus the make goals. It verifies the build
     identity and every `META/misc_info.txt` AVB fingerprint, app bytes,
     PRESIGNED state and signers, the compiled framework policy, and the
     Permission APEX gate and the SystemUI tray gate (see below).
   - `scripts/host-sign.py` validates the pinned OS key archive, checks the
     public certificate and AVB key against the pins, and runs the source
     tree's release command.
   - The job verifies again: OTA metadata, OTA package and payload signatures,
     the vbmeta signature and AVB key, the Permission APEX with its release
     signers and image bytes, the SystemUI tray code with its image bytes, and
     the public pins.
   - Finally it transfers only the allowlisted public files and receipts as a
     3-day Actions artifact.

   Signer access is a per-job gcloud config (`scripts/actions-gcp-login.py`). It
   exists only around the two signing stages and is removed as soon as each
   adapter returns. Secret copies live in owner-only RAM and are removed on
   success and failure.
4. **publisher:** a hosted job that revalidates the artifact, then mints a
   short-lived token for the pixel-ota App installation only
   (`actions/create-github-app-token`, revoked when the job ends). With it,
   `scripts/github_release_publisher.py` creates or reconciles the draft
   with exactly the seven assets, downloads them with authentication for full
   verification, and promotes only when asked. It never modifies a published
   release that differs from the candidate.
5. **cleanup:** stops the builder, whatever happened. The 12-hour automatic stop
   stays as the fallback.

**App-only mode** (`mode: app-only`) uses the same dispatch, guard, controller
and cleanup. The builder and publisher jobs are skipped, and an `app-builder`
job runs on the same runner instead. The guard admits only `apps` of exactly
`[["dev.pixel.voice", N]]`, with no resume and no promotion; `build_number` and
`build_timestamp` keep their formats but are unused. `scripts/host-app-release.py`:

- takes the builder lock and checks out the exact candidate as a worktree;
- builds only Rose Agent (`host-app-build.py build-voice`), with the stable SDK
  and the staged public connection inputs;
- signs it with `sign-apk.py` and the four pinned `PIXEL_APP_SIGNING_*`
  versions, held in owner-only RAM;
- independently checks the package, the dispatched versionCode, a versionCode
  above `appRelease.mustExceed` (the published 302) and exactly the pinned app
  signer.

It never reconciles, runs make or reads the OS signing secrets, and never builds
the controller or the Calculator. The private 3-day artifact
`app-release-dev.pixel.voice-<run id>` holds only the signed APK,
`app-release-receipt.json` and `SHA256SUMS`. The receipt records the candidate,
package, version, size, sha256, public signer, `appRelease.minimumTestedOs` and
the checks run. The worktree and every temporary input are removed on success or
failure, and cleanup stops the builder.

**Emulator-alpha mode** (`mode: emulator-alpha`) builds an unsigned, test-only
Cuttlefish image, `aosp_cf_x86_64_only_phone-cur-userdebug`, on the same builder
(`os/release-pipeline/cuttlefish-alpha.json`, `scripts/emulator-alpha.py`).

- **Source preparation:** the build never uses the retained Pixel tree, because
  that tree holds the Pixel Updater integration, which the Cuttlefish product
  must not include (OS-ALPHA-001). `emulator-alpha.py prepare`
  (`scripts/alpha_source.py`), run under the shared lock, makes an ordinary,
  independent copy of the source inputs into
  `rose-actions/alpha-source/<preparationId>/tree`:
  - **Copy:** every file is copied byte by byte into a new inode, keeping its
    mode and mtime. There is no reflink (unsupported on the builder's ext4), no
    hardlink, no symlink forest, no mount and no patch-and-restore.
  - **Exclusions:** the exact policy `preparation.exclusions`:
    - root `.repo`, `keys`, `releases`, `out`, `out-*` and `out_*`;
    - `.git` and `.repo` anywhere;
    - the cache boundary `vendor/adevtool/dl/unpacked`
      (`exclusions.cacheBoundaries`, INFRA-ALPHA-090). This is adevtool's
      extracted factory-image cache. Its absolute links point at device paths
      (`/system/bin`, `/vendor/...`, `/sys/kernel/debug`), not host inputs. The
      evidence that it is not a build input comes from pinned sources:
      - adevtool `17df3b79` unpacks images there read-only, byte-copies vendor
        blobs into `vendor/<vendor>/<device>/proprietary`, and tracks only an
        empty `dl/.find-ignore`;
      - Soong `250710f7`'s module finder prunes any directory that contains
        `.find-ignore`.

      The boundary holds only while that marker is an empty regular file in the
      base, the cache is a real directory, and no `.mk`, `.bp` or `.go` file in
      the copied selection names the cache. Otherwise the preparation stops and
      names the cause. The cache itself is never changed.

    Pixel release keys are never copied.
  - **Links:** relative links that stay inside are kept, and absolute links into
    the tree are rebased. Dangling links are kept only if their target is
    contained in the tree. Any other escape, a link into an excluded path that
    exists, a loop, or an unsupported file type stops the preparation and is
    named.
  - **Checks before copying:**
    - the base binding: the reconcile receipt is the latest, unchanged, and no
      file has drifted;
    - `build/make` and Cuttlefish at the OS contract's pins;
    - the resolved-manifest and policy digests;
    - the three app APKs by digest;
    - OS's contract at its pinned digest, consistent with this config;
    - the copy budget (150 GiB) plus 300 GiB free after the copy.
  - **Delta:** OS's patch (`os/patches/cuttlefish-alpha-no-updater.patch`, which
    changes only `build/make/target/product/media_system.mk`) is applied with
    `git apply` inside the copy, never to the retained tree.
  - **Staging:** the copy is staged in `.staging-<id>-<nonce>`. It becomes
    `<id>/` (`tree/`, `manifest.tsv`, `preparation.json`) only after the delta
    hashes match and the base is seen unchanged. A failed run removes only its
    own staging directory. A leftover from an interrupted run is never removed
    automatically and blocks a new preparation of that id.
  - **preparationId:** the sha256 of the base binding, the delta digests and
    the changed files' before and after hashes.
  - **preparation.json:** the 084 interface: base candidate, resolved-manifest
    and reconcile digests, `osDeltaCommit`, `patchDigests`, `changedFiles`,
    apps with paths, `policySha256`, `updaterMode`
    `excluded-no-compatible-feed` with no endpoint, `selectionSha256`,
    `effectiveSourceIdentity`, the exclusions and dangling links, the base git
    revisions snapshotted before `.git` is left out, and the preservation
    checks.
- **Verifying a prepared tree:** probe and build check the prepared tree
  against its receipt and the pinned digests. The Cuttlefish revision comes from
  the receipt, because the copy has no `.git`. `verify-prepared` re-hashes the
  whole tree against `manifest.tsv`.
- **Where it builds:** the prepared tree, under the same lock as the Pixel
  config, into its own `OUT_DIR` (`tree/out-rose-cf-alpha`). Nothing is cleaned,
  and the retained tree and Pixel output are never touched.
- **What it never does:** reconcile, adopt, sign apps, read any secret, publish
  or edit the retained tree.
- **Order of a build:**
  1. the prepared tree is checked against `prepared.preparationId` and
     `prepared.receiptSha256`. This covers:
     - the base binding, the delta, the apps, the policy and the routing;
     - the changed files and the reconcile-entry files;
     - the manifest digest;
     - `device/google/cuttlefish` at the pinned revision (from the receipt);
     - the product mapping in `AndroidProducts.mk`;
     - the host package mechanism (`hostPackage`);
     - free disk space.

     `hosttar` is not a Makefile goal. At the pinned revision, the Soong plugin
     `build/cvd-host-package.go` registers `cvd_host_package` and installs
     `<module>.tar.gz` into `HOST_OUT`, and it declares the `hosttar` phony only
     for the `vsoc_*` board platforms. `build/Android.bp` declares
     `cvd-host_package`. Preflight checks the plugin's pinned sha256 and these
     declarations;
  2. `lunch` resolution, which must equal the recorded `hostEvidence.lunch`,
     including `TARGET_BOARD_PLATFORM=vsoc_x86_64`. `TARGET_RELEASE` is read
     from the shell after a successful `lunch`, because `get_build_var` returns
     it empty in the retained tree. It must be `cur`, with no fallback;
  3. `m droid hosttar`;
  4. the required images and the host package must be written by this build;
  5. the partition `build.prop` identity must match: emulator build id
     `rose-cf-alpha-<run id>` as `BUILD_NUMBER`, the dispatched timestamp,
     `userdebug` and `test-keys`;
  6. the image-content gate (`scripts/alpha_image.py`) checks the
     `imageExpectations` of the pinned OS alpha contract (`preparation.osDelta`:
     contract `c825aa19…`, delta `f21bf683…`, OS PR61) from the partitions packed
     into the built `super.img`, never from loose files:
     - **Extraction:** `super.img` is unsparsed with `simg2img` when sparse and
       must be an LP super image. `lpunpack` writes its partitions, of which
       `system`, `system_ext`, `product` and `vendor` are inspected; each must
       have one non-empty image, `<name>.img` or `<name>_a.img`, and a
       non-empty `_b` is refused as ambiguous. Each partition must be the file
       system the probe recorded (`BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE`) and is
       extracted with its tool: `debugfs rdump` for ext4,
       `fsck.erofs --extract` for erofs. An unknown format, or a tool that is
       absent or not executable, stops the gate and is named. Nothing is
       installed.
     - **Apps:** Rose Agent 302, Calculator 4 and controller 28 at their contract
       paths (`PRODUCT/app/RosePreinstall_*`,
       `SYSTEM_EXT/priv-app/PixelBackgroundControl`) must match the contract by
       exact bytes, by package and versionCode (`aapt`), and by exactly the
       approved app signer (`apksigner`).
     - **Framework policy:** framework-res's compiled computer-control Agents,
       Targets and PrototypeAgents arrays and its LockedSessions value must equal
       those derived from the bound `background-policy.json` (re-checked by its
       sha256, with app digests equal to the contract's).
     - **Tray:** the last `persist.wm.debug.pixel_agent_tray` in the system
       `build.prop` must be `1`.
     - **Seedvault closure** (`imageVerification.closure`):
       - `system/priv-app/Seedvault/Seedvault.apk` and
         `system/app/LocalContactsBackup/LocalContactsBackup.apk` must be
         non-empty regular files, not links, whose `aapt` package is the pinned
         `com.stevesoltys.seedvault` or `app.grapheneos.backup.contacts`.
       - The four XML files under `system/etc/{sysconfig,default-permissions,permissions}`
         must be non-empty regular files that parse with the pinned root element
         and name the pinned package.
       - The paths, packages and roots are derived in tests from the pinned
         Android.bp files (OS fixtures) and the pinned Seedvault `7cfffed7`
         manifests and XML (`scripts/tests/fixtures/seedvault-7cfffed7`).
       - These are compiled OS modules, so no version, digest or certificate is
         pinned for them and the Rose app signer does not apply. The receipt
         records their paths, hashes and identities.
     - **No Updater:** no `app/` or `priv-app/` Updater directory, no
       `app.seamlessupdate.client.xml`, and no APK of that package among every
       APK inspected. An APK that `aapt` cannot read fails the gate, because
       absence must be proven.

     The images stay unsigned and test-only, and this is not a boot, UI or
     phone result. `verify-image` re-runs the gate on existing build output.
- **Receipt:** it hashes every top-level image and `cvd-host_package.tar.gz`,
  records the image-content result and the tools that ran, and binds the preparation id, its receipt digest and the effective source
  identity. It records `testOnly`, `signed: false`, `published: false` and
  `bootAttempted: false`, and lists what it does not prove. Only the receipt
  and `SHA256SUMS` become the Actions artifact `emulator-alpha-receipt-<run id>`;
  the images stay in the alpha `OUT_DIR`.
- **Guard and options:** the guard needs no resume and no promotion. The alpha
  job has no cloud identity.
- **`plan`:** prints the exact commands, marked `executed: false`, and runs
  nothing.

`sourceBinding` holds the lead's host evidence (INFRA-ALPHA-082):

- candidate `ae9cfd4d` (2804);
- reconcile receipt `0004-2026102804.json` and its sha256;
- apps 302/4/28, in the 2804 dispatch order.

The resolved-manifest digest and the `lpunpack` and `fsck.erofs` paths, with
their sha256 in `imageVerification.toolSha256`, were measured by the lead
(INFRA-ALPHA-090). A different tool file fails the image gate.

`prepared` and `hostEvidence` hold the lead's measured run (INFRA-ALPHA-094):
- **Preparation:** `b02dafbb…`, receipt `18582916…`, prepared with recipe
  `0f4a8db4`.
- **Verification:** a full `verify-prepared` passed with no differences.
- **Probe:** passed with no tree failures; receipt `9c19b5cb…`. Its `lunch`
  values include `BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE=erofs`.

The first full verify had failed on the two patched files, which had mode 0600
where the copy recorded 0644, because `git apply` ran under umask 0077. The lead
restored those two modes on the host, which is recorded as a historical repair.
Since then, `prepare` itself restores the recorded mode of exactly the declared
changed files after the delta (`changedFileModes` in the receipt). Mode drift
anywhere else still fails verification. Without these values the guard fails
with exit 4 before the builder starts.

The lead's sequence on the builder, through existing access, needs approved
capacity (the copy budget plus 300 GiB free after it) and no other job holding
the lock:

1. `python3 scripts/emulator-alpha.py prepare` prints the preparation receipt.
2. `python3 scripts/emulator-alpha.py verify-prepared --preparation-id <id>` is
   optional and re-hashes the whole tree.
3. `python3 scripts/emulator-alpha.py probe --preparation-id <id> --run-id <n> --receipt <new path>`
   runs the prepared tree's checks and `lunch` only, never make. `lunch` writes
   build-system state only inside that tree's alpha `OUT_DIR`.
4. Record the preparation id and receipt sha256 in `prepared`, and the passing
   probe's sha256 and `lunch` values (including
   `BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE`) in `hostEvidence`, in one config
   commit.

**Successor preparation** (INFRA-ALPHA-104). OS's ALPHA-099 delta (`156ccb8e`,
patch `aa975499`, contract `a9c757c0`) differs from the delta of the prepared
tree `b02dafbb…` (`f21bf683`, patch `e000da0d`) by one file:
`build/make/target/product/generic/Android.bp`, `a8fd1327…` to `cf5dff6d…`. The
contract records that difference as `alpha099IncrementalDelta`: patch
`c9350590…`, which applies once and refuses a second `--check`. The retained
Pixel tree has moved on (reconcile `0005`), so a fresh copy would not reproduce
the 2804 base. Instead the existing tree moves in place, with its alpha
`OUT_DIR`:

- **Pin:** `preparation.successorOf` pins the predecessor (`b02dafbb…`, receipt
  `18582916…`) and names the contract's incremental delta.
- **Command:** `emulator-alpha.py prepare-successor`, under the shared lock:
  1. **Ancestry checks:**
     - the predecessor receipt digest;
     - the predecessor's base binding, target, apps 302/4/28, policy, routing
       and `OUT_DIR`;
     - its delta equals the incremental base commit and patch;
     - every changed file's base equals the contract, and only the incremental
       file changes;
     - the incremental patch digest touches exactly that one file, and its
       postimage is the contract's full-delta result.
  2. **Full re-hash:** the whole tree is re-hashed against the predecessor's
     manifest. Any difference, mode drift, a symlink on the path, or a file
     that is not at the preimage stops the run before anything is written.
  3. **Journal:** a write-once `transition-started.json`.
  4. **Transition:** `git apply --check` and `git apply` of the incremental
     patch, then the recorded mode is restored. The postimage is checked, a
     second `--check` must be refused, and the `OUT_DIR` must be unchanged
     (inode and mtime).
  5. **Receipt:** a write-once receipt,
     `alpha-source/<predecessorId>/successors/<successorId>/successor.json`. It
     records the predecessor chain, the new delta, the incremental patch,
     modes, changed files, and the same selection manifest. The
     `effectiveSourceIdentity` is the one a fresh preparation of the new delta
     would have; the tests prove this.
- **Preserved:** the predecessor's `preparation.json`, manifest and probe are
  never changed, relabelled or reused as evidence for the new delta.
- **Rerun:** returns the existing receipt after re-checking the postimage. It
  never applies the patch twice.
- **Interruption:** a journal without a receipt is an interrupted transition.
  The command refuses it until `prepare-successor --recover` is run, which
  continues only from exactly the preimage or the postimage of that file.
- **Use:**
  - `verify-prepared --preparation-id <predecessor> --successor-id <id>`
    re-hashes the successor fully.
  - `probe ... --successor-id <id>` probes it, and its receipt names the
    successor.
  - With `successorOf` set, the guard, builder and build use only
    `prepared.successor` (`successorId`, `receiptSha256`). They also need
    `hostEvidence.probedSuccessorId` to name it, so the successor must be
    re-probed before `hostEvidence` is recorded.
  - Until the lead records those values, the guard exits 4 and names them. The
    predecessor alone is refused for the new delta.
- **Capacity:** the transition writes no new space, but it still enforces the
  unchanged `build.minFreeGiB` 300 before writing (INFRA-ALPHA-108), as the
  build does.

**The Actions route** (INFRA-ALPHA-108) runs this preparation through the same
private `os-release.yml` `workflow_dispatch` on main. It uses the same runner
group, controller OIDC start and always-cleanup stop, shared lock and
`os-release-frankel` concurrency. Mode `emulator-alpha-successor`:

- **Guard:** the usual input and ancestry checks, then
  `emulator-alpha.py preflight --job successor-guard`.
  - This needs `preparation.successorOf`, the contract's incremental delta at
    its digest, and exactly the bound base candidate `ae9cfd4d` with apps
    302/4/28.
  - No resume, promotion or `app_publish` is allowed in this mode.
  - The `alpha_recover` input is allowed in this mode only.
- **Job `alpha-successor`:** runs on the builder with `contents: read` only (no
  `id-token`, no secrets, no signer login).
  1. `preflight --job successor-builder` checks the predecessor, its ancestry,
     the tree and the unchanged `build.minFreeGiB` 300.
  2. `successor-run` runs `prepare-successor` (with `--recover` only when
     `alpha_recover` is true), then a full re-hash of the successor under the
     lock, then a probe (`lunch` only).
  - **Never:** make, compile, sign, publish, or write the config.
  - **Success:** only the evidence directory is uploaded, as the private
    artifact `emulator-alpha-successor-<run id>` (7 days).
  - **Failure:** nothing is uploaded. A completed transition stays recorded,
    so a rerun is a verified no-op.
  - **Always:** the attempt directory is removed. The successor and probe
    receipts stay beside the predecessor:
    `successors/<successorId>/successor.json` and `probe-rose-cf-alpha-<run id>.json`.
- **Evidence files:** `successor.json`, `probe.json`, `verification.json`,
  `summary.json` and `SHA256SUMS`.
  - `summary.json` records `executedMake`, `compiled`, `signed`, `published`
    and `configWritten`, all false.
  - It also records the predecessor, delta, contract, incremental patch,
    successor id and receipt sha256, full re-hash verdict, probe receipt sha256
    and path, and `lunch`.
  - `toRecord` holds the exact values for the lead to record: `prepared.successor`
    and the `hostEvidence` probe sha256, `lunch` and `probedSuccessorId`.
- **Normal alpha mode:** it stays fail-closed (exit 4) until those values are
  recorded in a config commit.

**Measured successor (INFRA-ALPHA-111).** The lead's run 40757989353 (job
123062259059, recipe `eca1fef9`) completed at 20:15:45 UTC on 2026-10-28.

- **Transition:** `b02dafbb…` moved in place to `156ccb8e` (contract
  `a9c757c0`) by the incremental patch `c9350590`. `alreadyTransitioned` and
  `recoveredFrom` were both unset.
- **Checks:** the full re-hash was OK with 0 failures, and the probe found no
  tree failures. No make, compile, signing, publication or config write ran.
- **Recorded in `cuttlefish-alpha.json`, from the job log's `summary.toRecord`:**
  - `prepared.successor`: `1497d493…` with receipt `5ea7f216…`;
  - `hostEvidence.probeReceiptSha256`: `e4aaa1d6…`;
  - `hostEvidence.probedSuccessorId`: `1497d493…`;
  - `lunch`: identical to the earlier probe, including `erofs`.
- **History:** the predecessor's probe `9c19b5cb…` stays as history only.
- **Guard:** it now passes.
- **Builder and build:** they re-check the real successor tree and receipts on
  the builder.
- **Unproven:** the alpha compile, `file_list_diff`, image and boot.

Dispatch fields for the lead:

| Field | Value |
| --- | --- |
| `mode` | `emulator-alpha-successor` |
| `candidate_sha` | `ae9cfd4d9fafd2db4f2bc6aa76b7d025d6473fb9` |
| `apps` | `[["dev.pixel.voice",302],["dev.pixel.calculator.agent",4],["dev.pixel.backgroundcontrol",28]]` |
| `build_number` | any ten digits (unused) |
| `build_timestamp` | ten digits (the probe's `BUILD_DATETIME`) |
| `channel` | `beta` |
| `promote`, `app_publish`, `alpha_recover` | `false` |
| `resume_failed_stage` | `none` |

The same sequence without Actions, on the builder with the lock free:
`prepare-successor`, then `verify-prepared --preparation-id b02dafbb… --successor-id <id>`,
then `probe --preparation-id b02dafbb… --successor-id <id> --run-id <n> --receipt <new path>`.
Then record `prepared.successor`, plus the probe's sha256, `lunch` and
`probedSuccessorId` in `hostEvidence`, in one config commit.

Source-only contract for the OS owner:

- the Rose changes are applied for the Pixel product. Their effect on the
  Cuttlefish product (shared `handheld_product.mk` packages versus
  Pixel-specific overlays) and the Updater's frankel feed URL inside an emulator
  are not established;
- the historical 2026-10-24 Cuttlefish boot (`docs/upstream/VALIDATION.md`) is
  not evidence for this build;
- the emulator reference scripts (`sdk_phone64`, stale roots, in-place product
  edits) are not used;
- guest operation stays a separate, lead-owned step on the existing runner. That
  step needs provenance checks against the receipt hashes,
  `sys.boot_completed`, UI, viewer, input and Stop evidence, and cleanup.

**Exporting the verified alpha and the separate alpha runtime** (INFRA-ALPHA-120;
source only, the runtime is proposed and not admitted).

The alpha compiled by run 40765631053 (BUILD_DATETIME 1793220134, product `ae9cfd4d`, apps 302/4/28) was fully
verified in place by run 40777816699 (recipe `e07d535`, `emulator-alpha-verify-retained`): super.img 2410014072
bytes, `e543c130…09ed3`. Run 40765631053 wrote no build receipt, and none is invented. Run 40777816699's attempt
directory was removed after its private artifact `emulator-alpha-verification-40777816699` (id 12854389864) was
uploaded, so that artifact is the only copy of its receipt. Pins: `os/release-pipeline/alpha-runtime.json`.

*Export*, os-release.yml `mode: emulator-alpha-export`:
- **Guard (hosted, `actions: read`).** The workflow's own `actions/download-artifact` fetches that artifact from run
  40777816699 with the job token. `scripts/alpha-export.py check-original` then checks:
  - through the API, the run: repository, `.github/workflows/os-release.yml`, main, `workflow_dispatch`, head SHA
    `e07d535…` and success;
  - through the API, the artifact: id, name, run, not expired, and a digest;
  - the receipt itself: exactly the receipt and `SHA256SUMS`, matching; verdict PASS; the original compile with
    `buildReceipt: null`; the matched super.img preimage; every required image and the host package measured;
    imageContent apps 302/4/28 signed by the app signer.
  The receipt's own sha256 was not recorded at hand-off (`receiptSha256: null`), so it is carried forward, and a
  configured value is enforced exactly. The checked receipt goes to the export job as
  `alpha-retained-verification-<run>`. All of this happens before the controller starts the builder.
- **Builder (`alpha-export` job, `contents: read`, shared builder lock).**
  - It re-checks the pinned tree and the retained build identity.
  - It then reads every top-level image and the host package the receipt measured: each must be a regular file of
    the measured size and sha256, and is hashed again after copying.
  - Anything missing, linked or changed fails the export and leaves no partial export.
  - It uploads only `images/*.img`, `host/cvd-host_package.tar.gz`, `original/emulator-alpha-verification.json`,
    `alpha-export.json` and `SHA256SUMS` as `emulator-alpha-export-<run>` (3 days).
  - Files the receipt did not measure (for example `android-info.txt`) are not exported.
  - No lunch, make, signing, secret, login or publication; the attempt directory is removed.

| Field | Value |
| --- | --- |
| `mode` | `emulator-alpha-export` |
| `candidate_sha` | `ae9cfd4d9fafd2db4f2bc6aa76b7d025d6473fb9` |
| `apps` | `[["dev.pixel.voice",302],["dev.pixel.calculator.agent",4],["dev.pixel.backgroundcontrol",28]]` |
| `build_number` | any ten digits (unused) |
| `build_timestamp` | `1793220134` |
| `channel` | `beta` |
| `promote`, `app_publish`, `alpha_recover` | `false` |
| `resume_failed_stage` | `none`; `reuse_binding` empty |

It runs in the `os-release-frankel` group, so it never overlaps an OS build.

*Runtime*, `.github/workflows/alpha-runtime.yml`. It is never os-release.yml, never the builder, and in its own
concurrency group `roseos-alpha-runtime`. Its inputs are the export run, the export artifact's API digest and the
`alpha-export.json` sha256.
- **`guard`** (hosted, `contents: read`, `actions: read`).
  - `ROSEOS_ALPHA_RUNTIME_READY` must be `true` and both new identity variables set, or it stops (SETUP_MISSING)
    before any controller step.
  - The run must be this repository's alpha-runtime.yml on main by `workflow_dispatch`.
  - The export run must be a successful os-release.yml run on main holding exactly one unexpired
    `emulator-alpha-export-<run>` with the dispatched digest.
- **`controller-start` / `controller-stop`** (hosted, `id-token: write`).
  - They log in only with `ROSEOS_RUNTIME_WORKLOAD_IDENTITY_PROVIDER` / `ROSEOS_RUNTIME_CONTROLLER_SERVICE_ACCOUNT`.
  - They make one async start or stop of `pixel-os-smoke-20261023-a` (us-west2-a), then `describe` polling only.
  - The stop job always runs, with the existing 2h automatic STOP as fallback.
  - The os-release provider admits any self-hosted os-release job to the signer, so it is never broadened or
    reused here.
- **`runtime`** (`[self-hosted, roseos-cuttlefish-runtime]`, 30 minutes, `contents: read`, `actions: read`; no
  id-token, secret or publisher credential). It downloads the export and runs `scripts/alpha-runtime.py run`:
  1. **Export check:** only regular files at safe paths, `SHA256SUMS` exactly over them, and the export receipt's
     dispatched sha256. Every image and the host package must equal both the export receipt and the embedded
     original receipt, which is checked again.
  2. **Preflight, changing nothing:** `/dev/kvm` read/write, free space, no foreign Cuttlefish process or instance
     lock, and a new task root. Anything absent is SETUP_MISSING; nothing is installed or granted.
  3. **Extraction:** the host package (no absolute, parent, escaping-link, special or setuid entry) into
     `~/rose-alpha-runtime/<run>-<attempt>` with its own HOME. The runner's historical `cuttlefish*` directories
     and userdata are never touched, and their listing is compared before and after.
  4. **Boot:** instance 21, `--start_webrtc=false`, then `sys.boot_completed` within 900 s.
  5. **Evidence:** `scripts/rose-verify.py runtime --interactive` with the receipt-generated expectation.
  6. **Cleanup, also after failure:** stop the guest, check that no task-root process remains, remove only the task
     root this attempt created, and record each step.
  7. **Output:** `alpha-runtime-evidence-<run>` holds the evidence receipt (unreached steps stay `null`), the
     expectation and rose-verify's output, with no serial, secret or personal data.

Required before the first runtime dispatch (admin, outside this source):
- the runner group `roseos-alpha-runtime`, restricted to this private repository and to
  `.github/workflows/alpha-runtime.yml`, with one runner labelled `roseos-cuttlefish-runtime` on
  pixel-os-smoke-20261023-a;
- a NEW workload identity pool/provider whose attribute condition admits only this repository, that workflow,
  `refs/heads/main` and `workflow_dispatch`;
- a NEW controller service account with only `compute.instances.get/start/stop` on that instance (no project or
  zonal-operations grant);
- the variables `ROSEOS_RUNTIME_WORKLOAD_IDENTITY_PROVIDER`, `ROSEOS_RUNTIME_CONTROLLER_SERVICE_ACCOUNT` and, last,
  `ROSEOS_ALPHA_RUNTIME_READY=true`.

Unverified host facts, each checked at run time and never assumed in a receipt:
- the runner user's `/dev/kvm` access and the Cuttlefish host dependencies on that host;
- that `launch_cvd`/`stop_cvd`/`adb` from the exported host package run there;
- the adb port `6520 + instance - 1`;
- that `launch_cvd --daemon` returns after boot;
- that the image directory needs no file the receipt did not measure;
- the Actions artifact size for the images.

**Public app publication** (INFRA-APP-096, APR024) is optional, using
`app_publish: true`, which the guard allows only with `mode: app-only` or `mode: app-reuse` (below). The hosted
`app-publisher` job downloads the builder's private artifact through the
workflow's own artifact step. It mints the existing pixel-ota App token, which
the action revokes at job end; the builder never receives it. The job then runs
`scripts/app_release_publisher.py`, which:

- **verifies before any API call:**
  - the artifact is exactly the APK, the receipt and `SHA256SUMS`, with every
    digest matching;
  - the receipt is non-simulated and names this candidate and the dispatched
    `[["dev.pixel.voice", N]]`, with N above `appRelease.mustExceed` (307: 303
    was delivered through the authenticated catalog; 304 is public on GitHub
    and catalog-delivered; 306 is public on GitHub as the first direct-GitHub
    client; 307 (`307-dock-label-beta`, candidate `d27dded6`, run 40765131690)
    is the public prerelease `rose-agent-307`);
  - a version in `appRelease.reserved` is accepted only for the app-only route
    and its candidate (any candidate when none is recorded). Any other N must
    exceed every reservation. `host-app-release.py` applies the same rule:
    - 305 belongs to the 2026102805 OS candidate `7fb94ae6`;
    - 308 (`308-home-tray-beta`) belongs to the 2026102806 OS candidate
      `93ad1c2b` (INFRA-CANDIDATE-115, BUILD_DATETIME 1793220605), so the next
      app-only version is 309 or above. Only `mode: app-reuse` publishes 308,
      and only as that build's own signed bytes.
  - the APK's size, sha256 and signer match the receipt, and the APK's own
    v2/v3 signing certificate, read from its signing block, is the pinned app
    signer;
- **requires** OTA Latest to be `appRelease.publication.preserveLatest` before and
  after: `frankel-2026102806` since that Beta was promoted at 22:53:12 UTC on
  2026-10-28 as release 427275514 (INFRA-APP-119). It was `frankel-2026102805`
  from 18:34:04 UTC that day (`rose-agent-306` and `rose-agent-307` were published
  under it) and `frankel-2026102804` before then (`rose-agent-304`). The value is
  one exact tag, compared before and after; any other Latest, older or newer, is
  refused, and moving it is a reviewed config change. The check never moves or
  skips Latest;
- **publishes** its own prerelease, `rose-agent-N`, with `make_latest` false.
  The prerelease holds only `dev.pixel.voice-N.apk`, a sanitized
  `dev.pixel.voice-N.receipt.json` and `SHA256SUMS`. The receipt carries
  package, version, size, sha256, signer, candidate, minimum tested OS,
  repository, tag, asset and `apkUrl`, plus `schemaVersion` 1 and
  `compatibility` (see the receipt section below);
- **never changes a published release:** a published release with identical
  assets is a no-op, any other published release is refused, and its own
  leftover draft is reconciled;
- **checks the result:** it downloads every asset back with authentication, and
  the APK once more anonymously from the exact public URL the client uses;
- **keeps the receipt:** the public receipt becomes the Actions artifact
  `app-publication-dev.pixel.voice-<run id>`.

**Publishing an OS-bundled Rose Agent unchanged** (INFRA-APP-118, `mode: app-reuse`).
This publishes the exact signed Rose Agent a verified OS candidate preinstalls as its own `rose-agent-N`
prerelease. Nothing is rebuilt or re-signed and OS Latest does not move. It uses the same dispatch, guard,
controller, shared builder lock, cleanup and hosted `app-publisher` job (and its App token) as app-only. The builder
job `app-reuse` has only `contents: read`: no signer login, cloud identity, secret, app build, OS compile, signing or
promotion. `scripts/host-app-reuse.py` reads the original attempt `$HOME/rose-actions/frankel-<build>/` without
writing to it and refuses unless:

- `apps` is exactly `[["dev.pixel.voice", N]]`, N is above `mustExceed`, and N is the `appRelease.reserved` row for the
  os-release route whose `candidate`, `osBuild` and `buildTimestamp` equal the dispatched `candidate_sha`,
  `build_number` and `build_timestamp`; the candidate's own `AndroidManifest.xml` declares N;
- the attempt's stage receipt chain is valid and is the original attempt (not a resume) of this candidate and build,
  with build, bytes-verify, sign, ota-verify and transfer completed with exit code 0;
- `unsigned-target_files.zip`, `signed-target_files.zip` and `signed-apps/dev.pixel.voice.apk` have the digests in
  `reuse_binding`; `sign-receipt.json` and the passing, non-simulated `public/verification.json` name that signed
  target-files for this candidate, build and timestamp;
- `background-policy.json` pins Rose Agent to those APK bytes and the app signer, and both target-files preinstall
  exactly those bytes at `PRODUCT/app/RosePreinstall_dev_pixel_voice/`, marked PRESIGNED;
- `aapt` reads the reserved package, versionCode and versionName and the source's minSdk from the retained APK, and
  `apksigner`, run again on those bytes, prints exactly the pinned app signer.

Only the APK, an app-release receipt with a `source` block (route `os-release-reuse`, OS build, timestamp, run,
target-files digests and preinstall path) and `SHA256SUMS` go into the same private artifact app-only uploads. The
publisher then applies every app-only check, plus these:
- the reservation must match the dispatched candidate, build and timestamp;
- the receipt's `source` must equal the dispatch;
- the APK sha256 must be `reuse_binding.apkSha256`.

An app-only artifact never carries `source`, and a reuse artifact is never accepted without it. The public receipt
keeps schemaVersion 1 unchanged, because the app refuses unknown keys. The release body states that the bytes are the
ones the OS build bundles, not rebuilt or re-signed. `publication.json` (an Actions artifact) keeps the `source` block.

`reuse_binding` is `{"sourceRun", "apkSha256", "unsignedTargetFilesSha256", "signedTargetFilesSha256"}`, taken from the
original os-release run. The builder records no run id, so the run is bound through those digests:
- the unsigned digest is the one the run's build stage printed (`unsignedTargetFiles`);
- the signed one is `signedTargetFilesSha256` in the run's transferred `verification.json`;
- the APK's is `background-policy.json` `voice.apkSha256`, which the lead reads on the builder after the OS run.

The run's Actions artifact is not downloaded by this mode.

**The public receipt is the phone's update manifest** (INFRA-APP-102,
APP-GITHUB-002-SCHEMA-001). Rose Agent from App #74 (`DirectUpdate`) reads it
directly from GitHub, with no PixelVoice catalog. `compatibility` holds exactly:

- `deviceCodenames`, `minOsBuild` and `minControllerVersion`: the declared support
  policy in `appRelease.compatibility`, currently `["frankel"]`, `"2026102804"`
  and `28`. This is a conservative floor on the published Beta and its controller.
  It is neither inferred from `minimumTestedOs` nor a claim of phone acceptance.
  - The phone compares `Build.DEVICE` and `Build.VERSION.INCREMENTAL`.
  - The signed 2804 OTA metadata has `pre-device=frankel` and
    `post-build-incremental=2026102804`.
- `minSdk`: measured by `host-app-release.py` from the signed APK
  (`aapt` `sdkVersion`), which must equal the candidate source's
  `minSdkVersion`.
- `abis`: the APK's own `lib/<abi>/*.so` entries.
  - The builder records them and checks them against `aapt`'s `native-code`;
    the publisher re-reads the APK and requires the same list.
  - `["universal"]` only when there are none. An ABI the app does not know
    fails.

A missing or malformed policy, measurement or ABI list fails before any API
call. `scripts/tests/test_app_release_publisher.py` runs the produced receipt
through the app's own parser (`DirectUpdateReceiptProbe` over the unchanged
`DirectUpdate`/`MiniJson` sources): it is eligible only where the policy says,
and any drift from the schema makes it unavailable. The published 304 receipt
(`058c4834…`) predates the schema. The client reports it unavailable, and it is
never republished.

Emulator-alpha and OS modes are unchanged.

`workspace/rose-agent/delivery/catalog.mjs` adds such an artifact to an existing
`/apps` catalog directory (the layout `server/delivery.mjs` reads). Before it
writes anything, it checks that:

- `SHA256SUMS` covers exactly the APK and the receipt;
- the receipt's size, sha256 and signer match the APK and the pin, with an
  optional `--apksigner` re-check;
- the version is above every record of the package.

It then writes the APK, an optional verified `--idsig` and the JSON record last,
each through a temporary file and a rename. It never alters retained records, and
rerunning the same artifact changes nothing. It writes a write-once, nonsecret
publish receipt. With `--public-receipt` (the app-publication receipt, after the
lead has verified the public bytes), the record also carries the canonical
`apkUrl`, `https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-N/dev.pixel.voice-N.apk`.
That receipt's package, version, size, sha256, signer and candidate must equal
the verified artifact, its tag and asset must be the configured names, and the
URL must be exactly that shape, or nothing is written. Without the option,
records are unchanged. A paired-device id from `--device-id-file` goes only into the
catalog record. The writer has no transport: copying the artifact to the service
host is a separate, authorized step, and no persistent automated transport
exists. A catalog entry is not a phone install; an install is verified only on
an actual phone.

Configuration, the host recipe used as adapter input, the public pins and the
exact bootstrap input names are in `os/release-pipeline/frankel.json`. Until they
exist, `scripts/release-pipeline.py preflight` fails with exit 4 and lists them.
That includes any source-tree recipe input the host adapters still need.

The host adapters implement the retained recipe:

- **`host-app-build.py`:** restores the five staged public connection inputs,
  checking them against their digest receipt and `check-public-assets.mjs`. It
  then runs `npm ci --ignore-scripts`, both app `build.sh` scripts and the
  controller compile with the stable SDK layout, and captures each new output by
  comparing directory listings from before and after.
- **`host-reconcile.py`:** runs the candidate's `apply-os-changes.py` on a
  shadow tree made of shared sparse clones. It writes only files that are
  pristine, task-owned (according to its receipts) or under Rose-only roots, and
  backs each one up. Any owner change is a conflict and stops the run with
  nothing written.
  - `patcher: trusted` runs this checkout's `apply-os-changes.py` against the
    candidate's own `os/` bundle, with the hashes recorded. It is a recipe fix
    for an already-pinned candidate whose patcher predates the default
    locked-session patch.
  - `adopt --path P --reason ...` adopts only the named conflicting files, for
    example a makefile that differs only in package order.
  - The projects include `packages/modules/Permission`, sparse
    `service/java/com/android/role/RoleService.java`, for OS #70's
    `rose-home-role-permission.patch`. The patcher applies that patch only to a
    build that preinstalls Rose Agent. Without the project in the shadow tree,
    the patcher fails and nothing is written (INFRA-HOME-098). The file is
    pristine at the pinned `4d6005db` (`8bc87856…`), so the first reconcile
    installs it with a backup and receipt; an owner change to it is a conflict.

**Permission APEX gate** (INFRA-HOME-098, `scripts/permission_apex.py`,
`permissionApex` in `frankel.json`). The Home selection ships in the
`com.android.permission` APEX, so the patched source is not enough; the gate
reads the target-files at both verify stages:

- **Location:** exactly one `SYSTEM/apex/com.android.permission.apex` (or
  `.capex`, whose `original_apex` is checked the same way and must match its
  outer manifest and key), and no Google prebuilt or second copy.
- **Identity:** `apex_manifest.pb` name `com.android.permission` and version
  370399999. That version is the pinned `RELEASE_DEFAULT_UPDATABLE_MODULE_VERSION`
  for `cur` (`cur` -> `aosp_current` -> `cp2a`), which Soong writes over the
  source's placeholder 0.
- **Payload:** `avbtool info_image` must show the `apex_pubkey` key, and
  `avbtool verify_image` must pass. The filesystem is read from its superblock:
  ext4 through `debugfs`, erofs through `host.tools.fsckErofs` (the retained P1
  otatools `fsck.erofs`). Anything else fails.
- **Patch markers:** `javalib/service-permission.jar` must exist, and its dex
  must hold both patch constants, `rose_home_role_selected` and
  `dev.pixel.voice`. Neither is in the pinned upstream RoleService.
- **Keys:**
  - Unsigned: the payload key must be the module's own
    `com.android.permission.avbpubkey` (`1ab7920a…`).
  - Signed, as `generate-release.sh` maps them:
    - payload: `avb.pem`, so `avbPublicKeySha256`;
    - container: `releasekey`, so `otaCertificateSha256`, checked by
      `apksigner` on the APEX and any `original_apex`.
  - The image bytes in `system.img` must equal the checked copy.

A prebuilt, an unpatched module, other keys or a missing pin fails. A missing
pin or tool exits 4 and names the input. The receipt (path, sha256, container,
version, payload key, filesystem, jar sha256, markers, signers) is in the
bytes-verify output and in `verification.json` (`permissionApex`). The static
source-selection evidence and the fixtures it is derived from are listed in
`scripts/tests/fixtures/permission-apex/README.md`. Those files are not build
evidence; only the gate run on a real build is.

The gate proves inclusion in the artifact, not behavior. The Home holder after
an update, at first boot and after a reboot, a later owner choice and the
rendered Rose Home are runtime evidence that needs a compatible booted
candidate.

**SystemUI tray gate** (INFRA-CANDIDATE-115, `check_systemui_tray` in
`scripts/release-pipeline.py`, `systemUiTray` in `frankel.json`). The tray's
screen-off lifecycle (OS #85: the new `AgentTrayLink.java` and the updated
`AgentTrayPrototype.java`) is an overlay into frameworks/base's WindowManager
Shell. At the pinned `84183aff`, Shell's `wm_shell-sources` is `src/**/*.java`
and reaches SystemUI through `WindowManager-Shell`, so a new file needs no
source-list edit; SystemUI is a privileged, multi-dex `system_ext` app, so it
lands at `SYSTEM_EXT/priv-app/SystemUI/SystemUI.apk`; and its R8 rules keep the
Shell classes and strip only `Log.v`. Those blueprints are pinned in
`scripts/tests/fixtures/systemui-84183aff/` with their derivation. Reconcile
installs `AgentTrayLink.java` as a new task-owned file and replaces the
previous candidate's `AgentTrayPrototype.java` with a backup; both appear in
the receipt (`TrayOverlayReconcileTest`). At both verify stages the gate:

- requires the SystemUI APK at `apkPath` in the target-files;
- reads every `classes*.dex` in it and requires all four markers:
  `Lcom/android/wm/shell/AgentTrayPrototype;`,
  `dev.pixel.voice.AgentTrayService`, `Lcom/android/wm/shell/AgentTrayLink;`
  and `Voice binding already released`. The 2026102805 SystemUI has only the
  first two, so an old or wrong artifact fails;
- fails closed when the APK has no dex (for example a dexpreopted APK whose code
  is only in odex/vdex), is not an archive, or the config is missing;
- in signed images, requires the `system_ext.img` bytes to equal the checked
  APK.

The receipt (path, sha256, dex count, markers) is in the bytes-verify output and
`verification.json` (`systemUiTray`). Like the APEX gate, it shows the code is
compiled in, not that the tray stops at screen off.

**What the gates do not count as image inclusion.** Candidate source
directories are not image contents: `workspace/models/` (`rose-chat-core`,
`serving`), `qa/` (`models/`, `first-update/`, `verification/`), `os/tests/`,
and `workspace/` sources other than the three dispatched, presigned APKs. A
change there reaches a phone only through a rebuilt APK or a separate service
deployment, never through this image. The image carries the presigned APKs,
the reconciled overlays and patches, and the framework, Permission APEX,
SystemUI and Updater outputs the gates read.

**Runtime evidence for 2026102806** that only the lead can collect on a real
build (the source cannot derive it):

1. The SystemUI compile itself, and whether the built `SystemUI.apk` keeps its
   `classes*.dex`. If dexpreopt strips them, the gate stops at bytes-verify
   instead of passing; report the APK listing.
2. The bytes-verify and ota-verify `systemUiTray` receipts.
3. On a booted candidate: the tray stops polling at screen off and resumes with
   fresh state at screen on, Home reuse on a HOME press, the removed trial
   switches, and the direct GitHub update check.
- **`host-sign.py`:** derives the release script from the source tree's
  `generate-release.sh`, each edit asserted to occur exactly once with hashes
  recorded:
  - OTA only;
  - an absolute `common.sh`;
  - the uniquely discovered, pinned key directory;
  - RAM-only decryption through `host-decrypt-keys.py`.

  It then maps the exact outputs. `UPDATE.md` is read from the selected
  candidate commit (`os/release-notes/{device}-{build}.md`) and must name the
  build.

Before building, `build` checks the dispatched apps against
`versionBaselines`. Each app must be at least the version in the delivered
2026102701 identities, which reconcile also passes to `apply-os-changes.py` as
`--installed-identities`. Rose Agent must be strictly above the retained,
unpublished P1 (299, build 2026102801). The build number and `BUILD_DATETIME`
must be greater than P1's. Publication evidence does not show which phones
installed a release.

The image gate records the path and sha256 of each tool it actually ran in
`verification.json` (`imageTools`); `simg2img` runs only for sparse images.
Builder start and stop are submitted with `--async` and confirmed by polling
`instances describe` only. A failed request is reconciled against the observed
state, never resubmitted.

The configured tool paths come from the retained P1 run:

- `simg2img`: P1 release `bin/`;
- `debugfs`: `/usr/sbin/debugfs`;
- `fsckErofs`: P1 release `bin/` (the alpha adapter pins the same file);
- `otatools`: the path relative to `OUT_DIR`.

Their existence and versions are checked only in the coordinated host window.

One-time host steps, run during a coordinated builder window:

1. `scripts/host-sdk-layout.py --apksigner-jar <retained jar>` installs the stable
   SDK layout.
2. `scripts/host-app-build.py stage <original inputs> /home/pixelbuild/rose-actions/public-assets <candidate>`
   stages the public connection inputs.
3. `scripts/host-reconcile.py adopt ... --reason "..."` records the hand-made P1
   integration as task-owned. Rerun the read-only preview first, and use
   `--path` for each remaining conflict that was reviewed; never adopt
   everything blindly.

The fake adapter,
used only in `scripts/tests/test_release_pipeline.py`,
runs the real verification code against synthetic artifacts. Everything it
produces is marked simulated, and production refuses it.

A successful build is never release completion. Recovery is explicit:
`mark-interrupted`, then run the stage again. A chain that failed at `bytes-verify`
after a completed build stays terminal and is never edited. To verify the
preserved build again without rebuilding, dispatch the same candidate, build
number and timestamp with:

- `resume_failed_stage: bytes-verify`;
- `resume_unsigned_sha256`: the `unsignedTargetFiles` value that attempt's build
  stage printed.

The builder copies the three build outputs into
`frankel-<build>-resume-<run id>/release`.
`release-stage.py resume` then opens a new chain. That chain is bound to the
failed chain's last receipt hash and records the carried files' hashes, which
must match the expected value and any outputs the failed chain recorded. The
failed stage, signing and everything after it then run in the new chain.

A chain that failed at `sign`, including a resumed one, resumes the same way
with `resume_failed_stage: sign`. Set `resume_source_run` to the failed resumed
attempt's run id, so the source directory is `frankel-<build>-resume-<id>`.
The carried files must still match the hashes that chain recorded.
`bytes-verify` is skipped. `host-sign.py` then moves the failed attempt's
task-owned `releases/<build>` aside, whole, to
`releases/<build>.failed-sign-<run id>` (never deleted). It does this only
when the owner marker matches this candidate exactly and no
`.rose-actions-signed.json` marker exists; a successful signing writes that
marker, and a signed release is never moved or overwritten.

The derived release script keeps `KEY_DIR=keys`, so every path
releasetools records stays relative and reproducible: otacerts.zip entry
names, apkcerts and the misc_info AVB key paths. Some signing subprocesses run
where `keys/...` does not resolve. This happened to the APEX payload re-pack in
run 40641163775 and to signapk for an APK nested in an APEX in run 40643229025,
while top-level APKs signed with the same relative paths. So key paths are made
absolute only at the subprocess boundary:

- `host-sign.py` writes key-path wrappers for `java` (signapk), `openssl`,
  `avbtool` and `delta_generator` into the task-owned release directory. They
  hold only paths, no secrets, and are removed after the script runs. Each one runs
  the next real tool on `PATH` with any `keys/...` or `--opt=keys/...` argument
  rewritten to the absolute path of the release-local `keys` link.
- The derived script puts the wrapper directory first on `PATH` after
  `KEY_DIR=keys` and after every later `export PATH=`.
- The `--extra_apex_payload_key` arguments also name that absolute path.
- The RAM cleanup trap is unchanged.

Runs 40641163775, 40643229025 and 40646237776 failed because a signing tool
could not open a key file that existed earlier in the same run. The last
failure named the absolute path, so the paths themselves are not the cause.
No code path in the adapter or the derived script removes the key family before
the script's EXIT trap runs. Every signing run therefore writes a nonsecret
lifecycle trace, `sign-trace.jsonl`, in the release directory
(`scripts/sign_trace.py`):

- every 0.1 s, role-based booleans for the private directory, the source keys,
  the release `keys` link, its RAM target and the key files through it, plus
  the script's liveness and this uid's logind state and session count;
- one event per wrapped tool start: tool, pid, ppid, and the readability of
  each key argument by basename;
- when the script's own EXIT trap starts, when the script exits, and when
  the private directory is removed;
- once, host facts: uid, the effective logind `RemoveIPC`, linger and the
  `/dev/shm` mount options.

On failure, a one-line JSON summary in the job log names the first key role
that went from present to missing, whether the script was alive or already
exiting at that moment, and the first tool start that found a key unreadable.
The trace never contains key bytes, argv, environment or raw key paths.

A chain that signed successfully but failed at `ota-verify` resumes with:

- `resume_failed_stage: ota-verify`;
- `resume_source_run`: that attempt's run id;
- `resume_signed_sha256`: the `signedTargetFiles` value its sign stage
  printed.

The builder copies the signed target-files, `sign-receipt.json` and the public
files beside the build outputs. Every one of them must match the hashes
recorded in that sign receipt and the expected values. Build, bytes-verify,
signer login and signing are skipped, and the successful release and its
`.rose-actions-signed.json` are never touched.

Signed verification runs the source tree's `check_ota_package_signature.py`
with the release's own otatools `bin` first on `PATH` (`host.tools.otatoolsBin`),
because its payload check runs `delta_generator`. It extracts every
`IMAGES/*.img` beside `vbmeta.img` for
`avbtool verify_image --follow_chain_partitions`. A missing `delta_generator`
or a missing referenced partition image is reported as a verifier dependency
or input failure, never as a pass. The tools that ran are recorded in
`verification.json` (`verifierTools`), and the embedded AVB key and OTA
certificate pins are unchanged.

Receipts hold hashes, times and exit codes, not command output, arguments or
environment.

Promote the candidate and channel metadata only after its acceptance gates pass.
Publishing `frankel-stable` may make an update available to existing users; it is
not a harmless documentation change. Nathan requested a released OS build, but a
blocked/untested candidate must not be advertised as a completed stable release.
Do not overwrite the existing release as a shortcut. No device flashing is
performed by this procedure.

## Signing-key bootstrap (guarded, not a release)

**Fold same-run local tool proof (OCT30-INFRA-S016, source only).** `fold-rango-v1` has no checked-in installed tool
identities. Its four tools (`generator`, `decrypt-keys`, `avbtool`, `openssl`) are proven on the original protected
builder in each run:

1. `signing-bootstrap.py prove --profile P --recipe-sha S --proof-dir D` runs after `preflight` and before signer
   login, under the shared lock. It calls the admitted helper's `verify_local(spec, root='/')`.
2. It writes `D/fold-tool-proof.json`, bound to the profile, recipe, run id, run attempt and helper blob. The job log
   prints only a status word and fixed codes.
3. `run ... --proof-file D/fold-tool-proof.json` loads only that same-run binding. Inside the lock it calls
   `recheck`, and uses the proven paths for that run only; the committed profile is not rewritten. Then the existing
   journal, tool re-hash and both-containers-empty checks run before generation and import.

The helper interface is `validate_spec`, `verify_local`, `recheck` and `Refused(code)`. This route consumes, from the
proof:
- `schema`;
- `profile`;
- `tools.<name>.path` and `tools.<name>.sha256` for each of the four tools;
- `environment`, which must equal `{"PATH": "/usr/bin:/bin", "LC_ALL": "C"}`.

The helper is loaded only when its git blob equals `localProof.admittedBlob`, which is pinned to an exact tested
merge (`admittedRevision`). The custom-crypto PR #190 blob is listed in `localProof.excluded` and refused even if
admitted. The admitted helper is Fold #191, the Product's minimal managed-host helper, merged as `b4e317ad` with blob
`66f52b19`. Its suite (`scripts/tests/test_fold_tool_proof.py`) runs in Release verification. Any other helper bytes
refuse with `proof-helper-mismatch`. `localProof.spec` (`roseos.fold-tool-proof-spec.v2`) names each script's root in
the original builder tree; the helper checks the bytes there against the pinned blobs.

The OpenSSL trust basis is the managed host's own package manager identity and integrity (`managed-host-package`). It
is not an independent signed-package attestation; the proof says so and names the inputs such an attestation would
need.

The builder image lineage (`debian-cloud/debian-12-bookworm-v20261014`, source image `3188631002576736407`) is
recorded as context only. The proof observes the actual guest OS and packages.


`os-release.yml` mode `signing-bootstrap` with `bootstrap_profile` runs `scripts/signing-bootstrap.py` for one reviewed
profile in `os/signing-bootstrap/profiles.json`. It admits no device and runs no build, OS signing or publisher job.
The hosted guard requires `candidate_sha` = the workflow commit on main, a manual dispatch, and default
`promote`/`app_publish`/`resume`/`alpha_recover`/`reuse_binding`. It fails before the controller starts unless the
profile is ready. On the builder, the preflight runs before signer login. Then, under the shared lock, the run checks
tool identities and each secret's metadata (it must exist and hold no versions), generates into an owner-only
transient directory, checks the exact outputs and the archive's public identity, and adds one version per secret. The
two adds are not atomic: committed version ids are journaled under `~/rose-signing-bootstrap`, and an uncertain or
partial import is `recovery-needed`, never complete or retried. Only the public receipt is uploaded.

Current state (OCT29-INFRA-S006): `fold-rango-v1` is resource-bound but blocked.
- **Key inventory.** Its roles and evidence are recorded: releasekey, platform (pinned adevtool `17df3b79`
  `soong.ts`), bluetooth, avb.pem with SHA256_RSA4096, avb_pkmd.bin and optional id_ed25519. The inventory stays
  incomplete until the `-d $KEY_DIR` default set in build/make `b0f697df` releasetools is admitted.
- **Generation.** The generator adapter runs a reviewed, closed-vocabulary `generation.recipe` through one process
  boundary and packs exactly the inventory as `rango/<file>`. Private keys must be encrypted. The recipe is null
  until its source is admitted.
- **Tool identities.** Each tool records its source identity separately from its installed path and sha256. The
  installed identity is a later builder preflight (`plan` lists it as `laterHostPreflight`). None is measured.

`models-v0` is unbound until its own secret resources, signer access, algorithm and package identity are supplied.
The collision check needs `secretmanager.secrets.get` and `secretmanager.versions.list` on both secrets. The add-only
and accessor grants do not include them, so metadata stays unavailable and the run stops. `python3
scripts/signing-bootstrap.py plan --profile fold-rango-v1` prints the exact operations. A merged workflow is not an admitted key operation.
