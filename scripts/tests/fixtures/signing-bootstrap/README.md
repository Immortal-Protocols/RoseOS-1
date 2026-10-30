Unmodified copies of two upstream files at the revisions pinned in `os/pinned-manifest.xml`, read through supported
read-only git access for OCT29-INFRA-S010 (owner-approved scopes; the three script helpers under the 00:05 approval). They are test fixtures for
`scripts/tests/test_signing_bootstrap.py`, which checks them against the source identities recorded in
`os/signing-bootstrap/profiles.json`. They are never executed and hold no key material.

| File | Upstream (repository, path, revision) | git blob | sha256 | License |
| --- | --- | --- | --- | --- |
| `make_key` | GrapheneOS/platform_development `tools/make_key` `4089e8b6cfe6cc088006fcc7ad34ba1132356a1b` | `54bf82dd5ce666c6575866ea08fe67d232d3b5ad` | `88073d52fd10ab7931c64c14be7860c8795350dfcb101460a315408c148d8e6d` | Apache-2.0 (header retained) |
| `decrypt-keys` | GrapheneOS/script `decrypt-keys` `09d651841b55e42b9cee7392f0ce29ebda8c8fb5` | `b621d1e02c1091089bd8e0d09c227d8f8e5006b5` | `c0109995fe2c64d0f0010bcdecd2db3dd285a17e330aee101077eb6b7d347e4c` | MIT, Copyright © 2015-2026 GrapheneOS |
| `generate-keys` | GrapheneOS/script `generate-keys` (same revision) | `5c38a53eeda4eb16c55bd3a3ad776a3cb1d6349e` | `f47098e935b3418f267c2f06f41105d01fef57ed74e872a46e888d63cf89f588` | MIT, as above |
| `encrypt-keys` | GrapheneOS/script `encrypt-keys` (same revision) | `90ab9b0cda1fbbd81e9fbbd48d352ab2e3edc3ad` | `70bc26f545bbbd2509d3fd8dc11e7c21054c170116781e87804003153222e861` | MIT, as above |
| `common.sh` | GrapheneOS/script `common.sh` (same revision) | `dd0e6e66ae765e4888e1baaaed8bb58b0b678b68` | `d6ff8cd70b065af7e129a95d026ff5ddd8564a6e12fa340cd02fd93deba76262` | MIT, as above |

Read but not copied (identities recorded in the profile): GrapheneOS/platform_build
`tools/releasetools/sign_target_files_apks.py` at `b0f697df8cc9519408ca0a404e918cd51bde0735` (blob
`b3bb318917f5e8744c4d5a505112373e58e141a5`, sha256 `066729a5083916d43e23616dc8b7b1b363cc18d2a2c0a661745b3209d1fe014d`)
and AOSP platform/external/avb `avbtool.py` at `ba2dec4b035b0a3b61c5f8f8a74d86bcd450b1ee` (blob
`c0f3c7c62c4121edca58727ae60e17818d46155c`, sha256 `1aa6f2b462aa67aa3fdf488bee83ab517de8ad9d8228c7b45c35267ab2cf6781`).
