# Frankel 2026110801: included changes

- Build `2026110801`, tag `frankel-2026110801`, channel beta; os-release run 42479142921 (published and promoted to Beta Latest).
- Source: RoseOS-1 main `577cb73915947642512dad2a63ee9082f41ce414`.
- Integrated candidate: `df789541dc5cbdbb0b10fc8fb615c4905667c8a5` (source + the version-only delta + generated notes, derived by `scripts/release-allocate.py derive` from this build's allocation; pushed as branch `claude/clever-ptolemy-fmr06h-candidate-frankel-2026110801`).
- Previous Beta: `2026110705` (source `c89d530cda33354ec593c1fb1338aa50e5dc7484`).
- Allocation: `44f77ee6de487d03fd841ac4107f4aa83a1e095f7a3bbe537ca148261c8e06d7`; delta `861256aaba851865ae60800ee67dd2db26612506e89758a5a3cf84de1673ca89`; BUILD_DATETIME 1794140013.

## What this build adds

The first Beta the scheduler published. Daily release dispatcher run 42479130098 is the `0 12 * * *` schedule (04:00 PST), started 12:12:14Z. For the frankel lane it decided `dispatch` ("main `577cb739…` is not yet delivered") and dispatched this run at 12:12:24Z. The mustang and yogi lanes skipped as disabled.

It holds everything in [frankel-2026110705](frankel-2026110705-included.md). No Frankel OS, app or UI change merged in between. The eight main commits are other lanes' work and one record:

| Commits | Lane |
|---|---|
| #675, #677, #680, #681, #682 | Mustang dedicated route |
| #673 | Yogi |
| #679 | Launcher3 compile lane |
| #678 | the 2026110705 record |

So this is a rebuild of 2026110705 with the same content, under a new build number, BUILD_DATETIME and bundled-app versionName. One of those commits does reach Frankel's signing path: #675 changed the release-script derivation in `scripts/host-sign.py` (`bind_checkout_helpers`). The log doesn't show whether that rewrite applied to Frankel's OTA-only script, since the derived-script hash is recorded only in the builder's sign receipt. Signing, the published metadata and ota-verify all passed with it. #675's new root `script/` files are Mustang's hosted-signer inputs. Frankel derives from the builder tree's own `script/generate-release.sh` and `script/common.sh`.

**Timestamps (UTC, 2026-11-08):**

| Event | Time |
|---|---|
| Dispatched | 12:12:24 |
| Builder started | 12:13:26 |
| Allocation | 12:13:33 (BUILD_DATETIME 1794140013) |
| Build complete | 12:18:23 |
| Signed | 12:32:14 |
| ota-verify | 12:33:45 |
| Published and promoted | 12:36:39 |
| Builder stopped | 12:37:19 |
| Run complete | 12:37:22 |

**Build, signing and publication evidence** (run 42479142921 logs):
- builder: `Install: …/product/app/RoseKeyboard/RoseKeyboard.apk` and both `RosePreinstall_*` apps, then `#### build completed successfully (03:50 (mm:ss)) ####`, unsigned target-files `2cd4e654…`;
- signing: `signing: Launcher3QuickStep.apk (keys/releasekey)`, `signing: SystemUI.apk (keys/platform)`, `signing: RoseKeyboard.apk (keys/releasekey)`, the pre-signed `RosePreinstall_dev_pixel_voice.apk` left unchanged, signed target-files `22bf30a8…`;
- `ota-verify` `"verdict": "PASS"`;
- publisher `"draft": "verified", "promoted": true, "simulated": false`;
- the public feed `releases/latest/download/frankel-stable` reads `2026110801 1794140013 frankel stable`, and pixel-ota tag `frankel-2026110801` is `e711b54a`.

The 2026110705 device checks still apply unchanged. This build adds no new on-device behaviour to check.

## Bundled apps

- `dev.pixel.voice` 368 (368-os-beta-2026110801): unchanged since the previous Beta
- `dev.pixel.calculator.agent` 5 (1.3): unchanged since the previous Beta
- `dev.pixel.backgroundcontrol` 40 (2.1.5-pip): unchanged since the previous Beta

## Main commits since the previous Beta (8, first parent, oldest last)

- `577cb7391594` Merge pull request #682 from Immortal-Protocols/cursor/mustang-hosttools-contract-c6bf
- `8b5cd0d2e4c2` Merge pull request #681 from Immortal-Protocols/cursor/mustang-source-evidence-c6bf
- `032b4bf9e95f` Merge pull request #680 from Immortal-Protocols/cursor/mustang-app-inputs-c6bf
- `3d6be23914c3` Merge pull request #679 from Immortal-Protocols/cursor/launcher3-verify-integrated-c4ef
- `149dd4e1d8fb` Merge #678: record Frankel 2026110705 (chin inset, Beta Latest) allocation and included changes
- `5454c2f1f079` Merge pull request #677 from Immortal-Protocols/cursor/mustang-source-dirs-c6bf
- `2607e120891c` Merge pull request #675 from Immortal-Protocols/cursor/mustang-tool-closure-c6bf
- `9d38a2fc0bf1` Yogi temporary-key source safeguards (#673)

Evidence limits: built, signed and verified by the release workflow (image, signature, version and download checks). Nothing here claims phone runtime acceptance.
