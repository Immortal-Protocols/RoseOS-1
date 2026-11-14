# First public Mustang OTA — 2026110401

[Mustang 2026110401](https://github.com/Immortal-Protocols/pixel-ota/releases/tag/mustang-2026110401)
became public **2026-11-04T02:54:17Z** (November 3, 6:54 PM Pacific).
The existing-route [delivery run 41761758623](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41761758623)
completed successfully at 02:54:46Z. Automatic publication remains off;
phone installation and acceptance have not been observed.

| Field | Actual value |
| --- | --- |
| Device / model | `mustang` / Pixel 10 Pro XL |
| New build / timestamp | `2026110401` / `1793755347` |
| Installed baseline reported by Nathan | `2026110302`, Beta selected |
| Native producer | [41750459835](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41750459835) |
| RoseOS integration / compile source | `434df146289981c93f1ff84db7ab01ce37136f38` |
| Delivery recipe | `a86b29287335c1648b731d320b13980b65f8c137` |
| Existing bootstrap / secret versions | `41719073037` / version `1` in both Mustang secrets |
| Public release ID / tag | `433457360` / `mustang-2026110401` |
| OTA asset | `mustang-ota_update-2026110401.zip` |
| OTA bytes | `1673390903` |
| OTA SHA-256 | `bd31790a1f295d8fda021c940730a3906eef0e6797c3a0f180d9dc1cd9032f4b` |
| Beta wire metadata | `2026110401 1793755347 mustang stable` |
| Latest before / after | `frankel-2026110301` / `frankel-2026110301` |

The [public OTA](https://github.com/Immortal-Protocols/pixel-ota/releases/download/mustang-2026110401/mustang-ota_update-2026110401.zip),
[stage receipt](https://github.com/Immortal-Protocols/pixel-ota/releases/download/mustang-2026110401/mustang-ota-receipt.json),
[channel metadata](https://github.com/Immortal-Protocols/pixel-ota/releases/download/mustang-2026110401/mustang-stable)
and [checksums](https://github.com/Immortal-Protocols/pixel-ota/releases/download/mustang-2026110401/SHA256SUMS)
are assets of this one new release. No existing release or asset was replaced
by this run.

## Execution and evidence

Native Make returned 0, running **01:22:47–02:33:27Z (70m40s)**. Preparation
began after the baseline check passed at 00:57:16Z. All four native output
identities were collected under this allocation. Unlike the historical
2026110302 collection correction, this producer completed successfully and
required no collection recovery or Make rerun. Its original receipts remain
unchanged. The Android tree remains the documented production byte snapshot;
these receipts do not establish a clean reconstructed pinned-manifest tree.

No active/queued runs were present before either dispatch. Main advanced during
compilation through unrelated app/backend work; the Mustang operational recipe
did not change. Delivery consumed the captured compiled source above rather
than rebuilding from newer main. Nathan explicitly authorized the first public
OTA/channel update; automatic publication and phone actions stayed separate.

| File | Evidence and scope |
| --- | --- |
| `producer/` | Exact downloaded allocation, candidate and compile receipts; packaged-baseline precheck; independent stopped-instance observation after native cleanup |
| `producer-artifacts.json` | Native receipt artifact IDs, archive digests and expiry |
| `collection.json` | Successful native producer/source/output binding; no recovery, Make rerun or original-receipt edit |
| `sign.json` | Signing command returned 0; original version-1 secret bindings; signed target-files and OTA identities |
| `baseline.json` | Repeated retained-baseline packaged updater, discovery, signer and OTA trust checks before login |
| `mustang-ota-receipt.json` | New target/version, actual packaged updater/trust and whole-package/payload signature checks; original certificate/AVB public-byte pins; verification-tool hashes |
| `journal.jsonl` | Fresh draft; OTA uploaded/read back first; metadata last; explicit finalization intent and actual public outcome |
| `publication.json` | Actual release/OTA URLs, metadata, public downloaded-file verification and unchanged Latest |
| `mustang-stable`, `SHA256SUMS` | Exact anonymous downloads from the public release |
| `release.json`, `latest.json` | Independent GitHub release/asset digest and Latest metadata readback |
| `artifacts.json` | This run's receipt/staged artifact IDs, archive digests and expiry |
| `workflow.json` | Successful signer, hosted publisher and ownership cleanup job metadata |
| `builder-state.json` | Independent original-instance GET at 02:55:34Z: `685707771105568194` is `TERMINATED`, last stop 02:51:52Z |
| `worker-readback.json` | Receipt-chain/anonymous-small-asset checks and their precise download scope |
| `hosted-ota-checks.json` | Targeted offline checks passed on main recipe `a86b292...` in run 41759771919; not full CI or phone acceptance |

Downloaded receipt ZIP digests matched the GitHub artifact metadata before
extraction. JSON receipts are retained as exact bytes. The public stage receipt
also matched its publication pin; small assets were anonymously downloaded and
their checksums checked by this worker. The **hosted publisher** performed the
full public OTA download/hash check; this worker did not download that 1.67 GB
file again. `publication.json` calls the same-run stage producer `producerRun`;
the distinct **native** producer is 41750459835, bound through collection/signing.

The original signing identity was reused, without bootstrap, key generation,
new secret versions, worker IAM changes or access extension. Existing read-only
grants expire `2026-11-04T04:44:11Z`. Signer logout passed before OTA staging;
ownership cleanup succeeded at 02:52:01Z. The shared FIFO/lock, held openssh-client
and independent 12-hour STOP fallback were preserved.

## Remaining acceptance and retained omissions

The original sign receipt retains its sprint-wide verification fields as false.
The later OTA stage establishes the narrower actual OTA signature, original
public-pin, target/version and packaged-updater checks recorded in that receipt.
It does not establish a comprehensive OS/AVB audit, compiled Rose/controller
policy, partition/firmware coverage or installer-content verification. The
input-body hash and other sprint omissions remain recorded. Full/required CI
was not reinstated by this delivery. Subsequent targeted hosted checks are
reported separately, not retroactively substituted for original omissions.

Nathan's initial installation is user reported. Discovery, download, phone-side
verification, install, reboot, actual new build, data retention and hardware/Rose
smoke acceptance still require the coordinated physical test. Automatic source
is implemented and tested but inactive; no unattended release is demonstrated.

Preserve the [baseline recovery installer](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41738359336/artifacts/13280698927),
which expires `2026-11-10T23:42:12Z`. This delivery's publication-receipt artifact
expires `2026-11-11T02:54:41Z`; durable copies here preserve the evidence.
The required candidate label remains:

**build-and-package candidate; omitted verification remains undone;
physical-device acceptance unproven**.
