# Verifying a RoseOS build

Use this guide to check an alpha or beta build. Every step produces exactly one
kind of evidence. One kind never stands in for another:

| Kind | What it proves | What it does not prove |
| --- | --- | --- |
| `artifact-inspection` | The published bytes: checksums, signatures, candidate SHA, image contents, APK identity and manifest | That the image boots, renders or behaves |
| `source-inspection` | What a source revision declares: manifest, routing, wallpaper bytes | Anything about built or shipped bytes |
| `runtime-readonly` | A booted guest's properties, installed bytes and versions, and HOME resolution | What the screen shows |
| `runtime-interactive` | Launch paths, screenshots, UI dumps and transitions on that guest | Physical-phone behavior, or behavior of a different OS product |
| `screenshot-comparison` | A heuristic match against the reference wallpaper, plus a side-by-side image | Visual acceptance (a person must review the side-by-side) |
| Physical acceptance | Owner-run checks on the actual Pixel | Nothing beyond that phone and build |

The tool is `scripts/rose-verify.py`. It runs offline except `runtime`, and it
prints JSON. Exit status: 0 ok, 1 check failed, 2 invalid input, 4 setup missing.
A check reported as `unavailable` was not measured; it never counts as a pass.
Every result carries `method`, the method revision that produced it (see
[Method revisions](#method-revisions)). `python3 scripts/rose-verify.py expected`
prints the repository's pinned identities.

## Targets

| Channel | Target | Runtime vehicle |
| --- | --- | --- |
| Beta (pixel-ota) | `frankel` (Pixel 10), `frankel-cur-user`, arm64-v8a, release-keys | A physical Pixel only. **An arm64 frankel OTA never boots on x86_64 Cuttlefish. Never try it, and never present an emulator image as evidence for it.** |
| Alpha | `aosp_cf_x86_64_only_phone-cur-userdebug` (`vsoc_x86_64_only`), test-keys | Cuttlefish guest on the lead's runner (see below) |

The alpha image carries the same app bytes as beta 2804. Its contract
(`os/alpha/cuttlefish-product-contract.json`) lists Rose Agent 302, Calculator 4
and controller 28 by SHA-256. It is a different OS product, though:

- the Updater is excluded (`excluded-no-compatible-feed`);
- whether the Pixel-specific overlays take effect is not established;
- it is userdebug with test keys.

So alpha runtime results speak for the apps on that guest. They do not speak
for the frankel OS.

## Candidate expectations (`--expect`)

Identity checks compare a build with one explicit **expectation file**: the
exact identities recorded for that candidate. There is no implicit default. A
build is never checked against another build's pins, and pins are never
loosened to make a build pass.

- **Required:** `runtime` needs `--expect`. So does `release --extract-apps`.
  `apk` without `--expect` reports identity only and compares nothing.
- **Schema:** `scripts/rose-verify.py` documents it. Required fields are
  `label`, `channel`, `candidate` (full SHA), `target`, `apps`
  (`package`, `version`, `sha256`, and `imagePath`, which `--extract-apps`
  needs), `appSignerSha256` and `basis`. `build` and `avbPublicKeySha256` are
  optional.
- **Fail-closed validation:**
  - unknown keys, nulls, short hashes, non-integer versions, repeated packages
    and empty app lists are rejected with exit 2;
  - a package that is not in the expectation fails;
  - an expectation app with no `imagePath` fails extraction; there is no
    built-in location;
  - a guest or OTA whose device differs from `target.device` fails;
  - a `verification.json` whose candidate or build differs fails.
- **Recorded:** every receipt records the expectation's label, candidate, path
  and file SHA-256.

Checked-in expectations live in `qa/verification/expectations/`:

| File | Build | Apps |
| --- | --- | --- |
| `beta-frankel-2026102804.json` | Published beta 2804, candidate `ae9cfd4d` | 302/4/28 |
| `alpha-40719075730.json` | Active alpha, Actions 40719075730 (recipe `2921d88d`, base `ae9cfd4d` + OS delta `f21bf683`) | 302/4/28 |

Both copy the existing pins unchanged. The test suite fails if either diverges
from `os/alpha/cuttlefish-product-contract.json` or
`os/release-pipeline/frankel.json`.

A **new candidate** (for example the next beta, or app-only 304) gets its own
file. It is written from Infra's actual image/APK receipt only after Infra
supplies it. Never write it from source, a planned version number or another
build's hashes. `qa/verification/expectations/README.md` describes how.

## Reference: the Fold prototype

The product Home implements the delivered Fold prototype:

- **Prototype:** `Rose-Fold-v3.html`, Library item
  `libfile_ff139fdeb5688191a918d4ca9210abe9`, version 2, 859,202 bytes, SHA-256
  `67bf7a505ffdd64087c76e6be496043afb7576c081b2fe8ed1c6642aec9405ee` (delivery
  ROS-PROTOTYPE-FOLD-003-DELIVERY-001).
  - Canonical records are the Notion "RoseOS Interaction Prototype — Fold
    Brief" and the Prototyper inbox.
  - A copy is attached to the OS P1 task page; retrieve it only through that
    supported attachment/Library path.
- **Wallpaper:** its narrow Home wallpaper is the first embedded JPEG
  (461 × 1024). The repo carries it as
  `workspace/rose-agent/app/res/drawable-nodpi/rose_home_wallpaper.jpg`, SHA-256
  `409c0dbfa309dbd56f97a59b04223532e9931ae5c5c4625d3567b77a9c56d8c5`. That hash
  is pinned by `workspace/rose-agent/tests/home-launcher.test.mjs`.
- **Status:** the Fold brief records that the prototype's browser rendering and
  visuals are *not* independently accepted. Nathan's positive feedback was
  functional. Comparisons below therefore mean "consistent with the delivered
  prototype", never "visually accepted".
- **Cropping:** `compare` crops the wallpaper exactly as `RoseHomeView` does
  (CSS `background-size: cover`, centered). It reports mean and 95th-percentile
  luminance, and the share of a 27×48 grid within 40 levels of the reference.
- **Verdicts:** `black-screen`, `wallpaper-consistent` or
  `differs-from-reference`. Overlays (status bar, Rose presence, dock) lower
  coverage. Always look at the side-by-side PNG it writes.

If someone retrieves the prototype HTML through the supported Library flow,
record its full SHA-256 before rendering it. Then render the narrow Home in an
approved browser at the device's logical size and compare it with `--reference`.

Never publish screenshots, wallpapers or dumps from a personal phone, or any
device identifier. `runtime` refuses to write inside the repository. It never
records the adb serial. It marks any non-Cuttlefish device as private evidence.

## A. Artifact inspection (any agent, no device)

1. **Download.** Fetch a public pixel-ota release through the supported
   anonymous public read mode: `github.com/Immortal-Protocols/pixel-ota/releases/download/<tag>/<asset>`.
   - Make one fresh download into a new, empty directory outside the repo.
   - If that read is denied, stop and report the exact denial. Do not switch
     routes (configured-repository API, push attachment, private Actions
     artifacts).
   - Fetch `SHA256SUMS`, `verification.json`, `frankel-stable`, `UPDATE.md` and
     the OTA. `avb_pkmd.bin` and `releasekey.x509.pem` are optional: their
     listed hashes are checked even if they are absent.
2. **Run the check:**

   ```sh
   python3 -I scripts/rose-verify.py release DOWNLOAD_DIR \
     --expect qa/verification/expectations/CANDIDATE.json --extract-apps /scratch/extracted \
     --partition product --partition system_ext --partition vendor --partition system \
     --receipt /scratch/release-inspection.json
   ```

   This checks:
   - every present file against `SHA256SUMS`;
   - the listed AVB key against the expectation's `avbPublicKeySha256`
     (otherwise `identities.avbPublicKeySha256`);
   - the candidate SHA and build in `verification.json` against the expectation;
   - the feed line against the candidate;
   - the OTA's `pre-device` against `target.device`.

   `--expect-source SHA` (r1) still checks the candidate SHA alone. Combined
   with `--expect`, it must agree with the expectation.

   It then reads OTA metadata and the payload partition table. The OTA's
   `pre-device` sets `runtime.cuttlefishBootable: false` for frankel.
3. **Extraction.** With `--extract-apps`, the tool decodes each named partition
   from `payload.bin` and checks every operation's data hash and the partition
   hash. Supported operations: REPLACE, REPLACE_BZ, REPLACE_XZ, ZSTD, ZERO and
   DISCARD (full OTAs only). It then reads files with `debugfs` (ext4) or
   `fsck.erofs` (erofs) without mounting. It records:
   - **Target/API/ABI:** `build.prop` fingerprints, SDK, build type and
     `cpu.abilist` per partition.
   - **App identity:** each expected app at its `imagePath`, for example
     `product/app/RosePreinstall_*` or `system_ext/priv-app/PixelBackgroundControl`.
   - **Each APK:** package, versionCode/Name, min/target SDK, native ABIs,
     launchable and HOME activities, the full signer SHA-256 against the
     expectation's `appSignerSha256`, and bytes and version against the
     expectation.
   - **Rose Agent:** the Fold wallpaper resource bytes, and whether
     `RoseHomeView`/`RoseConversationActivity` are compiled in.
   - **HOME candidates:** every APK in `app/` and `priv-app/` whose manifest
     declares HOME. This is image content only. The role holder is decided on
     the device.
4. **Cost:** about 5 GB of scratch for these four partitions and 3 minutes
   locally. Delete the images afterwards; they are public, but large.

`apk APK... [--expect FILE]` runs the same APK inspection on loose files, for
example App release assets or a local build. A local rebuild of the same source
is *not* the shipped artifact: it fails the expected byte and signer checks by
design.

## B. Source inspection (no build)

```sh
python3 scripts/rose-verify.py source CANDIDATE_SHA
```

This reads the Rose Agent manifest at that revision: package, version, SDK, and
HOME/LAUNCHER activities. From `MainActivity` it records:

- the launcher page order and indexes;
- the HOME-intent rule;
- where the Home presence navigates;
- the pairing redirect;
- the wallpaper hash.

Use it to say what a revision *declares* and to predict runtime routing. It does
not prove what a device runs.

## C. Alpha runtime on Cuttlefish (lead-owned host, explicit handback)

The Dev Infra lead owns the builder, runner, guest images and the boot, and
serializes runtime work.

**Prerequisites:**
- An explicit serialized handback from Chief or the Infra lead, through existing
  team coordination, naming the guest and the window. Record its reference
  verbatim in `--handback`.
- No concurrent VM change, reset or deploy. No new resources, credentials or
  network routes.
- Before the session, the lead supplies the alpha receipt (`emulator-alpha-receipt-<run>`):
  image SHA-256s, `testOnly`, `bootAttempted`, and the build id prefix
  `rose-cf-alpha-`.

1. **Provenance.** Record the Actions run, the receipt's image and host-package
   hashes, the source recipe, and the launch command the lead used. Do not
   relaunch or reset a guest yourself.
2. **Read-only capture:**

   ```sh
   python3 scripts/rose-verify.py runtime --serial SERIAL --handback 'HANDBACK REF' \
     --expect qa/verification/expectations/alpha-40719075730.json --out /scratch/runtime-RUN
   ```

   This records:
   - `ro.product.device`/`name`, `ro.hardware`, fingerprint, build type, SDK,
     release and ABI list, with the device checked against `target.device`;
   - `sys.boot_completed` and the tray property;
   - the **tested user** (see below), plus every user's id, flags and running
     state from `pm list users` (names are never recorded);
   - for each expected app:
     - `pm path --user U`;
     - the `User U:` state line of `dumpsys package` (installed, hidden,
       suspended, stopped, enabled);
     - the `dumpsys` versionCodes and whether the package is a SYSTEM
       (preinstalled) app (both device-wide);
     - the on-device `sha256sum` of the active APK for that user, compared with
       the expectation (an updated `/data/app` copy shows here);
   - the HOME role holders (`cmd role get-role-holders --user U
     android.app.role.HOME`);
   - the **unqualified** MAIN/HOME resolution (`cmd package resolve-activity
     --brief --user U -a MAIN -c HOME`, no component). An explicit component
     launch never stands in for it;
   - the user's default-HOME marker (`settings --user U get secure
     rose_home_role_selected`). Builds with
     `os/patches/rose-home-role-permission.patch` (OS PR70) set it once per full
     user. It is absent on builds without that patch, and it is read only, never
     written.

   It issues no input, launch or file write.

   **Tested user.**
   - `--user current` (the default) resolves the foreground user with `am
     get-current-user` and records that mechanism. `--user N` names a user
     explicitly; it must appear in `pm list users`.
   - An unreadable current user or an unknown id stops the run (exit 1 or 2)
     rather than attributing results to an unknown user.
   - Every user-scoped command and observation names the tested user. Every
     other user and profile is listed as `unavailable` (not measured). Default
     shell output is never read as proof for all users.
3. **Interactive capture.** Add `--interactive` only within the handback window.
   - Input, screenshots and UI dumps only reach the foreground user. So
     interactive capture runs only when the tested user *is* the foreground
     user. Otherwise it is reported `unavailable`: switching users is a guest
     mutation this method does not perform.
   - The foreground user is read again afterwards and must be unchanged.
  - In the receipt, `ok` means no check failed. `unmeasured` lists every
    `unavailable` check, such as other users.
   - The icon launch uses `am start --user U`.

   Each step saves a screenshot, a UI dump and the focused window, then:
   1. presses the HOME key: the Rose Home page should have an `Open Rose`
      element, and the screenshot is compared with the reference;
   2. taps `Open Rose`: focus should be `RoseConversationActivity`;
   3. presses BACK;
   4. starts the launcher-icon entry
      (`am start -a MAIN -c LAUNCHER -n dev.pixel.voice/.MainActivity`) and
      compares it too;
   5. presses HOME again.

   The only file it writes on the guest is a temporary UI dump in
   `/data/local/tmp`, which it removes.
4. **Review.** Look at every side-by-side PNG. Fill in the checklist below with
   the actual result per item. Keep screenshots outside Git. Cuttlefish
   screenshots hold no personal data and may be attached to a team page with
   their SHA-256.

## D. Physical beta phone (owner only)

Only the owner, or someone the owner explicitly asks, runs `runtime
--physical-device` on their own phone. Its receipt is private evidence. Report
only pass/fail facts and versions, never screenshots, wallpapers, serials or
other identifiers.

## Checklist

Record each item as observed, failed or unavailable, with the evidence kind and
where it came from.

| # | Item | Evidence kind | Expected (2804 / alpha) |
| --- | --- | --- | --- |
| 1 | Release checksums, candidate SHA, feed line, AVB key pin | artifact | All pass; candidate `ae9cfd4d` |
| 2 | Payload partition hashes for the extracted partitions | artifact | Match the manifest |
| 3 | Target/API/ABI from image `build.prop` | artifact / runtime-readonly | Beta: frankel, SDK 37, arm64-v8a, user. Alpha: vsoc_x86_64_only, x86_64, userdebug |
| 4 | Expected APK bytes, versions and signer (`--expect`) | artifact / runtime-readonly | The candidate's expectation. 2804 and the active alpha: 302/4/28 at the pinned SHA-256, signer `650d47f9…0bfb` |
| 5 | Fold wallpaper bytes in Rose Agent; Home/conversation classes compiled in | artifact | `409c0dbf…d8c5`; both classes present |
| 6 | HOME candidates in the image vs, for the tested user, the HOME role holder and the unqualified MAIN/HOME resolution | artifact vs runtime-readonly | The image lists candidates; the device decides per user. Report both, never infer one from the other or one user from another |
| 7 | HOME key → Rose Home page with `Open Rose`, not black | runtime-interactive + comparison | Only when Rose holds HOME |
| 8 | Launcher-icon entry → which page | runtime-interactive | 302 (beta 2804, alpha): `voicePage` ("Voice home"), not Rose Home. 304+ (`HomeEntry`, OS-HOME-2804-FIX-001): Rose Home, with the conversation opened over it when paired and launched fresh; Back returns to Home. Run `source REV` for the build's rule |
| 9 | Home → conversation → back transition | runtime-interactive | `RoseConversationActivity`, then back to the launcher |
| 10 | Truthful unavailable states | runtime-interactive | An unpaired guest opens `PairingActivity`; no backend means no fake connected/task state; alpha has no Updater |
| 11 | Visual review of side-by-sides against the prototype | screenshot-comparison + human | Consistent with the delivered prototype, not "accepted" |
| 12 | Tested user and the other users/profiles | runtime-readonly | One recorded tested user and how it was selected. Every other user/profile is `unavailable` unless run separately with `--user` |
| 13 | Default HOME selection (OS PR70 contract, `docs/OS-INTEGRATION.md`) | runtime-readonly, per tested user, on an image that contains the patch | See the cases below. Active alpha and 2804: patch absent, so not testable there |

For item 13, the expected per-user outcomes are:

- **First start of a full user on a fresh or updated image:** the HOME role
  holder is `dev.pixel.voice`, unqualified HOME resolves to
  `dev.pixel.voice/.MainActivity`, and the marker is `1`.
- **After a later owner choice:** that holder stays across reboot and update.
  The marker stays `1` and the choice is not reverted.
- **Rose Agent absent, disabled, without HOME, or not a SYSTEM app:** the
  holder is unchanged and the marker is `1`.
- **Profiles:** they have no HOME role.

An x86 Cuttlefish fixture of an "updated from 2804" state is labeled as such. It
is never a physical frankel OTA result.

## Method revisions

| Revision | Where | Change |
| --- | --- | --- |
| r1 (INFRA-VERIFY-001) | PR69, `f35b860c` | First method. App checks used the fixed alpha contract (302/4/28); HOME resolution and role holders were read without a user argument. The 2804 record (`qa/verification/frankel-2026102804.md`) was made with r1 and stays as recorded |
| r2 (INFRA-VERIFY-002) | This revision | An explicit, fail-closed `--expect` replaces the fixed contract. Every runtime observation is bound to one recorded tested user: package path, user state, HOME role holders, unqualified HOME resolution, the PR70 default-HOME marker and interactive steps. Interactive capture runs only for the foreground user. Every receipt carries `method`. Re-running r2 on the same 2804 bytes passed 26 of 26 checks (`qa/verification/expectations/README.md`) |

**Limitations (r2):**
- Only one user is measured per run. Other users and profiles stay
  `unavailable`.
- versionCode and APK bytes are device-wide, not per user.
- Interactive capture never switches users.
- No expectation exists yet for any candidate Infra has not supplied (next
  beta, app-only 304).
- Default-HOME migration, reboot and loop checks need a compatible image that
  contains the change, plus Infra's handback. The active alpha (302, no PR67
  and no migration) cannot show them.

## Never claim

- Runtime or visual results for an artifact from inspection alone.
- Pixel results from Cuttlefish, or Cuttlefish results from a Pixel OTA.
- Acceptance from a screenshot heuristic, or prototype acceptance that the Fold
  brief does not record.
- Historical boots (`docs/upstream/`) as evidence for a current build.
- One user's HOME, package state or screen as evidence for another user or
  profile.
- A candidate's identity from another candidate's expectation, from source, or
  from a planned version number.
