# Frankel 2026111001: included changes

- **Build:** `2026111001`, tag `frankel-2026111001`, channel beta. This was the normal 04:00 Pacific daily:
  - the scheduled dispatcher (run 42813866601, 12:00Z cron, started 12:13:08Z) dispatched os-release run 42813894214;
  - that run started at 12:13:20Z, attempt 1.
- **Source:** RoseOS-1 main `6bab12cdf9a6ac2fa2267966447b9820297650ec`.
- **Integrated candidate:** `ace876af4866d579973c2475e59688e2103c02af`.
  - It is the source plus the version-only delta, derived by `scripts/release-allocate.py derive` from this build's allocation.
  - The derivation was re-run locally and gives the same SHA.
  - It is pushed as branch `claude/clever-ptolemy-fmr06h-candidate-frankel-2026111001`.
- **Previous Beta:** `2026110902` (source `557a2dbbcec6d05a41c132b56715ba649f86e3ff`).
- **Allocation:** `51f142b1e139689ca2d946fe4d4acbaa4bd2f4974c218704bc4de03d38a29b9b`; delta `25cce814428eb9638ad1ae7940d10cbd6020397d49de16970d48ab846cb14d81`; BUILD_DATETIME 1794312863.
  - The record `frankel-2026111001-allocation.json` is byte-identical to the run's artifact `os-release-allocation-42813894214`.
  - That artifact is 13764345147 (a 1198-byte zip), digest `sha256:cf238c0ca7343fdf0c43881d59ca6eb634731fbc3fa44c4d3c877809e437b13d`.
  - The `allocation.json` sha256 is `32c59f8bd4a73d0ed8c2683d78ecb7a34308896a8bd0b7122c27c3d0d916e6a8`.
- **Result:** run 42813894214 succeeded on attempt 1. It was signed, verified, published and promoted to Beta Latest at 12:48:46Z.
  - Its three signed apps are exported in run 42851073284 and recorded in [`os/app-inputs/frankel-2026111001.json`](../app-inputs/frankel-2026111001.json).

## What this build adds

**Rose Agent:** `dev.pixel.voice` moves from 369 to 370 (versionName `370-os-beta-2026111001`). Four commits change its declared sources under `workspace/rose-agent`:
- #729 (1O): columns, wallpaper and font visibly change on a phone.
- #732 (1B): honest customization outcomes.
  - Its declared-source change is a test under `workspace/rose-agent/customization`.
  - Its runtime change is in the backend (`prototypes/voice-phone-demo`).
- #731 (1C): the V3 return control at the bottom left of Rose's content, hidden until the exact-task seam lands.
- #736 (1C, App Core): the return control's exact-task source over the SystemUI tray link.

The other two apps keep their versionCodes, because no commit touches their declared sources:
- `dev.pixel.calculator.agent` 6;
- `dev.pixel.backgroundcontrol` 41.

**OS:** two commits change Frankel release paths, and this build compiled both.
- #733 (1A) fixes the crash on every tray raise and the keyboard strip and key hide, and keeps the tray mic visible. It touches:
  - `os/overlays`: Launcher3 `RoseEdgeWindow` and RoseKeyboard `RoseKeyboardView`;
  - `os/compile-contracts/launcher3-quickstep-lib.json`;
  - `os/staged/rose-edge-resize`;
  - the Launcher3 compile and bundle-report scripts.
- #734 (1C) adds the exact-task return seam: WMShell records and restores the previous app, and the tray carries it. It touches:
  - `os/patches/rose-edge-wmshell.patch`;
  - `os/overlays`: WMShell `AgentTrayLink` and `AgentTrayPrototype`;
  - `os/compile-contracts`: the WMShell and Launcher3 quickstep contracts;
  - the contract, compile and bundle-report scripts.

In builder job 129822356286:
- WindowManager-Shell, Launcher3QuickStepLib, RoseKeyboard and SystemUI were recompiled;
- the log has no compile errors (Launcher3 quickstep javac gave 14 `[removal]` warnings);
- "build completed successfully" was logged at 12:27:50Z.

**Not touched:** no commit in this range touches:
- `.github/workflows/os-release.yml` or `.github/workflows/daily-release.yml`;
- `scripts/release-pipeline.py`, `scripts/host-integrate.py`, `scripts/host-sign.py` or `scripts/release-allocate.py`;
- `os/pinned-manifest.xml`, `os/release-pipeline` or `os/latest-beta`.

**Other commits:**
- #735 (1D) names the assistant and describes its voice in conversation.
  - It touches only the backend (`prototypes/voice-phone-demo`), `docs/CODEX-VOICE.md` and `.github/workflows/app-tests.yml`.
  - It is in the source, but not in the OS image or any APK. Deploying the backend is not part of this release.
- #737, #722 to #728 and #730 are Mustang dedicated-route and hosted-signing work: `scripts/mustang_*`, `os/build-only`, `.github/workflows/mustang-ota-dedicated.yml` and docs.

**Timestamps (UTC, 2026-11-10):**

| Event | Time |
|---|---|
| Dispatcher started | 12:13:08 |
| os-release dispatched | 12:13:20 |
| Builder started (controller `start-builder`) | 12:13:44 to 12:14:10 |
| Allocation | 12:14:23 to 12:14:25 |
| Integrate and build | 12:14:26 to 12:28:04 |
| Verify the unsigned bytes | 12:28:04 to 12:28:13 |
| Sign | 12:28:13 to 12:41:39 |
| Verify the signed candidate (`ota-verify`) | 12:41:40 to 12:43:10 |
| Transfer and artifact upload | 12:43:10 to 12:44:05 |
| Published and promoted | 12:48:46 |
| Builder stopped (cleanup `stop-builder`) | 12:49:01 to 12:49:38 |

