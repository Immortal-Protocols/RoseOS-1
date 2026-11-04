# Pixel 11 Pro Fold experimental RoseOS build

Target: **Pixel 11 Pro Fold / yogi**, Android 17, stock-kernel experimental port.
This is separate from the supported Pixel 10 family and its `rango` Fold route.
The current scope is to produce a reviewable candidate while leaving the phone
unchanged. No Yogi release, verified installation package or physical acceptance
is established by this guide.

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
export OUT_DIR=/absolute/path/to/new-yogi-output
export BUILD_NUMBER=NEW_MONOTONIC_BUILD_NUMBER
export BUILD_DATETIME=NEW_MONOTONIC_UTC_TIMESTAMP
source build/envsetup.sh
lunch yogi-cur-user
m target-files-package otatools-package
```

Allocate the number and timestamp after observing existing Yogi outputs; do not
reuse the phone's build identity. Confirm lunch and tools on the real builder,
and record the complete resolved manifest, Rose revision/worktree, local patches,
exact app/policy identities, tool versions, exit status, output sizes and hashes.
Compilation is followed by artifact inspection and signing review. Follow
[verification](VERIFY.md) for the evidence boundaries. No flashing, bootloader
locking, reboot or phone data change is part of this build scope.
