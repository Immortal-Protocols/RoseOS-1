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

Substitute actual artifact paths and the target OS's SDK number. Confirm the
policy's final allowlist before applying. RoseOS's product baseline calls for a
pinned app allowlist and explicit owner approval for installs, messages,
purchases and account changes. The imported `--prototype-control` and
`--locked-control` switches are historical capabilities, not release defaults.
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

A successful build is never release completion. Recovery is explicit:
`mark-interrupted`, then run the stage again. Receipts hold hashes, times and exit
codes, not command output, arguments or environment.

Promote the candidate and channel metadata only after its acceptance gates pass.
Publishing `frankel-stable` may make an update available to existing users; it is
not a harmless documentation change. Nathan requested a released OS build, but a
blocked/untested candidate must not be advertised as a completed stable release.
Do not overwrite the existing release as a shortcut. No device flashing is
performed by this procedure.
