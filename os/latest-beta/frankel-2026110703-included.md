# Frankel 2026110703: included changes

- Build `2026110703`, tag `frankel-2026110703`, channel beta; os-release run 42402706396 (published and promoted to Beta Latest).
- Source: RoseOS-1 main `7e7741c4bfff6695e9c4f75a016a1f60441534d6`.
- Integrated candidate: `2ff76cea30ae3e46c443970cff44f9f5fda5612e` (source + the version-only delta + generated notes, derived by `scripts/release-allocate.py derive` from this build's allocation; pushed as branch `claude/clever-ptolemy-fmr06h-candidate-frankel-2026110703`).
- Previous Beta: `2026110701` (source `74b7ef86a3b74e9047cff699aae37b9736c6e018`).
- Allocation: `d15195e37349702b6e674e3380b12b7d4c34e88b5b5170893aabee7f2f84ef32`; delta `ff463225dc30158dfce335c0af0531eaeddfe46e5423b3bc0b7a7e00aa5d85e6`; BUILD_DATETIME 1794077077.

## The combined-OTA pieces in this build

| Piece | PR | Merge on main |
|---|---|---|
| 1O glasses-on hook: Bluetooth audio connect/disconnect is the signal (not in 2026110701) | #645 | `3d6f1ad` |
| 1I Rose Models retired: Home offers an installed copy's uninstall through Android | #651 | `c6e667a` |
| 1I Release slice: the Models sign/publish routes removed (workflows/scripts only, not image contents) | #658 | `b0aacce` |
| 1I the Models app source, CI build and JNI adapter removed | #659 | `ddffbeb` |
| 1M Rose keyboard (RoseKeyboard on /product, the default once per user via Launcher3) | #654 | `4053bd6` |
| Keyboard rule 4: status-bar "Mic off" indicator, plus the 1G shade refinements | #653 | `735f990` |
| The chin rises over an open keyboard, plus OS Core's SystemUI-core javac fix for #653 | #665 | `7e7741c` |
| 1H cleanup: Classic Home, voice-controls and Mac-screen code removed | #656, #660 | `99f424a`, `53cb9e7` |

**Not in this build:** Terra screenshot-coordinate control (#649, `f5b1210`). It merged after this build's source
was captured; the next Frankel build, from `f5b1210` or later, carries it.

**Superseded attempt:** build 2026110702 (os-release run 42399788831, main `abc9358`) failed to compile in
SystemUI-core javac (`RoseShadeController.java:990`, fixed by #665). Its allocation (voice 367) stays consumed;
nothing was published.

**Compile and signing evidence** (builder job 128493910733 log):
- `Install: …/product/frankel/product/app/RoseKeyboard/RoseKeyboard.apk`: RoseKeyboard's first Soong compile;
- `//frameworks/base/libs/WindowManager/Shell:WindowManager-Shell javac`, `//packages/apps/Launcher3:Launcher3QuickStepLib
  javac` and `//frameworks/base/packages/SystemUI:SystemUI-core javac` ran, then `#### build completed successfully
  (12:49 (mm:ss)) ####`;
- release signing `signing: RoseKeyboard.apk (keys/releasekey)`, and the signed target files hold
  `PRODUCT/app/RoseKeyboard/RoseKeyboard.apk`;
- `ota-verify` `"verdict": "PASS"`; the publisher reported `"draft": "verified", "promoted": true, "simulated": false`.

## Bundled apps

- `dev.pixel.voice` 368 (368-os-beta-2026110703): changed, new version
- `dev.pixel.calculator.agent` 5 (1.3): unchanged since the previous Beta
- `dev.pixel.backgroundcontrol` 39 (2.1.5-pip): unchanged since the previous Beta

## Main commits since the previous Beta (24, first parent, oldest last)

- `7e7741c4bfff` Merge #665: the Rose chin rises over an open keyboard (F10 rules 1-2), plus OS Core's SystemUI-core javac fix
- `e9897f9394f2` Merge pull request #663 from Immortal-Protocols/cursor/yogi-iap-backend-wait-8d05
- `abc93581f63a` Merge pull request #664 from Immortal-Protocols/cursor/mustang-guest-ack-c6bf
- `0efeca4bd8e1` Merge #661: 1O Bluetooth-signal wording in the conversation policy; runbook uses the Frankel daily build
- `735f9908ad9a` Merge #653: status-bar mic display-only ("Mic off"), 1G shade refinements, WMShell sibling rebind
- `53cb9e7ad380` Merge #660: remove the unreachable Mac screen panel and its page
- `ddffbeb2b79d` Merge #659: 1I removes the retired Rose Models app's source, CI build and JNI adapter
- `62c2c3aa4c46` Merge pull request #657 from Immortal-Protocols/cursor/mustang-source-mainline-c6bf
- `1082e0f5ed5f` Merge pull request #652 from Immortal-Protocols/cursor/yogi-ssh-diagnose-8d05
- `b0aacce00971` Merge #658: 1I Release slice, Rose Models' sign and publish routes removed
- `4053bd6c2aac` Merge #654: 1M, the Rose keyboard, the selected design as an input method in the normal OS build
- `99f424a1cd30` Merge #656: 1H cleanup removes the Classic Home, voice-controls and Mac-screen code their settings opened
- `4bdf6b4ba973` Merge pull request #646 from Immortal-Protocols/cursor/mustang-metadata-cli-c6bf
- `0ea47da9c00d` Merge #655: 1O docs drop the closed Meta DAT / worn-signal question
- `c6e667a47128` Merge #651: 1I Rose Models is retired; Home offers an installed copy's uninstall through Android
- `e7fbd0e74caf` Merge #650: 1O docs record which Frankel builds carry the customization controls
- `d63c47f6b1f5` Merge #648: record the published Frankel 2026110701 allocation for app-inputs export
- `3d6f1ad0ff43` Merge #645: 1O glasses-on hook uses Bluetooth audio connect/disconnect (owner's choice)
- `c2c3b9419312` Merge #621: 1E Frankel daily lane on (04:00 America/Los_Angeles), after green manual build 2026110701
- `2099a1a1abd0` Merge pull request #644 from Immortal-Protocols/cursor/yogi-describe-read-8d05
- `fc232c94b275` Merge pull request #642 from Immortal-Protocols/cursor/mustang-jit-orphan-c6bf
- `ccc5b61a4e73` Merge #628: Node server requires an enrolled, unrevoked phone key for /phone/commands and the target phone
- `256fe0bc6399` Merge pull request #640 from Immortal-Protocols/cursor/mustang-allocation-authority-c6bf
- `13736a3f911c` Merge pull request #643 from Immortal-Protocols/cursor/yogi-start-stop-wait-8d05

Evidence limits: built, signed and verified by the release workflow (image, signature, version and download checks). Nothing here claims phone runtime acceptance.
