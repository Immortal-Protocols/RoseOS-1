# Phone control integration — 2026-10-09

Version 12 adds on-demand Android AccessibilityService UI inspection and actions to the existing authenticated phone command transport. Builds and Node validation tests pass. Version 10 was installed over the air and returned status successfully; version 11 was recovered by reinstalling the identical APK over ADB after an Android update race. No app data or settings were cleared.

## Verified
- `control_status` reports actual installed version 12 and `uiControlEnabled: true`.
- Before enabling accessibility, `inspect_ui` returned `accessibility_disabled`, rather than pretending access.
- Enabled Rose Agent phone controls through visible Android Settings; no other services changed.
- Real wireless Calculator test: inspect, tap AC/2/+/3/=, verified result 5, Home, verified Rose Agent foreground. Replayed stale snapshot rejected.
- Real wireless Settings test: Back, scroll forward/back, set search text to Bluetooth, verify it, clear test text, Home.
- Version 12 home activity displays correctly; backend receives fresh diagnostics and microphone tracks are empty.
- Server queue validates UI action arguments and protects expiry/replay semantics.

## Pending
- Locked-screen refusal still needs a dedicated real-device check.
- Spoken glasses command is the final user-path test. The actual Codex backend test passed: it independently listed/opened Calculator, cleared expression, tapped 2+3=, verified 5, returned Home and verified dev.pixel.voice, using only wireless phone tools.
- Keep self-update offers paused until the launcher update lifecycle is addressed. Root moved offers to the private delivery/paused-pixel-voice directory.

## Update failure evidence
At 01:28:12 CorePackageUpdate killed the current HOME process during replacement and relaunched HOME before package update completion. The new process loaded the prior APK path. At 01:28:13 it logged missing old base.apk, then ResourcesManager failed to load that path, and ConfigurationController.updateLocaleListFromAppContext threw a null Resources exception before application code. The same sequence happened at 01:28:27: process used the v10 path after v11 replaced it. Identical framework crashes were also logged earlier at 23:57, 00:28, and 01:10.

Force-stop is ignored for this protected device-owner package. Reinstalling the identical v11 APK over ADB recovered it with fresh PID 28113 and no rollback. This is not evidence of bad accessibility code or APK compression/alignment. A durable mitigation must prevent foreground HOME relaunch on stale package metadata during self-update, and should be tested before resuming self-update publishing.

A fallback artifact exists at `rose-agent/recovery/pixel-voice-v9-recovery-v12.apk`: exact known-good v9 code/resources, only manifest versionCode bumped to 12, re-signed with the existing key. It was **not installed or published**. Current source/installed feature version is 12. The recovery APK ALSO uses code12 but name0.9; do not confuse it with the feature APK (name0.12). Do not accidentally publish the recovery file as the next feature release.

Version 12 disables framework accessibility node caching for fresh on-demand screen reads. Initial version11 testing found stale text after clicks because no content events were subscribed; this was corrected and the full Calculator result test passed afterward. Version12 was installed by USB while Settings was foreground; no new framework crash occurred.

## Staged reliability update (version 13, not deployed yet)
The 08:37 spoken 2×3 test selected node16 (label6) rather than node20 (label3). The actual sequence was 2,×,6,=, producing12; the agent then cleared and retried correctly with3. This was a model target-selection error, not duplicate execution. The initial wrong selection used a complete snapshot. Two later retry observations were truncated under a 1500-token output limit.

Staged v13 adds `tap_label SNAPSHOT EXACT_LABEL`: native exact unique match against visible enabled clickable text/description, with the same screen freshness guard. No fuzzy or app-specific selection. Successful semantic taps return a fresh observation after one UI turn; the action result is stored before observation for replay safety. `control_next.py` and `README.next.md` are candidates; copy them to the active names during coordinated activation. `observations.py` renders compact model observations (example Calculator snapshot reduced from6966 to1462 characters without changing IDs/labels). Server source instructions/schema and Android sources are staged. APK builds, queue/schema tests, and three observation tests pass. No v13 install, publication, service restart, or native semantic integration test has happened yet.

Version13 activated via USB with Settings foreground (no OTA); CLI/docs promoted and backend restarted while idle and mic muted. open_app verified Calculator foreground and returned retained result6 without clearing it. Semantic test rejected nonexistent label, then exact labels AC/2/×/3/= each returned fresh expected display, ending6. Returned Home and verified Rose Agent. No new crash logged. Ready for the user spoken retest.
