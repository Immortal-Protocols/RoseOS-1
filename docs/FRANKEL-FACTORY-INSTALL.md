# Frankel (Pixel 10) initial factory installer

Status (2026-11-04): source only. Neither mode has run, the binding is unbound and no installer exists. Nothing here
was run on a host or a phone.

This route packages an **unsigned intermediate**. It adds no archive signature, so
`scripts/factory-package.py classify` reports `signatureVerified` missing. The archive is therefore not factory-ready
(docs/VERIFY.md §F) and not the delivered installer. Frankel OTA delivery is separate and unchanged.

## What it packages

The route packages one published Frankel build's own signed outputs, never a Rango, Blazer or Mustang image:

- **Signed target-files.** `/home/pixelbuild/rose-actions/frankel-<build>/release/signed-target_files.zip`, from that
  build's original os-release attempt. The attempt must meet three conditions:
  - its receipt chain is complete through `transfer` and is not a resume;
  - its `sign-receipt.json` matches the binding, and the release script it records equals the provenance pin;
  - its `public/verification.json` passes and is not simulated.
- **otatools.** `<host.sourceRoot>/releases/<build>/frankel-otatools.zip`. This is the copy `scripts/host-sign.py` kept
  when it signed that build. Its `.rose-actions-owner.json` and `.rose-actions-signed.json` markers must name the same
  candidate, build and signed target-files. The shared OUT_DIR copy is never used, because the next compile replaces it.
  A build signed on the dedicated signer (`ROSE_SIGNING_TRANSPORT=dedicated`) has no such directory on the builder. Its
  observation reports the directory missing, and that route needs a separate reviewed source change.
- **Release script and AVB key.**
  - `generate-release.sh` must match the `os/flavors/frankel.json` provenance pin.
  - The AVB key is derived from the signed target-files and checked against
    `os/release-pipeline/frankel.json identities.avbPublicKeySha256`.

## Inputs to bind

All inputs are bound in `os/factory-package/frankel.json`:

| Key | Source record | Needed by |
| --- | --- | --- |
| `build`, `buildTimestamp`, `candidate` | the published build's release and its verification receipt | observe, package |
| `sourceRun` | that build's original os-release run id | observe, package |
| `signedTargetFilesSha256` | that run's transferred verification receipt | observe, package |
| `otatoolsSha256`, `fastbootSha256` | a complete `frankel-factory-observe` receipt for this build | package |

Every value stays null until a reviewed commit supplies it from those records. While a value it needs is null, either
mode refuses (exit 4) in the hosted guard, before any host starts.

The observation checks the build values against the attempt's own receipts. It does not check `sourceRun` or that the
build was published; its receipt lists both under `notVerified`. The reviewer of the binding commit checks them
against the public release (pixel-ota tag `frankel-<build>`, whose body names the run that verified and published it).

## Run order

Each run is a separate os-release dispatch by Infra. None is approved here.

1. **Bind the five build values** by a reviewed commit.
2. **Run `frankel-factory-observe`.**
   - It is read-only and runs under the shared builder lock. Its ceilings are 1020 s for the step and 28 minutes for
     the job.
   - It uploads only `frankel-factory-inputs.json`, which must report `complete: true`.
   - It also runs the package helper's own preconditions early: the AVB key derived from the signed target-files
     against the pin, the otatools member limit, and its refusal of unsafe or linked members.
   - Until they are bound, `otatoolsSha256` and `fastbootSha256` show `matchesBinding: null` (nothing compared).
3. **Bind `otatoolsSha256` and `fastbootSha256`** from that receipt by a reviewed commit.
4. **Run `frankel-factory-package`.** Its ceilings are the Rango route's: the job's is 297 minutes.
   - It uploads the receipts (kept 7 days).
   - Only after inspection and the size and sha256 recheck, it uploads the private artifact
     `frankel-install-unsigned-intermediate-<run>-<attempt>`. That artifact holds exactly `SHA256SUMS` and
     `package/frankel-install-<build>.zip`, and is kept 3 days.
5. **Bind the retained installer** by a reviewed commit, from that run's receipts. These are the `RETAINED` values in
   `scripts/frankel-factory-package-wiring.py`: run id, attempt, bytes and sha256.
   - Nothing on main reads them yet. `scripts/factory_targets.py` (Signing, #515) has Frankel's
     `installerPins` null, and a Signing follow-up points them at this module.
   - Until then, Frankel delivery verification refuses (`installer_unpinned`).

## Checking an output before any use

- **Hashes.** Run `sha256sum -c SHA256SUMS` in the downloaded artifact directory. The digest must equal both
  `output.sha256` in the package receipt and `installer.sha256sums` in the cleanup report.
- **Classify report.** Read `structurallyComplete`, `contentVerified` and `signatureVerified` in `classify.json` as
  reported. `signatureVerified` is missing for this route.
- **Not the delivered installer.** Delivery requires a pinned archive signature from a separately approved Frankel
  factory-signing identity. `os/flavors/frankel.json factorySigning` is unbound.
  - Only the artifact name says "unsigned intermediate". The ZIP inside keeps the flavor's install-archive name, so
    never rename, re-share or present that ZIP as the delivered installer.
  - The receipts (package and firmware attempt records, staged record, cleanup report) carry the label.

## First install

The owner's procedure is `firstInstall.ownerSteps` in the classify report. It is Flavors' `os/flavors/frankel.json
firstInstall`, re-derived and pin-resolved by `scripts/factory-package.py classify`. This page does not restate or
extend it.

Its fixed requirements are:

- `product` frankel;
- fastboot 35.0.1 or newer;
- an unlocked bootloader;
- all user data erased;
- `relockClaimedSafe` false.

## Not in this route

The route does none of the following:

- signing, secrets, signer login or OIDC tokens;
- downloads;
- phone actions or flashing;
- publication.

A Frankel archive signature needs its own identity decision and approval first.
