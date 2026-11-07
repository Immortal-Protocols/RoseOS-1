# OS Customization Demo (Nov 7 plan 1O)

Workstream record. Owner: cloud Claude session `session_015TFqkRxXcFo3NjbNqFsvGF`, titled "OS Customization
Demo". At kickoff, 2026-11-07 ~01:15 UTC, `get_session` reported:
- configured and served model `claude-opus-5-5`, ultracode on, effort medium, permission mode auto;
- environment `env_01XNtRd4oLZAnNeeJyAMMgSv` (Anthropic cloud);
- repository `Immortal-Protocols/RoseOS-1`, branch `claude/epic-keller-k7462t`, from `5554add`.

## Scope (accepted)

There are four live controls. Conversation and CLI share one catalog,
`workspace/rose-agent/app/assets/customization-agent-tools.json`.
Every change is app-side in Rose Agent: no OS rebuild.

| Control | Operation | Values |
| --- | --- | --- |
| Home column count | `customization_home_columns` | `0` = automatic, or 3–8. Clamped to what fits; apps keep their order and reflow intact. |
| Oxblood wallpaper | `customization_wallpaper` | classic (the shipped field, unchanged), garnet, ember, velvet, dusk |
| Bundled font | `customization_font` | default (shipped type), nunito (app asset), sans, serif, condensed, mono (platform families) |
| Glasses-on hook | `customization_glasses_hook` | enabled; action `tray` (Rose Home and its tray forward) or `app` + packageName |

`customization_state` reads all four, plus Home's applied revision and the glasses-worn signal's availability.

Each result reports three things separately, as decided by `CustomizationOutcome`:
- `status`: `verified` means stored; it never means visible.
- `home`: `applied`, and `effectiveColumns`, the count Home actually draws. `effectiveColumns` is null until Home has
  drawn that exact revision.
- `glassesWear`.

So a 6-column request on a screen that fits 4 is never reported as 6. Home reports what it drew through
`CustomizationStore.markHomeApplied(revision, effectiveColumns)`, which is part of the Home seam.

The wallpaper variants are bounded colour transforms of the shipped Oxblood field: a hue turn of at most 12° and
saturation/brightness factors in 0.75–1.25. `OxbloodVariantsTest` checks them against `OxbloodField`'s own palette.

Excluded: theme switching, web-app shortcuts, motion controls and general widgets. Also excluded: an exported or
permissioned API that raises the OS edge, a direct command endpoint, and any trust expansion.

## File ownership

**This workstream (new files):**
- `app/assets/customization-agent-tools.json`
- In `app/src/dev/pixel/voice/`:
  - `CustomizationConfig`, `CustomizationOutcome`, `OxbloodVariants`, `RoseFonts`, `GlassesWearHook` (pure)
  - `CustomizationStore`, `CustomizationAgent`, `CustomizationVisuals`, `GlassesWearHookRunner` (Android)
- `app/tests/{CustomizationConfig,CustomizationOutcome,OxbloodVariants,GlassesWearHook}Test.java`
- `workspace/rose-agent/customization/` (CLI, test, README)
- `prototypes/voice-phone-demo/rose/customization.py`, tested by `workspace/rose-agent/customization/test_conversation_tools.py`.
  The test sits there so the offline suite runs it under the existing `workspace/**` CI trigger.
- Appended units in `scripts/test-local.sh`
- This document

**Seams agreed with existing owners. None is applied yet; each lands as its own reviewable change:**
- **Home/App Product** (`session_01EA17TTWtj67hdeC1KgHTe2`). This workstream authors one isolated commit on main after
  #617 merges:
  - `MainActivity`: listener, effective columns and capacity, wallpaper filter and `HomeGlassTile.Backdrop`,
    `chatFont`, `homeStamp`;
  - `HomeAppLayout`/`HomePlan`: column overloads;
  - typefaces in `HomeAppTile`/`HomeFolder`;
  - an `OxbloodField` colour-filter hook;
  - `home-launcher.test.mjs` pins, reported back to Home.

  Conditions:
  - keep `homePlanBreaker` for automatic replans and bypass it only when the revision changes;
  - rebuild only on an effective change, coalesced;
  - no flip-flop between pages, and never hide a second page;
  - test max columns with an inventory that overflows;
  - local hunks only;
  - no settings-dialog entry.
