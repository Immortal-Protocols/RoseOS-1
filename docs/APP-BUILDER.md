# App builder (Project 1) and workspace composer routing (Project 2)

Source of truth for App Core's side of the November 13 "Next round" plan:
Project 1 Levels 1–2 integration (phone request → existing Rose server/Codex →
durable build job → signed APK → install → revise → same-identity update), and
Project 2's conversational intent/result contract. Home/App Product owns the new
Android app's interface and Level 3 sharing; OS Core owns authentication; the
Chariot owner owns hosting; OS Product owns Project 2's native page and
composer; the Local RoseOS Server Worker is the only Mac deployment owner.

Nothing in this document is deployed or proven on a phone. Each claim below is
marked as **source** (in the tree), **container** (compiled or signed in a
cloud container), **deployed** or **phone**. Only source and container
evidence exists today.

## 1. What exists today (audit of main `d232e904`, 2026-11-13)

| Capability | Exists | Proof level | Gap |
|---|---|---|---|
| Android toolchain | `scripts/bootstrap.sh` + `scripts/setup-android-sdk.sh`: hash-pinned platform 35 and build-tools 35.0.0 (aapt v1, d8, zipalign, apksigner), JDK 21, Node 22 | container: bootstrap, `workspace/pixel-calculator-agent/build.sh` and `workspace/rose-agent/build.sh` all exited 0 | Linux-only (`build-tools_r35_linux.zip`, bash 4 `mapfile`, GNU `sha256sum`). No Gradle, AGP, Kotlin, AndroidX, dependency resolution or R8. The Mac uses an unpinned `workspace/tools/` layout that bootstrap does not reproduce. |
| App signing | `scripts/sign-apk.py`: owner-only key inputs outside Git, v1–v4 + `.idsig`, optional `--expected-cert-sha256` pin, receipt without secrets | container: a throwaway scratch key signed and `apksigner verify` passed | The only app key (`PIXEL_APP_SIGNING_*`, signer `650d47f9…`) is Rose Agent's. It carries Rose Agent's signature permissions, so it must not sign generated apps. The platform key is excluded. No generated-app key exists or is authorized. |
| Phone request → Codex → APK | Node server: `POST /task` → one `danger-full-access` Codex thread in `workspace/`, told to build and run `delivery/publish.mjs` (`server/codex.mjs:102`) | source; historical Moon Lander run only (`workspace/rose-agent/README.md`) | Informal: no project, no job, no isolation (it can read pairing and TLS secrets), no version or signer identity, and it serializes with voice tasks. The Python producer (`rose.app --brain codex`, the current Mac runtime) has read-only Codex threads with shell disabled, so it cannot build. |
| Delivery and install | `delivery/catalog.mjs` (write-once, signer pin, versionCode monotonic), `server/delivery.mjs` (`/apps`), `Delivery.java` (PackageInstaller session, unknown-sources prompt), `InstallReceiver.java` | source; offline tests 18/18 | The generic install never checks the archive's signer or package. There is no projectId/sourceRevision/signer in catalog records, and no install receipt back to the server. The WebView auto-installs every new catalog id once. Entries without a deviceId go to every paired phone. |
| Same-identity update checks | `SelfUpdate`/`Delivery.installSelf`: same signer, strictly newer versionCode, receipt pin | source (Rose Agent self-update only) | Not applied to other packages. |
| Durable job patterns | `server/app-generator` store and driver (queue, attempts, abort, retry; tool packs, **not APKs**), `LauncherTask` (restart → "interrupted, outcome unknown"), `TaskStop`, `EventDelivery` | source | Reused as patterns only. |
| RoseOS install integration | `os/patches/rose-agent-self-update-frameworks-base.patch` (Rose Agent self-update only). The optional installer is disabled and unverified. | source | RoseOS installs generated apps exactly like stock Android. No privileged installer exists. |
| Level 2 | `workspace/models/contracts/service-auth-v1.md` (Models endpoint only), the `rose-chat-core` Rust client, SQLite in Python (life, transcripts) | source | No Chariot code, no generic VM hosting, no TOTP/PIN auth, no Rust server, no Rust+SQLite. |
| Device and data-retention proof | the emulator harness (`workspace/rose-agent/emulator`) covers Rose Agent tray/IPC only | none | No install, update or data-retention test for any other package. |

The repository-wide "App offline tests" CI has been red on main since #747, on
six frozen Launcher3 compile-contract assertions owned by the stopped compile
harness. App Core does not touch that harness; its changes are judged on their
focused suites, and the red is disclosed.

## 2. Architecture for Level 1

The Python producer (`rose.app`) hosts the builder, because it is the runtime
the Mac runs and the link phones already pair with (`rose-auth-v1`,
`prototypes/voice-phone-demo/REMOTE.md`). The Node server's ad hoc build route
is not extended. Its `publish.mjs`/auto-install path stays outside generated
apps.

1. **Request.** The new app (Home/App) or a conversation tool calls the builder
   API (§3) over the signed remote link. The Codex conversation thread only
   enqueues and reads jobs. It never builds.
2. **Project.** `create` allocates a fixed `projectId`, a `packageName` in the
   reserved namespace `app.rose.gen.p<projectId>` (never `dev.pixel.*`, which
   carries first-party signature permissions). The package is fixed for the
   project's life. The project's signer is created and bound by its first
   signed build, then fixed for good. Per-project keys are the implemented
   default, but the key decision in §5 is still Nathan's. A project whose
   delivered history goes missing fails closed; it is never rebuilt from the
   skeleton.
