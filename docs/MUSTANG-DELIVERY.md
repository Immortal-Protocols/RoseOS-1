# Mustang signing and private candidate delivery

This continues [the unsigned Mustang source route](MUSTANG-SPRINT.md) under
[Nathan’s reduced-check plan](https://app.notion.com/p/40d7c646c81b81198e60cb76ca2ea603).
It implements the remaining signing, factory-package and private export source.
It is not evidence of a successful Mustang build or installer.

## Actual state

Unsigned run [41711304208](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41711304208)
used `2d9934a9c9c01e0197f8e4623fdb6d72c6900ef3`. Its source job succeeded;
the controller failed at `builder describe` (exit 1) before sending a VM start
request. Preparation/compile were skipped. Cleanup succeeded without sending
STOP because this operation never recorded start ownership. Current VM state
remains unobserved. The underlying controller error was suppressed by the
existing adapter; no access-denial cause is invented and no build is retried.

The configured `dev-infra@protocols-465723.iam.gserviceaccount.com` identity
created the two distinct containers in `protocols-465723`:

- `PIXEL_OS_SIGNING_KEYS_MUSTANG_V1`, 2026-11-03 20:21:41 UTC.
- `PIXEL_OS_SIGNING_PASSPHRASE_MUSTANG_V1`, 2026-11-03 20:21:42 UTC.

Both creation responses and metadata readbacks confirmed the resources. The
same actor’s `versions.list` was **PERMISSION_DENIED**, so present occupancy
is unconfirmed. No payload was generated, added or read, and no IAM changed.
Audit-log inspection was separately denied and was not retried.
The nonsecret evidence is in `os/signing-bootstrap/mustang-setup-evidence.json`.

## Implemented source

`mustang-sign-package.py sign` consumes a completed Mustang compile receipt and
a real `pro-mustang-v1` bootstrap receipt, bound by hosted GitHub run metadata.
It preserves the compile/integration source SHA, actual build number/time,
produced output hashes and the shared lock. The bootstrap pins **numeric
versions in the two Mustang-only containers**; no other-device key or artifact
is selected. The existing host-sign engine performs signing, with a
Mustang-only structural key-directory callback in place of the sprint-removed
intended-public-identity comparison. All default callers retain that comparison.
The existing RAM key handling, cleanup, key-path wrappers and access controls
remain. A bounded child runs the signing command; SIGTERM unwinds private-key
cleanup. Signed target-files/OTA output sizes and hashes are recorded, without
claiming independent cryptographic verification.

`... package` runs without signer credentials. It reuses the existing factory-only
script derivation, otatools extraction guards and bounded generator. The public
AVB bytes produced by signing are supplied to the generator without the omitted
public-identity comparison. The expected `mustang-install-<build>.zip` is
identified by the basic optimized-factory format, then sized and hashed.
No partition/firmware-coverage, app/policy, installer-reference, device-requirement,
AVB metadata, dynamic-content comparison or separate archive-signature check is
performed. On failure, partial nonsecret packaging work remains named in its
receipt. On success, temporary packaging work is removed.

`mustang-delivery.yml` separates bootstrap, signer, credential-free packager,
private upload, hosted download/checksum verification and ownership-aware STOP.
It uses the same non-cancelling shared FIFO and original builder. The hosted
provenance guard refuses while the Mustang profile is unbound. The installer,
notes, receipt and checksums are private Actions artifacts retained seven days.
The final receipt records the actual private URL, API-reported expiry, sizes and
verified delivered-download checksums. No public release or phone action exists.

`mustang-candidate.yml operation=diagnose-access` checks only the **same
controller token exchange** and prints fixed diagnostic categories. It makes
zero compute requests and changes no IAM. It exists to distinguish the failed
controller’s authentication from an unobserved VM permission issue, without
retrying `instances describe` or choosing another controller identity.

## Concrete remaining authorization and bindings

The existing role
`projects/protocols-465723/roles/roseosModelsBootstrapV1` was read successfully.
It contains exactly `secretmanager.secrets.get`, `secretmanager.versions.list`
and `secretmanager.versions.add`; it is not a newly created role.

A proposed minimal setup is **four conditional bindings**, each expiring at
most four hours after action time: that existing bootstrap role and
`roles/secretmanager.secretAccessor` on **each of the two Mustang containers
only**, for `roseos-build-signer@protocols-465723.iam.gserviceaccount.com`.
`mustang-signing-setup.py binding-plan --expires-at <UTC>` renders the exact
commands but performs no IAM mutation. New signer grants and generation/import
of a persistent Mustang OS key set require explicit action-time authorization;
the operator transfer does not authorize them. No creator payload/list access
or project-wide grant is proposed.

Once controller access is resolved and the setup is approved, the profile can
be resource-bound from actual evidence. The established bootstrap must prove
its tools in the same run, recheck both containers empty **as the signer**,
generate/import once under its write-once journal, emit only its public receipt,
and stop on any denial, occupied secret or uncertain/partial import. No key is
regenerated and no add retried. The same fresh common tool proof is reused as a
mechanism; no Rango receipt, key or artifact is consumed.

Focused, regression, required CI and full-suite checks remain omitted as
instructed. Source review and syntax/configuration parsing are distinct from
runtime acceptance. Any eventual delivery must retain this label:
**build-and-package candidate; omitted verification remains undone;
physical-device acceptance unproven**.
