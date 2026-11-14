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
   fixed skeleton: Java 8 source level, android.jar APIs plus declared
   pure-Java JAR libraries (see **Libraries** below), aapt v1 resources,
   min API 26 and target 35. It cannot change the package,
   versionCode or signing. Enabling this thread on the Mac is a
   security-relevant runtime change; see §5. The generator is injected. It is
   implemented in source (§4A) but disabled: nothing configures or runs it.
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

### 2.1 Libraries (pinned pure-Java JARs; source only, enabled nowhere)

The writer never ships a binary and never touches the network; that keeps the
writer isolated, and it is not the product's limit. An app *declares* the
libraries it needs in `dependencies.json`:

```json
{"schemaVersion": 1, "dependencies": ["group:artifact:version"]}
```

The declaration allows at most 16 coordinates and no module twice.
`rose/app_builder/dependencies.py` resolves each coordinate through an
injected, offline `Resolver`. `RecordedStore` is one: a host-owned directory
holding `index.json` (coordinate to sha256) and `<sha256>.jar` files. Nothing
in source downloads or populates a store. Then, per job:

1. **Copy.** Each artifact is copied into the job's own directory. A link is
   not followed, only a regular file is accepted, and size is bounded at
   16 MB a JAR and 64 MB a job.
2. **Digest.** The copy's sha256 must equal the digest the resolver recorded.
   For a coordinate any delivered version of the project used, it must also
   equal that version's lock, so a library never changes under an app
   without its coordinate changing, even if it was dropped and declared
   again.
3. **Read as a plain JVM JAR.** These bounds apply:
   - at most 20,000 entries, 64 MB unpacked, and 8 MB an entry;
   - no unsafe names (absolute, `..`, backslash, empty segments), no
     duplicate entries and no encryption;
   - class files only, Java 1.1 to 21; inert `META-INF` metadata (the
     manifest, license, notice, Maven and signature files) is allowed, and a
     `module-info.class` that really is module metadata is skipped;
   - each class is checked under the name its bytes define (`this_class`,
     read from the constant pool), which is the name `javac` and `d8` use.
     That name must equal its entry's path;
   - no class in `java/`, `android/` or `dalvik/`, none that `android.jar`
     defines, none in the app's own package, and none that two libraries both
     define;
   - anything the archive reader can't read (corrupt or unsupported
     compression, a malformed class) is a refused library, never a builder
     failure.
4. **Rewrite.** A job-local JAR is written holding exactly those class files.
   `javac` compiles against it, and `d8` first converts the libraries alone,
   so a library `d8` can't convert, or `javac` can't read, is a `dependency`
   failure and not blamed on the app's code. `d8` then adds them to the app.
   The app's own classes are still checked to be in its package. The builder
   checks a library's structure and names, not whether the library is
   correct. A library that contradicts itself (for example, generic
   signatures that disagree with its real method descriptors) can still show
   up as an app compile error or a runtime linkage error inside that app.
5. **Lock.** The delivered version records each library's coordinate, sha256
   and size (`Ledger.dependencies`), in the same transaction as the version.
   It is the next revision's lock.

With no resolver configured, an app that declares libraries fails as
`dependency`, exactly as an undeclared import does. The writer's policy says
to declare libraries rather than add them (`app-build.mjs`).
`CodexGenerator(libraries=...)` lists what the store provides in each turn.
No host configuration wires a store yet.

**Not supported yet.** These paths are interim, not product scope. Each is an
honest `dependency` failure:
- AAR packages and Android resources from libraries, which need a pinned
  aapt2;
- Kotlin sources (no pinned `kotlinc`) and Gradle;
- native code, Java resources inside a JAR, multi-release JARs and
  ServiceLoader files;
- transitive resolution: each library must be declared.

**Proof level.** `rose/tests/test_app_dependencies.py` builds only tiny fake
libraries the test makes itself: hand-built JARs for the checks, and one
compiled by the pinned JDK for the real build. It shows:
- an app that imports the fake library is built, signed and has its digest
  recorded, and the library's class is in its dex;
- with no resolver, or no declaration, the build is a `dependency` failure;
- a revision reuses the lock, and changed bytes under the same coordinate are
  refused, also after the library was dropped and declared again;
