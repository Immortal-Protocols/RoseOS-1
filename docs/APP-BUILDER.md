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
   fixed skeleton: Java 17 source level (§2.2), android.jar APIs plus
   declared pure-Java JAR libraries (§2.1), aapt v1 resources,
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
- **Template:** the skeleton plus its refusals: package,
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
separately, including a schema-changing revision (§5, blocker 5).

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
- AAR packages and Android resources from libraries. They need an aapt2
  resource pipeline. `aapt2` 2.19 is already in the hash-pinned build-tools
  35.0.0, but the builder doesn't use it yet;
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

### 2.2 Java language level (Java 17; source only, enabled nowhere)

Generated apps are Java at the **17** source level: records, sealed types,
switch expressions, text blocks, `var` and `instanceof` patterns. Preview
features and Java 21 language features, such as pattern `switch`, are a
`compile` failure. Only the pinned platform 35 SDK and the pinned JDK are
used, the way the Android Gradle plugin does it:

1. **System image.** `toolchain.system_image` makes a Java system image from
   platform 35's `core-for-system-modules.jar`, Android's core library as the
   SDK ships it for compilers. It generates a `module-info` that exports
   every package, then runs `jmod create` and `jlink`.
2. **Compile.** `javac` compiles with `--system <image> -source 17 -target 17
   -XDstringConcat=inline`, with `android.jar` and the declared libraries
   (§2.1) on the classpath. The app therefore sees exactly the `java.*` APIs
   Android has. String concatenation is compiled inline, because Android has
   no `StringConcatFactory`.
3. **Desugar.** `d8 --min-api 26` desugars records, lambdas and the like. It
   also backports some newer library methods, for example `List.of`,
   `Map.of`, `String.repeat`, `isBlank` and `strip`. It doesn't backport
   others: `String.formatted`, `stripIndent` and `Stream.toList` are API 34
   and stay direct calls (a test pins this).

A JDK package Android doesn't have, such as `java.lang.management` or
`javax.swing`, is an honest `compile` failure: "uses Java APIs Android does
not provide". It is never reported as a missing library. A library may not
put classes in a package the platform has (`org.json`, `javax.crypto`, ...).
Those packages belong to the core library that `javac` compiles against, so
such a class could never be seen.

**The image cache.** The image is cached under the builder's own `toolchain/`
directory. No link is followed there: the cache, each entry, its manifest
and its image must be plain directories and files. The cache key is the recipe version, the core jar's sha256 and the
JDK's version and platform, read from its `release` file. So a changed JDK or
platform gets a new image, and a stale one is never used. Each use recomputes
the image's tree digest and compares it with the one recorded when it was
made. A missing, partial, changed or linked image, or any other entry in its
place, is removed (a link is unlinked, never followed) and made again. If two
builders make it at once, the second uses the first's after checking it. The build is deterministic, so the same inputs give byte-identical
images. This guards against corruption and staleness, not against someone
who can already write the builder's own directory, which also holds the
signing keys. Making the image is a local build step, not runtime
activation.

**Unresolved: API compatibility with minSdk 26.** `javac` compiles against
API 35, and nothing checks that the app guards calls to APIs newer than 26.
`d8` backports only some library methods (`String.formatted`, for one, isn't
backported), and no framework API. An app that calls a newer API unguarded
compiles and is signed, but can fail at runtime on an older phone. The
writer is told to check `Build.VERSION.SDK_INT`, but that is guidance, not
enforcement. The advisory API level report (§2.3) now detects such
references offline with the pinned `data/api-versions.xml`. It never blocks a
build, and it can't see across methods, so compatibility stays unresolved.
An API 26 platform jar or Android lint would be other ways, and both would
need acquisition. Compiling and desugaring is not device proof.

**Proof level.** `rose/tests/test_app_java17.py` covers:
- a fixture using records, sealed types, a switch expression, a text block,
  `var`, an `instanceof` pattern and a lambda builds and signs, and its dex
  (inspected with `dexdump`) has no `invoke-custom`;
- Java 8-style sources still build, as do all earlier real-build tests;
- `java.lang.management`, `javax.swing` and a Java 21 pattern `switch` are
  `compile` failures, and an API 34 `String.formatted` stays in the dex;
- the cache is reused, and remade when it is tampered with, partial, missing
  its manifest, or a link at the image, its entry or its manifest. A broken
  link in its place heals. It survives two builders at once, a corrupt core
  jar is a `toolchain` failure, and the recipe and core jar each give their
  own image.

Mutation checks each make a test fail: no inline concatenation, JDK system
modules instead of Android's, the Java 8 level, an unvalidated cache, a key
without the recipe and core jar, a JDK API reported as a library, and links
trusted in the image.

### 2.3 API level report (advisory; source only, enabled nowhere)

After each successful build, `rose/app_builder/apilevel.py` writes an
**advisory** report of platform API references above minSdk 26 that it
finds. It goes into the job log as one summary line, and the full report is
recorded with the delivered version (`Ledger.api_report`). The library
serves each version a summary of it, `apiReport` (§3.3). It never fails or
changes a build, and adds no rule on what an app may do. It does take time:
`dexdump` runs within the build's tool timeout, and the analysis has a 60 s
budget.

**What it reads.** It reads the final dex, after `d8`, through the pinned
`dexdump -d`. Backported methods such as `List.of` and `String.repeat` are
therefore no longer platform references, and constants such as
`VERSION_CODES.*` are inlined. Each framework and `java.*` reference is
looked up in the pinned platform 35 `data/api-versions.xml`, with no
acquisition. That file gives every class, method and field the level it was
added, and its supertypes with the level each was added. It covers:
- invokes, field accesses and type uses;
- `catch` handler types, each judged over its whole try range: it is
  `guarded` only if every instruction it covers is;
- the app's own classes' supertypes and interfaces, including the synthetic
  classes `d8` makes for lambdas.

A call on the app's own class (for example `setShowWhenLocked` on
`MainActivity`) is resolved through the app's and the platform's inheritance.
A member the app's own class declares, such as an override it calls, is the
app's, not the platform's. An inherited member is available from the
earliest level it can be reached through the class it's called on: never
before that class, and never before the supertype that provides it was
added.

**Statuses.** Every reference above 26 gets one:
- `guarded`: in its method, a `Build.VERSION.SDK_INT` comparison with a
  constant dominates it on the branch that guarantees the API's level. This
  uses a per-method control-flow graph, with exception edges, and respects
  branch direction. An inverted guard (`SDK_INT < 34`) or a guard that is too
  low (`>= 30` for a level-34 API) is not a guard.
