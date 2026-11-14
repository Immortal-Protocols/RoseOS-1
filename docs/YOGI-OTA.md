# Yogi OTA route

Pixel 11 Pro Fold (`yogi`) OTA delivery on its own dedicated builder. Source is
implemented; **nothing has been built, signed, published or installed by this
route.** Every stage is refused until `os/ota/yogi.json` records the prerequisite
it needs. See [YOGI-BUILD.md](YOGI-BUILD.md) for the port and the lab build, and the
[handoff](https://app.notion.com/p/40e7c646c81b81629d22eb03edd73abd).

## Integrated first delivery

Nathan approved an integrated attempt in the current thread. Select `stage=one-shot`
and `publish=true` in the existing Yogi workflow once the actual setup below is
bound. This runs the complete path automatically, with no individual-stage
dispatches or manual transfer of bootstrap receipts:

1. Hosted admission checks the captured main commit, current immutable app
   export, exclusive protected Yogi runner and signing/setup bindings. The
   controller checks the actual 12-hour STOP limit and preserved dedicated disk
   before starting the replacement VM; it never adopts a running VM.
2. The native job verifies instance identity before checkout, then the actual
   filesystem UUID, paths and free space. The existing bootstrap tool proof runs
   before signer login. Yogi's keys are generated/imported once with the existing
   write-once recovery journal. A complete public receipt is retained on the Yogi
   disk. Later attempts reuse only a receipt matching the completed journal;
   incomplete or uncertain imports require recovery and never regenerate keys.
3. Allocate, integrate, compile and sign private baseline A. Retain its private
   install image on the Yogi disk. Allocate strictly newer B, copy A's integrated
   tree, compile and sign with the same identity and app inputs, then verify the
   full OTA, packaged updater and system trust. Nothing touches a phone.
4. A hosted publisher consumes the same-run public identity/baseline bindings,
   verifies the exact OTA assets before minting the existing pixel-ota App token,
   and publishes only `yogi-{build}`, OTA first and `yogi-stable` last. Existing
   releases and global Latest are preserved. Token cleanup and ownership-gated
   native VM STOP run on failure as well as success.

Both compiles stream stdout/stderr and retain `compile.log` plus a failure
receipt on the Yogi disk. They use the same pinned ccache implementation as
Mustang, in **Yogi's own** `/srv/rose-yogi/compiler-cache-v1`, bounded to 20 GiB.
No old OUT_DIR is adopted or deleted. A second compile can reuse C/C++ objects;
cache hits and Java/whole-build reuse are not promised. Signing credentials and
workflow tokens are removed from the compiler environment. Compile timeout and
sampled free-space stops terminate its process group. Delivery phases check
space using actual input sizes plus a 64 GiB reserve. These are conservative
allowances, not measured Yogi peaks or disk quotas.

**Observed setup blockers (2026-11-14).** The exact replacement VM is stopped,
with its preserved 2,000 GiB disk, but its actual STOP limit is **2,700 seconds**,
not the historical 12-hour figure. Both Yogi secret containers return metadata
404. The existing signer provider admits only `os-release.yml`; the existing
Yogi provider admits hosted controller jobs only. Organization runner eligibility
remains unknown after the historical 403 and has not been retried. The configured
dev-infra identity can set VM scheduling/metadata and create containers; it cannot
set secret or signer IAM policies. No live setup changes have been made.

The exact decision-ready scope is in
[`yogi-full-setup-proposal.json`](../os/ota/yogi-full-setup-proposal.json): a separate
self-hosted signer provider with exact repository/main/workflow claims, one
24-hour signer trust binding preserving existing members, two empty Yogi-only
secrets with a four-hour existing bootstrap-role binding and a 24-hour read
binding, protected runner eligibility, and the existing VM's 12-hour STOP limit.
The reviewed startup payload starts only the existing Yogi service; it creates
no SSH or registration credential. Administrator action/approval is needed for
those access/resource changes. Binding their actual evidence and expiries then
opens `fullRun.enabled`; this source commit deliberately leaves it false.

The current accepted app input is `frankel-2026111201` (voice 374, calculator 6,
background control 43), exported by run 43485652878, artifact 14067412309. Its
declared app-source sets are checked again against the actual workflow source;
the downloaded bytes and signatures are admitted by the existing verifier.
The initial one-shot needs two compiles and two signings; no successful timing
measurement exists yet. The native job is limited to 660 minutes, with a
separate 12-hour VM STOP fallback. No schedule is added.

## Bootstrap and identity decision

**Established (source evidence).** The lab artifact `2026110401` (Rose `c478983`),
per the artifact inspection recorded in [YOGI-BUILD.md](YOGI-BUILD.md):

- packages no Updater ("No Updater is included"; the lab recipe kept the upstream
  Updater with `OFFICIAL_BUILD` unset, and `apply-os-changes.py
  --keep-upstream-updater` ships none by design), so that image has no owned
  updater to discover a Yogi release;
- carries `test-keys` build tags, AVB public key `7728e30f…fef6` and Rose app
  signer `a40da80a…f5dc`, all public AOSP development identities.

**Not established.** That the phone runs exactly this artifact (installation is
owner-reported; ADB returned `error: closed`, so there is no installed-build
readback); the actual contents of its system OTA trust (`otacerts.zip`) and
recovery/update_engine acceptance, which were not inspected; and therefore whether
some sideload or data-preserving transition from the lab image is possible. Missing
an Updater rules out in-place discovery by that image, not every other path. No
data-preserving transition across signing identities is claimed either way.

**Proposed supported design (a decision, not a proven necessity).** Start the
durable Yogi identity with bootstrap build A, installed once by a fastboot wipe:
it erases userdata and metadata (apps and their data, accounts, Rose pairing and
endpoint state, settings, media, keystore keys); bootloader and radio are kept,
the bootloader stays unlocked, nothing is relocked. It needs the phone owner's
separate explicit authorization. Publication of OTA B does not wait for it: B
only has to be newer than A and signed by A's identity. Every later Yogi OTA then
updates A in place. Any alternative (for example a test-key-signed transition
package) would first need the unknowns above inspected under separate authority
and would remain a lab experiment until verified.

| Trust element | Yogi contract |
| --- | --- |
| OS keys (release/platform/shared/media/networkstack/sdk_sandbox/bluetooth/gmscompat_lib/nfc, AVB RSA-4096) | New Yogi-only set, profile `yogi-v1`, in `PIXEL_OS_SIGNING_KEYS_YOGI_V1` / `PIXEL_OS_SIGNING_PASSPHRASE_YOGI_V1`, read only by `roseos-build-signer`. Never another device's keys. |
| Public pins | `os/signing-bootstrap/yogi-bootstrap-receipt.json`, written by the authorized bootstrap. Absent today; no pin is claimed. Lab pins are refused. |
| Rose apps | Fleet app signer `650d47f9…0bfb`, already signed elsewhere (never on the Yogi builder); bound allocation versions. |
| Framework background policy | Pins exactly the admitted APK bytes, versions and signer; rebuilt with the image, never APK-only re-signing. |
| Updater | Owned Updater packaged (`updater-product-build-make.patch`), feed `https://github.com/Immortal-Protocols/pixel-ota/releases/download/`, release index `…/repos/Immortal-Protocols/pixel-ota/releases`, published channel `stable`. |
| OTA feed | Tag `yogi-{build}`, asset `yogi-ota_update-{build}.zip`, metadata `yogi-stable` = `{build} {buildDateUtc} yogi stable`. Never Latest, never GrapheneOS or another device's feed. |

Package, provider and permission names are unchanged (`dev.pixel.voice`,
`dev.pixel.backgroundcontrol`, `dev.pixel.calculator.agent`).

## Route

`.github/workflows/yogi-ota.yml` (manual dispatch only, concurrency group
`yogi-ota`) runs `scripts/yogi_ota.py`:

1. **guard** (hosted): names every missing prerequisite; nothing starts otherwise.

   `stage=key-bootstrap` (manual, one-time) instead runs only: guard (runner ready,
   `yogi-v1` defined, Yogi-bound and resource-bound, its bootstrap access evidence recorded
   with any recorded add-version window still open, no Yogi bootstrap receipt yet) →
   controller → on the Yogi runner the guard again, `signing-bootstrap.py preflight`,
   the same-run tool proof (`prove --lock /srv/rose-yogi/yogi-build.lock`), signer login,
   `run` under the same lock, logout → upload of the public
   `signing-bootstrap-receipt.json` only → cleanup. The receipt is then committed to
   `os/signing-bootstrap/yogi-bootstrap-receipt.json`. No image is built or signed.
2. **controller** (hosted, `YOGI_CONTROLLER_SERVICE_ACCOUNT` through its own provider
   `YOGI_CONTROLLER_WORKLOAD_IDENTITY_PROVIDER`; both checked non-empty before login): starts only
   the exact replacement VM and records whether it did (see "Replacement builder" below).
3. **build** (`[self-hosted, roseos-yogi-builder-20261105-b]`), under `/srv/rose-yogi/yogi-build.lock`:
   `preflight` (paths, lock, free space) → `allocate` (build number and
   BUILD_DATETIME strictly above the lab, the bootstrap and every attempt; exclusive
   attempt directory) → `prepare` (byte copy of the retained tree without `out*`;
   pins, reviewed vendor reference and all three port patches verified as applied;
   the retained tree is only read) → `integrate` (fleet app admission, then the
   existing `host-reconcile.py` with the owned Updater; on the first copy of the lab
   tree, only the lab-written paths listed in `yogi-reconcile.json` may be adopted;
   the app inputs are exactly the artifact named by Release's committed record, admitted
   by Release's `app-inputs-verify.py` with Yogi-tree tools, then re-admitted) →
   `compile` (`USE_STOCK_KERNEL=true`, `SOONG_NINJA=ninja`, relative `OUT_DIR`,
   `lunch yogi-cur-user`, `m target-files-package otatools-package`; target-files
   identity checked).
4. **sign**: approved window re-checked, signer login, the existing `host-sign.py`
   (pinned GrapheneOS release script, Yogi keys with public-pin discovery, OTA
   only), logout. Bootstrap: private `yogi-img-{build}.zip` + `avb_pkmd.bin` install
   image. OTA: `stage` verifies packaged Updater route/classes/signer, system OTA
   trust exactly the Yogi certificate, full A/B metadata newer than A, produced
   public identity equal to the pins, and whole-package/payload signatures.
5. **publisher** (hosted, `ota` with `publish` and `publicationEnabled`): re-verify,
   create a fresh draft, OTA first and `yogi-stable` last with readback, public only
   after that, anonymous download check, Latest unchanged.
After a successful bootstrap run, commit A's actual build, timestamp, source and
run to `os/ota/yogi.json` `bootstrap` (status `signed`) from its receipts; an `ota`
run then copies A's integrated tree (with its reconcile receipts) and is refused
until that record exists.

6. **cleanup**: stops the Yogi VM only if this run started it; its 12-hour STOP
   policy stays the fallback.

Frankel, Mustang, Rango, Blazer and Agent builds keep their own workflows, VMs,
locks and queues; nothing here edits `os-release.yml` or `release-pipeline.py`.

## Replacement builder

The route targets only the verified replacement (Mac operator receipts relayed by Chief, 2026-11-06), bound in
`os/ota/yogi.json` `builder`, `scripts/yogi_ota.py` and `os/release-pipeline/lanes/capacity.json`:

| Field | Value |
| --- | --- |
| Project / zone | `protocols-465723` / `us-west2-b` |
| VM / numeric ID | `roseos-yogi-builder-20261105-b` / `2720796557371812468` |
| Boot disk | `938743673401685238`, 2,000 GiB pd-ssd boot attachment; `/srv/rose-yogi` and the source tree on filesystem device 2049 |
| Runner | ID 21, `roseos-yogi-builder-20261105-b`, v2.338.2, repository-scoped, poolId 1 default group, service disabled/inactive |
| Controller | `roseos-yogi-controller@…`, role `roseosYogiControllerV1` (get/start/stop on this VM; the original zone-a binding is retained) |

The original zone-a VM `roseos-yogi-builder-20261103-a` is preserved but retired: config, controller and ownership
records refuse it, and nothing falls back to it, Frankel or Mustang.

**Controller.** Every describe, including every poll, requires the project, zone, name and numeric ID. START writes
the v2 ownership record first, holding the exact tuple, source SHA, workflow ref, run ID and attempt, and the state
observed before START. That record is exported by an `always()` step, so an uncertain START still leaves cleanup its
evidence. START is never retried. A VM already RUNNING is never adopted or stopped. Cleanup stops only with this run's
own valid record that started the VM. Legacy (v1), missing, stale, foreign-tuple and foreign-run/attempt records stop
nothing.

**Readiness only** (`stage=readiness`): guard → controller → `readiness` job on the replacement label. Its first
step, before checkout, refuses unless `RUNNER_NAME` is exactly `roseos-yogi-builder-20261105-b` and the GCE metadata
project/zone/name/numeric ID are exactly the replacement tuple (read directly from the metadata server, never through a
proxy). Missing or unreachable metadata refuses. A refusal fails the job and skips every later step: checkout, the
guard, path/lock inspection and the receipt upload (which runs only when that identity step succeeded). After checkout,
`yogi_ota.py readiness` repeats the identity refusal before inspecting anything, then checks:
- root/source/attempts directories, the lock and the private cover input;
- free space;
- `build/envsetup.sh` resolving inside the tree, `.repo` and the release script;
- the pinned aapt/apksigner/JDK 21 and host tools.

It writes only `yogi-readiness.json`, and nothing at all after an identity refusal. Its token is `contents: read`: no
login, id-token, secret, variable, build, download, key bootstrap, signing or publication. It does not change
`builder.status`. Build, key-bootstrap and signing jobs never run for this stage and stay closed while `builder.status`
is not `ready`.

**Readiness-only routing exception (Nathan-approved, 2026-11-06).** Organization runner eligibility is unknown, so
another eligible runner carrying the route labels could receive the readiness job. Only readiness may proceed without
`runner.organizationEligibility`: such a runner would hold only a read-only repository token and would stop at the
pre-checkout identity refusal. `key-bootstrap`, `bootstrap` and `ota` still require `runner.organizationEligibility`,
because their jobs mint OIDC tokens or handle build and signing inputs and need pre-dispatch exclusivity.

**Direct operator route (source only, unbound).** `docs/YOGI-DIRECT.md` / `scripts/yogi_direct.py` define a separate,
operator-driven path over host-key-pinned SSH that uses no GitHub self-hosted scheduling. It covers `check` and an
unsigned `compile` of bootstrap A; signing, key bootstrap, install, staging and publication are refused on it. It does
not change anything on this runner route: `runner.organizationEligibility`, `builder.status` and every gate above stay
as they are. `builder.direct.status` is `unbound` until its recorded dependencies and a separate approval exist.

**Receipt fields.**
- **Route label (recorded).** Chief-verified operator relay (2026-11-06; no timestamp recorded):
  `roseos-yogi-builder-20261105-b` was added to repository runner 21 and read back, with `self-hosted, Linux, X64,
  roseos-yogi-builder` preserved; runner offline/idle; both VMs stopped at that check. Before it, the complete
  repository runner list (2026-11-06T01:17:20Z) held only runner 21 with the four original labels.
- `runner.organizationEligibility`: unknown, still required for every stage but readiness. The organization
  runner-group read was denied (HTTP 403, 2026-11-06T01:24:17Z; it needs organization admin or the
  runners-and-runner-groups permission). It was not retried and no other route was used. The label readback does not
  change this; repository-list uniqueness is not global routing uniqueness or proof of job delivery.
- **`storage.mounts` (recorded).** Chief-verified operator mount receipt relay (2026-11-06; no timestamp recorded):
  filesystem `/dev/sda1` (`guestDevice`), ext4, UUID `64959ab3-d595-45a4-9cb8-da4af456efdd`, mounted at `/`, on the
  whole replacement disk `/dev/sda` (`guestDisk`, the known disk `938743673401685238` on VM `2720796557371812468`).
  The partition and the disk are recorded separately and are not interchangeable. The retained source, attempts and
  runner directories are on that same filesystem. Afterwards the temporary SSH key was removed, the runner service
  stayed inactive/disabled, both VMs were stopped with both disks preserved, and no readiness dispatch occurred.

`builder.status: runner-unregistered` is the not-ready source guard; it is not a registration claim (runner 21 is
registered and offline). Cleanup validates this run's ownership record before any login. A missing or not-started
record requests no credential, and an invalid one fails before login.

**Readiness source handoff.** Every source gate for `stage=readiness` is now open (`yogi_ota.py guard --stage
readiness` passes on main); the host checks above still run and refuse at runtime. What source does not provide, and
the operator must have separately before dispatching `stage=readiness` from `main` (attempt 1, `publish` false):
- authorization for the dispatch, the controller's VM start (compute is billed while it runs) and runner 21's
  service activation for the run;
- repository variables `YOGI_CONTROLLER_WORKLOAD_IDENTITY_PROVIDER` and `YOGI_CONTROLLER_SERVICE_ACCOUNT` (the
  controller and cleanup jobs fail closed before login without them);
- the VM stopped (`TERMINATED`) at dispatch, so this run's own START is recorded and cleanup stops it. A VM already
  RUNNING is never adopted or stopped by the run and would be left running.

Still closed: `key-bootstrap`, `bootstrap` and `ota` (unknown `runner.organizationEligibility`, `builder.status:
runner-unregistered`, unbound `yogi-v1` signing, no Release app-inputs record, no bootstrap receipt) and publication
(`publicationEnabled: false`). A passing readiness receipt does not by itself change `builder.status`.

**Readiness proof (recorded in `builder.evidence.readiness`).** This is a summarized relay of the operator's saved
receipts, from Chief on 2026-11-06. It is not the raw receipt.
- **Run:** `yogi-ota` readiness run 42152223661, attempt 1, source `a4730376706241e0b0b1387386a5e09c9f8146e2`, on
  runner 21 (`roseos-yogi-builder-20261105-b`), for `protocols-465723` / `us-west2-b` /
  `roseos-yogi-builder-20261105-b` / `2720796557371812468`.
- **Result:** passed, 20/20 checks, no failures. The relayed categories are identity, directories, lock, inputs,
  free space, retained-tree paths and tools. Free space was 1,708,976,877,568 bytes. The individual check names and
  values were not relayed, so `result.checks` is null.
- **Hashes:** the readiness JSON has SHA-256 `2574161d…a44ca`. Artifact 13472464984 (`yogi-readiness-42152223661-1`)
  is a 1,023-byte archive with SHA-256 `fe816882…f0b9d`. The two hashes cover different files.
- **Ownership (inferred, not captured):** `observedBeforeStart: TERMINATED` and `startedByThisOperation: true` are
  derived from the retained source and from the ownership-gated cleanup STOP having run successfully. The raw
  ownership JSON was not read, so `ownership.rawRecord` is null.
- **Cleanup:** job 127678829392's ownership-validated STOP succeeded.
- **Control-plane readback (direct):** the replacement instance `2720796557371812468` was `TERMINATED`. It stopped at
  2026-11-06T02:47:37.143Z, and that state was read back at 2026-11-06T02:48:45.783777Z. The original instance
  `8812446529823929967` was also `TERMINATED`, and both disks were `READY`.

This proves readiness only. It is not build or signing routing exclusivity (`runner.organizationEligibility` is
still unknown), signing readiness or publication authority. Recording it leaves `builder.status`
(`runner-unregistered`) and every stage gate unchanged.

Recorded but noted: repository-scoped runner, poolId 1, official default group. Registration does not restrict it to
Yogi workflows; label routing is the only separation. The controller account can also address the original VM.

## Remaining prerequisites (not performed by source)

| Prerequisite | Owner | Records |
| --- | --- | --- |
| Repository-scoped runner on the replacement Yogi VM carrying the replacement-specific label `roseos-yogi-builder-20261105-b` (server-side label readback); outbound GitHub/googleapis; a controller identity with get/start/stop on only that instance (`vars.YOGI_CONTROLLER_SERVICE_ACCOUNT`) through a Yogi controller workload identity provider admitting `yogi-ota.yml` (`vars.YOGI_CONTROLLER_WORKLOAD_IDENTITY_PROVIDER`; the shared roseos-actions provider admits only os-release.yml); signer jobs keep the existing signer provider. Open gap: if `vars.GCP_WORKLOAD_IDENTITY_PROVIDER` is the shared roseos-actions provider, the `key-bootstrap` and `sign` signer logins from `yogi-ota.yml` would be refused as well; which provider the Yogi signer uses is a Signing/Infra decision (os-sign.yml uses its own roseos-signing pool). Both jobs are already held by the unbound `yogi-v1` and the runner guard | Dev Infra | `builder.status: ready` |
| `yogi-v1` profile (Signing-owned, separate authority; tools from a same-run `fold_tool_proof.py` localProof on the Yogi builder); the manual `key-bootstrap` stage of `yogi-ota.yml` on the Yogi runner (source present, never run; needs Signing's `--lock` override); the two containers; one time-boxed binding per secret (get, versions.list, versions.add; at most 4 h) for one bootstrap; a time-boxed accessor on version 1 per delivery; escrow decision | Signing and Release, approved by Nathan | profile `resource-bound` (checked by `sign()` and the guard before any login), bootstrap receipt (project, empty reasons, workspace removed), `signing.access` |
| Release's `app-inputs-export` run for Frankel 2026110403 (PR #565) by the Frankel builder's runtime owner, then Release's committed record `os/app-inputs/frankel-2026110403.json` | Release | record present on main (guard binds it) |
| Yogi publication in pixel-ota | Nathan | `publicationEnabled: true` |
| One-time wipe install of A (proposed design), and ADB readback | Phone owner | `bootstrap.installEvidence` (not a publication gate) |

First-run assumptions to confirm on the builder (each fails closed):
- **Derived from readiness run 42152223661.** The source at `a4730376…146e2` defines exactly 20 checks, and the
  relay reports 20/20 passed. The individual results were not relayed, so the following is derived from that source,
  not read from the raw receipt:
  - `aapt`/`apksigner` are present at `prebuilts/sdk/tools/linux/bin/`;
  - Java 21 is present at `prebuilts/jdk/jdk21/linux-x86`;
  - the private cover-display input file is present;
  - the attempts filesystem had at least `minimumFreeBytes` free (1,708,976,877,568 bytes relayed).
- **Still open:**
  - the cover-display input holds a decimal `referenceDisplayAddress` (readiness checks only that the file exists);
  - the first reconcile of the lab tree conflicts only on the listed lab-written paths;
  - the copied tree fits in the free space at build time.

Known limitation: without `--agent-tray` the raised-edge build switch
(`rose-edge-build-make.patch`) is not applied on Yogi; the tray itself is enabled
by the Yogi product patch. The port keeps the stock-kernel security limits recorded
in YOGI-BUILD.md.

## Evidence boundary

Offline: `scripts/tests/test_yogi_ota.py` (contract, guards, allocation, copy and
lock, app admission, packaged-Updater/trust observation, OTA metadata, publication
order, builder ownership). On this route only the readiness run 42152223661 has run: the controller's
start/stop, and readiness checks on the replacement builder. No lunch, make, key bootstrap, signing, OTA generation,
publication or phone action has run. Record **build compiled**,
**signatures verified**, **OTA published**, **phone OTA passed** and **automation
demonstrated** separately when they actually happen.
