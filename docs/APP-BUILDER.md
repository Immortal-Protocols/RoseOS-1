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

Proof levels:

- **Container:** create, revise and a refused key change were built, signed and
  verified with throwaway keys. CI repeats this with a freshly fetched pinned
  SDK.
- **Container:** the signed routes, from an enrolled device's signed request
  to a signed artifact download, are covered by tests against the real listener
  app. Pairing itself (`POST /pair`) is covered by the existing remote-link
  test, not by these.
- **Not done:** enabling the builder on a host, the Codex generator, and
  Home/App's install client against the real API. Nothing has been installed.

Data survives an update because the package, signer and higher versionCode keep
Android's update path; the revise prompt also requires keeping storage keys
and schema migrations. Those requirements still have to be written into the
generator's instructions, which are not written yet. Acceptance still needs a seeded-data → update →
read-back test on a stock-Android phone and a RoseOS phone, run separately.

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

### 4.3 Agent tasks chosen on the page

Direct actions (launch an app, open a deep link, run a notification action)
run on the phone, never through the server. An agent task the owner explicitly
selects is sent as the existing typed task request, and its progress uses the
existing task card v1 and Stop contract 1. The composer cannot invent a
capability: it only references the action ids and apps it gathered on the
phone.

## 4A. Codex generator design (next; not implemented)

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
  isolation checks. The `app_build` purpose will be added beside them without
  changing their behaviour, as its own reviewed change.

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
3. **Build isolation on that host (Server Worker).** The per-job Codex thread
   needs `workspace-write` scoped to the project and no access to
   `ROSE_CONFIG_DIR` (pairing, TLS, provider tokens). Enabling it is a
   security-relevant runtime change, applied only by the Server Worker when
   authorized.
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
