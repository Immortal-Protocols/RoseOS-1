# Runtime record: app-only Rose Agent 304 on a historical Cuttlefish guest (INFRA-RUNTIME-100)

**Who measured what.**
- **Infra lead:** executed the runtime window and reported the results below.
- **Verifier:** recorded the results and their limits, and added an expectation
  file for 304 (`expectations/app-rose-agent-304.json`). The verifier has *not*
  yet inspected the screenshots or receipts in the evidence bundle. That
  bundle is listed under unavailable inputs.

**What this is not.**
- It is not the new alpha. Alpha Actions 40719075730 failed at `file_list_diff`
  and produced no image.
- It is not beta 2804 or a Pixel result.
- It does not test the OS PR70 HOME-role migration: the guest has no patched
  Permission APEX.
- It is not paired-session continuity.
- It is not phone acceptance.

## Provenance (as reported by the Infra lead)

| Item | Value |
| --- | --- |
| Window | INFRA-RUNTIME-100, executed 17:33:21–17:45:38 UTC. Compute TERMINATED verified 17:45:40 |
| Guest | Existing runner `pixel-os-smoke-20261023-a`, fresh isolated instance 2, user 0. New HOME, images and userdata from the retained original `images.zip` `e8f33200…aa36`. The historical userdata (mtime 2026-10-23T20:10:50Z) was never targeted |
| Guest build | `generic/aosp_cf_x86_64_only_phone/vsoc_x86_64_only:17/CP2A.260605.016/2026102300:userdebug/test-keys`, API 37, ABI `x86_64,arm64-v8a`. Six live framework/system file hashes match the historical receipt. This is the historical October 23 OS baseline |
| APK | Public `pixel-ota` prerelease `rose-agent-304`, `dev.pixel.voice-304.apk`: 1,385,629 bytes, SHA-256 `d8f69efcdcbc209ec5fd16badcaaf4c98fa614d7eb5fc54c1431a6f098d26966`, signer `650d47f9…0bfb`. Product `a3d839e6`, recipe `31e58006`, no native libraries |
| Install | `install --user 0` succeeded. The installed version is 304 and the installed hash matches the public artifact |
| Method | `scripts/rose-verify.py` at `b63377be` (r2), run read-only and unmodified, with an explicit app expectation, user 0 and handback `INFRA-RUNTIME-100` |
| Guest settings | No permissions granted (microphone granted=false). No pairing copied. No voice or session start. No tunnel, firewall, IAM or security change |
| Boot | The first start (`start_webrtc=false`) hung before the kernel. Only the isolated guest was stopped, and it booted with the historically working `start_webrtc=true` |

## Reported results

### Read-only capture (method r2; Infra receipt `ok=false`, which is correct)

| Check | Result | Interpretation |
| --- | --- | --- |
| Boot completed, guest target, installed version and bytes for user 0 | pass | 304 is installed for user 0 with the exact public bytes |
| Agent tray property | fail | The historical October 23 image predates the tray property. This is a baseline gap, not a 304 result |
| HOME role holder, user 0 | fail: `app.grapheneos.setupwizard` | On this unprovisioned historical guest, SetupWizard holds HOME |
| Unqualified MAIN/HOME, user 0 | fail: `app.grapheneos.setupwizard/.view.activity.WelcomeActivity` | Same. The system Home is not Rose here |
| `rose_home_role_selected`, user 0 | null | Expected: the guest has no PR70 Permission APEX |

These failures stand as recorded. They are the historical baseline, not
results of the HOME fix, and no check was weakened.

### Interactive capture

The Infra host lacks Pillow, so the lead ran these steps manually with ADB,
saving steps, focus, PNGs and XML. The comparison ran locally afterwards.

| Step | Reported observation |
| --- | --- |
| System HOME key | SetupWizard |
| Fresh explicit MAIN/LAUNCHER (app icon) | Android permission prompt. Back dismissed it to `PairingActivity` (unpaired: pairing-first) |
| Explicit MAIN/HOME to `dev.pixel.voice/.MainActivity` | Rose Home rendered: shoreline wallpaper, centered Rose, light dock. The All apps interaction was captured |
| Tap `Open Rose` | `RoseConversationActivity`, showing "Cannot reach server" (unpaired guest, no backend) |
| Back | Returns to Rose Home |

Reported heuristic comparison (`compare`, Home against bundled reference
`409c0dbf…d8c5`):
- verdict `wallpaper-consistent`;
- coverage 0.833;
- mean luminance 195.0;
- p95 luminance 240.0.

## Verifier interpretation

**What this evidence supports, as reported:**
- The exact public 304 APK installs on an API 37 x86_64 guest for user 0.
- Through an explicit HOME component, it renders the Rose Home page with the
  prototype wallpaper, centered Rose and dock.
- Its Rose presence opens the conversation surface, and Back returns to Rose
  Home.
- An unpaired, permissionless guest leads with the permission prompt and then
  pairing. The conversation shows a truthful "Cannot reach server" rather than
  a fake connected state.

**What it does not show:**
- **The system Home default.** SetupWizard held HOME, and an explicit component
  launch never proves the unqualified default.
- **PR70 migration or its per-user marker.**
- **The PR67 paired cold-launch path** (MAIN/LAUNCHER opening the conversation
  over Rose Home with valid pairing), Recents and recreation, or session
  continuity.
- **Reboot or update preservation.**
- **Anything about frankel or the new alpha.**

**Two kinds of visual evidence, kept separate:**
- *Heuristic comparison* (a 0.833 coverage number): it only says the screen's
  wallpaper regions match the reference crop.
- *Actual visual interpretation* (a person looking at the PNGs against
  `Rose-Fold-v3.html`): the lead reports an inspection. The verifier has not
  repeated it yet.

## Unavailable inputs (verifier)

| Input | Exact status |
| --- | --- |
| Evidence bundle: Library `libfile_a551aca1ebf0819195084fda2792ec38`, file `file_00000000e14c8230850f2a0baa1616ed`, `Rose-Agent304-app-only-runtime-evidence.zip`, 927,791 bytes, SHA-256 `097b64c6c1d73ece027c23337407fedeeb7cfdcff1f684aa830d6139ff983379` | Not retrieved. This session has no Library skill or tool (ListSkills/SearchSkills: none; no session upload). No other route was tried. Supported alternative: attach the ZIP to a Notion page as a file; the verifier can then fetch it through `notion-get-file-download-urls` and check the SHA-256 |
| Public `dev.pixel.voice-304.apk` and `SHA256SUMS` | One download attempt (about 17:50 UTC) was denied by this session's auto-mode permission classifier before any request was sent. It was not retried. The 304 identity is taken from Infra's receipt |

## Method follow-up (r3, INFRA-VERIFY-003)

This run exposed two gaps, now fixed in `scripts/rose-verify.py`:

1. **Pillow is optional on the capturing host.** Interactive capture no longer
   needs Pillow. Without it, each screenshot is kept and its comparison is
   recorded `unavailable` with a pointer to run `compare` locally. The "not
   black" check is then unmeasured, not passed.
2. **Labeled explicit Rose HOME step.** After the system HOME key, r3 also
   launches `-n dev.pixel.voice/.MainActivity` with MAIN/HOME and labels it
   "explicit component (does not prove the HOME default)". It taps `Open Rose`
   from there and checks that Back returns to Rose Home. A SetupWizard or
   Launcher3 holder still fails the system-HOME check, so `ok` stays false.
   This matches the steps the lead ran by hand here.
