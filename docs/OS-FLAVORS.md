# OS flavors and install artifacts

FLAVOR001. A flavor is one device build of RoseOS with its own install-artifact contract:
`os/flavors/frankel.json` (Pixel 10) and `os/flavors/rango.json` (Pixel 10 Pro Fold).
`scripts/factory-package.py` loads a contract, decides what a candidate artifact is, and
packages only a complete factory install archive. Everything here is offline. Nothing
flashes, wipes, relocks, signs or publishes.

## What a contract states

| Field | Content | Source of truth (cross-checked on every load) |
|---|---|---|
| `device` | codename, model, product, board, `user` variant, lunch | `os/devices/pixel10.json`; adevtool `android-info.txt` |
| `provenance` | manifest release 2026100600, vendor build CP3A.261005.005, adevtool 17df3b79, GrapheneOS/script 09d65184 `generate-release.sh` hash | same, plus the fixture's sha256 |
| `firmware` | bootloader `deepspace-17.4-16119390`, baseband `g5400i-260604-260807-B-16035863`, `partition-exists=vendor_kernel_boot` | pinned adevtool fixtures (shared by both devices) |
| `artifacts.ota` | `{device}-ota_update-{build}.zip`: in-place update only, no wipe, no unlock | `host-sign.py` (OTA-only route) |
| `artifacts.factory` | `{device}-install-{build}.zip` from the `generate-release.sh` tail; requires an unlocked bootloader and wipes data; `currentlyProduced: false` | `generate-release.sh` fixture |
| `inventory` | how the partition inventory is derived from the signed target-files | the artifact itself (no list is stored) |
| `identity` | where the AVB public key comes from | Frankel: `os/release-pipeline/frankel.json identities`; Rango: the runtime first-use record |
| `retained` | classification of retained/published builds | release receipts; bytes untouched |
| `missingForFactory` | each missing dependency with its sole owner and the next bounded action | — |

Contract drift (for example a guessed baseband) is a contract error with exit status 2.

## Four separate questions

1. **OTA applicability.** An `ota_update` zip whose metadata `pre-device` equals the device
   can apply in place on a phone already running a build of that device that trusts the same OTA
   certificate. It is never a first install, and its payload is never taken apart into images
   for fastboot.
2. **Factory first-install completeness.** This is the only thing `factoryReady` means (below).
3. **Bootloader-lock eligibility.** This needs `avb_pkmd.bin` equal to the flavor key, which is
   checked, *and* physical acceptance, which is not. Relock, downgrade and first boot are never
   claimed.
4. **Phone validation.** Receipts always say `phoneValidated: false`. Synthetic tests are not
   phone acceptance.

## Classification

`classify` assigns one of these roles: `ota-update`, `target-files`, `img-zip`,
`factory-install` or `unknown`. Only `factory-install` can be `factoryReady`, and only when all of
the following hold:

- **ZIP hygiene.** Member names are unique and relative, contain no `..`, are not links, every CRC
  is valid, and the archive is one `{device}-install-{build}/` directory with no subdirectories.
- **Device and firmware.** `android-info.txt` names the flavor board, bootloader, baseband and
  `partition-exists`. Exactly `bootloader-{device}-{bootloader}.img` and
  `radio-{device}-{baseband}.img` are present, byte-equal to target-files `RADIO/bootloader.img`
  and `RADIO/radio.img`. Any other version is `mixed-firmware`.
- **Inventory.** This is derived from the signed target-files the archive came from:
  `META/ab_partitions.txt`, `META/misc_info.txt` (`ab_update=true` required, `dynamic_partition_list`,
  `virtual_ab`) and `OTA/android-info.txt`. Each non-dynamic A/B partition with an
  `IMAGES/{p}.img` must appear as `{p}.img`, byte-equal and with a sane header (`ANDROID!`,
  `VNDRBOOT`, `AVB0`). A/B partitions without an image are recorded as firmware-carried. Dynamic
  partitions require contiguous sparse `super_1..N.img`. Any extra image is refused.
- **Identity.** `avb_pkmd.bin` must equal the flavor key, and the build number must match the
  archive name and target-files `ro.build.version.incremental`.
- **Install mode.** `flash-all.sh`/`script.txt` exist and declare `erase userdata`, matching
  `dataWipe: true`.

A refusal lists every precise `missing` input and `problems` code, such as
`factory:boot.img`, `target-files:RADIO/radio.img`, `wrong-device:…`, `mixed-firmware:…`,
`duplicate-entry:…`, `unsafe-path:…`, `corrupt-image:…`, `image-mismatch:…`,
`identity-mismatch:…` or `not-factory:ota-update`.

**Known offline limits.** Dynamic partition contents inside `super_N.img` are not decomposed
(that needs otatools `lpunpack`). The `.sig` is copied but not verified. The `RADIO/`/`IMAGES/`
layout follows AOSP releasetools convention; no Frankel or Rango target-files listing is in the
repo. A missing entry refuses; it never passes.

## Current state

| Build | Artifacts | Factory-ready | Phone-validated |
|---|---|---|---|
| frankel 2026102901 (Beta) | OTA | no | — |
| rango 2026103001 (release 429262439, OTA `2613738b…1bbf`) | OTA | **no** | no |

`rango-2026103001` is **OTA-only**. Its published notes say "manual installation". That can
only mean an in-place OTA on a Fold already running a matching Rango build. It is not a first
install, and there is no validated factory/first-install path for either flavor today. The
published bytes, notes and receipts are preserved unchanged.

The reported unresponsive Fold followed manual OTA partition extraction and flashing. Its cause
and permanence are unverified. Hashes and `OKAY` lines are not proof of boot.

## Callable contract (for release integration)

```bash
python3 scripts/factory-package.py contract --flavor rango
```

```bash
python3 scripts/factory-package.py classify ARCHIVE --flavor rango --target-files SIGNED_TF \
  --build 2026103001 --avb-public-key-sha256 HEX
```

```bash
python3 scripts/factory-package.py package ARCHIVE --flavor frankel --target-files SIGNED_TF \
  --build B --out NEW_DIR
```

```bash
python3 scripts/tests/test_factory_package.py
```

Exit status: 0 ready/packaged, 3 refused (JSON on stdout), 2 usage/contract error.
In Python: `classify(path, flavor, target_files, build, avb_key)` returns the result dict, and
`package(...)` raises `Refused(result)` and creates no output on refusal.
`package` writes `NEW_DIR/{archive}`, `{archive}.sig` if present, and `factory-package.json`
(schema `roseos.factory-package.v1`).

## Missing for a real factory package (sole owner → next action)

- **Factory stage. Owner: signing.** The tiny hunk is `scripts/host-sign.py`
  `derive_release_script`, `cut = once(r'\nimg_from_target_files', original)`. Add an opt-in mode
  that keeps the tail through `fastboot … optimize-factory-image` and maps
  `{device}-install-{build}.zip` (+ `.sig`) into the release dir, then run `classify` on it
  before any upload.
- **Rango AVB key pin. Owner: signing.** Copy it from the durable first-use record into reviewed
  source, or keep passing `--avb-public-key-sha256`.
- **Observed target-files listing. Owner: release builder receipts.** Record the names only of
  `META/`, `IMAGES/` and `RADIO/` for one signed frankel and the retained rango target-files.
- **Physical first-install acceptance. Owner: QA, with an explicit device handback.** This comes
  only after a classifier-ready archive exists.
- **Local suite. Owner: shared harness.** Add `python3 scripts/tests/test_factory_package.py` to
  `scripts/test-local.sh`.
