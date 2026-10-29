# Candidate expectations

Each file is one candidate's exact identities: source SHA, target, app bytes,
versions and signer. `scripts/rose-verify.py --expect FILE` checks a build
against it, and only against it. The schema is in the script's docstring and in
`docs/VERIFY.md`. The loader rejects unknown keys, nulls and malformed values.

## Files

| File | Candidate | Basis |
| --- | --- | --- |
| `beta-frankel-2026102804.json` | Published beta frankel-2026102804, source `ae9cfd4d`, apps 302/4/28 | Lead's INFRA-ALPHA-082 binding, plus the signer and AVB key from `os/release-pipeline/frankel.json`. Verified against the published release (`../frankel-2026102804.md`) |
| `alpha-40719075730.json` | Historical, not for any existing image. Alpha Actions 40719075730 (failed at `file_list_diff`; no image): recipe `2921d88d`, product base `ae9cfd4d` + OS delta `f21bf683`, `aosp_cf_x86_64_only_phone-cur-userdebug`, apps 302/4/28 | `os/alpha/cuttlefish-product-contract.json` and `os/release-pipeline/cuttlefish-alpha.json`, unchanged |
| `app-rose-agent-304.json` | App-only Rose Agent 304: `pixel-ota` prerelease `rose-agent-304`, Actions 40733909409, product `a3d839e6`, recipe `31e58006`; no device target | Infra's receipt (1,385,629 bytes, `d8f69efc…26966`, signer `650d47f9…0bfb`). The verifier has not downloaded the bytes. Used for INFRA-RUNTIME-100 (`../rose-agent-304-infra-runtime-100.md`) |

`scripts/tests/test_rose_verify.py` fails if either file diverges from those
checked-in pins.

The alpha file binds apps and target only. Image hashes, guest fingerprint and
boot state come from Infra's completed image receipt. They are recorded with the
runtime receipt when that receipt is supplied.

The verified alpha image (compile 40765631053, verification run 40777816699)
has no file here. Its expectation is generated from that run's receipt with
`scripts/alpha-runtime.py expectation` (`docs/VERIFY.md` section C), so it can
never drift from the bytes the image gate measured.

## Adding a new candidate

Applies to the next beta, an app-only 304, or any other new build.

1. Wait for Infra's actual receipt of the built and signed artifact. It gives
   the source SHA, build number, target, each APK's package, versionCode,
   SHA-256 and image path, and the full signer.
2. Copy those values into a new file. Never take them from source, from a
   planned version, or from another candidate's file.
3. Name the receipt and its SHA-256 in `basis`.
4. Never edit an existing file to make a different build pass. A changed build
   is a new candidate.
5. Run `python3 scripts/rose-verify.py expected --expect FILE` to validate it.
   Then use the file with `release`, `apk` or `runtime`.

## r2 re-run on beta 2804

Kind: artifact inspection. Run on 2026-10-28 with method r2 against the same
OTA bytes as the r1 record. Those bytes are `d49df810…`, already verified; there
was no new download.

```sh
python3 -I scripts/rose-verify.py release DIR \
  --expect qa/verification/expectations/beta-frankel-2026102804.json \
  --extract-apps /scratch/r2
```

26 of 26 checks passed. Only product and system_ext were extracted, so that is
r1's 21 checks for those two partitions plus five new ones:
- candidate build `2026102804`;
- OTA device equals `target.device` (frankel);
- each of the three apps is listed in the expectation.

No runtime result.