3. **Source.** Each project's source is a private git repository under
   `ROSE_CONFIG_DIR/app-builder/projects/<projectId>/source` (0700, with its git
directory beside it in `repo.git`), outside
   `workspace/`. Every version records the commit it was built from
   (`sourceRevision`). A revision starts from the last **delivered** version's
   commit, never from a failed attempt. If nothing has been delivered yet, it
   starts from the skeleton with the original description plus the change.
   So a failed first build is never a dead end.
4. **Generate.** A per-job Codex thread with `sandbox: workspace-write`, cwd =
   that project's source and no access to `ROSE_CONFIG_DIR`, writes code into a
   fixed framework-only skeleton: Java 8 source level, android.jar APIs only,
   aapt v1 resources, min API 26 and target 35. It cannot change the package,
   versionCode or signing. Enabling this thread on the Mac is a
   security-relevant runtime change; see §5. The generator is injected and is
   not implemented yet.
5. **Build.** Before git or any tool touches the generated tree, the builder
   refuses git metadata (`.git`, `.gitignore`, `.gitattributes`, `.gitmodules`
   in any case, with trailing dots or spaces, or as short names like `git~1`),
   symbolic and hard links, non-regular files, and entries the builder cannot
   read and write, anywhere in the tree. Before each reset, owner access is
   restored, so modes the generator left cannot block it. Compiled classes must sit in the
   app's own package, whatever the source text claims. The manifest may hold
   only `application`, permissions, features, `queries` and screen-support
   elements, and `<manifest>` may carry only `package`, so `split` and the
   split attributes are refused too. So `key-sets`/`upgrade-key-set`,
   `original-package` and
   `adopt-permissions`, which could hand the installed app's updates to another
   signer, are refused, and the built binary is checked for them again.
   The project's git directory sits beside the tree, out of the generator's
   reach, and git runs with hooks, fsmonitor and excludes disabled. The
   builder, not Codex, runs the pinned recipe (aapt v1 → javac → d8 →
   zipalign) in a job-owned output directory with a scrubbed environment.
   After the build, the binary manifest is checked again for debuggable and
   shared-uid attributes, by attribute id. `versionCode` comes from the project ledger and is injected at
   packaging. Generated code is compiled, never executed on the host. No
   generated scripts, Gradle builds or annotation processors run.
6. **Sign.** `scripts/sign-apk.py` runs outside the generation sandbox with the
   project's key and `--expected-cert-sha256` set to the project's signer, so
   a revision can never change identity.
7. **Deliver.** The signed APK is written once and content-addressed. Its record
   carries `projectId, versionCode, versionName, sourceRevision, jobId, sha256,
   size, signerSha256`. It is served only to the server owner's paired devices,
   over the signed link, and installed only on an explicit owner tap.
