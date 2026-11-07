# Frankel 2026110705: included changes

- Build `2026110705`, tag `frankel-2026110705`, channel beta; os-release run 42409144078 (published and promoted to Beta Latest).
- Source: RoseOS-1 main `c89d530cda33354ec593c1fb1338aa50e5dc7484`.
- Integrated candidate: `5ceafc8a2a4916c61c9670c2858e1c8e0a2bb3d4` (source + the version-only delta + generated notes, derived by `scripts/release-allocate.py derive` from this build's allocation; pushed as branch `claude/clever-ptolemy-fmr06h-candidate-frankel-2026110705`).
- Previous Beta: `2026110704` (source `f5b1210c358a31f305b004d8b9dc98a3906f398f`).
- Allocation: `4e63527df5d78e9eceea176a82d340f737b54f81d8ebe7add59d53738d61eb02`; delta `ca0c80fb0ade4b6a86eb82db6e15b979091a4dcbba0caebd9ee6580f26cdd750`; BUILD_DATETIME 1794081854.

## What this build adds

It holds everything in [frankel-2026110704](frankel-2026110704-included.md), the complete combined OTA, plus:

| Piece | PR | Merge on main |
|---|---|---|
| The Rose chin inset: the keyboard rides up 34dp, and the shade's panel and scrim stay above the raised chin | #669 | `40a3583` |
| Launcher3 compile contract re-pin (#669's first carrier) | #662 | `55cd9c4` |

The three bundled apps are unchanged since 2026110704 (voice 368, calculator agent 5, background control 40). So
Mustang's common-APK export from 2026110704 (`os/app-inputs/frankel-2026110704.json`) still matches current main.

**Compile and signing evidence** (builder job 128513490266 log):
- `Install: …/product/frankel/product/app/RoseKeyboard/RoseKeyboard.apk`;
- `//packages/apps/Launcher3:Launcher3QuickStepLib javac`: RoseEdgeWindow's `systemOverlays()` inset through
  hidden `InsetsFrameProvider` compiled for the first time;
- `//frameworks/base/packages/SystemUI:SystemUI-core javac`, then `#### build completed successfully (11:21 (mm:ss)) ####`;
- `signing: RoseKeyboard.apk (keys/releasekey)`; `ota-verify` `"verdict": "PASS"`; publisher
  `"draft": "verified", "promoted": true, "simulated": false`.

**Device checks still open.** Whoever holds the phone runs these on this build, with Infra's handback:
- the gap between the keyboard's space row and the chin's concave top is 0;
- the shade's panel and scrim end above the raised chin;
- an immersive app can't hide the raised chin;
- the keyboard stays up after the recents return.

## Bundled apps

- `dev.pixel.voice` 368 (368-os-beta-2026110705): unchanged since the previous Beta
- `dev.pixel.calculator.agent` 5 (1.3): unchanged since the previous Beta
- `dev.pixel.backgroundcontrol` 40 (2.1.5-pip): unchanged since the previous Beta

## Main commits since the previous Beta (10, first parent, oldest last)

- `c89d530cda33` Merge #676: record the app-inputs export of Frankel 2026110704 for Mustang
- `40a358379c97` Merge #669: the Rose chin inset (keyboard rides up 34dp, shade stays above the chin), #662's second carrier
- `ed5340b9f8c3` Merge #674: record Frankel 2026110704 (complete combined OTA, Beta Latest) allocation and included changes
- `55cd9c49593b` Merge pull request #662 from Immortal-Protocols/rebind-launcher3-integrated
- `595ac48766b7` Merge pull request #672 from Immortal-Protocols/cursor/mustang-jit-env-c6bf
- `98cbb59863e7` Merge pull request #668 from Immortal-Protocols/cursor/mustang-hosted-sign-c6bf
- `f2b821f8e9bd` Merge #670: record Frankel 2026110703 (Beta Latest) allocation and exact included changes
- `349f4809565b` Merge #671: 1O docs record Frankel 2026110703, the first build carrying all four controls
- `9f62bf8e165e` Merge pull request #667 from Immortal-Protocols/cursor/yogi-known-hosts-bind-8d05
- `e8e1f7008988` Merge pull request #666 from Immortal-Protocols/cursor/mustang-guest-ack-c6bf

Evidence limits: built, signed and verified by the release workflow (image, signature, version and download checks). Nothing here claims phone runtime acceptance.
