# OS flavors and install artifacts

FLAVOR001, FLAVOR002 and FOLD-FACTORY-003 to -005. A flavor is one device build of RoseOS with its own install-artifact
contract: `os/flavors/frankel.json` (Pixel 10), `os/flavors/rango.json` (Pixel 10 Pro Fold) and
`os/flavors/blazer.json` (Pixel 10 Pro).
`scripts/factory-package.py` loads a contract, decides what a candidate artifact is, verifies a
factory install archive in three separate tiers, and packages it only when all three pass.
Everything here is offline. Nothing flashes, wipes, relocks, generates keys or publishes.

## What a contract states

| Field | Content | Source of truth (checked on every load) |
|---|---|---|
| `device` | codename, model, product, board, `user` variant, lunch | `os/devices/pixel10.json`; adevtool `android-info.txt` |
| `provenance` | release 2026100600, CP3A.261005.005, adevtool 17df3b79, GrapheneOS/script 09d65184 `generate-release.sh` hash | same, plus the fixture's sha256 |
| `firmware` | bootloader `deepspace-17.4-16119390`, baseband `g5400i-260604-260807-B-16035863`, `vendor_kernel_boot` | pinned adevtool fixtures |
| `artifacts` | OTA: in-place only, no wipe or unlock. Factory `{device}-install-{build}.zip` (+ `.sig`): unlock and wipe; `currentlyProduced: false` | `host-sign.py` (OTA only); `generate-release.sh` |
| `factorySigning` | namespace `factory images`, pinned principal and `ssh-ed25519` public key; currently `null` (status `missing`) | `generate-release.sh` `ssh-keygen -Y sign -n "factory images"` |
| `identity` | AVB public key digest (Frankel: `os/release-pipeline/frankel.json`; Rango: published 2026103001 pin) | below |
| `inventory` | how the inventory is derived; `observed` (empty until release delivers one); generator fixtures | the artifact's own target-files |
| `missingForFactory` | each missing dependency: sole owner and next bounded action | — |

A contract that drifts from its sources is a contract error (exit 2). Examples: a guessed
baseband, a namespace that differs from the generator's, or a half-filled signing pin.

**Rango AVB identity.** The pin is `aee94794…3fd8f27`: the file sha256 of the public `avb_pkmd.bin`
in GitHub release 429262439 (`rango-2026103001`), as recorded by the completed authorized
publication receipt.
- It is an AVB key-metadata blob digest only. It is not the OTA certificate, not an SSH factory
  key, and not a reread bootstrap journal (held artifact 12961047183 is never retrieved).
- A `--first-use-binding` record that disagrees is `identity_conflict`.
- A pin equal to the Frankel key is `signer_collision`.

**Pins across flavors (FOLD-FACTORY-004).** Each load reads every other `os/flavors/*.json` as raw
JSON, never through the full loader, and resolves its AVB pin strictly.
- A non-null pin shared by any two flavors (Frankel, Rango, Blazer, …) is `signer_collision`, so
  both loads refuse.
- `null` means unknown and never matches, including null against null.
- A malformed peer refuses the load and is never skipped. That covers an unreadable file, a missing
  or non-object identity, a non-sha256 literal, and a pointer that doesn't resolve to a sha256.
- No separate archive-signing key is required by this check.

**Readiness declaration (optional `readiness` block).** The block holds `buildReady`,
`factoryReady` and `phoneValidated`, which must be strict booleans; `receipts`, a list of
non-empty references; and an optional `basis`. No other keys are allowed.
- Any `true` is an unsupported claim and refuses: source never declares a result. Artifacts are
  verified by `classify`/`package`, and phones by QA.
- A receipt reference naming synthetic evidence refuses.
- Contracts without the block are unchanged, and a passing synthetic classification never changes
  a declaration.

## Three tiers; `factoryReady` needs all of them

`classify` assigns one of these roles: `ota-update`, `target-files`, `img-zip`,
`factory-install`, `factory-intermediate` or `unknown`. Only `factory-install` can be ready.