- **App Core** (`session_015TFqkSQ4kabGkMUynZZPrN`).
  - **`SettingsAgentProvider`:** a ~6-line `customization_` forward plus the capabilities merge. App Core accepted it
    as owner and will treat it as a security-relevant review. `CustomizationAgent` stays in its own file. The
    provider's signature gate is unchanged.
  - **`rose/codex_conductor.py`:** App Core adds the ~4 lines that register `rose.customization.TOOLS` and the policy
    line. Their conditions: no free-text logging, `ok` only for the phone's confirmed status, no retries. Not
    touched here while App Core reconciles #550.
- **OS Product** (`session_015TFqkUvLjBgRPN1soK4VYh`). The Home fallback for "tray" is agreed. Raising the OS edge
  over the current app is a documented limitation: there is no exported or permissioned entry. Rebuilding the in-app
  `HomeTray` through `attachLauncherPages(true)` does not interact with the 1A OS edge (#618).
- **OS Core** (`session_015TFqkUBnNiWWDvKDuGw3hi`). Nothing in SystemUI, the shade or gestures is touched. OS Core
  confirmed that the platform has no glasses-worn signal.
- **Unassigned:** `GlassesReconnect`, `GlassesAdvertisement` and the glasses side of `VoiceService` are read-only
  here until an owner is agreed. App Core will accept a 1–3 line `GlassesWearHookRunner` call in `VoiceService` once a
  real wear source exists.

## Glasses-worn signal: finding

This repository has no real worn signal.

| In-repo signal | What it actually is |
| --- | --- |
| HFP/A2DP connection events (`GlassesReconnect`) | Bluetooth connection |
| `GlassesAdvertisement.hinge` | RB Meta 05XH BLE advertisement byte, `0x01ab` `80 0a 01 33`, then `0x14` open / `0x04` closed. Means unfolded, not worn, and is scanned only while disconnected. |
| `GlassesAutoStartPolicy` | Infers a "new wearing" from a reconnect gap of at least 10 s |

`voice-match/README.md:7` says the connection exposes "not reliable worn-state events". OS Core reports no platform
or vendor wear API.

**The real source is Meta's Wearables Device Access Toolkit.** `Device.donState` (`DONNED`/`DOFFED`/`UNKNOWN`,
module `com.meta.wearable.dat.core`) is per Meta's DAT 1.0 Android reference. It has not been verified on hardware
here. DAT is not in this repo. The external Glasses Camera app (`dev.pixel.glassescamera`) uses it, and only during its
own camera session.

Getting a worn event into Rose needs one of:
- (a) a coordinated change outside this repo: Glasses Camera relays `donState` to Rose Agent;
- (b) DAT in Rose Agent: a Meta App ID, Meta AI app registration and developer terms.

Either is an owner/access decision.

Until then:
- `GlassesWearHook` refuses every non-wear source (connection, hinge) and never fires.
- `customization_state` reports `signalAvailable: false` with this limitation.

The hook's rules, tested on the host:
- it fires once per observed not-worn → worn transition, with not-worn lasting at least 3 s;
- an unknown → worn change does not fire;
- disable and change take effect from the next transition.

## CLI route

No authorized route lets a local CLI send `app_command` as the paired phone. The Node server's
`phoneCommandRejection` refuses pair-token callers. A loopback command endpoint would be a new trust surface.

`rose-customize.mjs`:
- `--dry-run` (the default) validates and prints the identical command.
- `--say` submits the change as a typed request to Rose's conversation through the existing loopback `POST /api/say`,
  so it runs through the same tool. The CLI reports only that Rose accepted the request, not the phone's result.

## Security finding (reported, not exercised)

`workspace/rose-agent/server/server.mjs:360-367`: `phoneCommandRejection` treats any valid P-256 client certificate
(`peerIdentity`) as the phone, without `deviceAuth.paired(req)`. A loopback holder of the pair token could therefore
upgrade from read-only to mutations. Suggested fix: require `deviceAuth.paired(req)`.

App Core confirmed this and is reporting it to Chief. It matters only if the Node server is in use. Nothing was changed
here.

## Evidence at this revision

What was done:
- **Host tests:** the pure Java units, the CLI node test and the Python test, all run as registered in
  `scripts/test-local.sh`.
- **Compile check:** the Android glue type-checks with the whole `app/src` tree against android-35 `android.jar`. The
  build used a scratch `R.java` and ZXing stubs, because the ignored local config and the ZXing jar were absent. It
  was not a build.

What was not done:
- no APK build or signature;
- no emulator or device run;
- no physical-phone acceptance;
- no glasses hardware evidence;
- no publication.

The controls change nothing on a phone until the Home, provider and conductor seams land and an Agent build is
installed.
