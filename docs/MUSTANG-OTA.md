# Mustang OTA implementation and activation

Implements the source work for [Nathan's OTA plan](https://app.notion.com/p/40d7c646c81b81ce9485e0ca3eff8614)
and [issue #503](https://github.com/Immortal-Protocols/RoseOS-1/issues/503).
**UI follow-up OTA authorized; Frankel 2026110403 inputs are bound for runtime
admission. First OTA 2026110401 remains delivered. Automatic publication is off;
optional physical-phone verification is not a release-completion gap.**

## Authorized UI follow-up

**Latest attempt is storage blocked.** [Run 41884368820](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41884368820)
failed at 18:36:39Z with a runner **No space left on device** annotation.
Binding/signing/publication were skipped; cleanup succeeded and the original
builder was independently confirmed stopped. [Failure and storage handoff](../os/mustang-ota/41884368820/README.md).
No receipt artifacts were available, so actual 0403 app admission, allocation
and native compilation remain unconfirmed. The publication ETA is pending
storage recovery. No later attempt has been dispatched or failed data removed.

Chief located the later PM request for both Mustang and Frankel OTA builds/
releases after the UI work. The source message references and exact scope are
recorded in `os/ota/mustang-ui-followup-authority.json`. This is additional
publication authority after 2026110401, not another use of the first one-OTA
approval. The same Mustang worker remains the single owner. The user separately
specified that push-button delivery is the completion criterion and physical
phone verification is optional; no phone action is authorized by this record.

The existing [one-button route](OS-RELEASE.md#mustang-one-button-ota-mustang-ota-manual)
from PR #529 is used once: `os-release.yml@main`, mode `mustang-ota`,
`mustang_publish_ota=true`, other inputs default. Automatic publication stays off.
Signing read access no longer has a window. Nathan granted the existing signer an
unconditional `secretAccessor` binding on both existing Mustang secrets. Existing Dev
Infra read the two bindings back (IAM metadata only) at **2026-11-05T01:17:39Z**, and
`signingAccess` now points to that readback (`os/ota/mustang-access-readback-unconditional.json`)
with `expiresAt: null`. The earlier window, `2026-11-05T06:20:18Z`, no longer limits
signing. Release read no IAM policy and changed none.

Both host paths now name Frankel **2026110403**, producer **41873084560**, source
`4e85677ee273630a79eae195e0a044d75226e122`. Its downloaded allocation artifact
13345237098 matched API archive SHA256
`4223673ae505a31732ea96fc725757907fb44b2b83190fa69eae1020530f499b`.
The exact JSON is retained in `os/ota/mustang-frankel-2026110403-allocation.json`:
Rose Agent **364**, Background Control **38**, Calculator **5**. The version-only
local candidate `38b5da1...` is distinct from the dispatched source, as recorded
by that allocation and the public release notes.

The original builder was independently observed **TERMINATED** at 17:59:08Z,
after the intervening Agent Beta controller failed to reach RUNNING and its
cleanup completed. That failure was a 420-second state timeout, not an observed
IAM denial. No failed operation was replayed. The current retained file bytes
cannot be proved from a stopped-builder path confirmation. The normal owned
Mustang run must execute the existing APK/receipt/signer/policy admission, match
all three versions to the bound Frankel allocation, and write
`app-input-admission.json` before integration/compile. Missing/mismatched input
stops the attempt. Source configuration is not a claim that this check passed.

No workflow/helper ownership was transferred. This host/pipeline binding does
not edit Release's workflow changes in #529/#536 or the optional reconciliation
lineage work in #543. Preserve the existing FIFO, lock, original identity and
ownership cleanup, STOP fallback and openssh-client hold. Record runtime input
proof, fresh source/allocation, signing, public download checks and cleanup when
they actually occur; physical testing can be recorded separately without
blocking this release.

On November 3 Pacific, after completing the new read grants, Nathan instructed
this worker to continue. `publicationEnabled=true` enables one newer Mustang
build/sign/public OTA and its `mustang-stable` metadata through the existing
route. `automationEnabled=false`; no automatic trigger or phone action is enabled.
The worker checks occupancy and the actual baseline before compilation.

## First public OTA completed

[Mustang 2026110401](https://github.com/Immortal-Protocols/pixel-ota/releases/tag/mustang-2026110401)
became public at **2026-11-04T02:54:17Z** (November 3, 6:54 PM Pacific).
[Delivery 41761758623](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41761758623)
completed successfully at 02:54:46Z. The [durable receipt bundle](../os/mustang-ota/41761758623/README.md)
binds native producer 41750459835, integration/compile source
`434df146289981c93f1ff84db7ab01ce37136f38`, delivery recipe
`a86b29287335c1648b731d320b13980b65f8c137` and actual timestamp `1793755347`.
Native Make returned 0 in **70m40s**; a separate delivery reused the original
version-1 identity. No native compilation or bootstrap was duplicated.

Actual baseline/new packaged updater/trust, target/version and whole-package/
payload OTA signature checks passed. The hosted publisher uploaded/read back
the OTA before metadata, finalized a fresh release without Latest promotion,
and verified anonymous public downloads. This worker independently checked
the published small assets/receipt chain and GitHub asset digests. The OTA is
**1,673,390,903 bytes**, SHA-256
`bd31790a1f295d8fda021c940730a3906eef0e6797c3a0f180d9dc1cd9032f4b`;
`mustang-stable` contains `2026110401 1793755347 mustang stable`.
Frankel Latest stayed `frankel-2026110301`.

Signer logout and ownership cleanup passed. Independent GET at 02:55:34Z
confirmed original instance `685707771105568194` **TERMINATED**, last stop
02:51:52Z. The access window was reused without IAM/key/bootstrap changes.
**OTA published** is established; **phone update passed** and **automation
demonstrated** remain pending. Earlier sprint omissions and the candidate
label remain; no automatic flag or phone action was enabled.

## Reported phone channel warning and source correction

After publication Nathan reported "release channel not available now", then
confirmed **security preview was the cause**. This confirms the reported
channel-selection problem, not OTA discovery, download, installation or reboot.

Beta is wire channel `stable`, the only entry in the actual packaged
`channel_published` array. The
[pinned setup screen](https://github.com/GrapheneOS/platform_packages_apps_Updater/blob/52c34a55bd26f7856f9141d7e4237397084d6936/src/app/seamlessupdate/client/SecurityPreviewSettings.java)
defaults its unset security-preview checkbox to true, then writes `1` on
Save/Next. Ordinary `Settings` uses false while the preference is still unset;
these are different stages. Once enabled, `Settings.getChannel` appends
`-security-preview`: Beta becomes `stable-security-preview`, which the local
published-channel gate refuses before release discovery. A normal Beta feed
does not authorize a preview feed. Prod and Alpha are also unpublished.

Read-only anonymous observations found the complete public index on two pages
(100 + 2 entries, each below the two-MiB cap), and the complete Mustang release.
The actual Java channel/release helpers select `mustang-2026110401` for ordinary
`stable` from the reported baseline. They correctly refuse the preview variant.
These observations establish the worker's public route and selection rules,
not the phone's network state. Nathan's reply establishes the phone cause.

The source correction uses the declared published feeds for an unset setup
default, preserves saved opt-ins/opt-outs, and explains an unavailable preview
when its normal channel is available. It never silently falls back or changes
the selected channel. The targeted OTA check workflow now includes the actual
channel-policy tests as well as release selection. This is a **source fix for
a future image**: the already published 2026110401 and baseline 2026110302 have
the same recorded Updater APK hash and are not modified by this source change.
No new build, release, phone setting or infrastructure operation was performed.
The normal Beta diagnostic is coordinated by Chief with Nathan; record actual
discovery and successful installation separately when reported.

Correction validation: **45 targeted Python tests** and **49 Java checks**
(23 channel-policy + 26 release-selection) passed. The channel and release
patches apply in order to the exact pinned upstream files after their Git blob
hashes were verified; patched XML parses and the underlying source whitespace
check passes. Unified-diff blank context prefixes are preserved in the patch.
The revised targeted workflow parses, but its new channel step has not run on
GitHub yet. No Android compile or rendered setup/notification test was run.

Remaining test plan: record Nathan's current normal-Beta discovery/download/
install/reboot result against 2026110401, independently of this future source
fix. When a separately authorized image contains the correction, validate its
setup and saved-state restoration with unset and explicit preview preferences,
and verify the unavailable-preview notification. Source policy checks do not
replace those Android/phone observations.

## Baseline and current evidence

- Native baseline: `mustang` / Pixel 10 Pro XL, build `2026110302`, timestamp
  `1793742435`, source `2b521e732fa1537e54ff0a77880810ab1f34cf99`.
- Private delivery: run `41738359336`; [recovery installer](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41738359336/artifacts/13280698927)
  expires `2026-11-10T23:42:12Z`. Preserve it and the retained native/signed outputs.
- Nathan confirmed **2026110302 installed, Beta selected** in this worker thread
  on 2026-11-04. This is user-reported installation, not a worker-observed OTA cycle.
- Source maps the Beta label to wire token `stable`. It uses the public release
  index, selects a complete `mustang-{build}` release, then fetches
  `mustang-stable` and `mustang-ota_update-{build}.zip` from that same release.
  Prod remains the new-install default; the selected Beta channel is material.
- Initial read: no active/queued GitHub run or competing Mustang PR. Issue #503
  tracks this single-owner work. The original VM `685707771105568194` was
  independently observed `TERMINATED` at `2026-11-04T00:23:05Z`, last stop
  `2026-11-03T23:43:07Z`.
- Public repository observation: no Mustang tag; Frankel Latest was
  `frankel-2026110301`. These are snapshots, not claims of future occupancy.

## First runtime observation and correction

[Run 41749757884](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41749757884)
(source `2a10c8e3a12707051cff411b45e4727d50e2fcbf`) passed the hosted guard and
controller, then stopped in the baseline observer at `2026-11-04T00:51:11Z`:
`ValueError: duplicate metadata properties`. The preparation/allocation/compile
step was skipped; no new native build, key use or publication occurred.
Cleanup succeeded at 00:51:50Z. Independent observation at 00:53:33Z confirmed
the original instance `TERMINATED`, last stop 00:51:41Z. The
[public failure record](../os/ota/mustang-prebuild-failure-41749757884.json)
retains exact stage/source/cleanup evidence. This was an observer parsing error,
not evidence that the installed updater is broken.

The observer had applied the strict OTA-manifest duplicate rule to all vendor
properties. The correction reads only the required literal device/build/time
properties, following the in-file last-definition behavior in the
[pinned init loader](https://github.com/GrapheneOS/platform_system_core/blob/322ff5129d865a5e8d958667e6c63ac943449493/init/property_service.cpp#L809).
It still refuses missing or incorrect resulting identity values. This is an
archive metadata observation, not a live phone-property read. OTA metadata
keeps its separate strict duplicate rejection. A targeted archive/observer
regression reproduces unrelated repeats, effective values and wrong-device
refusal while preserving strict OTA metadata parsing.
The corrected baseline observer passed in producer 41750459835 before its new
compile, then passed again before the delivery's signer login. These are actual
retained-package inspections, not live phone-property or OTA-cycle evidence.
No compile was invoked by the failed prebuild attempt; it remains failed.

## Implemented source

`os/ota/mustang.json` pins the installed baseline and original bootstrap.
Publication activation is **true** for the first manual attempt; automation is
**false**. The user applied the two new read-only bindings;
independent policy readback at **2026-11-04T00:44:51Z** confirmed their exact
signer, role, secrets, condition and expiry **2026-11-04T04:44:11Z** (20:44:11
Pacific on November 3). `signingAccess.status` is now **approved** and links to
`os/ota/mustang-access-readback.json`. This records actual policy metadata; the
worker made no IAM write or secret-payload read. No signing/build/publication
was started by recording it.

The existing `os-release.yml` accepts `mustang_publish_ota=true` only for:

1. **mustang-build:** check the retained baseline's actual target-files identity,
   packaged Updater resources and discovery classes, package/signer and OTA trust
   before starting another preparation/compile. This catches a baseline repair
   dependency early. Then use the existing fresh allocation/integration/compile.
2. **mustang-delivery:** consume that completed producer and original bootstrap,
   collect actual outputs and repeat baseline observation before signer login;
   sign with strict existing public-key pin discovery and version 1 in both
   Mustang secrets. Logout, then check/stage the public OTA without credentials.
   The OTA branch does not run factory packaging again; the recovery installer
   is preserved. Existing private delivery defaults retain their behavior.

`scripts/mustang_ota.py` checks actual packaged device/build/time, URL/index,
channel defaults/values/published set, Rose discovery classes, APK signer and the
system OTA certificate trust. It checks the new full A/B OTA's Mustang target,
incremental version and timestamp, rejects downgrade/wipe/incremental-source
requirements, and verifies whole-package/payload signatures using the source
release tool and the newly built otatools. Public certificate/AVB bytes must
match the established bootstrap identity. These are OTA-specific checks; no
general claim that all earlier sprint omissions have been verified is made.

The hosted publisher downloads this same run's staged artifact and validates
its identity, proof fields and every checksum before minting the existing
pixel-ota App token. It refuses a consumed/newer Mustang tag, creates a fresh
draft, uploads and download-checks the OTA first, then the receipt/checksums and
channel metadata last. Only the complete checked draft becomes public, with
`make_latest=false`. Anonymous downloads and the unchanged Latest are checked.
Existing releases/assets are never reconciled, deleted or overwritten.

The journal records intent, draft ID, asset readbacks and finalization request.
A failed prepublication upload leaves a draft unadvertised. A lost finalization
response is **unknown**, not reported as private. A public-download failure is
reported as already public. Neither condition is automatically retried or
removed. Inspect the exact outcome first.

`publication.json` records source/recipe, producer/publication run, OTA URL,
tag, size/hash, metadata version/line, downloaded checksum results and Latest.
Shared `os-release-frankel` FIFO, nonblocking lock, ownership STOP, original
instance and held openssh-client behavior remain. No signing/publisher credential
is added; the publisher token never reaches the builder.

## Automatic path — implemented, inactive

`.github/workflows/mustang-ota-checks.yml` validates relevant main changes with
the targeted offline suites. `.github/workflows/mustang-ota-auto.yml` observes
successful checks and existing OS workflow completions. It does nothing unless
**both** committed activation flags are true and repository variable
`MUSTANG_OTA_AUTO=on`. Only the short-lived workflow token has `actions:write`;
this is a proposed activation scope, not a new cloud identity or credential.

The dispatcher re-reads repository/branch/event/attempt/conclusion metadata. It
requires the current main content to match the newest relevant successful main
checks, fingerprints relevant Git blob content (not commit/version numbers),
coalesces pending events and skips unchanged published content. A product/input
digest excludes publisher, dispatcher and activation-only changes; a separate
validation digest covers operational code too. Changing only publication code
does not create a version-only OS release. It dispatches
the existing unsigned build with `candidate_sha` equal to the expected main
SHA; the hosted guard refuses a main-ref race.

After FIFO admission, the guard repeats the content/previous-attempt check
before starting a VM. This closes the race with a manual release or duplicate
event. A real successful auto compile dispatches one matching delivery with the
original bootstrap pin and public-OTA flag. A skipped compile cannot supply that
producer. Existing failed/uncertain attempts are not automatically retried,
including when a docs-only commit changes the SHA but not relevant content.
The OS FIFO serializes manual and automatic build/sign/publication. A completion
event reconsiders work deferred behind another active OS run.

## Operational record and remaining authority

The implementation request authorized source work and these local tests. The
plan's Scope and activation section calls for explicit execution coverage of
one newer public OTA/channel update, subsequent automatic publication and a
separately coordinated phone test. The current user instruction activates the
first manual publication flag only. Automatic activation and repository
variables remain unchanged.

1. **First manual activation authorized:** Nathan instructed this worker to
   continue after the read grants were confirmed. Publication is enabled;
   automation remains false and `MUSTANG_OTA_AUTO` stays off. Later automatic
   activation and phone installation remain separately coordinated.
2. **Access prerequisite completed for this window:** the user applied and the
   worker read back two new `mustang-ota-read-four-hour` **read-only** bindings for
   the existing signer `roseos-build-signer@protocols-465723.iam.gserviceaccount.com`
   on only `PIXEL_OS_SIGNING_KEYS_MUSTANG_V1/versions/1` and
   `PIXEL_OS_SIGNING_PASSPHRASE_MUSTANG_V1/versions/1`. The standard
   `secretAccessor` binding is at each secret's resource scope. No get/list/add
   bootstrap role, new key, new version, new account or credential is needed.
   The old bootstrap bindings still expire `2026-11-04T00:50:06Z`; they were not
   extended. The two new read bindings expire **2026-11-04T04:44:11Z**. Their
   actual readback/expiry is recorded in `signingAccess`; do not extend it
   automatically. No new key, secret version or bootstrap permission was added.
   Later unattended signing requires an explicitly approved access period;
   this implementation always stops outside its recorded window.
   **Superseded on 2026-11-05:** both secrets now grant the existing signer an
   unconditional `secretAccessor`. This was read back from IAM metadata at
   2026-11-05T01:17:39Z; older conditional bindings remain and do not expire it.
   `signingAccess.expiresAt` is therefore `null`. `mustang_ota.guard` accepts that
   only when `approvalRef` is a committed `os/ota` readback recording exactly those
   two secrets, the role, the signer from
   `os/signing-bootstrap/mustang-access-binding.json`, and no condition or expiry.
   A recorded window is still enforced with its 30-minute margin. Unattended
   signing still also needs `automationEnabled` and `MUSTANG_OTA_AUTO`, which
   stay off.
3. **Producer complete:** existing `os-release.yml@main` run 41750459835 used
   `mode=mustang-build`, `mustang_publish_ota=true`, with `candidate_sha` empty.
   The actual baseline inspection passed before preparation/compile. Native
   Make returned 0 and all four output identities were collected.
4. **Delivery complete:** the same workflow's run 41761758623 used
   `mode=mustang-delivery`, `mustang_compile_run=41750459835`,
   `mustang_bootstrap_run=41719073037`, `mustang_publish_ota=true`. It reused
   the committed original public bootstrap receipt plus original run metadata;
   artifact expiry did not require another bootstrap. The new public release
   and channel metadata are complete, not pending another dispatch.
5. **Evidence complete:** publication, cleanup and independent stopped-instance
   readback are retained in `os/mustang-ota/41761758623/`. The workflow and
   publication outcome are successful, with no unresolved partial publication.
6. Coordinate Nathan's phone window. Record discovery → download → verification
   → installation → reboot → actual new build, data preservation, connectivity,
   display and Rose behavior separately. No wipe/relock or phone action is
   authorized by source implementation. Preserve the recovery installer; no
   automatic rollback is promised.
7. After explicitly approved automatic activation, observe a subsequent eligible
   changed build publishing through the automatic path. Source tests do not
   demonstrate an unattended real release.

The first public OTA is complete; its ETA is superseded by the actual publication
time above. This native compile measured 70m40s (the earlier baseline measured
73m13s); delivery ran 02:35:37–02:54:46Z. The phone window is still pending.
The plan's **20–40 minutes** for phone acceptance is an unmeasured planning
allowance. Automatic activation and a subsequent eligible real release remain
separate work; no end-to-end phone/automatic completion time is established.

## Acceptance ledger

| Expected | Observed | Evidence | State |
| --- | --- | --- | --- |
| Baseline installed, Beta selected | 2026110302, Beta | Nathan's direct reply; config baseline record | Reported |
| Current signing read-access window | Two exact secretAccessor bindings confirmed; expiry 2026-11-04T04:44:11Z; original versions 1 used successfully | Access readback and delivery `sign.json` | Passed for this window |
| Actual baseline updater/feed/trust compatible | Retained target-files packaged updater, discovery, signer and trust checks passed twice | Producer precheck and delivery `baseline.json` | Passed at archive scope |
| Mustang-only publication, OTA before metadata | New public mustang-2026110401; download verified; Frankel Latest unchanged | Delivery `journal.jsonl`, `publication.json`, independent release readback | OTA published |
| Newer compatible signed OTA | Actual mustang 2026110401/time1793755347; whole-package/payload signatures and original public pins passed | `mustang-ota-receipt.json` | OTA-specific checks passed |
| Discovery/download/install/reboot/new build/data preserved | No worker phone action | Phone window pending | Not tested |
| Automatic eligible release, serialized and unchanged-content skip | Offline decisions/FIFO cases pass; flags off | dispatcher tests and workflows | Source tested; not demonstrated |
| Cleanup and original VM state | Logout/ownership cleanup passed; independent 02:55:34Z GET confirms original instance stopped, last stop 02:51:52Z | Delivery `workflow.json`, `builder-state.json` | Passed |

## Source validation

Python 3.12: **29 Mustang OTA tests**, **7 existing GitHub publisher transport
tests**, **10 updater-channel tests**, **6 updater-release tests** passed.
Java 21.0.12.1 compiled the actual release selector: **20 existing checks** and
**6 new Mustang mixed-device checks** passed. Python/JSON/YAML parsing, workflow
dependency references/input count and `git diff --check` also passed.
These were source-only checks at implementation time. The subsequent actual
package/OTA/publication checks are recorded separately above. Targeted hosted
offline checks also passed on main recipe `a86b292...` in
[run 41759771919](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41759771919),
triggered by subsequent main work; they do not establish Android runtime,
automatic release or phone acceptance. No full/required-CI suite was reinstated;
source/receipt commits retain `[skip ci]`. Earlier sprint omissions remain in
the unchanged baseline and candidate receipts.