- `not_guarded_here`: the method was analysed completely and nothing in it
  protects the reference. A caller may still guard it: the analysis doesn't
  look across methods.
- `unverified`: the analysis can't tell, and the reference is never treated
  as safe. The causes are:
  - a branch it didn't recognise, in a method that calls the app's own code
    or reads an app field. A helper's result or a flag may decide it, through
    any number of moves, blocks or operations;
  - an `SDK_INT` value it doesn't follow, for example one kept in a variable
    and compared later;
  - an `SDK_INT` result that may reach a branch through a variable;
  - unreachable code;
  - an extension-SDK API, whose availability `SDK_INT` alone doesn't decide;
  - a class's own supertype, which only never loading the class protects;
  - a method it could not read completely: a `switch` (`dexdump` doesn't
    print switch targets), a code line or branch target it couldn't parse,
    instructions that don't exactly cover the method's code, or more than
    20,000 instructions.

Nothing is dropped silently:
- a platform reference the pinned database can't place (a class or member
  it doesn't list, such as some extension-SDK APIs, `sun.misc.Unsafe` or
  `javax.naming`) is reported `unverified`, "not in the pinned API
  database";
- so is a supertype the database doesn't know;
- so is an interface method, the app's or the platform's, that a class
  implements only through an inherited platform method above 26. Calls
  through the interface fail below that level. For example, a class
  extending `MediaMetadataRetriever` as an `AutoCloseable` gets `close()`
  only from 29.

A member the database leaves off a class it does know is assumed available
from that class's own level. These are mostly methods inherited from a
hidden superclass, such as `StringBuilder.setLength` and `substring`. They
are named in the report's `unlisted` field (up to 50, with a count) rather
than as findings, so ordinary code doesn't fill the findings cap.

`invoke-polymorphic` references (`VarHandle`, `MethodHandle`) are read like
any call. Only the platform's own `android/os/Build$VERSION.SDK_INT` counts
as a guard, never a look-alike field in the app. The dump is split on newlines
only, so characters `dexdump` prints raw inside strings (U+2028, U+0085 and
the like) can't forge dump lines.

**Overall status.** The analysis fails closed. A method it couldn't read
completely, or methods left unanalysed when the 60 s budget runs out (it is
checked before each method), make the report `incomplete` with a reason. If the database or `dexdump` is
missing, or the analysis fails, the report is `unavailable` with a reason
and no counts, so it never reads as clean. The build continues either way.

**Limits.** This is detection, not device compatibility proof, and an empty
list is no compatibility claim. These are not covered:
- reflection, and anything reached without a dex reference;
- a platform object passed as an interface its class only implements from a
  later level, which needs type flow;
- behaviour changes, permissions, OEM differences and hidden APIs;
- guards in other methods, including library `Api*Impl` classes, and
  `@RequiresApi`, which `d8` drops;
- libraries' own code is reported, tagged `origin: library`, under the same
  rules.

Findings are capped at 200 per report, and the number left out is counted.

**Proof level.** `rose/tests/test_app_apilevel.py` builds one fixture
through the real pipeline (compiled, never run) and asserts each row:
- correct `if`, early-return and `VERSION_CODES` guards are `guarded`;
- inverted and too-low guards are `not_guarded_here`;
- a helper guard is `unverified`;
- inherited members, fields and new classes are found;
- constants, backports and reflection leave no reference;
- a newer `catch` type is found, and a lambda's newer interface is an
  `unverified` supertype;
- an extension API and a `switch` method are `unverified`, and the report is
  `incomplete`;
- `flag || SDK_INT >= 34`, `!= 33`, and code reached through a caught throw
  below 34 are never `guarded`;
- an int helper, an app field flag, a boolean local and a reused `SDK_INT`
  value are `unverified`;
- a call to the app's own override isn't a platform reference.

Unit tests cover these, each `incomplete`, never clean, or never
`guarded`:
- `goto/32` edges and wide addresses;
- unreadable lines and branch targets;
- strings that would forge lines, and listings that don't cover the code;
- a look-alike `SDK_INT`, and a helper's value used a block later;
- the size cap, and the time budget checked between methods;
- the inheritance levels.

The fixture also has a `catch` whose try range covers unguarded code. Unit
tests add an `invoke-polymorphic` reference, unplaced classes, members and
supertypes, and an app interface implemented through a newer platform
method.

It also covers a missing database and a crashing analysis, which give
`unavailable` while the build stays `ready`. Unit tests cover every
comparison form and constant side, and inheritance. Mutation checks each make
a test fail:
- branch direction or constant side ignored;
- no app or platform inheritance;
- a helper treated as unguarded or as a guard;
- a switch claimed as analysed;
- extension SDKs treated as plain;
- catch handlers or supertypes skipped;
- an unread `SDK_INT` use trusted;
- a crash failing the build;
- an empty dump reading as clean;
- unreadable targets ignored, `goto/32` not parsed, fixed-width addresses or
  unparsed lines ignored;
- no size cap or time budget;
- an inherited level that ignores the newer class, or takes the first path
  rather than the earliest;