**Generator layout (pinned).** `scripts/tests/fixtures/factory-package/device_common-1f5ba267/` holds
device/common `generate-factory-images-common.sh` and `clear-factory-images-variables.sh`,
byte-identical to upstream (blob ids in `PROVENANCE.json`). They are read as text and never executed.
Every layout claim in `artifacts.factory.generatorMembers` must appear verbatim in them, or the
contract is refused. With the variables `generate-release.sh` sets for rango and frankel, the
generator does the following:
- copies `RADIO/bootloader.img` → `bootloader-{device}-{bootloader}.img`;
- copies `RADIO/radio.img` → `radio-{device}-{baseband}.img`;
- copies `$AVB_PKMD` → `avb_pkmd.bin`;
- copies every `VENDOR/firmware/dauntless/*` (GSC firmware) into the top directory;
- writes `flash-all.sh`/`.bat` (product check, both-slot bootloader, radio, `avb_custom_key`,
  `oem uart disable`, `dpm` erase, `fastboot -w --skip-reboot update`);
- zips the result as `{device}-factory-{build}.zip`. That intermediate is refused: only the
  `optimize-factory-image` install archive is signed and shipped.

**Optimizer layout (pinned, FOLD-FACTORY-005).**
`scripts/tests/fixtures/factory-package/platform_system_core-322ff512/` holds a line-numbered excerpt
of GrapheneOS platform_system_core `fastboot/`, with blob ids, sha256 and the BSD `LICENSE`. It is
read only and never executed. The loader checks the excerpt digest, every `optimizeMembers` anchor,
the `fastbootImages` table and `max-download-size` (`0x10000000` from `generate-release.sh`).
`FlashCapturer::Run` (`fastboot -S <max> optimize-factory-image FACTORY.zip OUTER`) works like this:
- **Input checks.** The factory zip needs exactly one stored `image-<product>-*.zip`, plus
  `flash-all.sh` and `.bat` with their `PROLOG_END` markers. No other script except `flash-base.sh`
  is allowed.
- **Flat copy.** Every other member is copied flat by basename into `OUTER/`.
- **Script conversion.** The `flash-all.sh` commands become `script.txt`:
  - `check-var product`;
  - `check-var slot-count 2`;
  - exactly two other-slot bootloader flashes;
  - then `check-var max-download-size`, `run-cmd set_active:a` and `check-var current-slot a`.
- **Update replay.** The update zip is replayed through flashall:
  - `android-info.txt`, plus a generated `android-info.zip` holding exactly that file, and
    `check-requirements`;
  - `snapshot-update cancel`;
  - each non-logical `fastbootImages` partition as `{p}.img`, raw or Android sparse when large.
    `.sig` files are fatal;
  - `super` as split sparse `super_1..N.img`, each flashed in order;
  - `erase` lines only from `fastboot-info.txt` `if-wipe` entries.
- **Script regeneration.** `flash-all.sh` and `.bat` are regenerated with the prolog kept.

A target-files image whose partition is not in `fastbootImages` is never flashed by the install
archive, so it needs the same firmware observation as a partition without an image.

1. **`structurallyComplete`**
   - ZIP hygiene: names unique and relative, no `..`, no links, CRCs valid, one
     `{device}-install-{build}/` directory.
   - `android-info.txt` and exactly the matching `bootloader-`/`radio-` images, byte-equal to
     target-files `RADIO/`.
   - Every `VENDOR/firmware/dauntless/` file present byte-equal under the same name. No other
     top-level member is allowed.
   - Inventory derived from the signed target-files (`META/ab_partitions.txt`, `misc_info.txt`
     with `ab_update=true`, `OTA/android-info.txt`). Every non-dynamic A/B image appears byte-equal
     with a sane header.
   - A/B partitions without an `IMAGES/` entry count only when a
     `roseos.factory-firmware-observation.v1` lists them. That observation must be bound to the
     archive's exact bootloader/radio digests; absence alone never counts.
   - Contiguous sparse `super_1..N.img`; `avb_pkmd.bin` equal to the flavor key; the build number
     matches; the install flash script declares `erase userdata` (WipeTask capture; the `if-wipe` source is not pinned).
   - The signed target-files must match the release-delivered
     `roseos.target-files-observation.v2` (digest and member names, now including `VENDOR/firmware/`).
