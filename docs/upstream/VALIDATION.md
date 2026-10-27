# Validation and limitations

## Cloud OS smoke — 2026-10-24

**PASS: candidate OS booted and Android Settings opened and rendered.** This is the
single requested pre-merge smoke. No PIN enrollment, owner arming, locked-app action,
security-regression suite or physical-device step is required by this scenario.

- Tested the locally compiled modified Cuttlefish image, not a stock emulator.
  Target: `aosp_cf_x86_64_only_phone-cur-userdebug`; framework revision
  `84183aff99be5e156dc0446a6fa2d4a7150f4bc3` with the captured patches and the
  separate resource-qualification compile fix.
- Verified image/host artifact hashes and actual guest `framework-res.apk`,
  `services.jar` and compiled service identities against the build receipt before
  launching Settings. `sys.boot_completed=1`; Settings launch returned `Status: ok`;
  the focused window was `com.android.settings`; its UI tree contained Search
  Settings, Network & internet, Apps and other categories. Screenshot visually
  checked. No crash-buffer signature occurred during this check.
- Report interval: `2026-10-24T19:59:51Z`–`2026-10-24T20:00:01Z`.
  Receipt SHA-256: `09a3e2b9d88fc4cb1f86440e9ba27ab01f05e85e0bb898c900621674c76f89b2`.
  Image SHA-256: `e8f33200c841d939cca0f434716af9647e075cc2df4b972d360481d420c0aa36`.
  Locked framework patch SHA-256:
  `8a9c06333f30462513839f55471678c84f7ce78233a386b2a11a0a293dca9abd`.
- The initial collection attempt lost its bounded host launch; another attempt was
  still in the normal setup wizard; the next exposed an obsolete expected UI label.
  These earlier reports were retained. The final script checks Android 17's Search
  Settings header and a Settings category, and the final run passed.
- Integration checks: 20 background-phone tests and 3 controller-policy tests passed;
  resource-fix/locked patches applied sequentially against the pinned framework plus
  the existing three patches; Python/shell syntax, XML and `git diff --check` passed.
  Integrated app/controller sources match the previously compiled candidate.
- The historical image includes pre-existing OTA/logger baseline changes. Those
  unrelated edits are excluded from this integration; modified framework and
  app/controller sources match the tested candidate. Evidence is retained privately,
  not added as generated artifacts or deployment data to this source repository.

[Reusable smoke instructions](../scripts/cloud-os-smoke/README.md) default to
`open-settings.py`. The old locked scenario remains optional and unvalidated; its
previous owner-authentication stop does not block this narrower smoke. This PASS
establishes boot/Settings behavior only, not successful locked app control. No new
public OS release, OTA deployment, or physical-phone installation is implied.

## Initial cloud-session screen/lock checks — 2026-10-23

Cloud-session checks only. No phone, hub service or signing key was used.

- Pinned `frameworks/base` `84183aff99be5e156dc0446a6fa2d4a7150f4bc3` was fetched by
  exact SHA from GrapheneOS. `frameworks-base`, both background-control patches and the
  new `locked-control-frameworks-base.patch` applied in order, with `git apply --check`
  before each one. The locked-control patch refuses to apply alone.
- The patched `config.xml`/`symbols.xml` parse after the apply script's substitutions,
  with the locked bool set. The three patched ComputerControl Java files pass a
  javac parse check. They were not compiled against a full AOSP tree.
- `apply-os-changes.py --locked-control` is refused without a background policy and
  without a `--prototype-control` policy.
- Pixel Voice v289 compiled and dexed against the official API 35 `android.jar`, using a
  scratch copy with a placeholder network-security resource. Unsigned; not installed.
- PiP v26, split v23 and the v6 reference controller passed unsigned compile checks.
  Controller unit tests passed: 8 installer-recovery, 5 label, 6 geometry and 12 pane
  checks, plus 3 policy tests.
- Hub: 34/34 background-phone, task-setup, app-policy and visual-verification tests
  passed, including 6 new locked-path tests. Of the full server/decisions run, 193
  passed. 3 test files fail only because this clone has no `node_modules` (`openai`)
  and no generated Thunderbird `agent-tools.json`.