- `SDK_INT` reuse, app-value branches or a variable-carried result ignored;
- the app's own override treated as platform;
- no exception edges;
- `splitlines` parsing, or no coverage check;
- a `catch` judged at its try start only;
- a look-alike `SDK_INT` accepted, or app values ignored;
- the budget checked only between classes;
- a guard memo that drops edge guards;
- the polymorphic comma kept;
- unplaced references or supertypes dropped;
- interface gaps ignored, platform interfaces skipped;
- app fields not known;
- unlisted members silently dropped, or flooding the findings.

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
apk: {sha256, size}, notes, apiReport}]}]`

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

**`apiReport`** summarises the version's advisory API level report (§2.3).
It was added after v1 was agreed and is optional: a client that doesn't know
it ignores it, as the client does with every unknown field. The server
sends it on every version. A client must still treat a missing field as
`none`.

| Field | Present | Meaning |
|---|---|---|
| `schemaVersion` | always | `1`, the version of this summary |
| `advisory` | always | `true` |
| `deviceProof` | always | `false` |
| `status` | always | one of the four statuses below |
| `note` | always | fixed text: advisory, detection, not device proof |
| `reason` | `incomplete`, `unavailable` | why; at most 300 characters. Display text, which can name the app's own classes |
| `minSdk` | `complete`, `incomplete` | the app's minSdk (26) |
| `counts` | `complete`, `incomplete` | `{notGuardedHere, unverified, guarded}`, over all findings, including those the full report did not keep |
| `highestLevel` | `complete`, `incomplete` | the highest API level a kept `notGuardedHere` or `unverified` finding names, or `null` |
| `truncated` | `complete`, `incomplete` | findings past the 200 the full report keeps |
| `unlistedCount` | `complete`, `incomplete` | members the pinned database doesn't list, assumed available from their class's level |

Statuses:
- `complete`: the analysis read every method.
- `incomplete`: the analysis ran but did not read everything, so `counts` can
  be zero while part of the app was never looked at.
- `unavailable`: no analysis result. Either the analysis did not run, or the
  recorded report can't be read or has a schema this server does not read.
- `none`: no report is recorded for the version. Either it was built before
  the report existed, or the build's report file could not be read.

`counts`, `highestLevel`, `truncated` and `unlistedCount` appear only for
`complete` and `incomplete`. No zero is ever sent for an analysis that did
not run.

`highestLevel` is a lower bound, never a ceiling. More can be needed by:
- a finding the full report did not keep;
- an `unverified` finding the database cannot place (a `null` level);
- a member the database doesn't list, which is placed at its class's level;
- a method left unanalysed.

Rules for a client:
- **No status is a pass.** That includes zero counts, `complete`, and a
  `highestLevel` at or below the phone's `SDK_INT`. A client never says
  "compatible", "safe" or "works on this phone" from this summary.
- The summary is treated as `unavailable`, never as clean, if it has any of
  these:
  - an unknown `status` or `schemaVersion`;
  - a field the table says is present for its status that is missing;
  - a field of the wrong type, or a negative count;
  - `advisory` other than `true`, or `deviceProof` other than `false`.

  Only the summary is refused, never the library.
- Anything shown says advisory and detection, not device proof. Showing it is
  optional.
- Nothing in it blocks or changes a build, an install or what may be built.
- `reason` is plain display text, never markup or a link.

The exact cases are in
`prototypes/voice-phone-demo/rose/tests/fixtures/api-report-summary-v1.json`:
- `cases`: each recorded report, with the `apiReport` the server sends for
  it;
- `clientUnreadable`: served values the server never sends, which a client
  must read as `unavailable`.

Server tests check every case, and also check that each summary the server
can send meets the rules above. A client can test its parsing against the
same file. This is the server and wire side only (App Core). The phone's
parsing and display are Home/App's, and are not implemented here.

**`share`** (schemaVersion 1; `app_builder/share.py`; source, not yet
served) tells the phone whether a version has a public download page it may
share.
- **Who talks to hosting.** The phone never contacts hosting and holds no
  Chariot credential. The builder's server reads the hosting metadata,
  `GET /apps/<hostedAppId>/releases/<versionCode>/metadata`, which is public,
  from the Chariot owner.
- **The hosted app id.** It comes from an explicit per-project mapping that
  owner tooling or the publisher records. It is never derived from
  `projectId`.
- **Links** are passed through exactly as hosting returned them: `release` as
  `versionUrl`, and `latest` (a `307` to the newest release) as `latestUrl`.
  The raw `download` link is never shared.

| Hosting's answer | `share` |
|---|---|
| no hosted app id recorded for the project (no lookup made) | `{schemaVersion: 1, status: "unpublished"}` |
| the builder's record for the version is incomplete (no signer yet, or a field of the wrong kind) | `unavailable`: "this version can't be checked against the hosted copy yet" |
| `200` whose `app_id`, `package_name`, `version_code`, `signer_sha256`, `sha256` and `size_bytes` all equal the builder's record, with plain absolute https `release` and `latest` links | `{schemaVersion: 1, status: "published", versionUrl, latestUrl}` |
| `200` that differs from the record in any of those fields | `unavailable`: "the hosted copy doesn't match this version" |
| `200` the server can't read (a link missing or not a plain https URL; any of the six fields missing, null or of the wrong kind) | `unavailable`: "the app's download host answered something this server can't read" |
| `404` | `unpublished` |
| `503` | `unavailable`: "the app's download storage is unavailable right now; try again in a minute". With a `Retry-After` over 90 s, it ends "try again later" instead |
| any other status, or no answer | `unavailable`: "the app's download host could not be reached" |

- **Caching.** A `200` stands for its `max-age`, at most 600 s, because
  hosting can't revoke a release yet. `404` and `503` are never kept. After a
  `503` the server waits its `Retry-After` (at most 1 hour) before asking that
  host again.
- **Identity fields.** Each identity field must be of its own kind on both
  sides: non-empty text, 64 lower-case hex digests, a versionCode of at least
  1, and a size. So two missing values never count as a match.
- **Links.** The server's link rule is at least as strict as the phone's
  `ShareState.link`:
  - https, at most 2048 ASCII characters;
  - a valid host name or IPv4 address, and an all-digit port if any;
  - no credentials, fragment or IPv6;
  - only RFC 3986 characters, with `%` only as a valid escape.

  A test compiles the phone's own `ShareState` and checks that it accepts
  every link the server would publish.
- **Retry-After** counts in ASCII digits only.
- **Fixture.** `rose/tests/fixtures/share-summary-v1.json` lists each
  recorded version, hosting's answer and the `share` served, plus
  `clientUnreadable` values a phone must not read as links.
  - **Verbatim answers:** they come from the Chariot owner's own fixture,
    `Immortal-Protocols/ProtocolsBackend`
    `chariot/test/fixtures/apk_http/metadata-responses.json` on
    `codex/project1-apk-hosting-contract` at `31de2ac1`. It is copied byte
    for byte to `rose/tests/fixtures/chariot/`, and a test pins its git blob
    `3222e6c5`.
  - **Derived cases:** cases marked DERIVED change one thing in that answer.
    Its URLs are synthetic, and none is live.
- **The hosted app mapping** is in the ledger
  (`record_hosted_app(project, uuid)`). It is to be recorded once, from
  owner tooling or the publisher. Nothing calls it yet, because that
  tooling and Chariot's publication adapter don't exist. The same id again changes nothing. Another id,
  or an id another project holds, is refused, because a moved mapping would
  send one app's phones another app's links.
- **Not yet done.** The library doesn't serve `share` yet. That needs a
  metadata reader on the builder host, and nothing is published. Until then phones read the absent field as
  "Not shared yet".

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
- **Toolchain facts:** Java at source level 17 against Android's own core
  library (§2.2), the framework and any declared pure-Java JAR libraries the
  offline store provides. There's no
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
one-page-left-of-Home workspace to the workspace composer, and route
agent tasks the owner explicitly selects there through the existing server. The
server runs the local decision model; the phone supplies the snapshot and renders its result. Native UI belongs
to OS Product under Nathan's approved prototype.

The conversation offers `compose_workspace`, now that Rose Agent
(`dev.pixel.voice`) serves the contract (#787, #820).
- **Every call checks first.** Each call asks the provider's `capabilities`
  for this contract and kind before sending anything. With no provider, an
  older one or a kind it doesn't list, the outcome is `unavailable`, and
  nothing is sent. Rose never claims a page change she can't make.
- **Request ids.** A call's `requestId` is derived from its tool call id, so
  a repeat of the same call composes nothing new.
- **One limit.** If a call is answered as cancelled but the phone had already
  composed, a fresh call from the model is a new request.

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

### Server-local layout inference (Nathan's 2026-11-14 correction)

The decision model runs in Rose server, not the client. Once the phone receives the above intent (or a page
button/text request), it posts its existing rose-layout-v1 request and current snapshot to
`POST /workspace/v1/compose` in #818's envelope `{schemaVersion:1, requestId, request}` on the paired TLS listener, signed with the same rose-auth-v1 headers as `/hello`.
The server returns a validated rose-layout-v1 result. The phone checks it again, retains pins/undo and reports
shown/prepared through the unchanged outer contract. No Project 1 build dependency, new listener, cloud model
endpoint or renderer format is added. Direct page requests now require the paired Rose server. Model setup
and source/runtime evidence are in `workspace/models/local-composer/README.md`.

The same device/requestId with a changed body is refused with 400; identical concurrent requests share the
first result. Loading is prewarmed independently of the phone request and returns 503 while unavailable.

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
 writer?: {argv[], timeoutS?}, backup?: {argv[], timeoutS?}, libraryStore?}
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
- **Offline libraries.** Optional `libraryStore` is an absolute path to an
  existing host-owned `RecordedStore` (§2.1): `index.json` with
  `{schemaVersion: 1, artifacts: {"group:artifact:version": "<sha256>"}}`
  and the corresponding `<sha256>.jar` files. The host passes this resolver
  to `Builder` and its live available-coordinate list to `CodexGenerator`.
  Omission preserves framework-only builds. A configured store that is missing,
  malformed or uses symlinked store/index/JAR paths is refused before the writer
  or listeners are constructed; it is never silently disabled. Job resolution
  still verifies artifact digests, structure and prior-version locks. No
  downloads or store population are performed by the host.
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
- configured-library advertisement and job-local resolution through the host,
  live catalog updates, and missing/malformed store refusals;
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
   separately. No harness exists for packages other than Rose Agent. One
   case must be a **schema-changing revision**, run on each phone and
   recorded as physical acceptance (docs/VERIFY.md):
   1. Build v1 of an app that keeps data in all three places: a SQLite
      database at schema version 1, SharedPreferences keys, and a file in
      its own storage.
   2. Install it, then seed known values in each place through the app
      itself.
   3. Ask for a revision that changes the schema, for example a new column or
      table. Build it and update in place through the installer (same
      package and signer, a higher versionCode). No uninstall, and no data
      cleared.
   4. Read back in the updated app:
      - every seeded row and value is unchanged;
      - the new schema element is present, with its default or migrated
        value;
      - the preferences and the file are intact.
   5. Record the build's versionCodes and source revisions, the phone's
      model, Android version and SDK level, and the result of each check.

   A crash, lost data or a cleared database on first launch of the update
   is a failure of this case, never a pass. A build that compiles and signs
   is not this evidence. This case is a test-plan step only: it has not been
   run.
6. **Level 2 (App Core co-generation; Chariot owner; OS Core).** App Core owns
   client/server co-generation and sign-in integration (§7). Each part is
   blocked on its owner, as follows:
   - **Chariot owner (the Codex Chariot engineering worker, via Chief).**
     Hosting: the server boundary (§7.7), ingress, sleep/wake, the sealing
     key's home (D9), backups, and the deploy tools.
   - **OS Core.** `rose-signin-v1` and the RoseOS native factors.
   - **Local Server Worker.** On the builder host: the Rust toolchain and the
     curated crate set, and build isolation for Rust (blocker 3).

## 7. Level 2: client/server co-generation (source; enabled nowhere)

A Level 2 app is a generated Android client and a generated server, built and
versioned together. Users sign in with `rose-signin-v1`
(docs/ROSE-SIGNIN-V1.md): an authenticator one-time code plus a passphrase.

Owners:
- **App Core:** co-generation, the server profile and sign-in integration.
- **OS Core:** `rose-signin-v1` and the RoseOS native factors.
- **Chariot owner:** hosting (§7.7).
- **Home/App:** the builder's phone UI.

Nothing here is built into a job, hosted, deployed or run on a phone. What
exists in source is §7.2's checks, §7.3's contract, and §7.6's migration
checks, ledger records, crate set and server build. None of it is called by
the builder yet.

### 7.1 Facts the design rests on

- **Chariot today.** The public Chariot CLI is `chariots-sh/chariot-cli` at
  `6c7b6e6`.
  - **No ingress.** Agent pods accept no network ingress. Requests arrive only
    as Chariot messages authenticated by the fleet's token-seed or the owner's
    login, and neither can be put in an APK.
  - **One page.** Each agent has one public HTML page, which can't be revoked
    or read while the agent sleeps, and nothing else is served.

  So no phone-facing endpoint exists yet. The Chariot owner is working on
  VM/endpoint hosting, and has proposed the boundary in §7.7.
- **rose-signin at `23062a04` (#819, #821, #822).** The generated server uses
  this revision, which completes the decided library. OS Core owns its storage, so App Core writes no sign-in SQL and no
  sealer. The server uses:
  - **Store:** the reference SQLite store
    (`store::sqlite::SqliteStore::new(conn)`, feature `sqlite`). It applies
    its own `auth_*` migrations, and each `Authenticator` call is already one
    transaction.
  - **Sealer:** the context-bound `secret::AeadSealer`, holding versioned
    keys that the host supplies.
  - **Readiness:** `Authenticator::ready()`, which gates startup and
    readiness.
  - **Restore:** `Authenticator::restore_after_backup(now)`, run once after
    any database restore.
  - **Admission:** `begin_enrollment(.., Admission::Invite(token) |
    Admission::Open, ..)`. Without a valid invite, every answer is
    `not_admitted`, so accounts can't be enumerated.
  - **Invites:** `create_invite(app, account, now)` makes a single-use token,
    valid for 7 days and bound to that account. It is reachable only through
    the creator's owner tooling, never a public route.
  - **Recovery:** `recover_authenticator` and `recover_passphrase`, served at
    `POST /signin/v1/recover/authenticator` and `/recover/passphrase`.

  Two more points apply:
  - `Unavailable` maps to `503 auth_unavailable`.
  - **Sessions:** the server uses `validate_for(app, token, now)` and
    `revoke_for(app, token)` (#822). Another app's token reads as no session
    and is left untouched. The runtime also checks `principal.app` itself, as
    a second check.
- **Decided by OS Core (Nathan's direction).** The passphrase is checked on
  the server, has at least 8 characters, and may be digits only.
  - **One-time codes:** SHA-1, 6 digits, 30 s steps, one step of skew.
  - **Failures:** decaying backoff with no lockout.
  - **Sessions:** 30 days absolute, 7 days idle.
  - **Admission:** creator invite by default, open sign-up when the app
    declares it.
  - **Recovery:** a recovery code plus one remaining factor, with no creator
    reset.
- **The client core.** `RoseSigninCore.java` is copied byte for byte at
  sha256 `6209a9cd…587f`, unchanged from `ee1e554f` to `23062a04`. OS Core
  will tell App Core when it changes.

### 7.2 Profile declaration and checks (`app_builder/appprofile.py`; source, called nowhere)

A project is `app-v1` (Level 1) unless it writes `rose-app.json` at its
root. Without that file, every Level 1 check is unchanged, and a `server/`
folder is still refused.

```json
{"schemaVersion": 1, "profile": "client-server-v1",
 "server": {"crates": ["serde@1.0.219"], "shared": false, "admission": "invite"}}
