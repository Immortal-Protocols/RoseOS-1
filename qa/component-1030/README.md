# QA-COMPONENT-1030-001 — self-update and launcher acceptance matrix (advisory)

QA-only. `test_component_paths.py` makes read-only assertions on the real changed files:

    QA_SRC=/path/to/RoseOS-1 python3 qa/component-1030/test_component_paths.py

`GAP` rows are expected to fail on main `6b30ede` and are reported as findings. `PENDING` rows wait on a known
handoff. Implementer tests run unchanged: `os/tests/test_rose_agent_self_update.py`, `DirectUpdateTest`,
`SelfUpdateTest`, `UpdateRecoveryTest` and `RoseReleasesTest` through `scripts/test-local.sh`.

Evidence levels stay separate: **S** is source or fixture, **E** is emulator, **P** is phone. In this session only S
is executable. There is no `/dev/kvm`, emulator or `adb`. Phone, GCP and SSH are out of scope.

## #202 self-update (`dev.pixel.voice`, app-created PackageInstaller session)

| # | Case | Expected | S | E/P procedure (prepared, not run) |
|---|------|----------|---|-----------------------------------|
| 1 | Same signer, newer version, real `Delivery.installSelf` session, with `base.apk` and a matching `base.apk.idsig` | Installs; versionCode rises; data and pairing kept | Platform helper allows it (Core test). **App path cannot pass: it stages no idsig (GAP)** | Install N, then self-update to N+1 from the app UI. Check `dumpsys package dev.pixel.voice` versionCode, signer and `codePath` under `/data/app`, and that the app data dir is intact |
| 2 | Same as 1 with no idsig | Rejected: `APK doesn't have fs-verity` (InstallPackageHelper) | Not testable from the pinned PackageInstallerSession alone; message from the #202 correction | Commit without idsig; expect INSTALL_FAILED_INTERNAL_ERROR with the fs-verity message |
| 3 | Mismatched idsig (other APK's v4) | Rejected during v4 verification | Order checked: fs-verity setup happens before v4 parse | Stage another build's idsig; expect a parse or signature failure; installed version unchanged |
| 4 | Other installer UID, including a spoofed initiating name | Blocked: "System app updates from unknown sources are blocked" | Core test | Install from a second app with the same APK and idsig; expect blocked |
| 5 | Other system target, different signer, same or older version, inherit, staged, APEX | Blocked | Core test, plus the app session shape checked here | — |
| 6 | Shell `adb install` | Not acceptance (issue correction) | — | Must not be used as evidence |
| 7 | Release pipeline publishes `dev.pixel.voice-N.apk.idsig` and DirectUpdate binds it (SHA-256 and size) | Present and verified | **GAP:** `host-app-release.py` and DirectUpdate have no idsig | Check the release assets, then repeat 1 |

## Launcher channel, recovery and data

| # | Case | Expected | S |
|---|------|----------|---|
| L1 | Alpha/Prod unavailable | No fallback request | DirectUpdateTest (unavailable receipt or schema cases) |
| L2 | Persisted channel: fresh, explicit, corrupt | Beta, kept as chosen, repaired | UpdateRecoveryTest |
| L3 | Corrupt or mismatched download | Refused before session commit; installed Home works | `PublicApkDownload` SHA-256 and size check; DirectUpdateTest |
| L4 | Interrupted download or session, user rejection, failed commit | Abandon own session only; installed version reported | UpdateRecoveryTest (pure). **PENDING: not wired into Delivery/InstallReceiver** |
| L5 | Process restart mid-update | Stale record classified as interrupted | UpdateRecoveryTest (pure); wiring PENDING |
| L6 | Post-activation | Installed versionCode proves success; recovery wording forward-only, no rollback claim | UpdateRecoveryTest |
| L7 | App, session, pairing and channel state survive | Preserved across the update | E/P only: compare the data dir and pairing before and after; no uninstall or data clear |
