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
| `POST /builder/v1/projects` | `{schemaVersion, clientRequestId, description}` | `{projectId, jobId}` |
| `POST /builder/v1/projects/<projectId>/revisions` | `{schemaVersion, clientRequestId, request}` | `{jobId}` |
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
    app-server starts as its own group, which is SIGTERMed and then SIGKILLed.
    On Linux, members that are already zombies count as gone.
- `rose/app_builder/generator.py`: `CodexGenerator` is the Builder's `generate`.
  Each job gets one bridge process and one turn, bounded by `timeout_s` and
  stopped on cancel. It returns only after the bridge confirms that process
  group stopped; an unconfirmed stop fails the job. Its `command` argument is
  where the Server Worker's separate-user launch goes.
- A process that leaves the group (`setsid`, a double fork) is not covered
  by that stop. So the Builder never reads the live source after the writer:
  - it copies the source into a private snapshot under `jobs/<id>/`, outside
    the writer's writable root, reading each file through one descriptor
    that is checked as a single-link regular file;
  - it refuses links, special files, more than 5000 entries or more than
    64 MB;
  - it checks, records (git with the snapshot as work tree) and builds only
    that snapshot.

  Something left running can still change `source`, but the next job resets
  it to the delivered revision. A test has an escaped writer add
  `debuggable` after the stop: the delivered revision and the APK don't have
  it.
- Writer output gets no trust: the Builder's `template.check` and build checks
  still apply, to the source as written and to the snapshot. A test has the
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