- a class relabelled into the app's package or the platform under a harmless
  entry name is refused, and a library `d8` can't convert is the library's
  failure;
- tampering, namespace and archive bounds are each refused.

This is compilation and signature verification only. No real library, store,
generated app or device was used.

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
| `POST /builder/v1/projects` | `{schemaVersion, clientRequestId, description}` | `{projectId, jobId, notes}` (`notes` is always `[]`, kept for client compatibility) |
| `POST /builder/v1/projects/<projectId>/revisions` | `{schemaVersion, clientRequestId, request}` | `{jobId, notes}` |
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
  - `dependency`: a library or language the builder can't provide. That
    covers Kotlin, Gradle, an AAR, a JAR or AAR file in the source, an
    undeclared or unavailable library, a library that fails its checks, or
    an import of a missing package.
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

minSdk 26, targetSdk 35, framework APIs, plus declared pure-Java JAR libraries
from the builder's offline library store (**Libraries**, §2). There is no native
code, so builds are ABI-independent. Anything needing AndroidX, Kotlin, Compose,
an AAR or a library the store doesn't have fails with category `dependency` and
an honest summary. It is never silently dropped.

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

### 3.6 No rules on what is asked for or built

Nathan, 2026-11-14: "we don't want to impose any rules on what people can ask
to build or be built". The builder doesn't screen request text, and it puts no
limit on what a generated app declares: permissions, services, roles. Android
and the phone's owner decide what an installed app is granted. Requests are
refused only for technical reasons:

- **API v1 request checks:** schema, field sizes, unknown fields, and
  clientRequestId idempotency. The same ID with other text is refused. Every
  route needs a paired device's signed request.
- **Identity and update integrity**, on the builder's private source snapshot
  and on the built APK:
  - the package is fixed;
  - versionCode and versionName are set by the builder;
  - the manifest can't set a sharedUserId, split, key-set, original-package,
    adopt-permissions, instrumentation or uses-sdk;
  - the manifest can't use namespaced elements, which those checks couldn't
    see;
  - the app can't be debuggable;
  - a permission the app declares (`<permission>`, `<permission-tree>`) must
    be named under its own package. That keeps one project from defining,
    and so controlling access to, another project's or another app's
    permissions. It is isolation between projects and puts no limit on what
    the app may request or do;
  - Java must be in the app's package;
  - no git metadata, links or special files.
- **Toolchain facts:** Java at source level 8, against the framework and any
  declared pure-Java JAR libraries the offline store provides. There's no
  Gradle, Kotlin or native toolchain. Any other library is a `dependency`
  failure.
- **Isolation:** each project has its own source, git history and signing key.
  The writer runs in its own process group, and the build reads only a
  snapshot.

Removed at Nathan's direction, 2026-11-14:
- the request text screen (`screening.py`, 82830ea0 to 8c72bfc5);
- the screening policy chain and its accept-nothing default (`policy.py`,
  c8bf8a6c and 64aaabb9);
- the manifest permission allowlist (82830ea0).

Those were App Core additions. No plan or owner requirement asked for them.
Proof level: container tests (`rose/tests/test_app_capabilities.py`,
`test_builder_routes.py`, `test_app_builder.py`). They show:

- requests that used to be refused are accepted and run;
- an app declaring SMS, overlay, background location, accessibility, VPN and
  device-admin services is built and signed;
- the identity checks still refuse.

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
  `uncertain`. Rose Agent's composer provider answers within about 8 s: a
  composition still running after its 7 s wait comes back as `uncertain`
  (`still_composing`), and its provider's own 8 s limit also answers
  `uncertain`. Neither the 20 s wait nor this 25 s one is reached for a
  composition alone. The composition can still commit afterwards; the page
  shows it when reached, and v1 does not fetch it later.
- A busy or transient reply to the capabilities check (`routine_busy`,
  `running`, `expired`, `error`) is reported as busy. Nothing was sent, so
  nothing changed.
- Rose Agent relays `app_command` only to same-signer or allowlisted packages,
  so the composer provider must be one of those.

