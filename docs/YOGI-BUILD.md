# Pixel 11 Pro Fold experimental RoseOS build

Target: **Pixel 11 Pro Fold / yogi**, Android 17, stock-kernel experimental port.
This is separate from the supported Pixel 10 family and its `rango` Fold route.
Candidate `2026110401` was compiled, inspected and installed on the owner's test
phone with an explicitly authorized data wipe. The owner reported that it
worked. This is an experimental lab result; OTA delivery, production signing
and a complete physical acceptance pass remain unestablished.

## Inputs and evidence

`os/fold-port/source.json` binds the public source inputs. Core source revisions
come from `os/pinned-manifest.xml` (GrapheneOS `2026100600`). The pinned adevtool
device definition includes `common/gen11pixel.yml`, which selects Malibu and
`CD1A.260714.001.A9`. Its factory index names
`yogi-cd1a.260714.001.a9-factory-0c09a12c.zip`, SHA-256
`0c09a12ce9e64aac17b4389cc926beade475ca3b8b77008be8e0742444fefcf6`.
These are reconstruction inputs, not a current stable security baseline. Reading
the index does not verify the archive or prove it matches the installed kernel.

The factory archive was downloaded and its full hash verified on the dedicated
builder. The pinned generator does not reproduce the older checked-in Yogi
reference: it emits library backport metadata, an ELF compatibility flag, updated
policy metadata and storage intelligence file declarations. The explicit
`os/fold-port/adevtool-yogi-reference.patch` records that Yogi-only delta, including
its file-tree specification. `source.json` binds the patch and each postimage.
This is a new experimental reference, not a pass against the older reference.
No proprietary binaries are committed. Offline fixtures exercise guards and patch
context; compilation and physical acceptance require separate evidence.

Keep the actual phone inventory, extracted APKs, paired-device state and signing
materials outside Git. Before choosing an update route, obtain:

- Installed OS build, firmware and AVB/OTA identity; the original build recipe
  and resolved source manifest if available.
- Exact installed Rose Agent, controller and target APK bytes, versions and
  signing certificates. Verify APK signatures with the matching SDK tools;
  extracting a certificate fingerprint alone is not signature verification.
- The intended app bundle and policy, checked against those installed identities
  with `--installed-identities`. A numerically lower controller is not an update,
  and changing its version alone does not establish protocol compatibility.

## Prepare on an isolated Linux builder

Inspect source, jobs, storage and locks first, as in [OS release](OS-RELEASE.md).
Use a separate source/output tree for Yogi. Preserve the other devices' trees,
outputs, keys and caches. The desktop Mac is an inspection client, not the
supported x86_64 Linux OS build host.

Reconstruct the pinned source and verify its upstream provenance. Verify the
factory archive against the full hash above. Under the existing builder lock,
check and apply the byte-bound reference patch to the pinned clean
`vendor/adevtool` project, using `git -C vendor/adevtool apply --check` and
`git -C vendor/adevtool apply` with its absolute patch path. Then use
**device-scoped** adevtool generation
(`generate-all -d yogi --doNotDownloadCarrierSettings`) in that isolated tree.
Keep normal reference verification enabled; do not use `--noVerify` or update the
reference during the verification run. Never use another
device's factory archive or the all-device generation default. Inventory the
generated vendor and stock-kernel files before proceeding.

Check the three port patches before the normal Rose integration:

```sh
python3 scripts/prepare-yogi-port.py check /absolute/path/to/yogi-source
python3 scripts/prepare-yogi-port.py apply /absolute/path/to/yogi-source \
  --lock-file /absolute/path/to/existing-builder.lock
```

The preparer requires the pinned clean `external/hardened_malloc`,
`external/selinux` and `vendor/state` projects. For `vendor/adevtool` it admits only
the exact reviewed reference overlay on the pinned revision, checks its reverse
applicability, and rejects all other local changes. The four patched files must
match byte-exact preimages, including the generated BoardConfig and product file.
It refuses drift, links, another device's vendor files and a held lock.
An existing customized build tree must be inventoried and
reconciled; do not reset it to make this check pass.

The patches retain the smaller allocator class region, the device-scoped
missing-kernel-interface exception, the Rose tray and the app-installation
workaround. The ordinary allocator default and other devices' routes stay as
defined upstream. This port does not have the security parity of a supported
GrapheneOS kernel.

## Integrate and compile

Use the normal [OS integration](OS-INTEGRATION.md) for the signed app bundle,
background policy, framework, launcher and viewer/Stop controls. Measure the
Fold's actual reference display address before selecting its cover display.
Do not reuse another phone's display address or change package identities.

Yogi has no established Rose updater feed or signing contract. Resolve those
before preparing an installable update; do not direct a custom-signed image at
GrapheneOS's update server or at the Frankel Latest assets. Source preparation
does not supply signing keys or authorize a clean install/data wipe.

The pinned product's stock-kernel selector requires `USE_STOCK_KERNEL=true`.
Once vendor inputs and Rose integration are verified, the candidate's build
environment must include:

```sh
export USE_STOCK_KERNEL=true
export SOONG_NINJA=ninja
export OUT_DIR=out/UNIQUE_YOGI_ATTEMPT
export BUILD_NUMBER=NEW_MONOTONIC_BUILD_NUMBER
export BUILD_DATETIME=NEW_MONOTONIC_UTC_TIMESTAMP
source build/envsetup.sh
lunch yogi-cur-user
m target-files-package otatools-package
```

