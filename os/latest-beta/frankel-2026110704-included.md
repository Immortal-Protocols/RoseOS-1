# Frankel 2026110704: included changes

- Build `2026110704`, tag `frankel-2026110704`, channel beta; os-release run 42403912447 (published and promoted to Beta Latest).
- Source: RoseOS-1 main `f5b1210c358a31f305b004d8b9dc98a3906f398f`.
- Integrated candidate: `01979fc2ba315f761ae6ba15b6355f3372ff14bb` (source + the version-only delta + generated notes, derived by `scripts/release-allocate.py derive` from this build's allocation; pushed as branch `claude/clever-ptolemy-fmr06h-candidate-frankel-2026110704`).
- Previous Beta: `2026110703` (source `7e7741c4bfff6695e9c4f75a016a1f60441534d6`).
- Allocation: `2987065508019406e182f43a64661c491b0db432d59fd9cd74e6974dd201a6f7`; delta `232c7980ce0ee87e29aab2db87b6475c37234a8eba789003f61ba8c1b3aad5c2`; BUILD_DATETIME 1794079122.

## The complete combined OTA

This build is the combined Frankel OTA Chief asked for. It holds everything in
[frankel-2026110703](frankel-2026110703-included.md) (#645, #651, #658, #659, #654, #653, #665, #656 and #660), plus:

| Piece | PR | Merge on main |
|---|---|---|
| Terra screenshot-coordinate control (opt-in), with controller `screen_text` safety (integrates #550) | #649 | `f5b1210` |

#649 changes the background controller, `dev.pixel.backgroundcontrol`, which moves from 39 to 40. Its Terra producer
side (`prototypes/voice-phone-demo/rose/vision_phone.py`) is not image content and is not deployed by this build.

**Build and signing evidence** (builder job 128502367475 log):
- an incremental build, `#### build completed successfully (03:49 (mm:ss)) ####`;
- `Install: …/system_ext/priv-app/PixelBackgroundControl/PixelBackgroundControl.apk` (the controller, presigned
  with the app signer, so release signing reports `NOT signing: PixelBackgroundControl.apk`);
- `signing: RoseKeyboard.apk (keys/releasekey)`;
- the publisher reported `"draft": "verified", "promoted": true, "simulated": false`.

## Bundled apps

- `dev.pixel.voice` 368 (368-os-beta-2026110704): unchanged since the previous Beta
- `dev.pixel.calculator.agent` 5 (1.3): unchanged since the previous Beta
- `dev.pixel.backgroundcontrol` 40 (2.1.5-pip): changed, new version

## Main commits since the previous Beta (1, first parent, oldest last)

- `f5b1210c358a` Merge #649: integrate #550 screenshot-coordinate Terra control (opt-in) with controller screen_text safety

Evidence limits: built, signed and verified by the release workflow (image, signature, version and download checks). Nothing here claims phone runtime acceptance.