### 4.3 Agent tasks chosen on the page (workspace task handoff v1)

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
  - `accepted` and `duplicate` always carry `taskId: "wt-<16 hex>"`. The
    server mints it at acceptance and stores it durably; a duplicate returns
    the first one. `refused` and `unavailable` carry none, and nothing is
    recorded for them.
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
  `workspace_tasks.connect(conductor, tasks)`. A confirmed task becomes the
  owner's own request in the conversation, marked as confirmed on the
  workspace page, so it runs with exactly the conversation's tools and policy.
  While Rose is replying, it is `unavailable` (503) rather than cutting her
  off. The conversation turn is bound to the page task's `taskId`. The
  conductor reports how that turn ends, and the phone tasks it starts or
  updates (`phone_task`, `update_task`).
- **Page-task state v1 (agreed with OS Product's #799 receipt):**

  ```
  {schemaVersion: 1, taskId, requestId, actionId, state, detail,
   phoneTasks: [{id, status}], createdAt, updatedAt}
  ```

  Times are epoch seconds. `detail` is owner-facing text of at most 200
  characters, possibly empty. `phoneTasks` holds the Mac's own task ids. An id
  is a non-negative JSON integer (the Mac's ids), or else a string of 1 to 128
  characters; clients accept both. The
  `state` values:
  - `running`: the turn hasn't ended, or a phone task it started is running.
  - `done`: the turn completed and every phone task it started finished done,
    or it started none.
  - `failed`: the turn failed, or any of its phone tasks failed.
  - `cancelled`: the turn was interrupted, and nothing failed. That covers a
    turn superseded by a newer request or by the owner speaking, a voice hold,
    a conversation reset, a shutdown, or a cancelled phone task.
  - `uncertain`: the server restarted before an end was recorded.

  Phone work is counted for the turn that asked for it. A `phone_task` or
  `update_task` call is bound to its own turn's page task when the call
  arrives. While the call is in flight, the page task stays `running`, even if
  its turn ends or is superseded first. The phone task it starts is attached
  to that page task, never to a later one.

  Every state except `running` is final, so a late report can't turn
  `uncertain` into `done`.

  OS Product's amendments, agreed:
  - **A.** `updatedAt` never decreases for a `taskId`.
  - **B.** `phoneTasks` shows at most the 8 newest entries, though every one
    still counts toward `state`. `detail` is at most 200 characters, and an
    `id` at most 128.
  - **C.** A page task is visible only to the device that asked for it, in the
    GET, the live events and the reconnect replay. Another device's `taskId`
    is 404 `unknown`.
  - **D.** A reply lost after acceptance is matched by `requestId` from live
    events or the reconnect replay. The replay holds only the 20 most recently
    updated tasks, so a task can fall out of it before the phone reconnects.
    The read by `requestId` below covers that case (agreed with OS Product,
    2026-11-14).
- **Delivery and recovery:**
  - **Live:** `{kind: "workspace_task", t, task}` on `/ws/events` at every
    change.
  - **Reconnect:** after the existing task snapshots, one `workspace_task`
    event for each of this device's 20 most recent page tasks. A change
    already included in that snapshot is not sent again after it, so a card
    never steps back to an older state. Changes go out in the order they were
    made.
  - **Authoritative read:** signed `GET /workspace/v1/tasks/<taskId>` returns
    200 with the state, or 404 `{schemaVersion: 1, status: "unknown"}`.
  - **Read by `requestId`**, for a reply the phone lost: signed
    `GET /workspace/v1/tasks?requestId=<percent-encoded>`, where the signature
    covers the query. The value is decoded as RFC 3986 percent-encoding, so a
    `+` stays a `+` and is not read as a space, as it would be in a form.
    - 200: the same state as the read by `taskId`, `taskId` included. It is
      looked up only among this device's own accepted requests.
    - 404 `unknown`: the request never arrived, came back `unavailable`, or is
      another device's.
    - 400 `refused`: there isn't exactly one `requestId` of 1 to 64
      characters, or there is another parameter.

    It is read only: it never starts or changes anything. A re-POST is not a
    read, because a request that never arrived would start late. The phone
    keeps a 404 as uncertain.

  The card follows its `taskId` through these, not through the local phone
  stream's `current.id`. The client side is OS Product's (#800).
- **Proof levels.** Source tests drive the real conductor with the simulated
  Codex and phone fixtures. They cover:
  - a turn that starts phone work, then a completed phone task, giving `done`;
  - a reply-only turn, giving `done`;
  - a superseded turn, a voice hold and a reset, each giving `cancelled`;
  - phone work whose call returns after its turn ended or was superseded,
    which stays with its own page task;
  - a stale queued change after the reconnect snapshot, which is not sent;
  - a phone task that fails before it is attached, giving `failed`;
  - a page task accepted during a reset, which runs on the new conversation;
  - a failed turn, giving `failed`;
  - a restart, giving `uncertain`;
  - a stable `taskId` across duplicates and restarts;
  - the signed GET route.

  Mutation checks each make them fail: no attach, ignoring phone tasks, no
  restart recovery, no supersede, a final state that can change, a null
  `taskId`, no in-flight hold, attaching to the session's current page task
  instead of the call's own, a hold that doesn't end the page task, no
  reconnect filter, no rollback after a failed insert, a phone status read
  outside the store's lock, a turn kept on a conversation that a reset
  replaced, and a hold not released when its call never starts. There is no live end-to-end proof: no phone, no real Codex turn,
  and the server is not enabled or running.

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

## 6. Standalone builder host (source only; not configured, deployed or enabled)

`rose/app_builder/host.py` runs the builder on its own machine, without the
Rose conversation server. It reads one strict JSON config file:

```
{schemaVersion: 1, root, remoteDir, name, port, adminPort, hosts[], sdk,
 javaHome?, buildTimeoutS?, keyCreation? (default false),
 writer?: {argv[], timeoutS?}, backup?: {argv[], timeoutS?}}
```

- **Signed listener.** It serves `/pair`, `/hello` and the builder API v1
  routes only. There are no voice or phone websockets
  (`build_remote_app(..., voice=False)`), and `/hello` reports
  `phone: "none"`. The builder client pairs with it as its own device.
- **Pairing.** Codes are minted only on the host, through a loopback-only
  admin listener (`/api/pair/*`, the existing `add_pairing_api`).
- **Writer.** `CodexGenerator` launched with `writer.argv`. On a host, that
  would be a command that runs the bridge as a separate OS user. With no
  writer configured, every job fails as "No app writer is set up on this
  builder host".
- **Signing-key backup before first delivery.**
  - After a project's first signed build, and before its signer is bound or
    anything is delivered, `Builder(backup=…)` must return a receipt. The
    receipt is recorded in the `key_backups` table.
  - A missing backup, a failed backup, a timeout or a bad receipt fails the
    job as `signing`: nothing is delivered and the signer stays unbound. A
    failure other than a missing backup can be retried.
  - Later builds of the project need no new backup.
  - `CommandBackup` runs `backup.argv + [keysDir, projectId, package]` with a
    scrubbed environment.
    - It runs in its own session, with an empty HOME and working directory
      outside the key directory, removed afterwards.
    - Its whole process group is killed when it finishes, fails or times
      out, so nothing it started keeps running with access to the key.
    - The receipt is the first line of its output, with tabs as spaces, at
      most 200 printable characters. The builder applies the same rule
      (`host.valid_receipt`).
    - It never logs the rest of the output or stderr.
  - Which backup tool and storage to use is not chosen in source.
- **Defaults are off.** Key creation is off unless the config enables it, and
  no writer and no backup are configured.
- **Config checks.**
  - Both directories are created 0700. They must not be symbolic links, and
    after resolving they must be separate.
  - `hosts` must pass the same Tailscale rule pairing uses
    (`configured_hosts`).
- **Serving.** A listener that fails is re-raised, so `main` exits non-zero.
  SIGINT, SIGTERM and SIGHUP stop both servers, a second signal forces exit,
  and device state is flushed on the way out.

Proof level: container tests only (`rose/tests/test_app_host.py`). They cover:

- config refusals, and `main` refusing a bad file without serving;
- the wired host: pairing through the admin app, signed `/hello`, the
  websockets absent, unsigned requests refused, and a signed create;
- no writer configured, and starting and stopping the worker;
- `CommandBackup` arguments, environment, failures and timeout;
- with the pinned SDK: no backup configured, a failed backup blocking
  delivery until a retry succeeds, the backup taken once per project, and bad
  receipts.

Each of these breakages makes the tests fail: skipping the backup, mounting
the websockets, defaulting key creation on, and accepting an empty receipt.
Nothing was bound to a public port, configured or deployed. No key left the
test, and no model was called.

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
