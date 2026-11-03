# Mustang runtime handoff

Snapshot: **2026-11-03T23:15Z**. Original sprint clock/deadline are not reset.
Producer is **41720536968**, source `2b521e732fa1537e54ff0a77880810ab1f34cf99`,
allocation **2026110302**. Bootstrap **41719073037** completed once. Do not
restart the original build or repeat bootstrap.

| Stage | Observed state | Evidence |
| --- | --- | --- |
| Source adapters | Merged through PR #500; output-lookup correction prepared separately | [collection correction](MUSTANG-OTATOOLS-COLLECTION.md) |
| Containers/access | Both exist; all four exact signer bindings confirmed; expiry `2026-11-04T00:50:06Z` | `os/signing-bootstrap/mustang-access-binding.json` |
| One-time bootstrap | Complete; both committed version 1; no recovery needed; workspace removed | [41719073037](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41719073037), committed public receipt |
| Preparation | Source completed 21:43:50Z; vendor 21:47:03Z; lunch 21:47:14Z | Producer receipt artifact below |
| Integration | Command returned 0, bound to the same source as compile | Producer candidate receipt |
| Compile command | Started 21:47:35Z; Make returned 0 at 23:00:48Z | `compile-started.json`, `compile-completed.json` |
| Original output collection | Failed: expected `host/linux-x86/otatools.zip` absent; target-files and vendor boot images hashed; actual build identity uncollected | Original receipt remains failed |
| Corrected output collection | Not executed; must locate the one Soong module output and pass device/build/time/hash checks before login | Exact case pinned in `os/build-only/mustang-collection-recovery.json` |
| Signing/package/private export | Not started | No installer or delivered checksum claim |
| Original run cleanup | Successful; independent VM read `TERMINATED`, last stop 23:01:22Z | Existing instance `685707771105568194` |

The new [receipt artifact 13277289458](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41720536968/artifacts/13277289458)
is 18,357 bytes, SHA-256
`47c70d286cd9d35e3ac2e0808ff93122bf19b4f78e73bf5d4fa29ee3347c9de8`,
expiry 2026-11-10T23:00:48Z. It was retrieved through the GitHub connector.
Target-files is 4,340,146,868 bytes, SHA-256
`87892fdabf63374d8afc66f3c986d92c9f2a7f0395e6df2d2eb8db4e9a833c03`.
Both vendor boot images are 67,108,864 bytes; their hashes and the five original
receipt hashes are pinned in the collection case record. The original native
outputs and receipts remain unchanged.

The earlier run `41714573501` failed before compile at updater-route admission;
`41717550485` stopped before any bootstrap generation due to missing job variables.
PR #495 corrected those configuration errors. `41719106957` was cancelled after
source review established a fatal host-config shape error; PR #498 corrected it.
Cleanup observed STOP before the fresh producer was dispatched. None of those
attempts supplies native outputs to the current delivery.

During the combined step, metadata exposed no internal substage. Its step began
21:19:21Z; phase receipts published only at the end. Compilation was unconfirmed
until those actual receipts became available. Elapsed time did not prove a stall.
The active job-log request returned 404 BlobNotFound; CPU telemetry returned 403.
Those reads stopped. The first failed build's local receipt archive download was
Forbidden and was not retried. No denied route or secret was used for diagnosis.

The existing `os-release.yml mode=mustang-delivery` consumes compile producer
`41720536968` and bootstrap `41719073037`. The collector correction admits only
this exact failed workflow with a successful native Make command, using pinned
producer/artifact/receipt identities. It checks preserved output bytes and actual
Mustang identity in a new same-delivery-run collection receipt before signer
login. It does not turn the old workflow green, rerun compilation, create keys,
change IAM or alter the original attempt. Any missing/ambiguous archive, changed
hash or wrong actual build identity stops before secret access.

No new delivery run has been dispatched at this snapshot. Before proceeding,
inspect current runs and the final corrected source. Preserve the existing
workflow/controller/FIFO/lock and ownership cleanup. Do not extend access or
repeat bootstrap automatically. Native failure, cancellation, unregistered
producer failure, access denial or an uncertain key action cannot be adopted by
this collection exception.

The sprint's omitted tests and verification remain undone. Source review and
Python/JSON/YAML parsing, fresh bootstrap tool proof, the actual Make outcome and
reported output hashes are the evidence available. No installer, verified signing
result or physical acceptance is claimed. Any delivery retains:
**build-and-package candidate; omitted verification remains undone;
physical-device acceptance unproven**.