2. **`contentVerified`**
   - Inputs: `--otatools ZIP --otatools-sha256 H`, the pinned otatools-package of that build.
     `bin/simg2img`, `bin/lpunpack` and `lib64/` are taken from that archive, never from the
     candidate.
   - All work happens in one job-owned directory under `--work-dir`, capped by
     `--max-work-bytes`, and is removed on success and failure.
   - `simg2img super_1..N raw`, then `lpunpack raw dir`, as the historical mustang verifier did.
   - The unpacked set must equal `dynamic_partition_list`, otherwise `dynamic-missing` or
     `dynamic-extra`. `_b` outputs must be empty (`mixed-partition`).
   - Each logical image must be byte- and size-equal to target-files `IMAGES/{p}.img`, flattened
     with the same `simg2img` when sparse.
   - Tool failures (truncated or corrupt segments, non-super input) refuse.
3. **`signatureVerified`**
   - `ssh-keygen -Y verify -f <allowed_signers built only from the pin> -I principal -n "factory images" -s ARCHIVE.sig`.
   - An adjacent key or `allowed_signers` is never read, and presence of a `.sig` proves nothing.
   - AVB key equality is a separate check.

A refusal lists every precise `missing` input and `problems` code. The receipt
(`factory-package.json`, schema `roseos.factory-package.v2`) binds:
- archive and signature sha256;
- signed target-files sha256;
- otatools, `simg2img`, `lpunpack` and verifier digests;
- the observed dynamic inventory (sha256 and size per partition);
- evidence sources, including any `synthetic` flag.

`phoneValidated` is always false. Relock, downgrade and first boot are never claimed.

**Separate questions.** OTA applicability (in place, same OTA certificate), factory
completeness (the tiers above), bootloader-lock eligibility (AVB key equality *and* physical
acceptance) and phone validation are four different claims.

## Current state

| Build | Artifacts | Ready | Phone-validated |
|---|---|---|---|
| frankel 2026102901 (Beta) | OTA | no | — |
| rango 2026103001 (release 429262439, OTA `2613738b…1bbf`) | OTA | **no** | no |
| blazer (no build) | — | no | no |

`rango-2026103001` is OTA-only. Its notes' "manual installation" can only mean an in-place OTA
on a matching installed build. Published bytes and receipts are unchanged. The Fold incident's
cause and permanence remain unverified.

**Historical observations** (`inventory.historicalObservations`). These are identity facts from
original Infra's authorized artifact inspection. They are not current claims, not a names-only
`observe` output, and they never change `classify` or readiness. The loader requires
`historical: true`, a `source`, a non-empty `doesNotEstablish`, lowercase sha256 digests and no
readiness or safety keys, and every `sha256From` pointer must resolve to the same digest.
- **Rango**: Fold run 41265984964, artifact 13067510533 (ZIP `2f51fbc8…4d58`).
  - Build 2026103001 signed target-files `73db76e5…0baf0`: 4,927,914,252 bytes, 2,874 real members,
    including bootloader, radio and firmware.
  - otatools `22d62464…b664`, equal to `os/retained-rango/release.json`.
  - fastboot `f18fdc8b…47a9`, `--version` exit 0, `37.0.0-2026103001`.
  - Firmware member hashes are not a carried-partition mapping, an optimized install archive or super
    validation.
  - The archive SSH signing identity stays null. The AVB pin `aee94794…` is not that key.
- **Blazer**: Pro run 41255851279, artifact 13063685882 (ZIP `8218c267…7d5e`).
  - Observed: the build-only configuration, kernel and adevtool vendor pins.
  - Absent: the factory image `blazer-cp3a.261005.005-factory-cd7d12c2.zip` (`cd7d12c2…18e3`), the
    unpacked factory, `vendor/google_devices/blazer`, product registration and firmware metadata.
  - Not executed: lunch and make. Node version unknown.
  - The build route is the existing `os/build-only/blazer.json` recipe (`scripts/blazer-build-only.py`),
    `blazer-reconcile.json` integration (`scripts/blazer_integration.py`) and
    `blazer-updater-route.json`. `buildRoute` references them without duplicating them.
  - The recipe's operator capacity allowances are still null and unresolved; this adds no new gate.

