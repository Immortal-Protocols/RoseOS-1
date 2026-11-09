# Frankel 2026110901: included changes

- Build `2026110901`, tag `frankel-2026110901`, channel beta. os-release run 42559170713, attempt 1, was dispatched manually at 03:57:49Z with Nathan's approval (via Chief), ahead of the 04:00 PT daily, so its three signed apps can be exported for Mustang.
- Source: RoseOS-1 main `9882edf0a68b640c24e6c0d0de119e663c6ac573`.
- Integrated candidate: `4a5ac5af02b049556804ddb383bfb13e18d39244`. It is the source plus the version-only delta plus generated notes, derived by `scripts/release-allocate.py derive` from this build's allocation. The derivation was re-run locally and gives the same SHA. It is pushed as branch `claude/clever-ptolemy-fmr06h-candidate-frankel-2026110901`.
- Previous Beta: `2026110801` (source `577cb73915947642512dad2a63ee9082f41ce414`).
- Allocation: `78354d1a98aa5ad28cde4e2a4f38fdd4adb480eb1a72e390be1757010aa99613`; delta `f13b384eb87db2d3095e0a5c96a659fdadb5a44cc0bdead653bc79e18a80b20b`; BUILD_DATETIME 1794196729. The record `frankel-2026110901-allocation.json` is byte-identical to the run's artifact `os-release-allocation-42559170713` (artifact 13648896394, digest `sha256:fb83c933c1e1180b46406302199cfa963012ebdef4aa1a3121d357e93f18c523`; `allocation.json` sha256 `7f5897f4c644cee87ef5651e79f7fc799a588a4ac55f6590988b180348bbbe9b`).
- Publication, signing and verification evidence is added with this build's app-inputs export record, after the run completes.

## What this build adds

The Frankel OS inputs are unchanged since 2026110801. No commit in this range touches `os/patches`, `os/overlays`, `os/release-pipeline`, `.github/workflows/os-release.yml`, `scripts/release-pipeline.py`, `scripts/host-integrate.py` or `scripts/host-sign.py`.

The app change is #684. It retired `mac-screen-connection.json` from Rose Agent's public connection inputs, and changed `scripts/host-app-build.py`, `scripts/host-app-release.py`, the public-asset checks and Rose Agent's `build.sh` and `portable-build.py`. `scripts/host-app-build.py` is a source of all three bundled apps, so each gets a new versionCode:
- Rose Agent's build no longer stages the retired `mac-screen-connection.json` input.
- The calculator agent and background control are rebuilt from the same app code.

The other 19 commits are Mustang dedicated-route work. The shared files they touch (`scripts/blazer-build-only.py` in #699, `.github/actionlint.yaml` in #687, `scripts/test-local.sh`) are outside Frankel's latest-beta build, signing and publication path.

## Bundled apps

- `dev.pixel.voice` 369 (369-os-beta-2026110901): changed since the previous Beta (368)
- `dev.pixel.calculator.agent` 6 (1.3): changed since the previous Beta (5)
- `dev.pixel.backgroundcontrol` 41 (2.1.5-pip): changed since the previous Beta (40)

## Main commits since the previous Beta (21, first parent, oldest last)

- `9882edf0a68b` Integrate Mustang full-run source, hosted signing and OTA staging (#706)
- `670d95e93bb1` Merge pull request #704 from Immortal-Protocols/codex/mustang-bootstrap-collector
- `f55f977bb1d3` Merge pull request #703 from Immortal-Protocols/codex/mustang-bootstrap-proof
- `096dd59b2613` Merge pull request #702 from Immortal-Protocols/codex/mustang-tool-qualification
- `48c08db21ee6` Merge pull request #701 from Immortal-Protocols/codex/mustang-vendor-consumer
- `b80dd7ac93e9` Merge pull request #700 from Immortal-Protocols/codex/mustang-release-probe
- `8a9c6648b643` Merge pull request #699 from Immortal-Protocols/codex/mustang-existing-lock
- `a7956fa4f135` Merge pull request #698 from Immortal-Protocols/codex/mustang-otatools-observation
- `6a532d4ff5cc` Merge pull request #697 from Immortal-Protocols/codex/mustang-otatools-proof
- `55c085ade9d0` Merge pull request #696 from Immortal-Protocols/codex/mustang-tsc-metadata
- `5e82e061acc5` Merge pull request #695 from Immortal-Protocols/codex/mustang-source-loader
- `8fbcc1486318` Merge pull request #694 from Immortal-Protocols/codex/mustang-vendor-preparation
- `78d5ebdfd4f3` Merge pull request #693 from Immortal-Protocols/codex/mustang-soong-ipc
- `93a3c9574816` Merge pull request #692 from Immortal-Protocols/codex/mustang-dependency-observation
- `34c7fe96af6c` Merge pull request #691 from Immortal-Protocols/codex/mustang-factory-input
- `efa46b7ebf8c` Merge pull request #690 from Immortal-Protocols/codex/mustang-admin-window
- `b1dc69b271b6` Merge pull request #689 from Immortal-Protocols/codex/mustang-lock-preparation
- `437f6f8964eb` Merge pull request #688 from Immortal-Protocols/codex/mustang-preflight-receipts
- `27c72f08032d` Merge pull request #687 from Immortal-Protocols/codex/mustang-hostdeps-adapter
- `92fe8d8e231a` Merge #684: retire mac-screen-connection.json from the public connection inputs (Release + App)
- `967e3b0736f3` Merge #683: record Frankel 2026110801 (first scheduled daily, Beta Latest) allocation and included changes

Evidence limits: this record is written from the run's allocation and source history. It claims no publication, signature or phone runtime result.
