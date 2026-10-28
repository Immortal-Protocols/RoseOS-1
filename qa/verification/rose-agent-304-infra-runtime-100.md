# Runtime record: app-only Rose Agent 304 on a historical Cuttlefish guest (INFRA-RUNTIME-100)

**Who measured what.**
- **Infra lead:** executed the runtime window and reported the results below.
- **Verifier:**
  - recorded the results and their limits, and added an expectation file for
    304 (`expectations/app-rose-agent-304.json`);
  - independently retrieved the two original screenshots (hashes verified),
    inspected them, re-ran the heuristic comparison, and rendered the
    prototype to compare against them. See "Independent visual inspection";
  - has not inspected the rest of the bundle (receipts, XML, step records).

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
- *Actual visual interpretation*: a person looking at the PNGs against the
  rendered `Rose-Fold-v3.html`. That is below.

## Independent visual inspection (verifier, 18:08–18:20 UTC)

### Inputs

| Input | Value |
| --- | --- |
| `explicit-rose-home.png` | Retrieved through the native Notion attachment on the verifier inbox (`notion-get-file-download-urls`). 890,898 bytes, SHA-256 `04077aba09bc15e8f47730c7e5afe86a96ad3e852e53a4f8a57c535c240cccaf` (matches). 720×1280 RGBA. Explicit MAIN/HOME component, not the system HOME |
| `rose-presence.png` | Same route. 34,152 bytes, SHA-256 `d50b66a57864df394f5c354634eae9f8fe6d7d911a33799bdca938aab036e33e` (matches). 720×1280 RGBA. `RoseConversationActivity` after Open Rose |
| Prototype | `Rose-Fold-v3.html` from the native Notion attachment on OS-P1-001 (OS-MGR-ASSET-M002). 859,202 bytes, SHA-256 `67bf7a505ffdd64087c76e6be496043afb7576c081b2fe8ed1c6642aec9405ee` (matches Library v2) |
| Prototype render | Headless Chromium 1243 (pre-installed Playwright). Network blocked (`file:`/`data:` only), viewport 1280×1000, reduced motion. "Folded phone" layout; `#device` element 390×800. Renderer label: "WebGL procedural petals · static state (reduced motion)". Home, then `#presence-hit` for Conversation |

The screenshots, comparison images and render stay in the verifier's scratch
space. They contain no personal data, but are not committed.

### Heuristic, re-run independently (method r3 `compare`)

| Screenshot | Verdict | Coverage | Mean / p95 luminance |
| --- | --- | --- | --- |
| Rose Home | `wallpaper-consistent` | 0.833 | 195.0 / 240.0, the same as the lead's numbers |
| Conversation | `differs-from-reference` | 0.197 | 246.9 / 249.3 |

The conversation result is expected. That surface has no wallpaper, so the
wallpaper heuristic does not apply to it. It is not a defect.

The Home difference image is near-black everywhere except the Rose tile, the
dock, the page dots, the status bar and the navigation arrow. So the wallpaper
is drawn as the exact reference JPEG (`409c0dbf…`) with the centered cover crop
of `RoseHomeView`, not an approximation.

### Rose Home: observed, compared with the prototype's Folded Home

Matches the prototype:
- **Wallpaper:** the same shoreline image (misty mountains, clear water,
  rocks) fills the screen with the same crop. The status bar draws over it.
- **Presence:** a rounded light-ivory tile holding the Rose mark, centered
  horizontally, about 40% of the screen width. That proportion is the same as
  in the prototype.
- **Dock:** a light, translucent, rounded dock at the bottom.
- **No noise:** no status prose, simulation labels or titles on the phone
  surface (the Fold brief's v1 review).

Differs from the prototype (findings for OS, who owns Home):
1. **Rose mark:** a flat, two-tone celadon leaf with an ivory opening, drawn
   from the prototype's SVG fallback paths. The prototype's live rendering is
   a shaded, three-dimensional folded-petal form (WebGL). The fallback is a
   recorded design choice (OS-P1-001-E003). It is not the WebGL form.
2. **Tile position:** the tile sits higher, its center about 38% from the top,
   versus about 44% in the prototype.
3. **Dock contents:** five entries (Phone, Messaging, Camera, Browser, All
   apps), resolved from the guest's real apps, versus the prototype's three
   fixtures. Two labels are truncated on this 720 px guest: "Messag…" and
   "Camera…". The prototype's single-word labels fit.
4. **Extras:** six page dots (Home is the first) and the Android back arrow
   appear below the dock. The prototype has neither.

### Conversation: observed, compared with the prototype's Folded Conversation

Matches the prototype:
- **Background:** a light ivory surface, with no wallpaper.
- **Presence:** large and central, in pale celadon and ivory.
- **Controls:** a single **Speak** control with a microphone. No typing
  input, "Type instead", coaching filler, Interrupt button or popup
  notification (the Fold brief's v1 review).
- **Output area:** no presented output, which is correct with no task.

Truthful unavailable state: "Can't reach the server" sits above Speak. This
guest is unpaired and has no backend. The text says so rather than showing a
connected or listening state. It is not evidence of a real outage.

Differs from the prototype (App owns the conversation):
1. **Presence form:** five overlapping translucent ellipses with a grey center
   dot (a five-petal flower). The prototype shows the same folded-petal Rose
   as its Home. The brief asks for "the same organism across contexts", yet
   here the Home mark (leaf) and the conversation presence (flower) are
   different shapes.
2. **Header:** no header. The prototype has "‹ Home" at the top left and
   "Rose" at the top right; the screenshot shows only an overflow "⋯" (More
   options) at the top right. On this guest the return path is the system
   back arrow, which the lead reports returns to Rose Home.
3. **Agent screens route:** "View agent screens ↗" is absent. The prototype
   shows it above Speak. With no task here, the screenshot cannot show whether
   it appears once tasks exist.
4. **Speak control:** a round button with a microphone emoji and the label
   below it, versus the prototype's wide pill with a line microphone icon and
   the label inside.

### Not shown by these two frames

- Motion, the listening/speaking/muted states, and mute or end controls
  during a session.
- The All apps page, beyond the lead's report.
- The PR67 paired cold launch.
- The system HOME default.
- Anything outside this 720×1280 emulator density.

## Unavailable inputs (verifier)

| Input | Exact status |
| --- | --- |
| Evidence bundle: Library `libfile_a551aca1ebf0819195084fda2792ec38`, file `file_00000000e14c8230850f2a0baa1616ed`, `Rose-Agent304-app-only-runtime-evidence.zip`, 927,791 bytes, SHA-256 `097b64c6c1d73ece027c23337407fedeeb7cfdcff1f684aa830d6139ff983379` | The ZIP itself was not retrieved: this session has no Library skill or tool (ListSkills/SearchSkills: none; no session upload), and no other route was tried. At 18:05 Chief attached its two screenshots as native Notion images; those were retrieved and verified (above). The bundle's receipts, XML and step records remain uninspected by the verifier |
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