**Build evidence** (builder job 129822356286):
- unsigned target-files sha256 `28943ffe6bece1d1a7d3aedb3b833d60a2efcdd05cbf4d62ee25bc621426e8fd`;
- signed target-files sha256 `3a3e1565306f63e8e0ca302c645ab15a18bac7f42fc0c0db90c762732b6780f6`;
- `ota-verify` verdict `PASS`, simulated false;
- the transfer staged 7 assets as artifact `os-release-frankel-2026111001`:
  - artifact 13766102652, 1679884042 bytes;
  - zip sha256 `3249754acbd79005b3a01cb0e43c568564456db43a830954089e1ca84785685d`;
  - 3-day retention.

**Publication evidence** (run 42813894214 logs):
- The publisher job 129836885387 checked the downloaded allocation artifact's digest before it published.
- The publisher reported `"draft": "verified", "promoted": true, "simulated": false` at 12:48:46Z. It used the pixel-ota publisher App, whose token was revoked after the job.
- Cleanup job 129839160987 `stop-builder` succeeded.
- A read-only Compute read afterwards showed `immortal-os-builder` TERMINATED, with lastStopTimestamp 2026-11-10T12:49:27.849Z.

## App-inputs export (for Mustang)

Nathan approved one export of this build's signed apps (relayed by Chief; his reply at 16:01:06Z). It was one `os-release.yml` dispatch on main `b3764041e335071f3bc07605bfe791563a9924ab` with `{"mode": "app-inputs-export", "build_number": "2026111001"}`.

- **Run:** 42851073284, attempt 1. Dispatched 16:05:27Z, created 16:05:32Z, completed 16:07:16Z, success.
- **Builder:**
  - the controller started `immortal-os-builder` (16:05:57 to 16:06:13Z);
  - the export job ran on it from 16:06:25 to 16:06:35Z and copied the original attempt's signed apps unchanged (16:06:29 to 16:06:30Z);
  - the owned cleanup stopped it (16:06:47 to 16:07:13Z, `"observed": "TERMINATED"`);
  - Compute shows lastStartTimestamp 16:06:13.010Z and lastStopTimestamp 16:07:06.701Z.
- **Artifact:** `app-inputs-frankel-2026111001-42851073284-1`, id 13781175876, 1875279 bytes.
  - Digest `sha256:4cb8c4ff5ed801210f687071ea5e48442ee11712e7e4f3877495456ee07376ef`; expires 2026-11-24T16:06:30Z.
  - It holds 12 files: the nine signed-app files, `background-policy.json`, the receipt `app-inputs-export.json` (sha256 `7f601b8fc6719df85f334a235901f57924bdd3a1fd83b9a87617588b7b7ca733`) and `SHA256SUMS`.
- **Record:** [`os/app-inputs/frankel-2026111001.json`](../app-inputs/frankel-2026111001.json), written by `scripts/app-inputs-verify.py record` from the artifact's own receipt and the run's metadata.
  - It binds the allocation record by its file-byte sha256 `32c59f8bd4a73d0ed8c2683d78ecb7a34308896a8bd0b7122c27c3d0d916e6a8`, not by the canonical `allocationSha256` (`51f142b1…`).
- **Admission:** `scripts/app-inputs-verify.py verify` with independent `aapt` and `apksigner` returned `ADMITTED` (`toolsRechecked: true`).
  - Versions: voice 370 (`370-os-beta-2026111001`), calculator agent 6, background control 41.
  - Signer `650d47f91b7f5af3c530fef53fe92018aa4521efba065bcb1d6fc9b699060bfb`; SDK 37.
  - Mustang's `app_source_drift` against main is empty for all three apps.

## Main commits since the previous Beta (16, first parent, newest first)

- `6bab12cdf9a6` Merge #735: 1D: name the assistant and describe its voice in conversation
- `df595a59a5e3` Merge #736: 1C (App Core): the return control's exact-task source over the SystemUI tray link
- `cb7f4b878378` Merge #734: 1C: exact-task return seam (WMShell records and restores the previous app; the tray carries it)
- `3697aaf26ce6` Merge #731: 1C the V3 return control at the bottom left of Rose's content (hidden until the exact-task seam lands)
- `01e86a96c1a4` Validate Android target payload links separately from executable archives (#737)
- `069a7c1f3f2a` Merge #733: 1A: fix the crash on every tray raise, the keyboard strip/key hide, and keep the tray mic visible
- `8676ae2ff7d6` Merge #732: 1B honest customization outcomes (applied only when Home drew it)
- `060f837aa11f` Merge #729: 1O columns, wallpaper and font visibly change on a phone
- `d21946133b17` Account for verified resident inputs in Mustang signer space (#730)
- `b27ffea4c214` Identify archive and member in Mustang ZIP refusals (#728)
- `1aa484287c75` Free unused image SDK space before Mustang hosted signing (#727)
- `c1985730bfc4` Integrate the exact retained Mustang compile with dedicated OTA delivery (#726)
- `f2b81b3c0246` Use the existing canonical lock for dedicated Mustang collection (#725)
- `4e6d35cd4ecc` Bind Mustang compile identity to the existing clock journal (#724)
- `8d6cb5434e25` Match declared Git text checkout semantics for pinned blobs (#723)
- `ce51e65d91b1` Evaluate pinned checkout attributes in isolated bounded metadata (#722)

**Evidence limits:**
- The release workflow built, signed and verified this build (image, signature, version and download checks).
- The publication evidence is the publisher's own receipt; pixel-ota is not read directly from this session.
- The app-inputs export re-checked and copied the original attempt's signed apps; it built, signed and published nothing.
- Nothing here claims phone runtime acceptance.
