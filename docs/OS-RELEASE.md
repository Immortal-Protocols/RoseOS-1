# RoseOS build and public release

Target channel: **Pixel 10 / frankel**, public repository
[Immortal-Protocols/pixel-ota](https://github.com/Immortal-Protocols/pixel-ota).
Its README specifies `frankel-stable` metadata and the matching
`frankel-ota_update-BUILD.zip`. Factory images and checksums may accompany it.
Do not use the imported Mustang (Pixel 10 Pro XL) scripts as Frankel instructions.

This is the release procedure and its remaining gates, not a claim that a new
OS release has already been built. Refer to [VALIDATION.md](VALIDATION.md).

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
     PRESIGNED state and signers, and the compiled framework policy.
   - `scripts/host-sign.py` validates the pinned OS key archive, checks the
     public certificate and AVB key against the pins, and runs the source
     tree's release command.
   - The job verifies again: OTA metadata, OTA package and payload signatures,
     the vbmeta signature and AVB key, and the public pins.
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

**Public app publication** (INFRA-APP-096, APR024) is optional, using
`app_publish: true`, which the guard allows only with `mode: app-only`. The hosted
`app-publisher` job downloads the builder's private artifact through the
workflow's own artifact step. It mints the existing pixel-ota App token, which
the action revokes at job end; the builder never receives it. The job then runs
`scripts/app_release_publisher.py`, which:

- **verifies before any API call:**
  - the artifact is exactly the APK, the receipt and `SHA256SUMS`, with every
    digest matching;
  - the receipt is non-simulated and names this candidate and the dispatched
    `[["dev.pixel.voice", N]]`, with N above `appRelease.mustExceed` (303,
    because the 303 bytes were built and never published);
  - the APK's size, sha256 and signer match the receipt, and the APK's own
    v2/v3 signing certificate, read from its signing block, is the pinned app
    signer;
- **requires** OTA Latest to be `frankel-2026102804` before and after;
- **publishes** its own prerelease, `rose-agent-N`, with `make_latest` false.
  The prerelease holds only `dev.pixel.voice-N.apk`, a sanitized
  `dev.pixel.voice-N.receipt.json` (package, version, size, sha256, signer,
  candidate, minimum tested OS, repository, tag, asset and `apkUrl`) and
  `SHA256SUMS`;
- **never changes a published release:** a published release with identical
  assets is a no-op, any other published release is refused, and its own
  leftover draft is reconciled;
- **checks the result:** it downloads every asset back with authentication, and
  the APK once more anonymously from the exact public URL the client uses;
- **keeps the receipt:** the public receipt becomes the Actions artifact
  `app-publication-dev.pixel.voice-<run id>`.

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