8. **Install.** The phone verifies the sha256, then reads the archive with
   `getPackageArchiveInfo` and checks the package, the signer (it must equal the
   project's `signerSha256` and, if installed, the installed signer) and that
   versionCode is strictly greater. Only then does it stage a PackageInstaller
   session. A changed signer is refused, never offered as
   uninstall-and-reinstall. The phone reports the outcome back (§3.5).

**Implemented so far** (`prototypes/voice-phone-demo/rose/app_builder/`, tests
`rose/tests/test_app_builder.py`):

- **Ledger:** projects, versions, jobs and installs in a 0600 SQLite file, with
  idempotent requests, versionCode allocation and a fixed signer.
- **Template:** the framework-only skeleton plus its refusals: package,
  version, SDK, shared uid, debuggable, build scripts, native code, symlinks
  and foreign packages.
- **Toolchain:** the pinned build with injected identity, a scrubbed
  environment, timeout and cancel. Signing goes through `sign-apk.py` with the
  project pin. Key creation is off unless the host owner enables it.
- **Service:** API v1 validation and the single worker, with git-recorded
  source revisions and a reset to the last delivered version after any failure.
- **Generator** (`generator.py`, §4A): the Codex app writer, an `app_build`
  session on the existing bridge per job. Constructed by no host.

Proof levels:

- **Container:** create, revise and a refused key change were built, signed and
  verified with throwaway keys. CI repeats this with a freshly fetched pinned
  SDK.
- **Container:** the signed routes, from an enrolled device's signed request
  to a signed artifact download, are covered by tests against the real listener
  app. Pairing itself (`POST /pair`) is covered by the existing remote-link
  test, not by these.
- **Container:** the generator path from `CodexClient` through the real
  bridge and `codex.mjs` to a fake app-server, then the Builder's check,
  compile and sign (§4A). No model ran.
- **Not done:** enabling the builder on a host, a real Codex app writer turn,
  and Home/App's install client against the real API. Nothing has been
  installed.

Data survives an update because the package, signer and higher versionCode keep
Android's update path; the revise prompt also requires keeping storage keys
and schema migrations. The writer's fixed instructions (`app-build.mjs`) say
so, but instructions are not proof. Acceptance still needs a seeded-data →
update → read-back test on a stock-Android phone and a RoseOS phone, run
separately.

## 3. Builder API v1 (agreed with Home/App, 2026-11-13)

All requests use the paired device's `rose-auth-v1` signature, under the
`/builder/v1/` prefix on the remote listener (`rose/app_builder/routes.py`).
The routes are mounted only when the server has an app builder enabled, and
`rose.app` enables none today. The signature covers the method, path, time and
nonce. Body integrity comes from the pinned TLS link. Every JSON response is an
object carrying `schemaVersion: 1`, including a 401 refusal. An unknown path
or method under `/builder/v1` gets the web framework's own 404 or 405 body. The library response is `{schemaVersion,
projects: [...]}`. Every paired device of the server's owner sees the same
library. Every body carries
`schemaVersion: 1`. Unknown request fields and versions are refused (`400`).
Responses may gain fields within version 1, so clients ignore fields they don't
know. A removed or changed field means a new version. A second client package
pairs as its own device, with its own key. Generated builds never enter Rose
Agent's `/apps` catalog. They reach the phone only through this API, and Rose
Agent may refuse the `app.rose.gen.` prefix outright.

### 3.1 Calls

| Call | Request | Response |
|---|---|---|
| `POST /builder/v1/projects` | `{schemaVersion, clientRequestId, description}` | `{projectId, jobId, notes}`; 400 `{error, code: out_of_scope, category: harm \| platform}` when screening refuses (§3.6), or 400 `{error, code: review_required, category}` when the policy can't allow it (§3.7) |
| `POST /builder/v1/projects/<projectId>/revisions` | `{schemaVersion, clientRequestId, request}` | `{jobId, notes}`; 400 as above |
| `POST /builder/v1/jobs/<jobId>/cancel` | `{schemaVersion}` | `{status: accepted \| not_running \| unknown_job}` |
| `POST /builder/v1/jobs/<jobId>/retry` | `{schemaVersion}` | `{jobId, attempt}`; 400 unless the job failed, is retryable, the project has no active job, and no later version has been delivered |
| `GET /builder/v1/jobs/<jobId>` | | job (§3.2) |
| `GET /builder/v1/projects` | | `{schemaVersion, projects: [...]}` (§3.3) |
| `GET /builder/v1/artifacts/<sha256>` | | APK bytes (same signature) |
| `POST /builder/v1/installs` | install receipt (§3.5) | `{recorded: true}` |

`clientRequestId` makes create and revise idempotent: retrying it returns the
same ids. `accepted` for cancel means stopping, not stopped. Only the job's own
`cancelled` state confirms it.

### 3.2 Job

`{jobId, projectId, attempt, state, stage, message, logTail, startedAt,
updatedAt, failure?}`

- **state:** `queued | preparing | building | testing | signing | ready |
  failed | cancelled`.
- **Times:** `startedAt`, `updatedAt` and every version's `createdAt` are Unix
  epoch seconds (UTC), sent as JSON numbers with a fractional part. A retry
  sets `startedAt` to the time of the retry.
- **failure:** `{category: toolchain | dependency | compile | test | signing |
  timeout | cancelled | internal, summary, retryable}`.
  - `dependency`: a library or language the framework-only toolchain lacks
    (Kotlin, Gradle, a jar or aar, an import of a missing package).
  - `compile`: anything else in the generated source.
  - `test`: reserved; no tests run yet.
- **Concurrency:** a project has at most one queued or running job. Revise and
  retry are refused while one is active. A retried job is queued again at the
  time of the retry.
- **Server restart:** a running job becomes `failed {internal, "interrupted;
  outcome unknown", retryable: true}`. It is never silently re-run; the owner can retry it (`POST ./retry`).
- **Logs:** `logTail` holds redacted build output only, never paths under
  `ROSE_CONFIG_DIR`, keys or tokens.
- **Display only:** `state` drives progress and Cancel. `stage`, `message`
  and `logTail` are text for display and are not an enum.

### 3.3 Library

`[{projectId, name, packageName, signerSha256, activeJob: {jobId, state} |
null, versions: [{versionCode, versionName, createdAt, jobId, sourceRevision,
apk: {sha256, size}, notes}]}]`

- `activeJob` is the project's queued or running job, so a restarted client or
  a second phone can show progress and Cancel.
- `apk.size` is the exact byte count.
- The APK is fetched only through a paired device's signed
  `GET /builder/v1/artifacts/<sha256>`. Public share links come separately from
  hosting (Level 3) and never grant backend or account access.

`packageName` never changes for a project. `signerSha256` is `null` until the
project's first signed build, then fixed for good. A client must re-read it
rather than cache a `null`; every delivered version has one. `versionCode`
strictly increases and is allocated by the server ledger, never by Codex.

### 3.4 Baseline

minSdk 26, targetSdk 35, framework APIs only. There is no native code, so builds
are ABI-independent. Anything needing AndroidX, Kotlin, Compose or third-party
libraries fails with category `dependency` and an honest summary. It is never
silently dropped.

### 3.5 Install receipt

`{schemaVersion, projectId, packageName, versionCode, signerSha256, status:
installed | updated | refused_signer | refused_version | cancelled_by_user |
failed, detail}`

- `versionCode` is the version the receipt is about: the one attempted.
- `signerSha256` is the signer the phone has for the package afterwards, read
  back from PackageManager. It is `null` when nothing is installed and must
  always be present.
- `refused_signer` and `refused_version` mean the client refused before
  Android saw the APK. When Android refuses, the status is `failed`, with
  Android's status message in `detail`, which may be empty.
- `updated` means success over an installed copy.
- A pending user action sends no receipt.

The server reports "installed" only from this receipt, never from publishing
alone.

### 3.6 Request screening and what generated apps may declare

Create and revise screen the request text before any job exists
(`rose/app_builder/screening.py`). The screen is offline, deterministic and
uses no model. Revise checks the project exists first, so an unknown project
is reported as `unknown project`. A refused request starts no writer, creates
no project or job, and returns 400 with these fields:

- `code`: `out_of_scope` for a phrase-gate refusal (`deny`), or
  `review_required` when the policy can't allow the request (§3.7). With no
  allow-authoritative gate configured, that is every request that isn't
  refused, with category `unavailable`;
- `category`: `harm` or `platform` for `out_of_scope`;
- `error`: an owner-facing sentence ending "Nothing was started.", which never
  quotes the request back.

It refuses only what it can name precisely:

- `harm`: covert surveillance of another person, such as "spy on my wife",
  spyware, or tracking someone "without them knowing". Also stealing passwords,
  phishing, imitating a named brand's sign-in, and malware.
- `platform`: a request whose subject is an app for another platform, such as
  "make me a website" or "an iPhone app for …". A request that mentions
  Android is never refused this way.

Everything else is accepted. `notes` (a list of sentences, often empty) says
what happens instead in three cases:

- a power generated apps never get, such as SMS or the call log, an
  accessibility service, device admin, a VPN, a keyboard, drawing over or
  blocking other apps, or root;
- a library;
- anything that needs a server.

Those are notes, not refusals. The words are ambiguous, as in "a piano keyboard
app" or "remind me to send texts". The policy below refuses the powers
themselves whatever the request said. A replay of a request the ledger
already accepted is never refused, so idempotency holds if the rules change. A
replay means the same clientRequestId and the same text. The same
clientRequestId with other text is refused (`clientRequestId was used for
another request`); before 2026-11-14 it returned the original ids.

The first version (82830ea0) also hard-refused capability words. Review found
47 of 112 ordinary requests refused. That corpus is now a test, and none of
its requests is refused.

The screen is a courtesy that reads English phrases. A reworded request can
pass it, and the writer can add things the request never asked for. For
capabilities, what holds is the manifest policy below.

For harm, nothing after the screen holds. An app with only allowed
permissions (location, microphone, camera, network) can still be put to a
harmful purpose, and the manifest policy does not judge purpose. So for harm
the text screen is the only gate in source, and review found that it misses
common rewordings. That is an open item (§5).

The manifest policy is checked on the source snapshot
(`template.check_capabilities`) and again on the built APK's compiled manifest
by attribute id (`toolchain.binary_permission_problem`):

- `uses-permission` only from an allowlist (`template.ALLOWED_PERMISSIONS`):
  - network, notifications, alarms, foreground services, camera, microphone,
    location without background, media, Bluetooth, NFC, biometrics, activity,
    contacts and calendar;
  - or the app's own `<package>.*` permissions.

  Refused: SMS, call log, phone state, overlays, querying all packages,
  installing packages, background location, settings, and every system,
  signature or binding permission. `<queries>` for named packages or intents
  (ordinary package visibility) stays allowed.
- `android:permission`, `readPermission` and `writePermission` on any
  element: only `BIND_JOB_SERVICE`, `BIND_QUICK_SETTINGS_TILE` or the app's
  own. That refuses the binding permissions that make a component an
  accessibility service, device admin, VPN, notification listener or
  keyboard.
- Manifest elements with a namespace prefix are refused, on the source and on
  the built APK, so an element form the policy doesn't know fails closed.
- `<permission>` declarations only in the app's own namespace, checked on the
  source and on the built APK. No permission name may come from a resource
  reference.

A failure is category `compile` with the policy's sentence as the summary.
This allowlist is App Core's proposed default. Widening it is Nathan's
decision.

Proof level: container tests only (`rose/tests/test_app_screening.py`, a
route test in `test_builder_routes.py`). That covers:

- refusals by category, and capability words as notes;
- the more-than-100-request near-miss corpus;
- normalization, and worst-case speed;
- a refusal recording nothing, and retry idempotency;
- an unknown project on revise;
- the source check alone. It also includes real builds where an
accessibility-service manifest is refused by the source check, and again by
the built-APK check when the source check is bypassed.

### 3.7 Screening policy interface (items 1–4 implemented in source; no approving gate)

This answers blocker 0 (§5). It chooses no moderation provider, enables
nothing and weakens nothing in §3.6.

**Implemented** (source only):

- `rose/app_builder/policy.py`: `ScreenRequest`, `Decision`, `Chain` (rules
  1–4) and `PhraseGate`, the §3.6 phrase screen as a deny-only gate. Gates run
  on one shared pool with a timeout. A gate that hangs, throws or answers
  malformed, or for another gate or version, counts as unavailable.
- `Builder(..., policy=None)` gets `policy.default_policy()`, which has no
  allow-authoritative gate, so every create and revise is refused with
  `review_required`/`unavailable` and nothing is recorded. No approving gate
  exists outside tests.
- A revise is judged with the project's history in submission order
  (`Ledger.request_history`). A retry doesn't reorder it.
- The decisions that accepted a job (gate, version, verdict, category) and its
  notes are written in the same transaction as the job (`screenings` table),
  so no job exists without them. A replay returns them unchanged whatever the
  policy is now.
- The worker never runs, and `retry` never re-queues, a job that has no
  recorded decision, for example one written before screening existed. Such a
  job fails as "never screened".
- `rose/tests/policy_fixtures.py`: test-only `RecordedGate`, which answers
  from recorded verdicts, and `approving_policy()` for build-path tests.
- `rose/tests/fixtures/screening/*.jsonl`: 155 recorded cases, all moved from
  the existing §3.6 tests (121 benign, 13 capability notes, 21 refusals). No
  new harm phrasings were written.

Proof level: container tests only (`rose/tests/test_app_policy.py`, plus a
route test). They cover:

- every chain of up to three gates over nine gate behaviours, each gate either
  allow-authoritative or not (6,175 chains),
  each checked against an independent oracle, plus the fail-closed invariants;
- a hanging gate;
- the default policy across all 155 fixtures, which allows none of them;
- recorded replay, matching every expected verdict;
- the default Builder recording nothing;
- review_required never overridden, and replays keeping their decision;
- after review of c8bf8a6c, these added cases:
  - `BaseException`, unreadable attributes, look-alike verdicts and a
    truthy non-bool `can_allow`;
  - a queued timed-out call is dropped;
  - history order after a retry;
  - a job and its screening written together;
  - unscreened jobs never run or retry;
  - an empty chain is `unavailable`.

Each rule has a mutation check. Each of these breakages makes the tests fail:
- accepting malformed answers;
- needing no allow-authoritative gate;
- letting an empty chain allow;
- letting review_required through;
- ordering history by `started`;
- writing the screening after the job's commit;
- running unscreened jobs;
- catching only `Exception`;
- not cancelling a timed-out call.

Not done:

- any allow-authoritative gate;
- any reviewer role;
- any model call;
- Home/App showing `review_required`. The contract is additive and was sent
  to them.

**Interface.** A screening policy is an ordered chain of gates. Each gate is
called with:

```
assess(request) -> Decision
request  = {kind: create | revise, text, priorText, locale}
Decision = {verdict: allow | review_required | deny, category, reason,
            gate, gateVersion}
```

- `priorText` is the project's accepted description and earlier revisions. A
  revise is judged with its context, so harm can't be built up step by step.
- `reason` is owner-facing text. It never echoes the request.
- `gate` and `gateVersion` are recorded with the job, so every decision is
  traceable to the policy that made it.

**Fail-closed rules** (property-tested, independent of any gate):

1. `allow` only when every gate in the chain returns `allow`. An empty chain
   is a misconfiguration, so it gives `review_required`/`unavailable`. It is
   never `allow`, and it is never reported as `out_of_scope`.
2. Any `deny` gives `deny`. Otherwise any `review_required` gives
   `review_required`.
3. Each of these counts as `review_required`, with category `unavailable`:
   - a gate that throws anything, `BaseException` included;
   - a gate that times out. A call still queued is cancelled;
   - a gate whose attributes can't be read as plain values;
   - an unknown verdict, a malformed or look-alike object (not exactly a
     `Decision` with plain `str` fields), or the wrong gate name or version.

   That is never `allow`, and no job is written.
4. A gate may be deny-only. The §3.6 phrase screen becomes such a gate: it
   returns `deny`, or `allow` meaning "no objection". It can never be the
   chain's only allow-capable gate. So the builder accepts no job until a
   second, allow-authoritative gate is configured.
5. Replays keep their recorded decision, as in §3.6. The same
   clientRequestId with the same text replays it; anything else is screened
   afresh.

**`review_required`** means no job exists. The response is 400 with `code`
`review_required` and `category`. The owner-facing reason is in `error`, as for
`out_of_scope`. It can be resolved only in
these ways:

- the owner rewords, and the new request is screened from scratch;
- someone in a reviewer role, who is not the requester, decides. The reviewer
  can only find a request not harmful. A reviewer can't approve harmful
  functionality, and no code path turns `deny` into `allow`.

The owner's confirmation alone never changes a verdict. It can add context,
for example that "track" means the owner's own runs. That arrives as new
request text and is screened again.

**Second gate: semantic moderation versus owner review**

| | Semantic moderation (a model judges the request) | Owner review (the requester confirms) |
|---|---|---|
| Catches rewordings and other languages | yes, within the model's quality | no |
| Can authorize harm | no, if fail-closed | it must not: the requester is not an independent check |
| Data and cost | request text goes to a model; a provider and data-use decision | none |
| Availability | an outage blocks builds (fail-closed) | always available |
| Proof | offline eval on fixtures, then a recorded-verdict regression | none possible for safety |

Recommendation: semantic moderation as the allow-authoritative second gate.
The phrase screen stays as a cheap deny-only first gate. Owner review is a
clarification step, never a safety gate. One option keeps data where it
already goes. The request text is sent to the Codex account the writer
already uses (§4A), so a separate ephemeral, tool-less classification thread
with a structured verdict adds no new recipient. Choosing that, or any other
provider, is Nathan's decision (below).

**Adversarial fixtures.** These are JSONL files of
`{id, kind, text, priorText, expected, category, source}`:

- **Benign near-misses:** the 112-request review corpus and the review-4
  lists, with expected `allow` overall and the phrase gate returning no
  objection.
- **Harm and other-platform sets:** expected `deny` or `review_required`.
  Their content belongs to whoever owns content policy. App Core keeps the
  current §3.6 examples and supplies the harness, but does not author new
  harmful phrasings.
- **Context sets:** a benign create followed by revises whose combination
  should be `review_required`.
- **Gate-failure cases** (code, not text): throw, timeout, unknown verdict,
  malformed result, version mismatch and an empty chain. Each must give no
  job.

CI replays recorded verdicts and never calls a model. A semantic gate's real
evaluation is a separate, explicitly authorized offline run.

**Proof bar before any enabling:**

- zero `allow` on the deny set;
- the false-`deny` rate on the benign corpus below a threshold Nathan sets;
- every gate-failure case non-`allow`;
- the eval run recorded with the gate version and fixture hashes.

**Source work that can proceed now** (container tests only, no model calls):

1. `rose/app_builder/policy.py`: the `Decision` and gate types, the chain with
   rules 1–5, and property tests for rule 3.
2. The §3.6 phrase screen wrapped as the deny-only gate.
3. `Builder` takes a chain. The default chain has no allow-authoritative gate,
   so it accepts nothing. That preserves the disabled state even if routes
   were mounted. Tests inject a recorded-verdict gate.
4. The fixture format, the harness and the failure-mode fixtures, plus the
   existing benign corpus.
5. The `review_required` 400 body, shared with Home/App as an additive v1
   field before they show it.

**Decisions for Nathan:**

1. Which allow-authoritative second gate, and whether request text may go to
   it. That includes whether the writer's existing Codex account counts as
   already-approved data use.
2. Who holds the reviewer role, confirming that the requesting owner can't
   self-approve.
3. Who owns the harm and platform content policy and its fixtures, and the
   acceptance thresholds.

## 4. Project 2: composer intent/result contract v1

Purpose: route the owner's explicit conversational requests for the
one-page-left-of-Home workspace to the **phone-local** composer, and route
agent tasks the owner explicitly selects there through the existing server. The
server never generates layouts. Native UI and the composer belong to OS
Product, after Nathan approves the prototype. Until a phone composer provider
reports this contract, the conversation tool is not offered, so Rose never
claims a page change she cannot make.

### 4.1 Server → phone (`app_command` over the existing phone link)

`{action: "app_command", packageName: <composer provider>, operation:
"workspace_compose", params: {schemaVersion: 1, requestId, kind, text?}}`

- `kind`: `prompt` (the owner's words, required non-empty `text`, ≤ 500
  chars) | `arrange` (best guess, no text) | `regenerate` | `undo`.
- `requestId`: opaque, ≤ 64 chars. A repeated `requestId` returns the first
  outcome and composes nothing new.
- The provider's `capabilities` operation must report
  `{status: "ok", schemaVersion: 1, kinds: [...]}`. A kind it does not list is
  refused before sending.

### 4.2 Phone → server result

`{schemaVersion: 1, requestId, status, revision?, reason?}`, with `status` one
of:

`revision` is a non-negative integer that grows with each composition. It is
required with `shown` and `prepared`; a result without one is treated as
`uncertain`.

- `shown`: the page drew exactly `revision`.
- `prepared`: composed and saved as `revision`, but not on screen (the owner is
  in another app; nothing interrupted them).
- `rejected`: refused, with `reason`; nothing changed.
- `unavailable`: no composer or model on this phone; nothing changed.
- `uncertain`: no definite answer.

The conversation's `ok` is true only for `shown` and `prepared`, never for a
mere submission.

- A link that drops after the request was sent, or a late answer, is
  `uncertain`. It is reported and never re-sent; v1 has no read operation, so
  the owner asks again if they want to. Only a link that reports the request
  was never sent (`offline`) means nothing changed.
- The server waits 25 s, longer than Rose Agent's 20 s `app_command` wait, so
  Rose Agent's own reply normally arrives. Rose Agent runs phone commands one
  at a time and starts its 20 s wait only when it reaches this one, so a
  command queued behind another can still come back late and be reported as
  `uncertain`. A composition slower than 20 s comes
  back as Rose Agent's `running` and is reported as `uncertain`; v1 does not
  fetch it later.
- A busy or transient reply to the capabilities check (`routine_busy`,
  `running`, `expired`, `error`) is reported as busy. Nothing was sent, so
  nothing changed.
- Rose Agent relays `app_command` only to same-signer or allowlisted packages,
  so the composer provider must be one of those.

### 4.3 Agent tasks chosen on the page (workspace task handoff v1, proposal)

The approved HiFi R2 prototype (`prototypes/rose-hifi`, source `6fcf8c9b`)
separates two kinds of page action. A **direct** action (launch an app, open a
deep link, run a notification action, toggle Do not disturb) runs on the phone
at once and never reaches the server. A **Rose task** ("Add missing items to my
grocery list", "Summarise prep notes") always asks first, on a confirm sheet
with an `ask` text. After the owner confirms, it runs as ordinary Rose work, and
the card and Rose's work read the same task state.

Today the producer has no route for the phone to start such a task over the
signed link: typed turns exist only on the loopback dashboard. This handoff adds
one.

- **Snapshot entry** (OS Product's registry, in `rose-layout-v1` terms): an
  action `{id, label, kind: "agent_task", targetId, ask, requiresConfirmation:
  true}`. `label` (≤ 60 characters) and `ask` (≤ 200) are display text the OS
  chose. The registry also keeps the task's `goal` (≤ 300 characters), the
  instruction Rose runs. The composer may place an `agent_task` only in a
  `button` or a `card`'s actions; it never sees or writes the goal.
- **Phone → server, after the owner confirms:** a signed
  `POST /workspace/v1/tasks` on the remote listener with `{schemaVersion: 1,
  requestId, actionId, label, goal, confirmed: true}`. It is mounted only when
  enabled, like `/builder/v1`.
- **Reply:** `{schemaVersion: 1, status: accepted | duplicate | refused |
  unavailable, taskId?, reason?}`.
  - `accepted` means started, never done.
  - A repeated `requestId` from the same device returns the first reply and
    starts nothing.
  - Without `confirmed: true` the request is `refused`.
- **Running it:** the server starts it as the owner's own explicit request,
  through the same conversation route, tools and policy as a typed request. So
  phone app work uses the existing `phone_task` path and its two workspace
  slots, and nothing gains capabilities. Progress and the final result arrive
  as the existing task events on `/ws/events` (task card v1). Stopping uses
  Stop contract 1.
- **What the page shows:** the action's state comes from that task, as in
  HiFi. The page never assumes success. Only `running`, `done`, `failed` and
  `cancelled` exist; there is no "needs your choice" state until the task
  model has one.
- **Implemented (source, tests only):** the route
  (`rose/workspace_routes.py`) and its checks and durable once-per-request
  store (`rose/workspace_tasks.py`), with a test suite
  (`rose/tests/test_workspace_tasks.py`). They are mounted only when the server
  has `workspace_tasks` set, and `rose.app` sets none.
- **Conversation hand-off (source, tests only):**
  `workspace_tasks.conversation_starter(conductor)`. A confirmed task becomes
  the owner's own request in the conversation, marked as confirmed on the
  workspace page, so it runs with exactly the conversation's tools and policy.
  While Rose is replying, it is `unavailable` (503) rather than cutting her
  off. `taskId` is `null`, because the conversation decides what phone work to
  start; the page follows the task events.
- **Proof levels.** Source tests drive the real conductor with the simulated
  Codex fixtures. There is no live end-to-end proof: no phone, no real Codex
  turn, and the server is not enabled or running.

### 4.4 App Core's answers to `rose-layout-v1` (Models Core, #781)

- **Mapping.** The mapping in `rose-layout-v1` "Relation to App Core" is
  confirmed as written. This outer contract stays as §4.1–4.2.
- **Q5.** A conversational request needs only `trigger: conversational`,
  `prompt` and `requestId`. App Core adds nothing else.
- **Q6.** Agreed: only the provider decides `shown` versus `prepared`, and the
  composer is not told.
- **Q7 (regenerate).** The owner expects a different arrangement. App Core's
  only requirement is a new `revision`, reported as `shown` or `prepared`. App
  Core recommends that the provider recompose against a fresh snapshot, passing
  the previous layout's item ids as a soft "avoid" hint. The mechanism is
  Models Core's and OS Product's choice.
- **Action kinds.** Please add `agent_task` (§4.3) to the action kinds, limited
  to `button` and `card` actions, and keep the goal out of the model's input.
- **Provider: confirmed by OS Product (#787 at `d71fd72a`).** It is Rose Agent
  itself (`dev.pixel.voice`), serving `workspace_compose` (kinds `prompt`,
  `arrange`, `regenerate`, `undo`) and `capabilities` like the customization
  tools. `capabilities` reports the contract only once the native page and a
  composer are on the phone.
- **The provider's result is §4.2 exactly.** It carries `schemaVersion: 1` and
  echoes `requestId`. `revision` is the layout store's monotonically increasing
  revision. `model_unavailable` reads as `unavailable`. Any other
  `rose-layout-v1` failure reads as `rejected`, with the failure token as
  `reason`.
- **Ids (opaque to App Core):** `app:<package>/<activity>`,
  `launch:<package>/<activity>`, `sc:<package>/<shortcut id>`,
  `intent:<template>` and `task:<template id>`. After OS Product's decision D1
  they also include `n:<key>` and `nact:<key>/<index>`.
- **Rose tasks:** registry kind `agent_task`, id `task:<template id>`. They
  are allowed only in a `button` row or as a `card` action, and their goal
  never enters the model.
- **Layout format:** OS Product dropped its own, so `rose-layout-v1` is the
  one format.

## 4A. Codex generator (implemented in source; enabled nowhere)

The builder takes an injected `generate(source, kind, request, cancelled)`. The
production generator will be a Codex thread opened through the existing
producer bridge (`codex-voice-bridge.mjs` → `codex.mjs`). There is no second
app-server client.

- **Bridge purpose `app_build`.** No dynamic tools. `cwd` is the project's
  absolute `source` path. The thread is `ephemeral`, uses `approvalPolicy:
  never` and `sandbox: workspace-write`, with network access off. Web search,
  apps, MCP servers, memories, hooks, plugins, multi-agent and image tools are
  disabled and re-verified after thread start, the same way the
  `public_research` purpose is today. Model and tier follow the producer's
  Terra Fast checks.
- **Instructions.** Write only under the skeleton (Java at the 8 source level,
  android.jar APIs, aapt v1 resources). Never touch the package, version, SDK
  levels or signing. On a revise, keep existing storage keys, preference names
  and database schemas, adding migrations instead of clearing data. Report
  honestly what was not possible.
- **Turns and cancel.** One turn per job, bounded by the build timeout. Cancel
  and shutdown use the bridge's existing `interrupt` op. The builder then
  resets the source as it does today.
- **Read isolation is not provided by Codex.** `workspace-write` limits where
  Codex may write, not what it may read. Keeping the generator out of
  `ROSE_CONFIG_DIR` (pairing keys, TLS, provider tokens, the project signing
  keys) needs the generator to run as a separate OS user, or in a container
  that only mounts the project source. That is blocker 3 below: it belongs to
  the Server Worker, and nothing here claims it.
- **Shared code.** `codex.mjs` also carries the research and character-life
  isolation checks. The `app_build` purpose sits beside them; their launch
  arguments, thread parameters and turns are unchanged, and a test pins that.

**Implemented** (source only):

- `workspace/rose-agent/server/app-build.mjs`: the fixed policy, the thread
  config and the per-turn sandbox. The config is the research isolation with web
  search off and the shell kept, plus `sandbox_workspace_write` network off and
  no temporary directories. Every turn carries
  `sandboxPolicy: {type: workspaceWrite, writableRoots: [], networkAccess:
  false, excludeTmpdirEnvVar: true, excludeSlashTmp: true}`. The cwd must be an
  absolute `…/source` path.
- The bridge `open` with `purpose: app_build`:
  - refuses any tool or other cwd;
  - ignores the caller's instructions;
  - stops the turn on any item other than messages, reasoning, plans, commands
    and file changes, or on more than 400 items;
  - closes only once the app-server's whole process group is gone. The
    app-server starts as its own group. The group is sent SIGTERM and then
    always SIGKILL, which reaches every member at once. Only after that does
    an empty group count, and on Linux members that are already zombies count
    as gone.
- `rose/app_builder/generator.py`: `CodexGenerator` is the Builder's `generate`.
  Each job gets one bridge process and one turn, bounded by `timeout_s` and
  stopped on cancel. It returns only after the bridge confirms that process
  group stopped; an unconfirmed stop fails the job. Its `command` argument is
  where the Server Worker's separate-user launch goes.
- A process that leaves the group (`setsid`, a double fork) is not covered
  by that stop. So the Builder never reads the live source after the writer:
  - it copies the source into a private snapshot under `jobs/<id>/`, outside
    the writer's writable root. Each directory is opened without following
    links, and each file is read through one non-blocking descriptor that is
    checked as a single-link regular file with owner access;
  - it refuses links, special files, more than 5000 entries, nesting deeper
    than 32 or more than 64 MB;
  - it never opens the live source by path, since something left running
    could plant a FIFO or a link to an endless device there;
  - it checks, records (git with the snapshot as work tree) and builds only
    that snapshot.

  Something left running can still change `source`, but the next job resets
  it to the delivered revision. In a test, an escaped writer adds `debuggable`
  and a broken Java file after the snapshot and before the build reads
  anything. The job is ready, and neither the delivered revision nor the APK
  contains them. With the snapshot replaced by the live source, the same test
  fails.
- Writer output gets no trust: the snapshot's own refusals, then the
  Builder's `template.check` and build checks on the snapshot, all still
  apply. A test has the
  fake writer add `debuggable`, and the job fails with nothing delivered.
- A version's `notes` (§3.3) carry the writer's own summary of what the app
  does and what it could not do. It is untrusted display text, at most 1200
  characters. When the writer gives none, `notes` falls back to the request.

Proof levels:

- **Container:** `app-build.test.mjs` (fake app-server: launch arguments,
  thread and turn parameters, refusals, bridge boundary) and
  `rose/tests/test_app_generator.py` (fake client; snapshot refusals; real
  bridge; Builder with the real bridge, compiled and signed with throwaway
  keys, including a writer that escapes the process group). The group stop is
  tested with a member that ignores SIGTERM, and with one that never goes away.
- **Assumed, not verified:** the app-server field names `sandbox:
  workspace-write`, the turn's `sandboxPolicy` and the
  `sandbox_workspace_write.*` config keys come from the Codex app-server
  contract. No Codex binary exists in this container. Whether a real
  `codex-cli` 0.167.x honours them (writes outside the project and network
  refused) needs a live check by the Server Worker before any enabling.
- **Not done:** any real model turn, read isolation (blocker 3) and any host
  wiring.

## 5. Blockers (exact) and owners


0. **Harm screening quality (decision: Chief or Nathan).** The phrase-list
   screen (§3.6) catches only some phrasings of the harm it names. Review of
   1809f85b confirmed two problems. Rewordings of an imitated sign-in pass,
   and bare security words refuse education requests. App Core has stopped
   hand-extending harm phrase lists. A dependable second gate needs a
   decision on its form: a moderation model, an owner-confirmed review step,
   or narrower harm rules owned by whoever owns content policy. Until then the
   builder must stay disabled, which it is. Proposal: §3.7.
1. **Signing identity for generated apps (decision: Nathan).** Choose:
   - per-project keys (recommended, because sharing one key makes every
     generated app the same signer) or one shared generated-apps key;
   - storage: owner-only files under `ROSE_CONFIG_DIR` on the build host, or a
     managed secret store;
   - backup: losing a project key makes in-place updates impossible, so the
     owner must uninstall and loses data.

   Creating or storing keys is not authorized by this plan. Source refuses to
   create keys unless the host owner explicitly enables it
   (`toolchain.Tools(key_creation=True)`, off by default). Once a project's
   signer is bound, a missing key is never silently replaced.
2. **Build host (decision: Nathan, operated by the Local RoseOS Server Worker).**
   The pinned toolchain is Linux-only and the Mac's toolchain is unpinned.
   Either the Server Worker pins and hands back a macOS toolchain (build-tools
   35.0.0 and JDK 21 with recorded hashes), or a separate builder is
   authorized. A separate builder is a provisioning decision; no new paid
   infrastructure is assumed.
3. **Build isolation on that host (Server Worker).** Source now asks Codex for
   `workspace-write` scoped to the project with network off (§4A). That part is
   unverified against a real Codex binary. Read access to `ROSE_CONFIG_DIR`
   (pairing, TLS, provider tokens, project keys) is not blocked by Codex at
   all. The writer needs a separate OS user or a container that mounts only
   the project source, launched through `CodexGenerator(command=...)`.
   Enabling it is a security-relevant runtime change, applied only by the
   Server Worker when authorized.
4. **Runtime (Nathan).** The Rose server is stopped at Nathan's request. No
   endpoint, build or install can be exercised until he asks the Server Worker
   to restart it and a deployment is authorized.
5. **Device proof (device owner handback).** Install, revise and data-retaining
   update must be shown on a stock-Android phone and a RoseOS phone,
   separately. No harness exists for packages other than Rose Agent.
6. **Level 2 (Chariot owner, OS Core).** There is no Chariot code in this
   repository. Hosting, sleep/wake, storage and deploy tools belong to the
   Chariot owner. The one-time code + PIN/passphrase protocol belongs to OS
   Core, including whether the PIN verifies server-side or unlocks a device
   credential.
