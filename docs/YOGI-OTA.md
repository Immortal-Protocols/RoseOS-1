# Yogi OTA route

Pixel 11 Pro Fold (`yogi`) OTA delivery on its own dedicated builder. Source is
implemented; **nothing has been built, signed, published or installed by this
route.** Every stage is refused until `os/ota/yogi.json` records the prerequisite
it needs. See [YOGI-BUILD.md](YOGI-BUILD.md) for the port and the lab build, and the
[handoff](https://app.notion.com/p/40e7c646c81b81629d22eb03edd73abd).

## Bootstrap and identity decision

**Established (source evidence).** The lab artifact `2026110401` (Rose `c478983`),
per the artifact inspection recorded in [YOGI-BUILD.md](YOGI-BUILD.md):

- packages no Updater ("No Updater is included"; the lab recipe kept the upstream
  Updater with `OFFICIAL_BUILD` unset, and `apply-os-changes.py
  --keep-upstream-updater` ships none by design), so that image has no owned
  updater to discover a Yogi release;
- carries `test-keys` build tags, AVB public key `7728e30f…fef6` and Rose app
  signer `a40da80a…f5dc`, all public AOSP development identities.

**Not established.** That the phone runs exactly this artifact (installation is
owner-reported; ADB returned `error: closed`, so there is no installed-build
readback); the actual contents of its system OTA trust (`otacerts.zip`) and
recovery/update_engine acceptance, which were not inspected; and therefore whether
some sideload or data-preserving transition from the lab image is possible. Missing
an Updater rules out in-place discovery by that image, not every other path. No
data-preserving transition across signing identities is claimed either way.

**Proposed supported design (a decision, not a proven necessity).** Start the
durable Yogi identity with bootstrap build A, installed once by a fastboot wipe:
it erases userdata and metadata (apps and their data, accounts, Rose pairing and
endpoint state, settings, media, keystore keys); bootloader and radio are kept,
the bootloader stays unlocked, nothing is relocked. It needs the phone owner's
separate explicit authorization. Publication of OTA B does not wait for it: B
only has to be newer than A and signed by A's identity. Every later Yogi OTA then
updates A in place. Any alternative (for example a test-key-signed transition
package) would first need the unknowns above inspected under separate authority
and would remain a lab experiment until verified.

| Trust element | Yogi contract |
| --- | --- |
| OS keys (release/platform/shared/media/networkstack/sdk_sandbox/bluetooth/gmscompat_lib/nfc, AVB RSA-4096) | New Yogi-only set, profile `yogi-v1`, in `PIXEL_OS_SIGNING_KEYS_YOGI_V1` / `PIXEL_OS_SIGNING_PASSPHRASE_YOGI_V1`, read only by `roseos-build-signer`. Never another device's keys. |
| Public pins | `os/signing-bootstrap/yogi-bootstrap-receipt.json`, written by the authorized bootstrap. Absent today; no pin is claimed. Lab pins are refused. |
| Rose apps | Fleet app signer `650d47f9…0bfb`, already signed elsewhere (never on the Yogi builder); bound allocation versions. |
| Framework background policy | Pins exactly the admitted APK bytes, versions and signer; rebuilt with the image, never APK-only re-signing. |
| Updater | Owned Updater packaged (`updater-product-build-make.patch`), feed `https://github.com/Immortal-Protocols/pixel-ota/releases/download/`, release index `…/repos/Immortal-Protocols/pixel-ota/releases`, published channel `stable`. |
| OTA feed | Tag `yogi-{build}`, asset `yogi-ota_update-{build}.zip`, metadata `yogi-stable` = `{build} {buildDateUtc} yogi stable`. Never Latest, never GrapheneOS or another device's feed. |

Package, provider and permission names are unchanged (`dev.pixel.voice`,
`dev.pixel.backgroundcontrol`, `dev.pixel.calculator.agent`).

## Route

`.github/workflows/yogi-ota.yml` (manual dispatch only, concurrency group
`yogi-ota`) runs `scripts/yogi_ota.py`:

1. **guard** (hosted): names every missing prerequisite; nothing starts otherwise.
2. **controller** (hosted, `YOGI_CONTROLLER_SERVICE_ACCOUNT`): starts only
   `roseos-yogi-builder-20261103-a` and records whether it did.
3. **build** (`[self-hosted, roseos-yogi-builder]`), under `/srv/rose-yogi/yogi-build.lock`:
   `preflight` (paths, lock, free space) → `allocate` (build number and
   BUILD_DATETIME strictly above the lab, the bootstrap and every attempt; exclusive
   attempt directory) → `prepare` (byte copy of the retained tree without `out*`;
   pins, reviewed vendor reference and all three port patches verified as applied;
   the retained tree is only read) → `integrate` (fleet app admission, then the
   existing `host-reconcile.py` with the owned Updater; on the first copy of the lab
   tree, only the lab-written paths listed in `yogi-reconcile.json` may be adopted;
   the app inputs are exactly the artifact named by Release's committed record, admitted
   by Release's `app-inputs-verify.py` with Yogi-tree tools, then re-admitted) →
   `compile` (`USE_STOCK_KERNEL=true`, `SOONG_NINJA=ninja`, relative `OUT_DIR`,
   `lunch yogi-cur-user`, `m target-files-package otatools-package`; target-files
   identity checked).
4. **sign**: approved window re-checked, signer login, the existing `host-sign.py`
   (pinned GrapheneOS release script, Yogi keys with public-pin discovery, OTA
   only), logout. Bootstrap: private `yogi-img-{build}.zip` + `avb_pkmd.bin` install
   image. OTA: `stage` verifies packaged Updater route/classes/signer, system OTA
   trust exactly the Yogi certificate, full A/B metadata newer than A, produced
   public identity equal to the pins, and whole-package/payload signatures.
5. **publisher** (hosted, `ota` with `publish` and `publicationEnabled`): re-verify,
   create a fresh draft, OTA first and `yogi-stable` last with readback, public only
   after that, anonymous download check, Latest unchanged.
After a successful bootstrap run, commit A's actual build, timestamp, source and
run to `os/ota/yogi.json` `bootstrap` (status `signed`) from its receipts; an `ota`
run then copies A's integrated tree (with its reconcile receipts) and is refused
until that record exists.

6. **cleanup**: stops the Yogi VM only if this run started it; its 12-hour STOP
   policy stays the fallback.

Frankel, Mustang, Rango, Blazer and Agent builds keep their own workflows, VMs,
locks and queues; nothing here edits `os-release.yml` or `release-pipeline.py`.

## Remaining prerequisites (not performed by source)

| Prerequisite | Owner | Records |
| --- | --- | --- |
| Repository-scoped runner `roseos-yogi-builder` on the Yogi VM; outbound GitHub/googleapis; a controller identity with get/start/stop on only that instance (`vars.YOGI_CONTROLLER_SERVICE_ACCOUNT`) and WIF admission of `yogi-ota.yml` | Dev Infra | `builder.status: ready` |
| `yogi-v1` profile with Yogi builder tool paths; the two containers; a time-boxed version-adder for one bootstrap; a time-boxed accessor on version 1; escrow decision | Signing, approved by Nathan | profile `resource-bound`, bootstrap receipt, `signing.access` |
| Release's `app-inputs-export` run for Frankel 2026110403 (PR #565) by the Frankel builder's runtime owner, then Release's committed record `os/app-inputs/frankel-2026110403.json` | Release | `appInputs.status: bound` |
| Yogi publication in pixel-ota | Nathan | `publicationEnabled: true` |
| One-time wipe install of A (proposed design), and ADB readback | Phone owner | `bootstrap.installEvidence` (not a publication gate) |

First-run assumptions to confirm on the builder (each fails closed): the private
cover-display input holds a decimal `referenceDisplayAddress`; `aapt`/`apksigner`
live at `prebuilts/sdk/tools/linux/bin/` and Java 21 at `prebuilts/jdk/jdk21/linux-x86`
in the Yogi tree; the first reconcile of the lab tree conflicts only on the listed
lab-written paths; the copied tree's free space fits `minimumFreeBytes`.

Known limitation: without `--agent-tray` the raised-edge build switch
(`rose-edge-build-make.patch`) is not applied on Yogi; the tray itself is enabled
by the Yogi product patch. The port keeps the stock-kernel security limits recorded
in YOGI-BUILD.md.

## Evidence boundary

Offline: `scripts/tests/test_yogi_ota.py` (contract, guards, allocation, copy and
lock, app admission, packaged-Updater/trust observation, OTA metadata, publication
order, builder ownership). No lunch, make, signing, gcloud, GitHub API, OTA
generation or phone action has run for this route. Record **build compiled**,
**signatures verified**, **OTA published**, **phone OTA passed** and **automation
demonstrated** separately when they actually happen.