```

- The file is strict UTF-8 JSON, at most 16 KB, with no repeated or unknown
  keys.
- `crates`: up to 32 exact `name@version` entries, with no ranges and no
  repeated name. They are resolved only from a crate set the builder host
  provides, as JAR libraries are (§2.1).
- `shared`: allows a database shared by all users of the app, alongside each
  user's own.
- `admission`: `invite` or `open`.

The tree adds a `contract/` folder (the API both halves share; required) and
`server/`:
- `server/src/**.rs`, with `server/src/handlers/mod.rs` required;
- `server/assets/**` (data files);
- `server/migrations/account/NNNN_name.sql`, and `migrations/shared/` only
  when `shared` is true.

The builder writes the rest itself: the server's Cargo files, its entry point,
the code generated from the contract, and the client's `<package>.rose`
package (sign-in and the API client).

Every refusal is identity, integrity or isolation, never a rule on what an
app may do. Each has a stable `reason`:

| Refused | `reason` | Category |
|---|---|---|
| A bad declaration | `profile_invalid` | compile |
| No declaration after a client-server version was delivered (its users' data lives on the server) | `profile_dropped` | compile |
| `server/Cargo.toml`, `Cargo.lock`, `build.rs`, `.cargo/` or `rust-toolchain*` | `server_build_file` | dependency |
| Anything else outside the layout above | `layout_invalid` | compile |
| A file in `src/<package>/rose/`, or any Java `package <package>.rose…` | `reserved_path` | compile |
| A manifest without `android.permission.INTERNET` in force from minSdk, as a `<uses-permission>` or `<uses-permission-sdk-23>` child of `<manifest>` | `missing_internet` | compile |
| A Level 1 check failing on the project (links, modes, sizes, git names, forbidden suffixes) | `template_refused` | compile, or dependency for library files |
| Server Rust that reads the builder's machine while compiling, or brings in native code | `forbidden_construct` | compile |
| Server Rust that isn't UTF-8 or doesn't lex | `source_unreadable` | compile |

The Level 1 checks on links, modes, sizes, git names and forbidden suffixes
(`.c`, `.so`, `.sh`, `.py`, …) also cover `server/` and `contract/`. A plain
app without `rose-app.json` keeps the Level 1 refusals exactly as before.

The Rust scan lexes the source as rustc does, so comments, strings, raw
strings, character literals and lifetimes are not code. It reads source the
way rustc does:
- rustc's whitespace set, including U+200E and U+200F;
- Unicode identifiers, including combining marks;
- a leading byte order mark, which is skipped.

Any other character no Rust token starts with is `source_unreadable`. Its cost
is linear in the file's size, however deeply attributes nest. It refuses:
- **File and environment reads at compile time:**
  - `include!`, `include_str!`, `include_bytes!` and `option_env!`, by name
    wherever they appear, so an item named `include` is refused too;
  - `env!` as a call (`env != x` is a comparison and is allowed);
  - renaming `env` in any import (`env as e`). This covers std's `env`
    reached directly, through `self::` or through a crate alias. The scan
    can't tell std's `env` from the app's own module, so that is refused too;
    a module needs no alias. `env::{self as e}` imports only the module and
    is allowed;
  - `#[path]` and `#[debugger_visualizer]`, also inside `cfg_attr`.

  Data files go in `server/assets/`.
- **Native code:** `asm`, `global_asm`, `naked_asm` and `llvm_asm` by name,
  so renaming imports can't hide them and an item with one of those names is
  refused too. `#[link(..)]` is refused as well.
- **Macros that hide either:**
  - a `macro_rules!` body that calls a macro named by its argument (`$x!(..)`,
    not `$crate::..`);
  - an attribute built from a macro argument (`#[$a]`). The exception is an
    attribute the macro captured as `#[$a:meta]`, which the invocation wrote
    out where the scan reads it.

Allowed: `std::env::var`, the app's own `env` module, `env as u32`,
`std::fs`, `std::net`, `unsafe`, `extern "C"` and `#[link_section]`.

The scan is a second line of defence. It does not cover:
- macros and build-time code from declared crates;
- tokens that local macros recombine (for example the name and the `!`
  passed separately).

The boundary is build isolation (blocker 3).

### 7.3 The shared API contract (`app_builder/contract.py`; source, called nowhere)

The writer describes the app's types and routes once, in `contract/api.json`.
The builder will generate the server's Rust handlers trait and the client's
Java API from it (slice 8). It also checks every revision against the
delivered one, so an update never silently breaks the copies already
installed on phones.

```json
{"schemaVersion": 1, "api": 1,
 "types": {"Note": {"fields": {"id": "int", "text": {"type": "string", "max": 10000},
                               "parent": {"type": "Note", "optional": true}}},
           "NoteList": {"fields": {"notes": {"type": "list", "of": "Note"}}}},
 "routes": {"listNotes": {"method": "GET", "path": "/app/notes", "response": "NoteList"}}}
```

- **Fields.** A field is one of:
  - a primitive: `bool`, `int`, `float`, `string` or `bytes`;
  - a `list`, or a `map` with string keys, `of` a field type, nested at most
    4 deep;
  - one of the contract's own types.

  `optional` fields may be absent or null. `max` bounds a string, bytes,
  list or map in requests, where the server refuses more; it promises
  nothing about responses. Generated code ignores fields it doesn't know, in
  both directions.
- **Routes.** A route has:
  - a method: `GET` and `DELETE` carry no body;
  - a path under `/app/`, with 1–8 lower-case segments and no parameters;
  - `auth`: `account` (the default, a signed-in user) or `public`;
  - an optional `request` type and a required `response` type.

  The `(method, path)` pair is unique.
- **Strict parsing.** The file is strict UTF-8 JSON, at most 256 KB, with no
  repeated or unknown keys. Limits: 128 types, 64 fields per type, 128
  routes. Malformed contracts are `contract_invalid`.
- **No infinite types.** A type that contains itself only through required
  fields could never have a value, so it is `type_cycle`. An optional field,
  a list or a map breaks the cycle.
- **Canonical digest.** The sha256 covers the canonical form: defaults
  spelled out, sorted keys, no whitespace. Key order and spelled-out defaults
  don't change it, and the canonical form is itself a valid contract.

**Revisions** (`check_update`) are judged by what an installed older client
would trip on.
- **Which versions.** A revision is compared with every contract delivered
  since `api` last rose, not only the newest. So a field dropped in one
  revision and brought back with another shape in the next still meets the
  phones that kept the first.
- **Directions.** Responses are compared by what the old client expects to
  read, and requests by what it still sends.
- **Matching.** Routes are matched by method and path, which is what an
  installed client calls, and types by shape. Renaming either changes
  nothing.
- **Recursive types** are walked with a queue, each pair of types once per
  comparison, so cost is bounded and recursion can't crash it.
- **Additive, allowed as is:** new routes, types and response fields; new
  optional request fields; a request field dropped (servers ignore extras);
  a request field made optional, or its `max` raised; a response field made
  required, or an optional one dropped; an account route made public.
- **Breaking:**
  - a method and path no longer served, or a public route that now needs
    sign-in;
  - a response field removed, made optional or retyped, nested types
    included;
  - a new or newly required request field, a `max` lowered or added, or a
    route that now needs a body older clients don't send.

A breaking change at the same `api` is refused as `breaking_without_major`,
naming what breaks. Raising `api` allows it, and marks the version as
needing older clients to update, through the library's minClientVersionCode
(slice 9). `api` can never go down (`api_lowered`).

### 7.4 Server profile (planned; next slices)

- **Runtime.** A small runtime crate (App Core) runs the generated handlers,
  serving `rose-signin-v1`'s routes (`/signin/v1/*`) and the contract's routes.
  - **App check.** Every account route requires a bearer session whose
    `principal.app` is this app.
  - **One database per user.** Each user's data is in its own SQLite file,
    keyed by a hash of (app, account), so the API path can't reach another
    account.
  - **Migrations** go forward only. A database from a newer schema is
    refused, never downgraded.
- **Honest limit.** Generated Rust is native code in that process, and the
  app's author is trusted like any server operator. Scoping protects users
  from the app's mistakes, not from its author.
- **Readiness.** The server reports ready only when the sealer is ready, the
  migrations are done and any restore has run. Without a key it refuses to
  start, and never invents one.
- **Logs** never contain bodies, tokens, passphrases, codes or keys.

### 7.5 Client sign-in (planned)

- The client includes `RoseSigninCore.java` at the pin above.
- It shows the `otpauth://` URI as a QR and as copyable text, and also opens
  it with `ACTION_VIEW`. OS Core will build a RoseOS system authenticator for
  that scheme, and on stock Android any authenticator app claims it, so the
  flows are the same.
- The user types the code and the passphrase. Only the bearer token is kept,
  in no-backup storage.
- **Naming the app (#824).** OS Core's RoseOS authenticator
  (`dev.pixel.rose.authenticator`) names the sending app only when Android
  vouches for it (API 34+). So the client starts the `ACTION_VIEW` with
  `ActivityOptions.makeBasic().setShareIdentityEnabled(true)`. Without that,
  the dialog says "an unidentified app" and still works.
- **Names.** Issuer and account names must have no control, bidi or
  line-separator characters, and at most 128 UTF-16 units. rose-signin's name
  rules already require this.

### 7.6 Builder (planned)

- **Both halves.** The builder builds both halves offline, from the snapshot
  only:
  - the client as in Level 1;
  - the server with `cargo --offline --frozen` against the host's crate set,
    under build isolation.
- **Honest failures.** A missing toolchain, crate set or isolation is a
  `toolchain` or `dependency` failure, never a skipped half.
- **The crate set** (`app_builder/crates.py`; source, called nowhere).
  Crates come from a `CrateSource` that the builder host injects, for
  example `RecordedCrates`, a host-owned `index.json` of
  `name@version → sha256` plus one `<sha256>.crate` file each. Nothing
  downloads a crate, and filling the set is the host's job (blocker 6).
  No index is an empty set. An index that can't be read, or that lists an
  entry the builder can't use, is a broken set (`crate_set_invalid`), never
  a smaller one.
  - **Checked before use.** The whole set is unpacked once into a read-only
    cargo directory source, which the builder then reuses. Each crate must
    meet all of these:
    - its file is read without following a link, and hashes to the digest
      the set records;
    - it is a gzip tar of plain files and folders only, all under
      `<name>-<version>/`, with no link, sparse or special file;
    - it has no absolute path, no `.`, `..` or empty segment, no control
      character, and no entry listed twice;
    - it stays within bounds on file size, on entry count, and on the
      bytes actually written to disk;
    - its own `Cargo.toml` names exactly that crate and version.

    A crate that fails refuses the whole set (`crate_set_invalid`,
    category `toolchain`). The host fixes its set; the builder never quietly
    builds without the crate. Builds of one set take turns through a lock
    file, so it is unpacked once and never replaced while another build
    uses it.
  - **The lock.** Cargo resolves dependencies of dependencies from the set,
    starting from the last delivered version's `Cargo.lock`. A rebuild
    therefore keeps the versions that version used, and only adds or drops
    what the declaration changed. Every crate the resulting lock names must
    be in the set with the set's digest. A crate any delivered version used
    must also keep that version's digest (`crate_changed`). The only
    packages without a source may be the server itself and the builder's
    own crates, and every declared crate must appear as the set's.
  - **Not decided here: which crates are safe.** A crate's build script and
    procedural macros run while the server compiles, like any server
    operator's dependencies. The boundary for that is build isolation
    (blocker 3), and the host chooses its set.
- **The server build** (`app_builder/server_build.py`; source, called
  nowhere). The builder writes the server's `Cargo.toml` itself:
  - one binary at the builder's entry point `rose/main.rs`;
  - exact `=version` dependencies;
  - `build = false`, and Cargo's own target discovery off, so the writer's
    files are only modules.

  It then runs two commands, each through the host's `Isolation`:
  1. `cargo metadata --offline`, which resolves the lock;
  2. after the lock check, `cargo build --offline --frozen --release
     --target <target>`.

  `Isolation` lets a command write only the job's folder, read only the
  toolchain and the crate set, and reach no network. Without one, nothing
  runs (`build_isolation_unavailable`): the builder neither provides nor
  imitates isolation.

  Cargo's environment is scrubbed:
  - nothing is inherited;
  - `HOME`, `CARGO_HOME`, the target folder and `TMPDIR` are all inside the
    job;
  - the network is off;
  - the builder's own cargo config is passed with `--config`, which outranks
    every file;
  - the job's and the set's paths are remapped in what rustc compiles for
    the target.

  Cargo also reads a `.cargo/config.toml` in any folder above where it
  runs, ahead of `CARGO_HOME`'s. Such a file could replace the crate set or
  wrap rustc, so a build under one doesn't start
  (`build_isolation_unavailable`). A crate's procedural macro or build
  script runs on the build machine and could still write a builder path
  into the binary. A binary holding the job's or the set's path is refused
  (`build_path_embedded`).

  Rustc's diagnostics are read from cargo's JSON messages and counted by the
  package they belong to, never by their words. So text in the writer's own
  code, such as a string, a `compile_error!` or a diagnostic note, can't
  make its failure look like the toolchain's or a crate's.
  - **The toolchain's fault:** only rustc's own errors for a missing standard
    library (`E0463`) or a missing linker, or linking that failed for some
    other reason than the code.
  - **The writer's fault:** linking their own code wrong, such as an
    undefined or duplicate symbol.

  All paths are made canonical first, so a job folder reached through a
  link is still remapped and scanned for.

  The toolchain is a Rust toolchain's own folder, never rustup's proxies.
  The product target is `x86_64-unknown-linux-musl` (§7.7).

  Failures, each with a stable `reason`:

  | `reason` | Category | When |
  |---|---|---|
  | `build_isolation_unavailable` | toolchain | The host supplied no isolation |
  | `build_isolation_unavailable` | toolchain | A cargo config sits in a folder above the job |
  | `rust_toolchain_unavailable` | toolchain | No cargo or rustc, no standard library for the target, no linker, or cargo wrote no lock or binary |
  | `crate_set_invalid` | toolchain | The set can't be read, or a crate in it fails the checks above |
  | `no_crate_source` | dependency | Crates are declared and the host has no set |
  | `crate_unavailable` | dependency | A declared crate, or one it needs, isn't in the set, or no version in the set fits |
  | `crate_changed` | dependency | A crate differs from the set's digest or from a delivered version's |
  | `crate_compile` | dependency | A crate, or its build script, failed |
  | `build_path_embedded` | dependency | The binary holds the job's or the set's path |
  | `server_compile` | compile | The writer's server code didn't compile |
  | `server_build` | compile | Any other cargo failure |

  Logs have the job's, the toolchain's and the set's paths redacted.
- **One version** (the ledger; source, not yet called by the builder):
  - **Pairing.** A client-server version records its APK and its server ELF
    (`{sha256, size, target}`) and its migration lock, in the same
    transaction as the version row. A failure anywhere rolls all of it back,
    and the job doesn't become ready.
  - **Binding.** The first client-server delivery binds the project's
    profile. A later version without its server is refused.
  - **Lock.** `migration_lock(project)` is every migration any delivered
    version ran, which `migrations.check` holds later revisions to. The
    ledger enforces it too: a client-server version whose migrations leave
    out or differ from a delivered one is refused, so the lock can't
    become contradictory.
  - **Crates.** A client-server version also records the crates its server
    was built with and its `Cargo.lock`, in the same transaction. Two reads
    feed the next build:
    - `crate_lock(project)`: every `name@version → sha256` any delivered
      version used;
    - `cargo_lock(project)`: the newest version's `Cargo.lock`.

    A version that lists a delivered crate version with other contents is
    refused. Dropping a crate, or moving to a new version of it, is allowed.
  - **Level 1.** Its versions and their library entries are unchanged, and
    an older ledger file opens with the new tables added.
- **Migrations** (`app_builder/migrations.py`; source, called nowhere) are
  checked offline before anything is built:
  - **Layout.** Each scope, `account` (every user's own database) and
    `shared`, is numbered `0001…` with none missing (`migration_gap`). Files
    are UTF-8, at most 256 KB each, and at most 1000 per scope.
  - **The lock.** Every migration of every delivered version must still be
    there under its number, with the same name and bytes. New ones may only
    follow (`migration_removed`, `migration_changed`). The ledger records
    each version's lock (slice 4).
  - **A sandbox.** The migrations are applied in an in-memory SQLite, one
    transaction each as the runtime does, with foreign keys on and a step
    budget. Temporary data stays in memory (`temp_store = MEMORY`), and a
    trace showed that no file is created even for a large sort.
  - **Statements** are split by a one-pass port of SQLite's own
    `sqlite3_complete` state machine, which is linear in file size.
  - **An authorizer** refuses anything that could touch the builder's files
    or the server's own settings:
    - `ATTACH` (and `VACUUM INTO`, which attaches) and `DETACH`;
    - transaction control and `load_extension`;
    - any other database;
    - temp objects, which would outlive their migration on one connection,
      never on a phone's;
    - every PRAGMA outside a short list (`foreign_keys`, `table_info` and
      the like). `page_size` and `data_version` may be read, as fts5 and
      rtree do, but never set.

    Common DDL passes: `DROP COLUMN`, `RENAME`, rebuilds, generated columns,
    `STRICT`, `WITHOUT ROWID`, upsert, fts5 and rtree.
  - **Fresh and upgrade runs.** The set is applied from an empty database.
    It is also applied as an upgrade: the delivered set first, then sample
    rows, then the new migrations.
    - **Sample rows.** Every table gets two rows of values typed by column
      affinity. They are identical where its constraints allow, otherwise
      two different rows, otherwise default rows. Rowid keys and foreign
      keys are left to SQLite. This is best effort: a table whose
      constraints refuse all three stays empty.
    - **Effect.** A change that only fails over a phone's data is caught and
      says so. Examples are a unique index over duplicates, and a rebuild
      that loses NOT NULL values.

    Integrity and foreign key checks follow each run, and running out of
    steps anywhere is a refusal.
  - **Results.** Any failure is `migration_sql`, naming the file. Only
    generated SQL runs, never generated Rust, and nothing is written to disk.

### 7.7 Hosting boundary (Chariot owner's proposal: native ELF + manifest + health path + client base URL)

App Core accepts this boundary. The server is one Rust ELF plus a manifest,
with nothing Chariot-specific inside the app. The fake host in tests checks
the same manifest.

Proposed manifest:
- `schemaVersion: 1`, `projectId`, `versionCode`;
- `elf: {sha256, size, target}`;
- `listen`: the host passes `PORT`, default `8080`;
- `health: "/health"`;
- `dataDir`: the host passes `DATA_DIR`, the only writable path, holding
  `TMPDIR`;
- `signin: {revision: "23062a04"}`;
- `schema` versions.

**Agreed with the Chariot owner (relayed by Chief, 2026-11-15):**
- **Binary:** a static x86-64 musl ELF, taking `PORT` and `DATA_DIR`, behind
  a stable HTTPS endpoint. A stopped or unhealthy server answers `503` with
  `Retry-After`.
- **Readiness** is served on the app's own listener (default `8080`), with
  no separate liveness check. `/health` returns `200` only once the sealer is
  ready, migrations are done and any restore has run, and `503` until then.
  It carries the same identity fields either way, and no secrets, request
  data or account information. The wire keys are the host's own, in
  snake_case (Chariot owner, relayed by Chief). The generated server will
  write them as they are, with no adapter in between:
  - `app_id`: the Chariot host's UUID. It is not the builder's `projectId`.
    The builder's ledger keeps an explicit mapping from `projectId` to the
    hosted app id and the hosted APK app id, written only from owner tooling
    or publisher results, never guessed.
  - `revision_sha256`: measured by the server itself, by hashing its own ELF
    at start.
  - `deployment_sha256`: the authoritative value is the
    `CHARIOT_DEPLOYMENT_SHA256` the host passes, which the server echoes. Any
    hash the server computes itself over its args, port and schema is
    reported separately and never replaces it.
- **Publication** uses Chariot's existing creator auth, by sha256, never the
  phone or the APK. The publication adapter isn't finished, and there is no
  live URL or credential.

Still open for the Chariot owner (App Core's proposal in brackets):
1. Further manifest fields [resources, shutdown grace].
2. The name of the variable that carries the host's app id to the server
   [for example `CHARIOT_APP_ID`].
3. Sealing key (D9). A pinned Secret Manager descriptor is a
   recommendation only. Delivering the key to the server (for example as a
   key file the host resolves) has no agreed consumer contract and no
   implementation on either side. Until it does, no server can be ready,
   and nothing here is deployable. [App Core's proposal: Chariot resolves
   the descriptor at start into a read-only key file outside the ELF,
   `DATA_DIR` and backups; the server never holds the descriptor's
   credentials; no key means not ready. The server hands the keys to
   rose-signin's `AeadSealer`, which already takes host-supplied keys, so
   no OS Core change is needed.]
4. Schema-changing deploys, which return a conflict today [the manifest
   states `schema: {from, to}`; the owner confirms; Chariot snapshots
   `DATA_DIR` first; the server migrates forward only and is ready only
   afterwards; rollback restores the snapshot and the previous ELF].
5. Restore. The marker is reserved and there is no executor yet [the
   server runs `restore_after_backup` once, then removes the marker before
   it reports ready; App Core makes no restore claim until Chariot has a
   restore executor].
6. Client address for rate limits [one header only the proxy sets].
7. Rollback [keep the previous ELF; report the running sha256].
8. Process and logs [SIGTERM within a grace period; no secrets in logs].

Older APKs on phones keep working against a newer server version. That is
the generated server's job, through a versioned API and the stable base URL.

### 7.8 Slices

Each slice is source only, offline, and tested before merging. Slices 2–5
need no hosting and no further OS Core change.
1. **The profile declaration and checks (§7.2).** Done:
   `rose/tests/test_app_profile.py`. An adversarial review found these
   evasions of the scan, which are now refused and tested:
   - a macro handed the name;
   - renamed native macros;
   - invisible separators;
   - macro-built attributes;
   - `#[debugger_visualizer]`.

   A second pass found three more, now also refused or fixed:
   - `env` renamed through `self::` or a crate alias;
   - a recursion crash on deeply nested attributes;
   - quadratic cost.

   It also fixed false refusals of Unicode identifiers, a byte order mark,
   `env::{self as x}` and `$(#[$m:meta])*`.
2. **The shared API contract and its compatibility rules (§7.3).** Done:
   `rose/tests/test_app_contract.py`.
3. **Offline migration checks (§7.6).** Done:
   `rose/tests/test_app_migrations.py`.
4. **Ledger pairing of client and server artifacts, the migration lock, and
   the hosted app mapping (§7.6, §3.3).** Done:
   `rose/tests/test_app_ledger_level2.py`.
5. **The injected crate set and the server build plumbing (§7.6).** Done:
   `rose/tests/test_app_server_build.py`.
   - **Fake cargo.** It records what the builder runs and in what
     environment, and maps cargo's failures.
   - **Real cargo, offline.** It builds a dependency-free fixture, and one
     against a small crate set made in the test. That second build shows
     resolution, a rebuild keeping its lock, `crate_changed` and
     `crate_unavailable`.

   These real builds use the machine's own toolchain for its own target.
   CI requires them (`ROSE_REQUIRE_CARGO`). The pinned musl toolchain on the
   builder host is still the Local Server Worker's (blocker 6).
6. The manifest and ELF checks.
7. The runtime crate on rose-signin `23062a04`, with its own CI.
8. Codegen and the client sign-in overlay.
9. The library wire summary and its fixture for Home/App.
10. The hosting seam: a fake host, and a Chariot adapter that stays blocked
    until §7.7 is answered.
