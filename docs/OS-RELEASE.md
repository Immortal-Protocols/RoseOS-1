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
using this builder for this task, with no spending limit. Preserve existing
sources, caches, disks, keys and outputs. Do not assume no jobs run after boot.

Use the authenticated Dev Infra account and supported IAP SSH. Before building,
record OS/tool versions, free storage/memory, mounts, current jobs, source
locations/revisions, local changes, vendor inputs, signing boundaries and output
locations. Acquire an exclusive build lock and use a new output directory. Do
not reset or clean existing trees to make a bootstrap script succeed.

The current environment's IAP connection is blocked by proxy CONNECT 403 at
`tunnel.cloudproxy.app`. A supported network-policy update is pending. No remote
shell command reached the builder during the failed connection. Do not replace
IAP with direct network access to route around the denial.

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

Promote the candidate and channel metadata only after its acceptance gates pass.
Publishing `frankel-stable` may make an update available to existing users; it is
not a harmless documentation change. Nathan requested a released OS build, but a
blocked/untested candidate must not be advertised as a completed stable release.
Do not overwrite the existing release as a shortcut. No device flashing is
performed by this procedure.
