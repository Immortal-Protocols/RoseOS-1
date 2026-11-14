# Mustang runtime handoff

Snapshot: **2026-11-03T23:44Z**. Original sprint clock/deadline are not reset.
Producer is **41720536968**, source `2b521e732fa1537e54ff0a77880810ab1f34cf99`,
allocation **2026110302**. Bootstrap **41719073037** completed once. Do not
restart the original build or repeat bootstrap.
Follow-up delivery is **41738359336**, recipe
`7b5848c420d070d0219f0e99f05997ece245c748` (PR #501), created 23:20:29Z.

| Stage | Observed state | Evidence |
| --- | --- | --- |
| Source adapters | Merged through PR #501, including the Soong output-lookup correction | [collection correction](MUSTANG-OTATOOLS-COLLECTION.md) |
| Containers/access | Both exist; all four exact signer bindings confirmed; expiry `2026-11-04T00:50:06Z` | `os/signing-bootstrap/mustang-access-binding.json` |
| One-time bootstrap | Complete; both committed version 1; no recovery needed; workspace removed | [41719073037](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41719073037), committed public receipt |
| Preparation | Source completed 21:43:50Z; vendor 21:47:03Z; lunch 21:47:14Z | Producer receipt artifact below |
| Integration | Command returned 0, bound to the same source as compile | Producer candidate receipt |
| Compile command | Started 21:47:35Z; Make returned 0 at 23:00:48Z | `compile-started.json`, `compile-completed.json` |
| Original output collection | Failed: expected `host/linux-x86/otatools.zip` absent; target-files and vendor boot images hashed; actual build identity uncollected | Original receipt remains failed |
| Corrected output collection | Complete 23:21:54Z; all four outputs collected, actual Mustang identity confirmed, prior hashes unchanged | [collection receipt](../os/mustang-delivery/41738359336/collection.json) |
| Signing command | Exit 0; job complete 23:35:07Z; Mustang versions 1 consumed | [sign receipt](../os/mustang-delivery/41738359336/sign.json); independent signing-result/identity checks omitted |
| Factory generation | Command exit 0, complete 23:42:12Z; basic optimized-installer format identified | [package receipt](../os/mustang-delivery/41738359336/package.json), temporary work removed |
| Private export | Upload complete; installer, candidate note/receipt and checksums retained seven days | [candidate artifact 13280698927](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41738359336/artifacts/13280698927) |
| Delivered download/checksums | Download complete 23:43:09Z; all recorded file checksums matched 23:43:12Z | [delivery receipt](../os/mustang-delivery/41738359336/delivery.json); hosted job success 23:43:15Z |
| Original run cleanup | Successful; independent VM read `TERMINATED`, last stop 23:01:22Z | Existing instance `685707771105568194` |
| Follow-up cleanup | Job success 23:43:15Z; independent read at 23:44:22Z returned `TERMINATED`, last stop 23:43:07Z | [builder-state observation](../os/mustang-delivery/41738359336/builder-state.json) |

The new [receipt artifact 13277289458](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41720536968/artifacts/13277289458)
is 18,357 bytes, SHA-256
`47c70d286cd9d35e3ac2e0808ff93122bf19b4f78e73bf5d4fa29ee3347c9de8`,
expiry 2026-11-10T23:00:48Z. It was retrieved through the GitHub connector.
Target-files is 4,340,146,868 bytes, SHA-256
`87892fdabf63374d8afc66f3c986d92c9f2a7f0395e6df2d2eb8db4e9a833c03`.
Both vendor boot images are 67,108,864 bytes; their hashes and the five original
receipt hashes are pinned in the collection case record. The original native
outputs and receipts remain unchanged.

The corrected collection's actual build identity is `mustang` / `2026110302` /
`1793742435`. Its Soong otatools output is 536,807,666 bytes, SHA-256
`f8fc065e87083a51f64c74be57f4f06611721645273b9ae45081148203cebc22`.
The signing command produced signed target-files (4,884,085,713 bytes, SHA-256
`baf24f4b433d66e7115e5fc5a15af1394482a59af10c08e222913a65e6bf7814`)
and an OTA (1,673,383,799 bytes, SHA-256
`5347cf47bad561ce71f91704b332a7b3536cbca97d8614fc6e212414381cb76e`).
These are observed output identities, not the omitted signing verification.

The delivered installer is **mustang-install-2026110302.zip**, **2,108,057,621
bytes**, SHA-256
`4eae7e7e54191f30168649e75f2cfefb10fdbff1bffbfc2655ec079a4a8cce5e`.
The private Actions artifact is 2,108,061,108 bytes, expires
**2026-11-10T23:42:12Z**, and includes `CANDIDATE.md`, the candidate receipt and
`SHA256SUMS`. The separate hosted download verified the installer, note and
receipt bytes; this is the retained delivered-download checksum check, not an
omitted installer-content/signature verification. The follow-up workflow is
successful. There is no current delivery blocker or remaining delivery ETA.

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

The follow-up uses that existing route. It does not rerun Make or bootstrap and
records the original producer's failed workflow conclusion. Preserve the existing
workflow/controller/FIFO/lock and ownership cleanup. Do not extend access or
repeat bootstrap automatically. Native failure, cancellation, unregistered
producer failure, access denial or an uncertain key action cannot be adopted by
this collection exception.

The sprint's omitted tests and verification remain undone. Available evidence
includes source review and Python/JSON/YAML parsing, fresh bootstrap tool proof,
the actual Make outcome, output hashes, successful follow-up collection/signing
and package commands, and downloaded-file checksum results. The installer
exists and was delivered privately; independently verified signing results and
physical acceptance remain unproven. Delivery retains:
**build-and-package candidate; omitted verification remains undone;
physical-device acceptance unproven**.