**Test evidence is synthetic.** Tests use fresh throwaway OpenSSH keys, the real AOSP `simg2img`
where the runner has it (otherwise a double that decodes the real sparse format), and an
`lpunpack` double reading a synthetic `SYNTHLP1` super container (no real `lpunpack` is
available offline). Synthetic observations are accepted only through the Python API flag
`allow_synthetic`; the CLI refuses them.

## Blazer preparation interface (BLAZER-PREP, for Release integration)

`scripts/blazer-build-only.py {plan|preflight|run} --stage S --host LABEL --lock PATH [...]` and
`... inspect --host LABEL --lock PATH [--source-root ISO] [--download-dir D] [--node N]` (recipe
`os/build-only/blazer.json`). `plan`, `preflight` and `inspect` read only. `run` needs a new `--receipt-dir`
and writes `preflight.json`, `started.json` and `completed.json` there, write-once.

| Stage | Required inputs | Does | Bound (proposal) |
|---|---|---|---|
| `download` | `--download-dir D` | Streams the pinned `https://dl.google.com/dl/android/aosp/blazer-cp3a.261005.005-factory-cd7d12c2.zip` to `D/<file>.part`, then renames it to `D/<file>` only if sha256 = `cd7d12c2…18e3`. The byte count is checked as it streams. | 8 GiB (unverified for Pro), 3600 s |
| `vendor` | `--download-dir D --source-root ISO --node N` | `N` must be named `node`, keep its preflight file identity, and be what the child `PATH` (`dirname(N):/usr/bin:/bin`) resolves. Runs `N --version` (≥ 24), then `vendor/adevtool/bin/run generate-all -d blazer` in `ISO` with `ADEVTOOL_IMG_DOWNLOAD_DIR=D`. | 128 GiB, 10800 s |
| `lunch` | `--source-root ISO` | Runs `lunch blazer-cur-user` (`PATH=/usr/bin:/bin`), then checks `TARGET_PRODUCT=blazer`, `TARGET_RELEASE=cur`, `TARGET_BUILD_VARIANT=user`, `TARGET_DEVICE=blazer`, with `OUT_DIR=out-blazer-lunch`. No make. | 8 GiB, 1800 s |

- **Time**: the stage timeouts total 16200 s (4.5 h). That excludes building the isolated source
  tree, whose byte and time cost is unknown and separately scoped.
- **Space**: each stage needs available − bound ≥ 322122547200 bytes on the filesystem it writes.
  - The preparation preflight's peak is the 144 GiB sum of the bounds. That sum plus the 300 GiB
    reserve excludes the isolated source copy.
  - No prior Blazer measurement is needed.
- **Byte limits are a sampled stop, not a filesystem quota.**
  - Every `limits.sampleSeconds` (5 s), the watched outputs are checked against the bound and the
    filesystem's available bytes against the reserve.
  - An overrun or the timeout stops the stage's process group: SIGTERM, then SIGKILL after
    `limits.killGraceSeconds` (30 s). Outputs are kept.
  - Writes between samples can overshoot.
- **Descendants**: each stage runs in its own session and process group. Live members left after a
  normal exit are ended the same way.
- **Gaps that need host-side authority** (not provided here):
  - an enforced quota (project quota, loop device or cgroup io);
  - containment of descendants that start their own session (cgroup or systemd scope).
- **Isolation**: `ISO` and `D` must not be, contain or sit inside the production source root
  `/home/pixelbuild/grapheneos-2026100600` (`os/release-pipeline/frankel.json`), nor share its inode,
  and no operator path may be a symlink.
- **Lock**: the lock (`/home/pixelbuild/frankel-build.lock` by contract) is probed and then held with
  `LOCK_NB`. A held lock refuses.
- **Existing outputs**: these refuse and are never deleted or reused: the archive, a `.part`,
  `vendor/google_devices/blazer`, `vendor/adevtool/dl/unpacked/blazer-CP3A.261005.005` and
  `out-blazer-lunch`. A failed download removes only the `.part` it created.
