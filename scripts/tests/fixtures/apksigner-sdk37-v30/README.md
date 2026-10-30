`observed-line.txt` is the single stdout line reported by the Mac operator on 2026-10-30 (OS-RANGO-SIGNER-004, relayed
by Chief; operator-reported, not re-run here). It came from
`apksigner verify --print-certs --min-sdk-version 37 --max-sdk-version 37 dev.pixel.backgroundcontrol.apk`.

- **Result:** exit status 0, empty stderr.
- **APK:** `dev.pixel.backgroundcontrol`, versionCode 28, sha256 `566c84574c99ed04d9e206dbc74e499b7424f44e8d895e8fda03069def6a62cc`.
- **Certificate:** the digest is the public Rose app-signing certificate, the same as `os/build-only/rango-reconcile.json`
  `inputs.appSignerSha256`. It is not a secret.
- **Limits:** this is the observed line only, not a complete stdout transcript, and nothing else here is invented.
  `background_apks.py` rejected this line before OS-RANGO-SIGNER-004.
