# Mustang OTA implementation and activation

Implements the source work for [Nathan's OTA plan](https://app.notion.com/p/40d7c646c81b81ce9485e0ca3eff8614)
and [issue #503](https://github.com/Immortal-Protocols/RoseOS-1/issues/503).
**First manual public OTA activation is authorized; automatic publication remains
off. No new OTA or phone acceptance is claimed until actual runtime receipts.**

On November 3 Pacific, after completing the new read grants, Nathan instructed
this worker to continue. `publicationEnabled=true` enables one newer Mustang
build/sign/public OTA and its `mustang-stable` metadata through the existing
route. `automationEnabled=false`; no automatic trigger or phone action is enabled.
The worker checks occupancy and the actual baseline before compilation.

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

## Exact next operations and authority

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
3. With authorization and no competing work, dispatch the established
   `os-release.yml@main` with `mode=mustang-build`, `mustang_publish_ota=true`,
   leaving `candidate_sha` empty for the first manual build. It first performs
   the actual baseline package inspection. If repair is required, report the
   installation dependency; do not assume the baseline can update itself.
4. After a successful producer, dispatch the same workflow with
   `mode=mustang-delivery`, its exact `mustang_compile_run`,
   `mustang_bootstrap_run=41719073037`, `mustang_publish_ota=true`. The OTA path
   uses the committed original public bootstrap receipt plus original run
   metadata, so expiration of its seven-day artifact never prompts rebootstrap.
5. Capture actual publication and cleanup receipts and independently observe
   builder state. Reconcile any partial/uncertain outcome before retrying.
6. Coordinate Nathan's phone window. Record discovery → download → verification
   → installation → reboot → actual new build, data preservation, connectivity,
   display and Rose behavior separately. No wipe/relock or phone action is
   authorized by source implementation. Preserve the recovery installer; no
   automatic rollback is promised.
7. After explicitly approved automatic activation, observe a subsequent eligible
   changed build publishing through the automatic path. Source tests do not
   demonstrate an unattended real release.

Forecast: the plan's **3–5 hours** remains a planning estimate for execution,
compilation and the phone window, not an ETA established by these offline tests.
The previous Mustang compilation measured 73m13s; new compile duration and
actual OTA install time are still unknown. Access/publication activation and the
phone window must be resolved before an end-to-end ETA can be committed.

## Acceptance ledger

| Expected | Observed | Evidence | State |
| --- | --- | --- | --- |
| Baseline installed, Beta selected | 2026110302, Beta | Nathan's direct reply; config baseline record | Reported |
| Current signing read-access window | Two exact secretAccessor bindings confirmed; expiry 2026-11-04T04:44:11Z | `os/ota/mustang-access-readback.json` | Policy readback passed; key use not attempted |
| Actual baseline updater/feed/trust compatible | Inspector implemented; actual builder inspection not run | `observe` before compile and login | Not tested |
| Mustang-only publication, OTA before metadata | Synthetic upload/readback and failure cases pass | `scripts/tests/test_mustang_ota.py` | Source tested; no public OTA |
| Newer compatible signed OTA | Source gates implemented; no new native build/sign | stage checks and original version-1 identity | Not executed |
| Discovery/download/install/reboot/new build/data preserved | No worker phone action | Phone window pending | Not tested |
| Automatic eligible release, serialized and unchanged-content skip | Offline decisions/FIFO cases pass; flags off | dispatcher tests and workflows | Source tested; not demonstrated |
| Cleanup and original VM state | Existing VM stopped at initial inspection; no new runtime started | 00:23:05Z compute observation | Current snapshot only |

## Source validation

Python 3.12: **28 Mustang OTA tests**, **7 existing GitHub publisher transport
tests**, **10 updater-channel tests**, **6 updater-release tests** passed.
Java 21.0.12.1 compiled the actual release selector: **20 existing checks** and
**6 new Mustang mixed-device checks** passed. Python/JSON/YAML parsing, workflow
dependency references/input count and `git diff --check` also passed.
No Android image, actual APK signature, OTA signature, cloud publication,
automatic release or phone acceptance was tested here. No full/required-CI
suite was reinstated; source commits retain `[skip ci]` and the new check
workflow has not yet run on GitHub. Earlier sprint omissions remain recorded
in the baseline and candidate receipts.