Use a fresh relative output directory under the Android source root. The pinned
default Siso backend failed bootstrap with `@config//main.star` when using an
external absolute output directory; the Ninja retry also rejected that absolute
path during graph generation. The pinned build system supports
`SOONG_NINJA=ninja`; use it with the relative output path and record both in the
build receipt. This changes execution tooling, not source inputs.

Allocate the number and timestamp after observing existing Yogi outputs; do not
reuse the phone's build identity. Confirm lunch and tools on the real builder,
and record the complete resolved manifest, Rose revision/worktree, local patches,
exact app/policy identities, tool versions, exit status, output sizes and hashes.
Compilation is followed by artifact inspection and signing review. Follow
[verification](VERIFY.md) for the evidence boundaries. The preparation and
compilation commands above perform no phone operations. The separately
authorized lab installation below is not a general installation procedure.

## Candidate 2026110401: measured build evidence

On 2026-11-04 UTC, Rose source
`c47898373a72fc856517a91c0cf13e75c8b80662` compiled `yogi-cur-user` with
`USE_STOCK_KERNEL=true`, `SOONG_NINJA=ninja`, 48 jobs and a relative output
directory. Both requested goals completed in 01:04:58 with no failed Ninja steps.
Earlier absolute-output attempts and a later receipt-path error are retained in
the private build record; the latter occurred after successful compilation and
was recovered by locating the actual Soong tools package, without a rebuild.

- Target-files: 4,472,190,371 bytes, SHA-256
  `bcf6b5b9a7a1b84977543f764489e7d1bbc7301d7829bccaf32be750c2e04431`.
- OS tools: 536,791,285 bytes, SHA-256
  `34bf8e37e2c9b4238c79777ab591839fdd5ee974709da72ecd036df57c40304e`.
- Both archives passed full ZIP CRC checks. Image properties identify Yogi,
  Android 17 / API 37, build `2026110401`, SPL `2026-10-05`, `user`,
  `test-keys`, and `ro.debuggable=0`. The tray and data-loader workaround are
  present. Soong selected the 2 GiB allocator class region.
- All 211 packaged APK identities decoded. Rose Agent 311, Calculator 4 and
  controller 28 exactly match the verified input APKs and passed SDK 37 signature
  inspection. Rose Agent declares HOME and contains the Oxblood Home classes;
  that is not a rendered or runtime HOME-role result. No Updater is included.
- AVB signatures and partition hashes/hashtrees passed verification against the
  pinned **public AOSP development key**, public-key SHA-256
  `7728e30f50bfa5cea165f473175a08803f6a8346642b5aa10913e9d9e6defef6`.
- The selected offline suite and required CI no-unexpected-skip cases passed.
  The updated artifact verifier passed 53 Linux tests; the Yogi preparer passed
  13. Verification tool revision:
  `432d2b2154ac314afdc2bd38f9ee91fce166e3f4`.

The pinned optimizer warned that API 37 exceeds its supported API 36 table;
compilation continued successfully. The factory kernel release string matches
the observed phone kernel, but installed kernel bytes and module loading were
not verified. These are experimental development artifacts, not a production
release or an OTA package. Before the wipe, the phone had a different app signer
and controller 43; controller 28 was therefore not an in-place update. The lab
installation replaced that baseline. Re-read the actual installed versions and
certificates before allocating any subsequent update; the packaged lab inputs
are Rose Agent 311, controller 28 and Calculator 4.

Private build receipts and artifacts belong in the ignored
`out/yogi-2026110401/` directory and on the dedicated builder. Preserve the tested
source tree. The later merge with main includes newer UI changes that have not
been compiled or accepted on Yogi by this build's evidence.

## First lab installation: owner-reported success

On 2026-11-04 UTC, the owner identified this as a test phone with expendable data
and authorized the install. Preflight selected only the connected Yogi, checked
its unlocked bootloader, matching stock bootloader/radio, battery, partition
sizes and absence of an active snapshot. The inactive slot was reported
unbootable; it is not an established recovery baseline.

A local fastboot installation ZIP was assembled from the verified target-files,
retaining the original image bytes and package instructions. It is
2,173,200,684 bytes, SHA-256
`93f5b24d789193abf1d97923b9c6ca168adbb403e4e9e9e1ad1336770d872a73`.
It contains Android partition images, including `pvmfw`, but no replacement
bootloader or radio image. The scoped fastboot update targeted slot B, skipped
the secondary slot and automatic reboot, and wiped userdata/metadata. It
completed with exit status 0 in 82.337 seconds. Post-flash fastboot inspection
confirmed the original bootloader/radio versions and unlocked state before the
explicit reboot. No bootloader relock or verification-disabling flags were used.

After first boot, the owner replied **"That worked!"** This records a successful
installation/boot as reported by the person holding the phone. Automated ADB
shell and sync requests returned `error: closed` after reboot, so there is no
post-boot property inventory, rendered HOME capture, hardware smoke pass or
verified installed-build readback. Those checks remain for the OTA handoff.

The lab uses public AOSP development signing keys and includes no OS Updater.
It cannot discover a future Yogi OTA as installed. Establish an owned updater
route and a durable private signing/bootstrap plan before treating Yogi as an
OTA-supported device; do not assume a data-preserving migration from the lab
keys. APK re-signing must also update the framework's bound signer policy.

Installation receipts and the lab ZIP remain outside Git in the ignored
`out/yogi-flash-20261104-1/` directory. A verified matching Google factory archive
is staged privately for recovery; it restores stock Google Android, not the
previous custom GrapheneOS build or its erased data. Nothing was published and
no OTA workflow or automatic trigger was enabled by this lab test.
