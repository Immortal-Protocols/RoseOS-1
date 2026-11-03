# Mustang runtime handoff

Snapshot: **2026-11-03T22:23:38Z**. Original sprint clock/deadline are not reset.
The current builder run is **41720536968**; the successful one-time bootstrap
is **41719073037**. Do not duplicate either operation.

| Stage | Observed state | Evidence |
| --- | --- | --- |
| Source adapters | Merged through PR #498, source `2b521e732fa1537e54ff0a77880810ab1f34cf99` | [current source](https://github.com/Immortal-Protocols/RoseOS-1/tree/2b521e732fa1537e54ff0a77880810ab1f34cf99) |
| Mustang containers/access | Both exist; four exact per-secret bindings confirmed, expiry `2026-11-04T00:50:06Z`; human applied IAM, worker read it back | `os/signing-bootstrap/mustang-access-binding.json` |
| One-time key bootstrap | Complete; version 1 in both containers, no recovery needed, workspace removed | [run 41719073037](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41719073037), committed public receipt |
| Fresh unsigned attempt | Hosted configuration guard/controller passed; combined preparation/build step active since 21:19:17Z | [run 41720536968](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41720536968) |
| Integration/compile | No completed result from current attempt | Its final/failure allowlisted receipts will identify actual stages |
| Delivery signing | Not started | Must consume successful compile producer plus bootstrap `41719073037` |
| Factory package/private download | Not started | No installer, checksum or expiry claim yet |
| Current run cleanup | Pending | Existing ownership-aware cleanup; do not stop unrelated work |

The earlier established-route build `41714573501` failed at `admit`, before
integration or compile, due to an extra route field. Bootstrap workflow
`41717550485` stopped in preflight before any key generation because signer
environment variables were missing. PR #495 corrected those inputs. The next
unsigned attempt `41719106957` was cancelled after source review proved a
rendering error from flat versus nested host configs; PR #498 corrected it.
Its cleanup observed `TERMINATED` at 21:17:18Z before the fresh dispatch. No
failed/cancelled source tree or allocation is adopted.

At the latest snapshot, the combined step is active but its current substage and
whether compilation has begun are unconfirmed. Substage receipts upload only
when it ends. The active job-log request returned `404 BlobNotFound` at 21:37:59Z;
it is not retried before job completion. A single CPU-telemetry read, scoped to
the same builder instance and configured actor, returned HTTP 403 at 21:43:56Z;
that read stopped without retry or an alternative endpoint/identity. Neither
response establishes a build failure or a stall. Fifteen-minute status alarms
have reported the absence of new confirmed stage evidence, not invented progress.

All completed bootstrap facts come from the allowlisted public receipt and
run metadata. No private payload was downloaded here. No Blazer/Rango artifact,
key or identity is selected. Creator listing, audit/provider inspection, and IAM
write denials were respected. The first failed build's local artifact download
was Forbidden and not retried. No access restriction was bypassed.

Once the current build actually completes successfully, use the established
`os-release.yml mode=mustang-delivery` with its successful compile run ID and
`mustang_bootstrap_run=41719073037`. Inspect existing runs first. Do not dispatch
delivery on a failure, cancellation, uncertain output or expired grant. Do not
repeat bootstrap or extend access automatically. A denial or uncertain key-add
outcome requires stopping that scope and reporting it.

The sprint's focused/regression/required CI/full-suite tests and the other listed
verification checks remain omitted. Source review/configuration parsing and the
fresh bootstrap tool proof are the actual checks performed. Physical-device
acceptance is unproven, and no phone operation is authorized. Any delivered
installer must retain:
**build-and-package candidate; omitted verification remains undone;
physical-device acceptance unproven**.