- Device behavior is not validated. See the checklist in
  [LOCKED_CONTROL.md](LOCKED_CONTROL.md#validation-status).

## Viewer and Linux update — 2026-10-22

- Exported backend, pairing, delivery, decisions and voice-provider tests: 212 passed.
- A fresh checkout generated its own key and TLS configuration, installed npm
  dependencies, built Pixel Voice, Notes, Notes Agent and Memories, and passed
  all 147 backend tests again without the original working tree's generated files.
- Pixel Voice v287 compiled and signed using a locally generated development key;
  the export retains Nate's Samsung adaptive layout and status glow.
- Both PiP and split controllers passed compile checks. The PiP controller also
  passed signing and V4 sidecar checks with the same developer key as Pixel Voice.
- Three policy tests and 26 split/reconnect/geometry assertions passed. A generated
  broad-control policy accepted the recipient key without an app-by-app list.
- All framework patches applied sequentially against the exact pinned reference
  files, including the reconnect and window-removal fix. No full OS rebuild from
  this exported tree has been performed.
- Backend bundling resolved its relative modules. Private catalog generation,
  overwrite refusal, file permissions and endpoint certificate generation passed.
  All documentation links resolve inside the checkout. The source tree passed
  a full secret scan; generated keys, endpoints, pairing data and build products
  are excluded from Git.
- Original-device live tests established PiP automatic opening, background control
  after closing the viewer, repeated reopen without the old system crash, and
  Fold handoff to the outer display. Linux voice and phone tasks also completed.
  These live observations predate this source export; they are not tests of Nate's
  hardware or a fresh installation.
- The optional fast-install helper source and original Fold port patches are
  included for review. The remaining fast-install name mismatch is unresolved,
  and the full portable Fold port has not been independently rebuilt.

## Background-control update — 2026-10-21

- Exported backend: all 192 tests passed, including background routing, no foreground fallback, serialization, task checking, task status and provider startup.
- Exported Pixel Voice app compiled and signed with a newly generated local development key; Nate's Samsung layout and status-glow changes were preserved.
- Controller v6 compiled against API 35; label-target and signing-policy tests passed. OS patches were checked against the exact pinned upstream files, together with the existing SystemUI patch.
- The backend entry point bundles successfully with all relative runtime imports resolved. The 1,101 tracked/candidate files passed a full-tree secret scan.
- No private deployment configuration, original keys, enrolled devices, app inventory or recorded phone screenshots are included. Tests that previously depended on private benchmark recordings now use synthetic fixtures.
- On the original updated Pixel, normal Pixel Voice task requests completed browser and Maps actions in the background while Pixel Voice stayed visible. Those live results precede this export; no claim is made that the recipient's device has been installed or tested.
- The exported full OS has not been rebuilt independently. Background execution on stock Samsung is not supported by installing these APKs alone.

## Earlier handoff checks

- All 26 small Android app build scripts passed with a newly generated development key.
- Pixel Voice compiled and signed after local TLS/public-pin setup.
- Memory context/review: 11 tests passed. Device pairing: 2 tests passed.
- Voice request context: 3 tests passed. Mail/Calendar wiring: 3 tests passed. Native catalog validation: 2 tests passed.
- The backend entry point's 120 local JavaScript modules are all included.
- Both OS patches pass `git apply --check` against the exact upstream file versions from the build machine.
- Custom changes in all 14 upstream-based app repositories passed secret scanning. Pre-existing public upstream test-fixture findings were identified separately.

## Limits

- A fresh GitHub clone fetched all 14 pinned repositories at their exact revisions. SDK/key setup and builds of Pixel Voice, Calculator, Notes, and Memories succeeded. All 13 selected memory, pairing, Mail/Calendar and native-catalog tests passed again there.
- The larger Gradle and platform forks have not all been rebuilt from this exported checkout.
- No new full OS build or port to Nathan's particular device has been run.
- Google authorization, provider APIs, voice calls, and physical-device acceptance require Nathan's own accounts and hardware.
- Older apps retained for reference may use retired connection paths; current launcher choices are in APPS.md.
- OS/system-app signing must match the target installation. A fresh development key cannot update the original signed system packages.

Historical tests on the original phone do not establish acceptance on a different phone, signing identity, account, or Codex version.
