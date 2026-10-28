# frankel-2026102701 public distribution reference

Inputs for `.github/workflows/release-verification.yml`. They hold public identities and hashes
only, never keys or private configuration.

- `manifest.json` is the pre-publication candidate manifest in `scripts/release-manifest.py`
  format. Its SHA-256 is `e4d8c28545e510ef85c6a48afaa100b319ff720fea1900dca413bdc0b12b3070`. That
  is the same digest Dev Infra obtained by running `create` over the retained original files
  (INFRA-ENG-M004).
  - Asset names, sizes and hashes come from INFRA-HANDOFF-044.
  - The resolved-manifest hash comes from INFRA-BUILD-RUNNING-011.
  - The source revision `34fa48f` is the candidate baseline that Dev Infra recorded.
- `expected-identities.json` holds the public pins:
  - the app signer;
  - the OTA release certificate DER SHA-256;
  - the AVB public key blob SHA-256.

  App versions and signers are claimed metadata; the public assets don't let anyone recheck them.
  The workflow independently derives the OTA certificate and AVB pins from the published
  `releasekey.x509.pem` and `avb_pkmd.bin`. That is a public-identity match, not a verification of
  the OTA signature.

The release's own `verification.json` is an immutable pre-publication receipt. Its
`published=false` is expected and does not affect byte-identical delivery.
