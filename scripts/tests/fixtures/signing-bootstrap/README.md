Unmodified copies of two upstream files at the revisions pinned in `os/pinned-manifest.xml`, read through supported
read-only git access for OCT29-INFRA-S010 (owner-approved scopes). They are test fixtures for
`scripts/tests/test_signing_bootstrap.py`, which checks them against the source identities recorded in
`os/signing-bootstrap/profiles.json`. They are never executed and hold no key material.

| File | Upstream (repository, path, revision) | git blob | sha256 | License |
| --- | --- | --- | --- | --- |
| `make_key` | GrapheneOS/platform_development `tools/make_key` `4089e8b6cfe6cc088006fcc7ad34ba1132356a1b` | `54bf82dd5ce666c6575866ea08fe67d232d3b5ad` | `88073d52fd10ab7931c64c14be7860c8795350dfcb101460a315408c148d8e6d` | Apache-2.0 (header retained) |
| `decrypt-keys` | GrapheneOS/script `decrypt-keys` `09d651841b55e42b9cee7392f0ce29ebda8c8fb5` | `b621d1e02c1091089bd8e0d09c227d8f8e5006b5` | `c0109995fe2c64d0f0010bcdecd2db3dd285a17e330aee101077eb6b7d347e4c` | MIT, Copyright © 2015-2026 GrapheneOS |

Read but not copied (identities recorded in the profile): GrapheneOS/platform_build
`tools/releasetools/sign_target_files_apks.py` at `b0f697df8cc9519408ca0a404e918cd51bde0735` (blob
`b3bb318917f5e8744c4d5a505112373e58e141a5`, sha256 `066729a5083916d43e23616dc8b7b1b363cc18d2a2c0a661745b3209d1fe014d`)
and AOSP platform/external/avb `avbtool.py` at `ba2dec4b035b0a3b61c5f8f8a74d86bcd450b1ee` (blob
`c0f3c7c62c4121edca58727ae60e17818d46155c`, sha256 `1aa6f2b462aa67aa3fdf488bee83ab517de8ad9d8228c7b45c35267ab2cf6781`).
