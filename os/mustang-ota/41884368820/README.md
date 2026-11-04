# Mustang UI follow-up attempt 41884368820 — failed, storage blocked

[Run 41884368820](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41884368820)
used source `6be302942f348b58f8a0ce2406c7202f2dd676bf` through the existing
manual `mustang-ota` route. Guard, controller and retained-baseline updater check
passed. The producer failed at **2026-11-04T18:36:39Z**; binding, signer and
publisher were skipped. No OTA was published by this attempt.

The [check-run annotation](https://api.github.com/repos/Immortal-Protocols/RoseOS-1/check-runs/126765260084/annotations)
provides the confirmed error: **System.IO.IOException: No space left on device**
while the Actions runner wrote its `_diag/Worker_20261104-181049-utc.log`.
This proves a filesystem ENOSPC error. Free-byte, inode and mount observations
are still needed to select the storage remedy; disk metadata alone is not a
filesystem-usage measurement.

At 18:45Z the ordinary artifact metadata API still returned zero artifacts.
The finalized producer-log request still returned **404 BlobNotFound**. That
is evidence unavailability, not an authorization denial. No permission, actor,
network route or held older artifact/log was substituted to obtain it. The
separate, permitted check annotation supplied the error above.

Individual step metadata remained incomplete. **0403 app admission, the allocated
Mustang number and native compilation are unconfirmed**, not marked passed or
not-started. The on-host receipts and `mustang-allocations` ledger must be
inspected before retrying; a possibly consumed build number cannot be guessed,
deleted or reused.

Ownership cleanup passed at **18:37:15Z**. Independent original-instance GET at
**18:38:33Z** confirmed `685707771105568194` **TERMINATED**, last stop
**18:37:12Z**. A later read confirmed the same stopped instance and its one
persistent boot disk `immortal-os-builder`, `sizeGb=4656`, `autoDelete=false`.
The exact observations and annotation are retained beside this record.

## Storage handoff before any fresh attempt

The worker has no existing SSH identity; new credentials/access are outside
scope. On-host files cannot be freshly inspected while this VM remains stopped
through the available metadata APIs. The existing Infra operator's established
host access is needed for a coordinated maintenance inspection, preserving the
single Mustang build/sign/publication owner. Do not invent a diagnostic workflow
that depends on the same full runner.

Chief assigned the existing Infra owner in thread
`01a17ac0-0f96-7165-8b57-562517b6e02e`, turn
`01a1a7e3-626f-74a5-9696-a754d08196b2`, to existing-access diagnosis and
data-preserving options only. This does not authorize deletion, resize, new
credentials or access expansion. The Mustang worker sent no second Infra
handoff; release ownership remains here and no fresh dispatch occurs while
storage is unresolved.

Minimum read-only inventory on the affected original host:

- Mount source/type and free bytes/inodes for `/home/pixelbuild/actions-runner/_diag`
  and `/home/pixelbuild/rose-actions` (`findmnt -T`, `df -hT`, `df -i`).
- The current attempt's fixed allowlisted JSON receipts under
  `/home/pixelbuild/rose-actions/mustang-prep-41884368820-1/`, including candidate,
  allocation, app-input admission and per-stage preflight/started/completed JSON,
  if present. Preserve partial/unparseable records as evidence.
- Matching records under `/home/pixelbuild/rose-actions/mustang-allocations/`
  and the derived integrate/reconcile directories; establish any consumed number.

Choose a specifically authorized, data-preserving storage remedy from that
inventory. No data deletion, disk resize, new VM, IAM/key/credential change,
grant extension or new dispatch was performed for this diagnosis. Earlier
sources, caches, outputs, keys and recovery installer remain protected.

After storage is restored and ownership/queue are reconciled, the existing
additional-release authority covers continuing the release goal. A fresh
manual `mustang-ota` dispatch allocates anew. Re-running this attempt is refused,
and the failed chain cannot be adopted by `mustang-delivery`. The original
signing read window ends `2026-11-05T06:20:18Z` and is not extended automatically.
