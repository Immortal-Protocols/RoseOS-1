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
| Theme (plan 1E) | `customization_theme` | `oxblood` (default) or `glacier`. See "Theme (plan 1E)" below. |

`customization_state` reads all four, plus Home's applied revision and the glasses-on signal's state.

Each result reports three things separately, as decided by `CustomizationOutcome`:
- `status`: `verified` means stored; it never means visible.
- `home`: `applied`, and `effectiveColumns`, the count Home actually draws. `effectiveColumns` is null until Home has
  drawn that exact revision.
- `glassesWear`.

So a 6-column request on a screen that fits 4 is never reported as 6. Home reports what it drew through
`CustomizationStore.markHomeApplied(revision, effectiveColumns)`, which is part of the Home seam.

The wallpaper variants are bounded colour transforms of the shipped Oxblood field: a hue turn of at most 15° and
saturation/brightness factors in 0.5–1.6. They were strengthened after the owner's phone check (Nov 10) found the first
set, 0.95–1.12, invisible. `OxbloodVariantsTest` checks every variant against `OxbloodField`'s own palette.

The owner's phone check also found two other problems:
- Columns: a phone fits only four 60dp Home cells at the 17.5dp gap, so 4–8 all clamped to four and only 3 showed. A
  chosen count may now narrow the gap toward 6dp (`HomeAppLayout.fitGap`), so a phone fits 3–5. Automatic plans keep
  their gap.
- Font: the Home page shows no labels. The Home date now carries a chosen font; the default is unchanged.

## Theme (plan 1E)

Owner: OS Core (`session_015TFqkUBnNiWWDvKDuGw3hi`), as one implementation owner. The theme is added to this catalog;
the four controls above are unchanged.

- **Definition.** `themes/rose-themes.json` (see `themes/README.md`) defines each theme: stable id, its own wallpapers,
  per-surface colours and Glacier's derivation, the approved prototype's colour map.
- **What a switch changes.** "Switch to Glacier" (`customization_theme`) recolours Home, the Rose screen, the tray and
  chin, notifications and the keyboard. Columns, font, app icons and the hook are kept.
- **Wallpapers belong to their theme.** Glacier draws its own field; Oxblood's chosen variant is remembered and comes
  back with Oxblood. A wallpaper request in another theme is refused with the reason.
- **Reaching other processes.** Rose Agent stores the choice beside the other controls and sends it on the existing
  SystemUI tray link. SystemUI applies it, mirrors it to `Settings.Global rose_theme` with its existing permission for
  Launcher3 and the keyboard, and acknowledges what it shows.
- **Truthful outcomes.** A theme is reported `applied` only when Home drew that revision (`home.applied`) and SystemUI
  acknowledged drawing it (`system.applied`). Otherwise the outcome is `shown_on_home` or `saved_not_shown`.
- No new permission, exported component or endpoint.

Excluded from 1O (the theme came later, as 1E): web-app shortcuts, motion controls and general widgets. Also excluded: an exported or
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
  confirmed no platform change is needed.
- **Untouched:** `GlassesReconnect`, `GlassesAdvertisement` and `VoiceService`. The Bluetooth audio signal needs none of
  them; it is registered from `RoseAgentApplication`.

## Glasses-on signal

**Owner decision (Nathan, 2026-11-07).** The hook's signal is any
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
- **Frankel 2026110703** (Beta Latest; os-release run 42402706396 on main `7e7741c`; integrated candidate `2ff76ce`;
  Rose Agent `voice` versionCode 368, `368-os-beta-2026110703`). It carries #626, #627 and #645, so it has all four
  controls including the Bluetooth glasses-on signal. The candidate's Rose Agent sources match main `7e7741c`, and it
  registers `BluetoothAudioWear` and forwards `customization_*`. Release's record:
  `os/latest-beta/frankel-2026110703-included.md`. The first attempt (run 42399788831) failed in SystemUI on an OS Core
  shade bug, fixed by #665.
- **Frankel 2026111001** (Beta Latest; daily os-release run 42813894214 on main `6bab12c`; integrated candidate
  `ace876a`; Rose Agent `voice` versionCode 370, `370-os-beta-2026111001`). This is the first build with the fixes
  from the owner's Nov 10 phone check: #729 makes columns (3–5 on a phone), wallpaper and font visible. Its source also
  contains #732, whose honest outcomes take effect on the producer, not the phone. The candidate's Rose Agent sources
  and assets are identical to main `6bab12c`.
- **Producer.** The Mac mini producer was running `149dd4e1` (Nov 7) at the owner's 10:38Z Nov 10 receipt. That
  includes #627 and #661 but not #732. Columns, wallpaper, font and the hook can be phone-checked on it. Honest-outcome
  acceptance needs it redeployed from main at `8676ae2` or later by its existing owner.

This workstream checked these build records and sources only; it has not installed a build or run one on a device. The identities above come from the
Release lane's run record.

On a phone the controls are reachable through conversation only after the Rose backend is restarted from main and a
build that carries them is installed. Frankel 2026110703 is published as Beta Latest, but its installation on a phone
and a backend restart from main have not been confirmed. This workstream itself has not built, installed or run an APK.
The glasses-on hook fires only in a build that includes #645.

## Demo runbook (for whoever deploys; nothing here has been run)

Each step is an owner or operator action. This workstream performs none of them.

1. **Build (Release lane).** Use a Frankel latest-beta build from main at or after `3d6f1ad`; the daily Frankel lane
   (04:00 America/Los_Angeles, #621) produces one. Frankel 2026110701 predates #645, so the glasses-on hook has no
   signal in it. Verify the build per `docs/VERIFY.md`.
2. **Producer (the owner's Mac).**
   - Integrate main at or after `db03077`, keeping any local changes.
   - Rerun `bash scripts/setup.sh` only if `requirements.txt` changed.
   - Restart with `bash scripts/rose.sh --brain codex --phone real` from `prototypes/voice-phone-demo`. The local brain
     does not carry the customization tools.
3. **Phone.** Install the build through the OS Updater on the Beta channel and reboot. Keep the phone unlocked for the
   glasses-on check, because a locked phone refuses `open_app` and the tray action only reaches Home behind the lock.
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
