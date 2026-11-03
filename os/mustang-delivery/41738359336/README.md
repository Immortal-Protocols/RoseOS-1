# Mustang delivery evidence — 41738359336

Actual delivery completed **2026-11-03T23:43:15Z** through the existing
[`os-release.yml` run](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41738359336).
Native source is `2b521e732fa1537e54ff0a77880810ab1f34cf99`, build `2026110302`;
delivery recipe is `7b5848c420d070d0219f0e99f05997ece245c748` (PR #501).

The four command receipts are the exact downloaded public JSON bytes, extracted
from allowlisted Actions receipt artifacts after their archive digests matched
the API metadata. They contain no private key, passphrase or access token.

| File | Evidence |
| --- | --- |
| [collection.json](collection.json) | Complete outputs, actual Mustang device/build/time, preserved earlier hashes; no Make rerun or original-receipt edit |
| [sign.json](sign.json) | Signing command exit 0, Mustang version 1 bindings, signed target-files and OTA sizes/hashes; verification fields remain false |
| [package.json](package.json) | Factory command exit 0, basic optimized-installer format, installer size/hash, temporary work removed |
| [delivery.json](delivery.json) | Private URL, actual expiry, separate hosted downloaded-file checksum results |
| [artifacts.json](artifacts.json) | Final receipt/candidate artifact IDs, archive digests, sizes and expiry from GitHub API |
| [builder-state.json](builder-state.json) | Independent compute describe at 23:44:22Z: original instance `685707771105568194` is `TERMINATED`; last stop 23:43:07Z |

The [private candidate](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41738359336/artifacts/13280698927)
expires **2026-11-10T23:42:12Z**. Its installer
`mustang-install-2026110302.zip` is **2,108,057,621 bytes**, SHA-256
`4eae7e7e54191f30168649e75f2cfefb10fdbff1bffbfc2655ec079a4a8cce5e`.

The original unsigned workflow `41720536968` remains failed because its old
collector looked for otatools at an obsolete path despite Make exiting 0. The
new collection receipt and all downstream receipts retain that failure and the
specific collection-correction identity. No compilation or bootstrap was repeated.

Focused/regression/required CI/full-suite tests and all other checks listed in
the receipts remain omitted as instructed. Downloaded output checksums establish
the delivered bytes only. Independent signing-result/identity, installer content,
partition/firmware, compiled app/policy and physical-device acceptance are not
established. Source readiness flags remain false. The required label is:

**build-and-package candidate; omitted verification remains undone;
physical-device acceptance unproven**.
