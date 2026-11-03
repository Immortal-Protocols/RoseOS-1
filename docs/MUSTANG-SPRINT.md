# Mustang candidate source route

This implements the source/preparation/integration/unsigned-compile portion of
[Nathan’s Mustang plan](https://app.notion.com/p/40d7c646c81b81198e60cb76ca2ea603).
The target is **Pixel 10 Pro XL / mustang**. It is separate from Pixel 10 Pro
(blazer), Pixel 10 Pro Fold (rango), and the excluded Pixel 11 Fold investigation.

The source baseline is `fffd4d00095f3a4edecfa2630f485b25e643fb33`.
The original checkout was clean at `297122a`; implementation uses an isolated
worktree. Open Blazer PRs #486 and #488 were inspected and remain untouched,
as does Rango signing PR #489 and the parked access draft. GitHub reported no
running or queued workflows at inspection. Builder occupancy and lock state
have not been observed on the builder by this source task.

## Source changes

- `os/devices/pixel10.json` now includes Mustang. Other device records are
  unchanged. Pinned upstream adevtool source supplies the device configuration,
  board/firmware metadata, 1344×2992 display and Laguna/muzel kernel binding.
- `os/build-only/mustang.json`, `mustang-inputs/` and `os/flavors/mustang.json`
  record the official input metadata: factory file
  `mustang-cp3a.261005.005-factory-099acc44.zip`, SHA-256
  `099acc44e2f87fe2735d92952081d64550ff08b4a6390bb6b001c8451695657e`, and
  [official URL](https://dl.google.com/dl/android/aosp/mustang-cp3a.261005.005-factory-099acc44.zip).
  One HEAD response reported **11,366,092,596 bytes**; no factory body was
  downloaded or hash-verified during source work.
- `mustang-build-only.py` loads a private instance of the existing build engine
  with Mustang-specific device, recipe, allocator, integration and output
  collector bindings. The normal Blazer entry point retains its default checks.
- Independent Mustang allocation, reconcile-seed, bundle and integration
  adapters use `mustang-allocations`, `mustang-integrate-<build>` and
  `mustang-reconcile-<build>`. They consume the established Frankel signed app
  inputs and owner-preserving reconciliation mechanism. No Blazer/Rango image,
  source snapshot, integration receipt, key or artifact is used.
- `mustang-pipeline.py` sequences a fresh Mustang source copy, genuine pinned
  Git metadata, input acquisition, vendor generation, lunch, allocation,
  reconciliation and compilation. Integration and compile use one checkout SHA.
  `BUILD_NUMBER` and `BUILD_DATETIME` come from the write-once allocation;
  `OUT_DIR=out-mustang-build-<build>` is fresh. Output existence, device/build
  metadata, sizes and SHA-256 values remain checked.
- `mustang-updater-route.json` configures device-qualified future Mustang
  discovery in the existing index. It creates no release or feed and selects
  no other device’s assets. Delivery remains private.
- `.github/workflows/mustang-candidate.yml` is a manual unsigned route using
  existing controller identity, builder selectors, non-cancelling shared FIFO,
  nonblocking host lock, ownership receipt and ownership-aware STOP cleanup.
  No signer login, signing secret or public publisher is present. Receipts are
  private artifacts retained seven days; large outputs stay on the builder.

The source work does not change the openssh-client hold or install packages.
Existing source trees, caches, release data and keys remain protected by the
underlying mutation and access controls. The implementation retains basic
no-overwrite/path admission; the independent verification tests are omitted.

## Deliberately reduced checklist

`os/build-only/mustang-sprint.json` records every omitted check from Nathan’s
plan. Focused, existing-device regression, required CI and full-suite tests
were not run. The source commit uses `[skip ci]` for this sprint’s PR/push path;
the manual Mustang workflow contains no test suite. No skip is reported as a pass.

Downloaded factory hashes are not computed or compared in the Mustang download
helper or its vendor preflight. The pinned adevtool unpacker also hashes the
factory internally; `mustang-inputs/adevtool-no-input-hash.patch` deliberately
removes that check for Mustang **in the new isolated copy only**. Production
adevtool is untouched. Existing byte/time limits and vendor generation’s own
functional assertions remain. Reserve admission and reserve sampling are omitted.
The output collector omits archive CRC sweeps, partition/firmware coverage,
packaged app/policy and signing verification, while retaining actual output
identity, size and hash recording.

Source review included Python/JSON syntax parsing and read-only configuration
review. It did not execute preparation, vendor/lunch, compilation, signing,
packaging, export, cleanup/STOP, any test suite or any phone operation. There is
no Mustang build, signed-installer, flash-ready or phone-acceptance claim.

## Signing and package blocker

`os/build-only/mustang-signing-requirements.json` gives the precise proposed
profile and container names and compatibility findings. They are source
requirements, not created cloud resources or approved access bindings.

The separate `os/signing-bootstrap/mustang.json` defines `pro-mustang-v1` and the
loader recognizes it as **unbound**. No admitted container/public identity exists.
The normal host-sign adapter is Frankel-bound and the retained adapter is
Rango-bound. The common pinned upstream release script does support Mustang,
but that alone does not admit the existing adapters. The existing factory
packager enforces identity/content checks deliberately removed from this sprint;
a compatible, scoped reduced path still needs implementation after signing is
bound. No Rango/Blazer grant or key can fill this gap.

The source definition names `PIXEL_OS_SIGNING_KEYS_MUSTANG_V1` and
`PIXEL_OS_SIGNING_PASSPHRASE_MUSTANG_V1` and preserves the established inventory,
consumer and same-run tool-proof mechanisms. Resource binding, effective access
and the journaled bootstrap remain pending; a definition grants no permissions.
No key generation, container creation, IAM change, signer login or signing has
occurred. Independent source implementation reaches unsigned compilation; no
compilation has executed.

For a later authorized runtime, dispatch **Mustang unsigned candidate** at one
main SHA after checking current runs. A successful unsigned compile still stops
before signing; its receipt names that blocker. The original sprint start,
absolute Pacific deadline and remaining budget must be recorded by Chief; this
source implementation has no evidence for those values and does not restart the
clock. The job cap is 11 hours, compile cap 8 hours, and existing independent
12-hour builder STOP fallback remains; these are separate from the four-hour goal.

Restore the omitted checks post-hoc. A later delivered package must be labelled
**build-and-package candidate; omitted verification remains undone;
physical-device acceptance unproven**, with actual stage receipts, output and
download checksums, private link/expiry and independent lifecycle evidence.
