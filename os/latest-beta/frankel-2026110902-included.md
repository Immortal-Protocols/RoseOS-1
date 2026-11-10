# Frankel 2026110902: included changes

- **Build:** `2026110902`, tag `frankel-2026110902`, channel beta. This was the normal 04:00 Pacific daily:
  - the scheduled dispatcher (run 42615398256, 12:00Z cron, started 12:14:50Z) dispatched os-release run 42615421758;
  - that run started at 12:15:00Z, attempt 1.
- **Source:** RoseOS-1 main `557a2dbbcec6d05a41c132b56715ba649f86e3ff`.
- **Integrated candidate:** `79703f53ced0ce7c5e872c0256bff69b64c4a7bb`.
  - It is the source plus the version-only delta, derived by `scripts/release-allocate.py derive` from this build's allocation.
  - The derivation was re-run locally and gives the same SHA.
  - It is pushed as branch `claude/clever-ptolemy-fmr06h-candidate-frankel-2026110902`.
- **Previous Beta:** `2026110901` (source `9882edf0a68b640c24e6c0d0de119e663c6ac573`).
- **Allocation:** `cae07c70937566619e567356cfc86b43706f9308e9b20905a7cda019b473b4b9`; delta `71b3fc649083c4d16577acd8a5233be4c64e2b4cf987bca862c9945b57bf4122`; BUILD_DATETIME 1794226560.
  - The record `frankel-2026110902-allocation.json` is byte-identical to the run's artifact `os-release-allocation-42615421758`.
  - That artifact is 13670914599, digest `sha256:2ae8819b961dc4b3817926a8836ab0b9d554e3cc70c0972758f2f715f871ae1a`.
  - The `allocation.json` sha256 is `f035564dc0af0c4018a51c642e5a2642757a6cebce3a1e9833867e3b302cdcb8`.
- **Result:** run 42615421758 succeeded on attempt 1. It was signed, verified, published and promoted to Beta Latest at 12:40:06Z.

## What this build adds

No Rose app source changed since 2026110901. All three apps keep their versionCodes:
- `dev.pixel.voice` 369;
- `dev.pixel.calculator.agent` 6;
- `dev.pixel.backgroundcontrol` 41.

**Release paths:** the only commit touching a Frankel release path is #711, and only in the lane map (`os/release-pipeline/lanes/capacity.json`). No commit in this range touches:
- `os/patches`, `os/overlays` or `os/compile-contracts`;
- `.github/workflows/os-release.yml`;
- `scripts/release-pipeline.py`, `scripts/host-integrate.py` or `scripts/host-sign.py`.

**Other commits:** Mustang dedicated-route work (#708, #710, #712 to #721) and Release's two 2026110901 records (#707, #709).

**Timestamps (UTC, 2026-11-09):**

| Event | Time |
|---|---|
| Dispatcher started | 12:14:50 |
| os-release dispatched | 12:15:00 |
| Integrate and build | 12:16:03 to 12:21:28 |
| Sign | 12:21:38 to 12:35:30 |
| Verify the signed candidate | 12:35:30 to 12:37:01 |
| Transfer | 12:37:07 to 12:38:08 |
| Published and promoted | 12:40:06 |
| Builder stopped (cleanup `stop-builder`) | 12:40:20 to 12:40:47 |

**Publication evidence** (run 42615421758 logs):
- publisher `"draft": "verified", "promoted": true, "simulated": false`, using the pixel-ota publisher App, whose token was revoked after the job;
- cleanup `stop-builder` succeeded.

## Main commits since the previous Beta (15, first parent, newest first)

- `557a2dbbcec6` Verify declared pinned ANGLE CRLF checkout representations (#721)
- `9e025faffdd8` Identify pinned source mismatches at first measurement (#720)
- `3312b2d03d0c` Bind selected source-sync manifest to exact pinned bytes (#719)
- `84577ff033bf` Report bounded unclassified Mustang source paths without reading payloads (#718)
- `1fc5e452bfd0` Align Mustang observation and source copy for an absent optional cache (#717)
- `2cf8ac0ca6e8` Size the bounded Mustang source entry scan for the real tree (#716)
- `8377725ded58` Verify pinned or empty gitlink boundaries in Mustang source (#715)
- `7eac541e63c9` Preserve Mustang clock runtime bindings and expose bootstrap refusals (#714)
- `1039b30c7e4e` Use native HTTP transport for Mustang app artifacts (#713)
- `0af311990076` Provide pinned artifact CLI before Mustang native preflight (#712)
- `d64ca301031e` Bind Mustang full delivery to verified signer scope and bounded outputs (#711)
- `ee891745960a` Bind fresh Mustang apps and qualified runtime inputs before signer admission (#710)
- `303f69823b0c` Merge #709: record the app-inputs export of Frankel 2026110901 for Mustang (export run 42561803703)
- `00f86cdd3d87` Use local UTC-hour Mustang reservations with legacy-disjoint suffix (#708)
- `fb7a2995f335` Merge #707: record Frankel 2026110901 (early build for the Mustang app export) allocation and included changes

**Evidence limits:**
- The release workflow built, signed and verified this build (image, signature, version and download checks).
- The publication evidence is the publisher's own receipt; pixel-ota is not read directly from this session.
- Nothing here claims phone runtime acceptance.
