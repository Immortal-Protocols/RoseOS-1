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

**Existing files this workstream edits, as agreed with their owners and applied in PR #626:**
- **Home/App Product** (`session_01EA17TTWtj67hdeC1KgHTe2`) reviewed the Home commits (`4c84aff` and `f2c492e`, on main
  after #617):
  - `MainActivity`:
    - the listener and a coalesced apply;
    - planning, capacity and staleness through `CustomizationConfig.effectiveHomeColumns`/`homeCapacity`/`homeStale`;
    - the wallpaper filter on Home's `OxbloodField`, so `HomeGlassTile.Backdrop` gets it;
    - `chatFont`;
    - the `markHomeApplied` report.
  - `OxbloodField`: the `setColorFilter` variant hook.
  - `HomeAppTile` and `HomeFolder`: label typefaces.
  - `tests/home-launcher.test.mjs`: the planning and label pins.
  - `HomeAppLayout` and `HomePlan` are unchanged.

  Conditions, all met:
  - `homePlanBreaker` still governs automatic replans; only a customization change bypasses it.
  - Home rebuilds only on an effective change (plan, wallpaper or font), coalesced.
  - No page flip-flop, and a second page is never hidden.
  - The max-columns overflow case is covered by `CustomizationConfigTest.reflow`.
  - Hunks are local, and there is no settings-dialog entry.
- **App Core** (`session_015TFqkSQ4kabGkMUynZZPrN`).
  - **`SettingsAgentProvider`** (`7a14fef`, applied): a two-line `customization_` forward inside the signature-gated
    `execute()`, plus the capabilities merge. App Core reviewed it as owner and treated it as security-relevant.
    `CustomizationAgent` stays in its own file. The gate, receipts and timeout are unchanged, and a node test pins this.
  - **`rose/codex_conductor.py`** (App Core's file; their #627, merge `db03077`): registers `rose.customization.TOOLS`
    and the policy line on the conversation thread. Their conditions were no free-text logging, `ok` only for the
    phone's confirmed status, and no retries. This workstream did not edit the file.
- **OS Product** (`session_015TFqkUvLjBgRPN1soK4VYh`). The Home fallback for "tray" is agreed. Raising the OS edge
  over the current app is a documented limitation: there is no exported or permissioned entry. Rebuilding the in-app
  `HomeTray` through `attachLauncherPages(true)` does not interact with the 1A OS edge (#618).
- **OS Core** (`session_015TFqkUBnNiWWDvKDuGw3hi`). Nothing in SystemUI, the shade or gestures is touched. OS Core
  confirmed that the platform has no glasses-worn signal.
- **Unassigned:** `GlassesReconnect`, `GlassesAdvertisement` and the glasses side of `VoiceService` are read-only
  here until an owner is agreed. App Core will accept a 1–3 line `GlassesWearHookRunner` call in `VoiceService` once a
  real wear source exists.

## Glasses-on signal

This repository has no real worn signal (finding below); the owner chose a Bluetooth audio proxy instead.

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

**Owner decision (Nathan, 2026-11-07): skip both the Glasses Camera relay and Meta DAT.** The hook's signal is any
Bluetooth audio device connecting (glasses on) and the last one disconnecting (removal). `BluetoothAudioWear` reports
this from AudioManager's device list, which needs no permission, and registers once from `RoseAgentApplication`.

This is a proxy, not a worn sensor: other Bluetooth headphones count too. `customization_state` says so in
`glassesWear.basis`, and names the source `bluetooth_audio_connection`. The RB Meta hinge advertisement and generic
connection events are still refused.

The hook's rules, tested on the host:
- it fires once per observed not-worn → worn transition, with not-worn lasting at least 3 s, so a reconnect blip does
  not fire;
- a device already connected when the process starts does not fire;
- a second device joining while one is connected does not fire;
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

## Source delivery

- #626 (merge `002d5d4`): the controls, Home integration, provider forward, conversation module, CLI and this record.
- #627 (merge `db03077`, App Core): conductor registration.
- #645 (merge `3d6f1ad`): the glasses-on hook's signal is Bluetooth audio connect/disconnect (owner's choice).

Builds that carry it:
- **Frankel 2026110701** (Beta Latest; os-release run 42389218389 on main `74b7ef8`; Rose Agent `voice` versionCode
  366). It includes #626 and #627: columns, wallpaper, font and the conversation tools. It does not include #645, so
  the glasses-on hook has no signal in that build.
- **The next Frankel daily build** (04:00 America/Los_Angeles, enabled by #621) builds from main, so it carries #645.

This workstream has not verified either build, installed it or run it on a device. The identities above come from the
Release lane's run record.

On a phone the controls are reachable through conversation only after the Rose backend is restarted from main and a
build that carries them is installed. Frankel 2026110701 is published as Beta Latest, but its installation on a phone
and a backend restart from main have not been confirmed. This workstream itself has not built, installed or run an APK.
The glasses-on hook fires only in a build that includes #645.

## Demo runbook (for whoever deploys; nothing here has been run)

Each step is an owner or operator action. This workstream performs none of them.

1. **Agent release (Release / 1F owner, owner-approved).** Today `agent-beta-auto.yml:29` holds automatic Agent releases
   with `if: false`. The remaining route is an owner-approved `os-release.yml` dispatch on main at or after `d74a8e0`,
   with `mode: app-only`, `apps: auto`, `app_publish: true`. It runs on `immortal-os-builder`, which has to be back in
   service first. The output must be a Rose Agent prerelease with a higher versionCode and its v4 `.apk.idsig`; without
   the `.idsig` the phone refuses the system-app update. Verify it per `docs/VERIFY.md`.
2. **Producer (the owner's Mac).**
   - Integrate main at or after `db03077`, keeping any local changes.
   - Rerun `bash scripts/setup.sh` only if `requirements.txt` changed.
   - Restart with `bash scripts/rose.sh --brain codex --phone real` from `prototypes/voice-phone-demo`. The local brain
     does not carry the customization tools.
3. **Phone.** Install the new Rose Agent and confirm Android's prompt. Nothing installs on its own.
4. **Check.**
   - Ask Rose "set Home to five columns", or on the producer Mac run
     `node workspace/rose-agent/customization/rose-customize.mjs --say columns 5`.
   - Then ask for the state: `home.applied` should be true and `home.effectiveColumns` should be what Home draws.
   - Repeat for `wallpaper ember` and `font serif`.
   - With `glasses-hook --on --tray`, connect any Bluetooth audio device: Rose Home comes forward once. Disconnect it for at
     least 3 s and reconnect: it fires again. `glassesWear.basis` names the Bluetooth audio proxy.
   - Restore with `columns auto`, `wallpaper classic`, `font default` and `glasses-hook --off`.

What counts as evidence: a phone check like this is physical-device evidence for this exact build only. Record the
Agent tag, versionCode, source commit and producer commit next to it. Host tests are not a substitute.
