# Mustang signing and private candidate delivery

This implements [Nathan’s reduced-check plan](https://app.notion.com/p/40d7c646c81b81198e60cb76ca2ea603)
for **Pixel 10 Pro XL / mustang** on the existing original builder. Source,
bootstrap, signing, packaging, downloaded checksums and physical acceptance are
separate stages; the evidence below does not claim an installer exists.

## Actual state

The one approved bootstrap
[41719073037](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41719073037)
used source `82a51373e1473df707731a78dc68786cf7245fed`, passed fresh tool proof,
and reported `complete` with no reasons at 2026-11-03T21:10:21Z. Both Mustang-only
containers committed numeric **version 1**. Its public receipt records
`recoveryNeeded=false`, `workspaceRemoved=true` and no payload readback.
Cleanup succeeded. Do not repeat this bootstrap.

The public identity hashes are OTA certificate
`93cc757e7f4e4e5c4ef7726fbeec9acc77b1f97d471c1cd0d62753cf94e471a8`
and AVB public key
`608b39aac976984e45ddbc39fc9ebc916f066656d8d4316395c9c408e06dbf2b`.
The receipt is preserved in `os/signing-bootstrap/mustang-bootstrap-receipt.json`.
Its private [artifact 13271233281](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41719073037/artifacts/13271233281)
is 2,193 bytes, SHA-256
`c1fcd6007a41ed69f3b264476f1a952b2559ed2e15dfb8a7e27a2db9a8610549`,
expiring 2026-11-10T21:10:21Z. It was downloaded through the GitHub connector;
no secret payload was downloaded to this worker. The common tool-proof format
retains its historical `fold-rango-v1` label; this proof was generated in the
Mustang run, not consumed from a Rango artifact.

The full `os/flavors/mustang.json` contract now references this public receipt
for its AVB and OTA identity metadata and records the committed container
versions. Its build/factory/phone readiness flags remain false. This source
binding is not the sprint-omitted intended-public-identity comparison and does
not establish a signing result or installer acceptance.

As of 2026-11-03T22:23:38Z, fresh unsigned run
[41720536968](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41720536968)
uses corrected source `2b521e732fa1537e54ff0a77880810ab1f34cf99`. Its hosted
configuration guard and controller succeeded; the combined preparation/build
step is active since 21:19:17Z, with no completed compile receipt.
Compilation, delivery signing, packaging and export are unproven. Earlier
replacement `41719106957` was cancelled before compilation after source review
found a deterministic rendering error. Its cleanup observed `TERMINATED` at
21:17:18Z before the fresh run was dispatched. See
[host-config correction](MUSTANG-HOST-CONFIG-CORRECTION.md) and the
[runtime handoff](MUSTANG-RUNTIME.md).

## Approved resources and access

The configured `dev-infra@protocols-465723.iam.gserviceaccount.com` created the
two distinct containers in `protocols-465723` at 20:21:41Z and 20:21:42Z:

- `PIXEL_OS_SIGNING_KEYS_MUSTANG_V1`
- `PIXEL_OS_SIGNING_PASSPHRASE_MUSTANG_V1`

Creation responses and describes confirmed both resources. Creator versions-list,
audit-log and provider inspection denials were respected. The worker's first
IAM write failed without applying a binding because it lacks `setIamPolicy`.
Nathan applied the explicitly approved four bindings as an existing IAM admin;
the worker confirmed their public policy metadata. Each container grants the
existing `projects/protocols-465723/roles/roseosModelsBootstrapV1` role
(`secrets.get`, `versions.list`, `versions.add`) and
`roles/secretmanager.secretAccessor` to
`roseos-build-signer@protocols-465723.iam.gserviceaccount.com`, expiring
**2026-11-04T00:50:06Z**. No project-wide grant or additional identity was added.
See `mustang-setup-evidence.json` and `mustang-access-binding.json` under
`os/signing-bootstrap/`.

The separately approved bootstrap checked both containers empty as the signer,
generated/imported once under its write-once journal and emitted its public
receipt. Signing must pin its two numeric versions. No further generation,
import retry or access-window extension is authorized or needed. The operator
transfer alone did not authorize these grants or credentials.

## Established workflow and source

PR #492 merged the explicitly approved route correction as
`216184cba518c3d2f5e86aedfdddfa3b1fa27834`. The existing `os-release.yml`, original
builder, controller identity, non-cancelling FIFO, shared lock and ownership-aware
STOP are reused. No WIF/provider/service-account binding was changed. The three
Mustang modes hardcode Mustang and never enter the Frankel public publisher:

- `mustang-build`: fresh preparation, allocation, integration and unsigned compile.
- `mustang-bootstrap`: one journaled `pro-mustang-v1` bootstrap; already complete.
- `mustang-delivery`: bind successful main producer runs using
  `mustang_compile_run` and `mustang_bootstrap_run`, then sign, package, export
  privately and verify the delivered download's checksums.

`mustang-sign-package.py sign` consumes completed compile and real bootstrap
receipts bound by hosted GitHub metadata. It retains integration/compile source,
actual device/build/time, produced hashes, numeric Mustang versions and lock.
The host-sign engine uses scoped structural key discovery in place of the omitted
intended-public-identity comparison. Existing callers retain their defaults.
PR #496 merged as `5d3ca2f21320610ba952507b1317ee5f6f13df62` nests the decrypted
working directory inside managed RAM cleanup for Mustang, as detailed in
[signer cleanup](MUSTANG-SIGNER-CLEANUP.md). Signing is bounded; SIGTERM unwinds
private cleanup. Signed target-files/OTA sizes and hashes do not establish
independent cryptographic verification.

Packaging runs without signer credentials and reuses the existing factory-only
script derivation, extraction protections and bounded generator. Public AVB
bytes from signing are supplied without the omitted identity comparison. The
expected Mustang installer is identified by basic optimized-factory format,
sized and hashed. Partial nonsecret packaging work is preserved on failure;
temporary packaging work is removed on success. Installer, notes, receipt and
checksums are private Actions artifacts retained seven days. Hosted download
verification records actual URL, API expiry, sizes and checksum results.

## Earlier failures and corrections

Standalone run [41711304208](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41711304208)
failed controller describe before any VM start. Authentication-only diagnostic
[41713153197](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41713153197)
reported `attributeConditionDenied` for the new workflow context with zero compute
requests. That context was not retried; Nathan approved PR #492 and the existing
route. The two new workflow entry files were removed; Git history remains.

Established-route build [41714573501](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41714573501)
passed its controller, then failed at route admission before integration/compile.
Bootstrap workflow [41717550485](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41717550485)
failed preflight before proof, signer login or key generation because its job
lacked the existing provider/signer variables. PR #495 fixed both configuration
errors as `82a51373e1473df707731a78dc68786cf7245fed`. Both cleanup jobs succeeded;
[admission evidence](MUSTANG-ADMISSION-FIX.md) records exact errors and receipts.
No failed attempt was adopted or resumed.

Focused, existing-device regression, required CI/full-suite tests and all other
checks listed in `os/build-only/mustang-sprint.json` remain omitted as instructed.
No Rango/Blazer image, key or artifact is consumed. No public publication or phone
action is performed. Any delivery must retain the label:
**build-and-package candidate; omitted verification remains undone;
physical-device acceptance unproven**.