- **Exit status**: 0 ready/success, 1 not ready or failed, 2 invalid input.
- **`inspect`** (schema v2; proposed for a later observation of at most 20 minutes; not authorized or run). Usage:
  `inspect --host H --lock L [--candidate-root R] [--candidate-source-root S] [--candidate-download-dir D]
  [--candidate-receipts-dir X] [--candidate-node N] [--reference-source-root P] [--max-entries 200000] [--max-seconds 900]`.
  - `--candidate-root R` proposes `R/source`, `R/downloads` and `R/receipts`; explicit candidate flags override those.
  - Candidates are proposals, never bindings. `plan`, `preflight` and `run` refuse every candidate or
    reference flag (exit 2), and null `--source-root`/`--download-dir`/`--node` still refuse preparation.
  - The output has `bound` (the prep inputs as given, normally null) and `candidates` (`proposed: true`).
    Each candidate gets `lstat` metadata, symlink component, isolation, nearest-existing-ancestor
    `statvfs`, `sameFilesystemAsReference`, and, where it exists, project presence, the 128-byte
    `adevtool_revision` and bounded collision sizes. For the download candidate it also reports the
    archive and `.part` metadata (not hashed).
  - `reference` (default: the production source root, read-only) gives the same tree facts plus a
    bounded total size.
  - `node.runner` is `node` as the runner's own `PATH` resolves it, and `node.candidate` is
    `--candidate-node`: path and realpath `lstat` identity, name and executable bit. Neither is ever
    executed.
  - `unknowns` lists what metadata cannot establish: null bindings, missing candidates, Node version,
    reflink support (never assumed), the isolated-copy cost, the Pro archive size, and truncated walks.
  - It starts no process, opens no connection, reads no secret and writes nothing. The kernel may
    still update directory access times (relatime); contents, mtime and ctime are unchanged.
- **Not yet verified**: the only known source is historical production ext4. Paths such as
  `/home/pixelbuild/rose-actions/blazer-prep-<run-id>-<attempt>/{source,downloads,receipts}` are a
  proposal, not verified paths. Building the isolated copy (no writable hardlinks, no assumed
  reflink, with outputs, signing material and caches excluded) is separately scoped.
- **Out of scope**: `compile` is outside preparation and still needs a measured
  `compileAllowanceBytes`. Nothing here compiles, signs, publishes or flashes, and no runtime scope
  is approved.

## Callable contract

```bash
python3 scripts/factory-package.py observe SIGNED_TF --flavor rango --build B --source RECEIPT_REF --out OBS.json
```

```bash
python3 scripts/factory-package.py classify ARCHIVE --flavor rango --target-files SIGNED_TF \
  --target-files-observation OBS.json --firmware-observation FW.json \
  --otatools OTATOOLS.zip --otatools-sha256 H --work-dir JOB_DIR --build B
```

```bash
python3 scripts/factory-package.py package ARCHIVE --flavor frankel --target-files SIGNED_TF ... --out NEW_DIR
```

```bash
python3 scripts/tests/test_factory_package.py
```

Exit status: 0 ready/packaged/observed, 3 refused (JSON on stdout), 2 usage or contract error.
Optional flags: `--ssh-keygen PATH`, `--max-work-bytes N`, `--avb-public-key-sha256`,
`--first-use-binding`.

## Missing for a real factory package (sole owner → next action)

- **Factory-signing identity. Owner: signing; Chief decision.** No approved key exists:
  `id_ed25519` is optional and not produced by generate-keys. Decide whether to sign install
  archives, then pin the public half in `factorySigning`. Until then `signatureVerified` is false
  by design.
- **Factory stage. Owner: signing.** A default-off tail in `host-sign.py` `derive_release_script`
  through `optimize-factory-image`. It maps `{device}-install-{build}.zip`, `.sig` and a firmware
  observation built from the generator's own inputs.
- **Names-only target-files observation. Owner: release.** Run `observe` in the next
  already-needed admitted signed-output operation, and list the receipt in `inventory.observed`.
- **Shared harness and CI. Owner: release.** Add the test to `scripts/test-local.sh` and an
  offline CI path. The runner needs `ssh-keygen`; `android-sdk-libsparse-utils` is optional.
- **Update-zip content. Owner: release, via a names-only observation.** `img_from_target_files`
  output and `fastboot-info.txt` (build/make) are not pinned. The generator (FOLD-FACTORY-003) and
  optimizer (FOLD-FACTORY-005) are.
- **Physical first-install acceptance. Owner: QA, with a device handback.** This comes only after
  a ready archive exists.
