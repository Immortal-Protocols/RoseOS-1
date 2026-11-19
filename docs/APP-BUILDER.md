# App builder (Project 1) and workspace composer routing (Project 2)

> **Backend policy, 2026-11-18:** New backend builds use only the existing
> Chariot agent container (`client-backend-v1`). The builder-managed Rust
> compiler, prescribed Cargo files, crate store and host qualification are
> retired. `clientServer` is an inert compatibility key. See
> [backend migration and runtime handoff](APP-BACKEND-MIGRATION.md) for the
> current path, retained records, deployed-usage evidence and legacy revisions.
> Rust build/provisioning instructions and old proof claims below are historical
> context, not current run instructions or evidence of zero deployed users.


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
4. **Generate.** A per-job Codex thread under the `rose_app_build` permission
   profile (§4A), cwd = that project's source, writes code into a
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
   packaging. In the older flat layout, generated code is compiled, never
   executed on the host: no generated scripts, Gradle builds or annotation
   processors run. An ordinary Gradle project (§2.4) does run its own build
   scripts, so it builds only inside the build isolation, offline, with no view
   of keys, the ledger or other projects, and its APK is checked the same way
   afterwards.
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
  session on the existing bridge per job. The standalone host constructs it
  when a writer command is configured (§6); source tests use a fake transport.

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

### 2.4 Ordinary Gradle projects (Kotlin, Compose; in source, off unless `gradle` is configured)

An app can be an ordinary Android Gradle project: `settings.gradle.kts` (or
`.gradle`) at the top and the application module in `app/`
(`app/build.gradle.kts`, `app/src/main/AndroidManifest.xml`, Kotlin or Java
under `app/src/main/java` or `kotlin`, `res/`, `assets/`), with Kotlin, AndroidX
and Jetpack Compose. It goes through the same job, signing, delivery and phone
install and update as every other app. The phone needs no change.

**Which path builds a snapshot.** `settings.gradle(.kts)` at the top means
Gradle (`rose.app_builder.gradle`); anything else is the older flat layout and
builds exactly as before (§2 step 5). When `gradle` is configured, a new app
starts from a minimal Kotlin + Compose skeleton (`gradle.skeleton`: one
activity, Material 3, the builder's identity in `app/build.gradle.kts`).
Templates and themes are Home/App's and are not part of the builder.

**Pinned stack** (`app_builder/gradle_stack.py`, the one place every version
and digest is named): Gradle 9.9.1, Android Gradle Plugin 9.5.1 (which compiles
Kotlin itself), Kotlin 2.4.21 with the Compose compiler plugin, Compose BOM
2026.09.00, compileSdk 37 (`platforms;android-37.0`), build tools 36.0.0 (AGP's
default), JDK 21 (Temurin), plus a short list of AndroidX and Kotlin libraries
(activity, core, lifecycle, navigation, DataStore, Media3, coroutines,
serialization). The writer's turn names all of them, since builds are offline.
A project may use only those plugins and libraries, from `google()`,
`mavenCentral()` and `gradlePluginPortal()`. Adding one is a reviewed change to
`gradle_stack.py` followed by reprovisioning.

**Identity and signing stay the builder's.**
- The builder's own init script (`gradle.init_script`) sets the application id
  to the ledger's package and the version code and name to the ledger's
  version, whatever the build files say. Only the release variant is built.
- Gradle produces an unsigned APK. An APK the project's build signed itself is
  refused, never signed over.
- The project's single universal APK is checked on the binary like every other
  build (`toolchain.verify_apk`):
  - package, version code and name, and no splits;
  - not debuggable or testOnly;
  - no shared uid, key sets or other identity elements;
  - permissions only under its own package;
  - the manifest's top-level elements limited to the allow-list;
  - minSdk at most 29 and targetSdk at least 24;
  - 16 KB page alignment.
- Only then is it signed with the project's own key (`toolchain.sign`), so an
  update keeps the same package and signer and a higher version code.

**Isolation.** Gradle runs the project's own build scripts, so it never runs
outside `gradle.isolation` (`app_builder/isolation.py`):
- **Linux:** `bubblewrap`. The root is empty and read-only, with no network,
  `/etc`, `/tmp` or home. The pinned JDK, Gradle, SDK and dependency cache are
  mounted read-only, and the job's build folder is the only writable one.
- **macOS:** `seatbelt`, which uses `sandbox-exec`. It allows by default, then
  denies network beyond localhost, every write outside the build folder, reads
  of the builder root, the home folder, the Rose configuration and the user's
  temporary folders, and the keychain services.
- Keys, the ledger, artifacts and other projects are never visible.
- The APK is copied out of the writable folder before it is checked and signed.
- The job's Gradle home and build outputs are removed afterwards.

**Errors.** Kotlin, Java, resource and manifest-merger errors, and the build
files' own errors, are `compile` failures with the extracted diagnostics. They
go back to the writer as a repair turn (§4A). A plugin or library the cache
doesn't hold is `dependency`.

**Existing apps keep updating.** An app delivered in the older layout keeps that
layout and its rules until a revise converts it to a Gradle project. Its package,
data and signing key stay the same, so the next version updates in place. A
client-server (builder-built Rust server) app keeps the older layout; that path
is being retired separately.

**Provisioning (runtime, Local Worker).**
`python -m rose.app_builder.gradle_prepare stack --out DIR` downloads the pinned
archives and checks their SHA-256. It installs the SDK packages, each with its
`package.xml`, seeds the read-only dependency cache by building a builder-owned
catalog project online, and prints the builder config `gradle` object:
`{jdk, distribution, sdk, dependencyCache, isolation, bubblewrap?,
buildTimeoutS?}`. `scripts/setup-android-gradle.sh` is the CI wrapper.

**Verified here:** the Gradle path builds, signs and updates on Linux under
bubblewrap, as a non-root user (CI job `gradle` and the container this was
written in).

**Not verified:**
- the Seatbelt isolation on the Mac, which needs qualification there;
- installing on a device or emulator. API 34 and current Android are the
  validation targets, not a cutoff; the APKs are checked against them on the
  binary only.

## 3. Builder API v1 (agreed with Home/App, 2026-11-13)

All requests use the paired device's `rose-auth-v1` signature, under the
`/builder/v1/` prefix on the remote listener (`rose/app_builder/routes.py`).
The routes are mounted only when the server has an app builder enabled:
`rose.app --app-builder CONFIG` (§6.1) enables it on the one Rose server, the
listener Rose Agent already pairs with, and no host passes that flag yet. The
signature covers the method, path, time and
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
  - `dependency`: a library or plugin the builder can't provide. In a Gradle
    project (§2.4): a plugin or library, or a version of one, that the
    offline dependency cache doesn't hold, or native code (no NDK). In the
    older flat layout: Kotlin, Gradle, an AAR, a JAR or AAR file in the
    source, an undeclared or unavailable library, a library that fails its
    checks, or an import of a missing package.
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
apk: {sha256, size}, notes, apiReport, server?}]}]`

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
  owner tooling or the publisher, through the owner records' writer
  (`owner_records hosted-app`, §7.7). Nothing runs it yet, because that
  tooling and Chariot's publication adapter don't exist. The same id again changes nothing. Another id,
  or an id another project holds, is refused, because a moved mapping would
  send one app's phones another app's links.
- **Not yet done.** The library doesn't serve `share` yet. That needs a
  metadata reader on the builder host, and nothing is published. Until then phones read the absent field as
  "Not shared yet".

**`server`** (schemaVersion 1; `app_builder/server_summary.py`; served,
slice 9) says, for a client-server version (§7), which contract `api` its
server speaks and the oldest installed version whose client that server
still serves. Like `apiReport` it was added after v1 and is optional.
- **Plain versions** have no `server` field: a plain version's client never
  talks to a server. A client reads an absent field as "no server".
- **`ok`:** `{schemaVersion: 1, status: "ok", api, minClientVersionCode}`.
  A revision that breaks what installed clients rely on must raise `api`
  (§7.3), and the server keeps serving every client since `api` last rose.
  So `minClientVersionCode` is the first version of the unbroken run of
  readable client-server versions at this `api` that ends with this one. An
  installed version older than that must update before it uses this
  version's server. A project's first client-server version is its own
  minimum, since plain versions before it never talked to a server.
- **`unavailable`:** `{schemaVersion: 1, status: "unavailable"}`. The ledger
  has no contract record for the version, or one this server can't read.
  Nothing is claimed about it, and the next readable version claims nothing
  older than itself.

Rules for a client:
- The summary is treated as `unavailable` if it has an unknown
  `schemaVersion` or `status`, a field missing or extra for its status,
  an `api` or `minClientVersionCode` that is not a JSON integer from 1 to
  2³¹−1, or a `minClientVersionCode` above the version's own `versionCode`.
  A JSON integer has no fraction or exponent, so `1.0` is refused, as is
  `true`; the phone's parser already types numbers this way (`Long`). The
  same holds for `schemaVersion`. The server never sends `null`; a client
  may read one as the field being absent. Only the summary is refused,
  never the library.
- `unavailable` never blocks an install and never says an older client
  still works.
- It describes compatibility between a version's client and its server.
  It never limits what may be asked for or built, and the server is not
  hosted yet (§7.7), so nothing in it says the server runs.

The exact cases are in
`prototypes/voice-phone-demo/rose/tests/fixtures/server-summary-v1.json`:
- `cases`: one project's delivered versions (whether each had a server, and
  the contract recorded), with the `server` served for each (`null`: absent);
- `clientUnreadable`: values the server never sends, which a client must
  read as `unavailable`;
- `clientReadable`: edge values a client must accept (the largest numbers,
  and a version that is its own minimum).

`rose/tests/test_app_server_summary.py` checks every case through the
ledger's library, checks that each served summary meets the rules above,
and checks every short history. A contract record the server can't parse,
even one nested too deep, reads as `unavailable` and never fails the
library. The phone's parsing and display are
Home/App's, and are not implemented here.

### 3.4 Baseline

- **Gradle projects (§2.4):** Kotlin or Java with AndroidX and Jetpack Compose
  from the pinned stack (`app_builder/gradle_stack.py`): compileSdk 37, the
  skeleton's minSdk 26 and targetSdk 37. A built APK must have minSdk at most 29
  (Rose Agent itself needs API 29, so no phone that runs Rose is left out) and
  targetSdk at least 24. AndroidX's own small native libraries ship for every
  ABI; there is no NDK, so the app has no native code of its own.
- **Older flat layout:** minSdk 26, targetSdk 35, framework APIs, plus declared
  pure-Java JAR libraries from the builder's offline library store
  (**Libraries**, §2). There is no native code, so builds are ABI-independent.
  Anything needing AndroidX, Kotlin, Compose or an AAR fails with category
  `dependency` there; such an app can move to a Gradle project in a revise.
- Either way, a library or plugin the builder doesn't have fails with category
  `dependency` and an honest summary. It is never silently dropped.

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
  - in the older flat layout, Java must be in the app's package; in a Gradle
    project only the builder's own `<package>.rose` package is reserved;
  - no git metadata (a Gradle project may keep its `.gitignore`), links or
    special files; no key files, prebuilt binaries or native code.
- **Toolchain facts:** a Gradle project builds with the pinned Gradle, AGP,
  Kotlin and Compose stack, offline, inside the build isolation (§2.4). The
  older flat layout builds Java at source level 17 against Android's own core
  library (§2.2), the framework and any declared pure-Java JAR libraries the
  offline store provides. Any other library is a `dependency` failure.
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

The producer has an opt-in signed route for these confirmed tasks, using the
same conversation policy and tools as typed requests.

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
  (`rose/tests/test_workspace_tasks.py`). Normal `rose.app` startup sets
  `workspace_tasks` only with explicit `--workspace-tasks` in live
  `--brain codex` mode. The default is off. The store is private at
  `ROSE_CONFIG_DIR/workspace-tasks/workspace-tasks.sqlite3`, created before
  route construction. Requests return `unavailable` while the conductor is
  loading or Rose is not ready; rejected/unavailable requests start nothing.
  Startup connects the existing conductor callbacks and device-scoped bus
  events. Reset keeps the same durable store and connection. Shutdown stops
  admission before the conductor stops, then closes the store; late callbacks
  and reads tolerate closure. Unfinished work is recovered as uncertain on
  restart and is never replayed. This source wiring does not enable the phone's
  separate task gate or supply OS Product's task templates, and no deployment
  or restart is implied.
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
  never` and the `rose_app_build` permission profile, with network off. Web search,
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
- **Read isolation is a Codex permission profile.** The legacy
  `workspace-write` sandbox limits writes, not reads. Codex 0.167.1's
  experimental permission profiles (opted into by the `experimentalApi`
  capability the bridge already sends) can also deny reads. The writer defines
  and selects its own profile, `rose_app_build`, and nothing else:
  - it extends `:workspace` (which keeps `.git`, `.agents`, `.codex` and
    `.aws` read-only). It denies `:root`, `:tmpdir` and `:slash_tmp`, allows
    `:minimal` (the runtime Codex needs) for reading, and allows
    `:workspace_roots` `.`, which is the job's `source` cwd, for writing.
    Network is off. So `ROSE_CONFIG_DIR` (pairing keys, TLS, provider tokens,
    the project signing keys), home and every other project fall under the
    `:root` deny;
  - it is defined only by launch flags, `--strict-config -c
    default_permissions="rose_app_build" -c permissions.rose_app_build={…}`.
    No user or shared config file is written;
  - there is no legacy fallback. Thread start carries `permissions` and no
    `sandbox`, and each turn carries `permissions` and no `sandboxPolicy`;
  - thread start names the one workspace root itself, as
    `runtimeWorkspaceRoots: [source]`. The schema allows the field to be
    omitted, `null` or `[]`, but does not define what those mean, so the
    writer never relies on them.
    The base options' `danger-full-access` is removed for this purpose, and a
    test pins that.

  Before the turn, the writer refuses (`app_build_isolation_unavailable`, then
  surfaced as `research_isolation_unavailable`) unless all of these hold:
  - the version is a released `codex-cli` 0.167.1 or later;
  - the effective config has no `sandbox_mode` or `sandbox_workspace_write`
    (either would make the legacy sandbox win), at the top level or in a
    selected config profile (`profile` + `[profiles.<name>]`, e.g. in the
    owner's `~/.codex/config.toml`). A selected profile must exist and must not
    set those keys, `default_permissions` or `permissions`. A field that is
    absent or `null` counts as unset;
  - the config names `rose_app_build` as the default, and its definition is
    exactly the one passed, so an entry merged in from another config layer
    (an extra read path, network on) refuses;
  - the thread start reports `activePermissionProfile.id = rose_app_build`,
    with `extends` unset or `:workspace`, and every `runtimeWorkspaceRoots`
    entry (the thread-scoped roots that `:workspace_roots` expands to) is
    the job's `source`. An extra root would widen the writer's only writable
    path;
  - `permissionProfile/list` for the job's cwd lists it with
    `allowed: true`.

  A profile that is selected and allowed is not yet proved enforced. That is
  why blocker 3 stays open until the live check below.
- **Shared code.** `codex.mjs` also carries the research and character-life
  isolation checks. The `app_build` purpose sits beside them; their launch
  arguments, thread parameters and turns are unchanged, and a test pins that.

**Implemented** (source only):

- `workspace/rose-agent/server/app-build.mjs`: the fixed policy, the thread
  config, the `rose_app_build` profile and its launch flags, and the profile,
  version and listing checks. The config is the research isolation with web
  search off and the shell kept; it sets no sandbox keys. The cwd must be an
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
- **Matched to the installed 0.167.1 experimental schema export** (excerpts
  from Local Server, read from files with no runtime):
  - `ThreadStartParams.permissions` and `TurnStartParams.permissions` are
    `string | null` and exclude `sandbox` / `sandboxPolicy`. A turn's value
    applies to that turn and later ones;
  - `ThreadStartResponse.activePermissionProfile` is an optional
    `{id, extends?} | null` (default `null`), and
    `runtimeWorkspaceRoots` is an optional absolute-path list (default `[]`);
  - `PermissionProfileListParams` takes an optional `cwd`, `cursor` and
    `limit` (uint32). Its response has a required
    `data: [{id, allowed, description?}]` and an optional, nullable
    `nextCursor`;
  - app-server parses each `-c key=value` as TOML and falls back to a raw
    string on a parse failure; `--strict-config` rejects unknown fields.

  The code follows these shapes, and a `null` anywhere refuses. The `-c`
  profile value is valid TOML: a test parses it with `tomllib` back to
  exactly the profile. If Codex ever took it as a raw string, the exact-match
  check would refuse. The `[permissions.<id>]` keys and filesystem tokens
  come from the published permissions page. No Codex binary exists in this
  container.
- **Known not to be guaranteed:** the installed 0.167.1 experimental
  `ConfigReadResponse` schema leaves `config` open (`additionalProperties`).
  It lists the legacy `sandbox_mode` and `sandbox_workspace_write`, and it has
  no `permissions` or `default_permissions` property. So whether `config/read`
  returns the profile at all, verbatim or normalized, is neither defined by
  the schema nor observed (Local Server).

  Every point above fails closed: a missing, renamed or normalized field
  refuses every app build rather than weakening isolation. Nothing here claims
  that the named profile equals what Codex enforces. Still needed, from one
  real 0.167.1 app-server started with the writer's launch flags (only
  `config/read`, `thread/start` and `permissionProfile/list`; no turn, writer
  or model):
  1. what `config/read` returns for `permissions` and `default_permissions`.
     If Codex fills in defaults or resolves `extends`, the exact-match check
     refuses and must be revised to the observed form, not loosened;
  2. that the inline-table profile is accepted under `--strict-config` as a
     profile, not as a raw string;
  3. whether `config/read` resolves a selected `profile` into the top-level
     keys, leaves it under `profiles`, or both. The check covers both forms;
  4. what `runtimeWorkspaceRoots` the response reports when the request names
     `[source]`. Both `[source]` and `[]` are accepted, and neither is wider.

  A first bounded inspection (installed 0.167.1 under `codex sandbox` with a
  fresh `CODEX_HOME`, `--strict-config`, network denied, `initialize` only)
  exited before answering `initialize`, cause unverified. It reached no
  `config/read`, profile listing or thread, so all four items stay
  unobserved.
- **Live check before any enabling (Server Worker / Local Server):** a
  synthetic denial run under the profile, with no writer and no model. Reads
  of `ROSE_CONFIG_DIR`, home, `/tmp` and a sibling project must be refused;
  writes outside `source`, and any network, must fail; a write inside
  `source` must succeed. Until then the profile is selected, not proved.
- **Not done:** any real model turn, the live enforcement check of read
  isolation (blocker 3) and any host wiring.

## 6. Builder hosting (source only; not configured, deployed or enabled)

### 6.1 On the one Rose server (`rose.app --app-builder`)

The builder runs inside the Rose server (`rose.app`), the one Rose Agent
already pairs with. There is no separate builder server, pairing or device.

- **Switch.** `--app-builder CONFIG` is off by default. CONFIG is a strict
  builder config: the standalone host's builder fields below, without its
  listener fields (`remoteDir`, `name`, `port`, `adminPort`, `hosts`):
  `{schemaVersion: 1, root, sdk, javaHome?, buildTimeoutS?, keyCreation?,
  writer?, backup?, libraryStore?, clientServer?}`. Both configs are parsed
  by one rule (`host.parse_builder_config`, `host.parse_config`).
- **Refusals before anything is served.** The server refuses to start (exit 2)
  with one line on any of these: an unreadable or unknown-field config;
  `--no-remote`; a root that can't be created or opened;
  a library store or Level 2 facility that doesn't check out; a root that is a
  symbolic link; or a root that is, contains or is inside the server's own
  configuration (`ROSE_CONFIG_DIR`, with pairing, TLS and provider state).
- **Serving.** `Server.app_builder` is set, so `build_remote_app` mounts
  `/builder/v1` (§3) beside `/hello` and the voice websockets. It answers the
  same paired devices with the same `rose-auth-v1` signatures, and Rose
  Agent's existing device key is enough. The single build worker starts with
  the server. At shutdown it stops, and a running step is cancelled: that job is
  recorded failed and retryable ("Stopped by a server shutdown"), and the Agent
  can retry it after restart. The ledger stays open until the process exits,
  because the listener still answers `/builder/v1` until it stops. The entry
  point in the Agent is the Workspace page's "Build an app" button (Home).
- **Data.** `host.build_builder` builds the same builder, writer, key backup
  and Level 2 facilities as the standalone host. Pointing `root` at the
  directory a standalone host used serves its projects, versions, signing keys
  and records unchanged. Nothing is copied or migrated.
- **Tests.** `rose/tests/test_app_builder_one_server.py` covers the real
  `rose.app` parser and `Server` in `--ui-only` mode with a temporary
  `ROSE_CONFIG_DIR`. It checks:
  - off by default;
  - each refusal, including `main` exiting 2;
  - a paired device reaching `/builder/v1` beside `/hello` and the voice
    websockets;
  - the worker lifecycle;
  - a standalone host's ledger root served unchanged.
- **Not done:** enabling it on the Mac (Local Server), the Agent client (Home,
  §3), and retiring the standalone host (§6.2) once this path works.

### 6.2 Standalone builder host (to be retired once §6.1 works)

`rose/app_builder/host.py` runs the builder on its own machine, without the
Rose conversation server. It reads one strict JSON config file:

```
{schemaVersion: 1, root, remoteDir, name, port, adminPort, hosts[], sdk,
 javaHome?, buildTimeoutS?, keyCreation? (default false),
 writer?: {argv[], timeoutS?}, backup?: {argv[], timeoutS?}, libraryStore?,
 clientServer?: {rustToolchain, rustToolchainSha256, bubblewrap, crateStore, runtimeRoot}}
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
- **Backend endpoint.** The host passes its one ledger and that same
  ledger's `backend_endpoint` reader to `Builder(server_url=...)`. It returns
  the recorded agent container's app URL (§7.7a), else a recorded native
  host's endpoint (§7.7). A client-server job reads its project's current
  recorded endpoint when it
  generates `RoseServer.URL`; a missing record yields `null`. An explicitly
  replaced record affects later builds, not previously generated clients.
  This adds no configuration field, record writer, owner route or provisioning
  call. Chariot's owner tooling still owns recording the binding in this ledger.
- **Optional Level 2 composition.** Omitted or null `clientServer` preserves
  Level 1 behavior. An object is explicit opt-in and must supply every field
  above; booleans, incomplete objects and unknown fields are refused.
  - `rustToolchain`: an existing canonical absolute toolchain directory;
    `rustToolchainSha256`: the owner's independently supplied lowercase
    whole-tree digest in the format documented in
    [host isolation](APP-BUILDER-HOST-ISOLATION.md). The host constructs the
    existing `PinnedRustTools`, verifies it without executing it, and keeps
    the fixed `x86_64-unknown-linux-musl` target. `buildTimeoutS` applies to
    Rust tool runs too. No release, actual toolchain or production pin is
    selected or downloaded by this source.
  - `bubblewrap`: an existing canonical absolute executable path. The host
    supplies the existing `BubblewrapIsolation` provider, with no fallback.
    Its required Linux x86-64 unprivileged namespaces/kernel policy are
    checked when a job prepares its actual isolated command; construction
    and file existence are not proof of Linux confinement. The owner must
    supply the provider's supported non-setuid installation.
  - `crateStore`: an existing canonical directory with a plain, readable
    `RecordedCrates` index and available plain archive files (§7.6). An
    empty index is valid; missing dependencies still fail the offline build.
    The host does not download or populate crates. Archive contents/digests,
    dependency resolution and version locks remain the existing per-job
    server build's checks.
  - `runtimeRoot`: an existing canonical directory holding the matching
    trusted source trees `rose-app-runtime`, `rose-app-runtime-signin` and
    `rose-signin`, each with `Cargo.toml` and `src/lib.rs`. The first two
    become `server_crates`, the last `server_crate_deps`; the relative sibling
    dependency layout stays intact. Cargo package names and dependency paths
    are checked by the existing server build. The owner keeps these sources
    immutable to jobs and matched to the host's codegen source revision.
  - The host injects the existing codegen module and all these facilities
    into `Builder`, and sets its configured `CodexGenerator(client_server=True)`.
    No real writer is called at construction. With no writer configured,
    the existing no-writer failure remains. Invalid configured paths,
    toolchain pins or catalogs fail before writer/listener/ledger construction;
    they never silently revert to Level 1.
  Source composition adds no real configuration, Linux host, crate set,
  approved toolchain/pin, binding, deployment or runtime activation. A full
  pinned musl build and real Linux isolation remain unverified.
- **Signing-key backup before first delivery.**
  - `requireKeyBackup` defaults to `true`. An owner may explicitly set it to
    `false` to deliver without a configured backup. Keys still persist in the
    private project key directory; package/signer binding and missing-bound-key
    refusal are unchanged. No backup receipt is invented. Losing this Mac or its
    key storage can then make app updates impossible. A configured backup still
    must succeed; this option only permits an absent backup.
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
- the host's own ledger endpoint reader, persistence through reconstruction,
  and synthetic missing/recorded/replaced endpoints in generated client files
  (other Level 2 inputs faked; compilation deliberately stopped);
- optional Level 2 parsing and actual host composition
  (`test_app_host_level2.py`): defaults preserved, synthetic supplied facilities,
  configured writer capability through a fake transport, real generated API/
  sign-in client files and all facilities reaching the server-build boundary;
  invalid config refused before construction. Compilation is intercepted,
  so this is neither a real musl build nor Linux confinement acceptance;
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
   **Qualifying a Linux host for the server build.**
   `python -m rose.app_builder.qualify --config builder.json` checks a
   candidate host with the builder's own code, and names what's missing; it
   never weakens anything. It checks, in order:
   1. the config, which must set `clientServer`;
   2. Linux x86-64, as a user other than root;
   3. the facility checks the server runs at start;
   4. a confinement probe through the configured bubblewrap, which must find
      no network, `/etc` and home hidden, a read-only folder read-only and
      its job folder writable;
   5. one throwaway client-server job through the real `Builder`, which must
      produce a static `x86_64-unknown-linux-musl` server whose manifest
      `server_manifest.check` accepts.

   Inputs the config's `clientServer` needs:
   - **`rustToolchain`:** an unpacked toolchain tree, not rustup's proxy,
     with `bin/cargo`, `bin/rustc` and the musl target's
     `lib/rustlib/x86_64-unknown-linux-musl/lib`, and no links. Its version
     is not yet pinned.
   - **`rustToolchainSha256`:** `toolchain_digest(tree)`, supplied by the
     owner.
   - **`bubblewrap`:** non-setuid, supporting `--disable-userns`, on a
     kernel that allows unprivileged user and network namespaces, including
     loopback setup.
   - **A musl C compiler** (`musl-gcc`) under `/usr/bin`, because `rusqlite`
     builds its bundled SQLite.
   - **`crateStore`:** `index.json` plus `<sha256>.crate` files for the 51
     registry crates in `rose-app-runtime-signin/Cargo.lock`.
   - **`runtimeRoot`:** `rose-app-runtime`, `rose-app-runtime-signin` and
     `rose-signin` at the pinned sign-in revision.

   **Preparation recipe.** Each step is marked by what this session actually
   ran on 2026-11-17, in a disposable Linux x86-64 container.

   1. **Rust toolchain tree. VERIFIED.**
      - Fetch the official standalone archives and check them against their
        published `.sha256` files:
        `https://static.rust-lang.org/dist/rust-1.97.0-x86_64-unknown-linux-gnu.tar.xz`
        and
        `…/rust-std-1.97.0-x86_64-unknown-linux-musl.tar.xz`.
      - Unpack both, then install into the tree's final, canonical path, not
        inside a home directory:
        `<rust-1.97.0>/install.sh --prefix=<TREE>
        --components=rustc,cargo,rust-std-x86_64-unknown-linux-gnu
        --disable-ldconfig`, then
        `<rust-std-1.97.0-x86_64-unknown-linux-musl>/install.sh
        --prefix=<TREE> --disable-ldconfig`.
      - The result was 812 MB, with no links and no setuid or setgid file.
        `PinnedRustTools.available()` accepted it.
      - 1.97.0 is the version this run used. Choosing the pin is the owner's
        decision.
   2. **Digest. VERIFIED.** `python -m rose.app_builder.prepare digest
      <TREE>` gives `rustToolchainSha256`.
      - The installer writes `<TREE>` into `lib/rustlib/install.log` and the
        `manifest-*` files, so the digest belongs to that path.
      - Install at the final path and digest it there; never copy the tree.
      - At this run's path the digest was `299bae99…6fbf`, so another path
        gives another digest.
   3. **Crate store. VERIFIED.**
      - Fetch the crates into a dedicated download cache:
        `CARGO_HOME=<dedicated empty dir> <TREE>/bin/cargo fetch --locked
        --manifest-path workspace/rose-app-runtime-signin/Cargo.toml`.
      - Build the store:
        `python -m rose.app_builder.prepare crate-store --lock
        workspace/rose-app-runtime-signin/Cargo.lock --from
        <CARGO_HOME>/registry/cache/<index dir> --out <STORE>`.
      - It writes the 51 crates, each checked against the lock's own
        checksum, and reads the store back as the builder will.
   4. **Runtime root. VERIFIED.** `python -m rose.app_builder.prepare
      runtime-root --repo <clean checkout> --out <RUNTIME>` copies the three
      path crates (`Cargo.toml`, `src/`). It refuses uncommitted changes, and
      refuses a `rose-signin` that differs from its tree at `23062a04`.
      - The checkout's history must contain
        `23062a04899f1d5f48a441a7ef00332146ef1072`. A shallow clone is
        refused with that instruction: use a full clone, or fetch that
        commit first.
      - Both writers (`crate-store` and `runtime-root`) are all-or-nothing:
        they write a sibling folder and move it into place only when it is
        complete.
   5. **Facility check. VERIFIED.** Steps 1–4 with `/usr/bin/bwrap` pass
      `host.client_server_facilities`, the server's own start-up check.
   6. **musl C compiler. Required. Compiler and fixture evidence only,
      UNCONFINED.**
      - Without a musl C compiler, the build fails: "The crate
        libsqlite3-sys's build script failed".
      - With Ubuntu `musl-tools` 1.2.4 (`/usr/bin/musl-gcc`), one fixture job
        through the real `Builder` and real code generator produced a static
        `x86_64-unknown-linux-musl` server of 3,203,624 bytes, and
        `server_manifest.check` accepted its manifest.
      - That run replaced the confinement with a pass-through, because the
        container runs as root. It shows the inputs and compiler suffice. It
        is **not** qualification, and **not** proof that anything can be
        delivered.
      - No build will be repeated with isolation bypassed.
   7. **Confinement and `qualify` as a whole. NOT TESTED.**
      `python -m rose.app_builder.qualify --config builder.json` must run as
      a non-root user, on a host whose kernel allows bubblewrap's
      unprivileged user and network namespaces, including loopback setup.
      No such host has been run. dot-cloud fails the loopback step, and an
      attempt here as an unprivileged user also failed.

   Verified so far (2026-11-17): no host qualifies yet.
   - CI builds real servers only for the GNU target, with the runner's
     unpinned cargo and a test-only pass-through isolation.
   - Local Worker's dot-cloud Linux host fails the network-namespace
     loopback setup (`NETLINK_ROUTE … Operation not permitted`), and it has no
     Rust or `musl-gcc`.
   - The Mac can't run bubblewrap.
3. **Build isolation on that host (Server Worker).** Source now runs the
   writer under the `rose_app_build` permission profile (§4A). It denies reads
   outside the project source and the minimal runtime, allows writes only in
   the source, turns network off and refuses to start unless that exact
   profile is active and allowed. Enforcement is unverified against a real
   Codex binary: the synthetic denial check in §4A must pass on the host
   before enabling. If it fails, the writer stays off, with no broad-read
   fallback. The alternative is a separate OS user or a container that mounts
   only the project source, launched through `CodexGenerator(command=...)`.
   Enabling is a security-relevant runtime change, applied only by the Server
   Worker when authorized.
4. **Runtime (Nathan).** Nathan has asked for the Rose server to run again and
   for deployment (2026-11-15). The Server Worker and the Chariot operator
   restart and deploy; the builder host runs separately (§6).
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

The builder's job runs all of it (§7.6), and the standalone host injects
its inputs (§6). What remains is operational: a real musl build on the
builder host, a provisioned native host, and the deploy (§7.7).

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
- `server/assets/**` (data files, embedded in the server and read with `crate::assets::get("data/x.json")`,
  §7.6);
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
needing older clients to update, through the library's `server` summary
(`minClientVersionCode`, §3.3). `api` can never go down (`api_lowered`).

### 7.4 Server profile (`workspace/rose-app-runtime`: the core in source, the rose-signin adapter next)

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
  migrations are done and any restore has run. Without a key it is not
  ready, and it never invents one (§7.7, item 3).
- **Logs** never contain bodies, tokens, passphrases, codes or keys.

**The core** (`workspace/rose-app-runtime`; source, built on by nothing yet)
is standard library only, with no dependencies. It builds and tests offline
anywhere, and its own CI runs it with the runner image's toolchain, fetching
nothing (`.github/workflows/rose-app-runtime.yml`).
- **HTTP.** One bounded HTTP/1.1 request per connection: 16 KB of head, 64
  headers and a 1 MB body. It refuses:
  - `Transfer-Encoding` (so no chunked body, and no way to disagree with a
    proxy about where a request ends);
  - a repeated or malformed `Content-Length`;
  - folded or malformed headers and bare line feeds.

  Every response carries `Cache-Control: no-store` and then closes the
  connection. Closing stops writing first, then reads and drops what the
  client still sends (up to 64 KB or 1 s), so a refusal arrives whole rather
  than as a reset.
- **Connections.** At most 64 at once; more are answered `503 busy`.
  - **Deadlines.** A request must arrive within 10 s of the connection, and
    its response must leave within 10 s. Each is one deadline for the whole,
    not a timeout per read, so a client trickling a byte at a time can't
    hold a slot.
  - **Panics.** A panic anywhere in answering a request (a handler, the
    authenticator, the log sink) answers `500 internal`, and the slot is
    given back however the thread ends.
  - **Accept failures.** Failing to accept a connection (out of file
    descriptors, say) backs off briefly and goes on; it never stops the
    server.
- **JSON.** Strict: one value, no repeated key, at most 64 nested
  containers, finite numbers. Integers that fit an `i64` stay integers. The
  repeated-key check is linear in the number of members, so a 1 MB body
  can't cost quadratic time.
- **Sign-in routes** map `docs/ROSE-SIGNIN-V1.md` onto an injected `Auth`,
  rose-signin's `Authenticator` fixed to this app (its `validate` is
  `validate_for`). The documented error words and statuses are kept. Where
  the document names no status or body, App Core chose these, and the
  generated client reads them:
  - enrollment and authenticator recovery answer
    `{otpauth_uri, handle, recovery_codes}`;
  - sign-in answers `{token, account}`, plus `recovery_codes` after a
    restore issued new ones;
  - passphrase recovery answers `{recovery_codes}`;
  - confirmation and both sign-outs answer `204`;
  - statuses: `400` for `passphrase_too_short`, `passphrase_too_long`,
    `invalid_name` and `bad_request`; `403` for `not_admitted`; `409` for
    `enrollment_pending` and `already_enrolled`; `401` for `not_confirmed`,
    `sign_in_failed`, `recovery_failed` and `session_invalid`; `429` for
    `try_later`, with `Retry-After`; `503` for `auth_unavailable`.

  Sign-out answers `204` whether or not the token was live, so it reveals
  nothing. Admission (`invite` or `open`) is the app's declaration, never the
  request's. A sign-in request is read and checked (route, method, token,
  body) before the authenticator is taken. One authenticator serves the
  process, so its calls (passphrase hashing included) take turns.
- **App routes.** The contract's exact method and path under `/app/`.
  - An account route runs only with a live session of this app. The
    runtime checks `principal.app` again after `validate_for`, and the
    handler gets the principal.
  - A route with a request type takes `application/json`, and any other
    route takes no body.
  - A handler returns a value (`200`) or a 4xx word. A panic, a status
    outside 4xx, a malformed word or a non-finite number is
    `500 internal`.
  - A handler reaches the adapter's databases through `ctx.data`
    (`Server::with_data`), which it downcasts. This keeps the core free of a
    database dependency.
- **Readiness.** `/health` carries exactly `status` (`ok` or `not_ready`),
  `app_id`, `revision_sha256` and `deployment_sha256` (§7.7). The three
  identity fields are supplied at start-up, and none is made up.
  It answers `200` only when the authenticator, the migrations and any
  restore are all ready, and `503` until then. Every other route answers
  `503 not_ready` until then.
- **Logs** hold the method, the route's template (never the path the client
  sent), the status and the time taken. They hold no body, header, token,
  passphrase, code, key, account name or client address. The `Debug` output
  of requests, responses, principals, sign-in calls and their results shows
  no header value, body, account, token or code.
- **Client address.** Rate limits count by the peer's address, unless the
  server is given its host's client header (`Server::with_client_header`,
  Chariot's `X-Chariot-Client-Address`, §7.7). The header is then used
  when it holds exactly one IP address, and the peer otherwise. It is never
  trusted by default. Every generated server is given it: the generated
  `main.rs` calls `.with_client_header("X-Chariot-Client-Address")`
  (§7.5, #850).

**The adapter (slice 7b)** (`workspace/rose-app-runtime-signin`, OS Core; source, built on by nothing yet) binds `Auth` to rose-signin `23062a04`.

- **Sign-in.** `SigninAuth` wraps `Authenticator<SqliteStore, AeadSealer>`.
  - It is fixed to the app, so every session goes through `validate_for` and `revoke_for`.
  - Handles, tokens and invites cross HTTP as rose-signin base32: letters and digits, no padding.
  - A value that doesn't decode is treated as a wrong value, never as an error.
  - Closed admission without an invite is `Admission::Invite(b"")`, which admits no one.
- **Data.** Under `DATA_DIR` there are three kinds of file:
  - `auth.sqlite3`: rose-signin's own store, with its own migrations.
  - `accounts/<sha256>.sqlite3`: one file per account. The name is a length-prefixed SHA-256 of (app, account), so no name reaches the disk. The directory is `0700`.
  - `shared.sqlite3`: only when the app declares shared data.

  Handlers reach the data as `ctx.data` (`AppData`), and `account(principal)` opens only this app's principal's own file.
  - **SQLite in handlers.** The adapter re-exports its `rusqlite` (0.32, bundled, the version in rose-signin's lock). Handlers name `rose_app_runtime_signin::rusqlite::{Connection, params, OptionalExtension}`, and the generated server declares no rusqlite of its own.
- **Migrations.** The builder-embedded lists run forward only. All pending ones run in one immediate transaction with their `user_version`s. A database above the list is refused and left untouched, at start-up and on open.
  - **Foreign keys.** They follow SQLite's documented schema-change procedure. They are switched off outside the transaction, because inside one a migration's `PRAGMA foreign_keys` has no effect.
    - **The check.** After the pending migrations, the bare `PRAGMA foreign_key_check` covers every table, and the adapter also checks that no foreign key points at a table that no longer exists. Both must pass before the commit. So a parent table can be rebuilt under existing children without losing them or failing.
    - **A violation** rolls back every pending migration and refuses the database (`DataError::ForeignKeys`, which names the table only). The database stays at its old version and nothing is lost. At start-up, `start()` returns the error and the server does not start.
    - **No `ON DELETE` or `ON UPDATE` actions** act during migrations, so a migration deletes or re-keys child rows itself.
    - **Handlers** never get the connection the migrations ran on. If migrations ran, they get a fresh connection that enforces foreign keys.
- **Start-up.** `start` opens and migrates the sign-in store, the shared database and every existing account file. It then measures `revision_sha256` from the binary.
  - **Environment.** `Config::from_env` reads `DATA_DIR`, `CHARIOT_APP_ID` and `CHARIOT_DEPLOYMENT_SHA256` (echoed as passed). When the host also sets `CHARIOT_REVISION_SHA256`, a binary that measures differently is held not ready.
  - **Keys.** `Keys::from_env()` loads the keyring from `CHARIOT_SEALING_KEY_FILE`, the absolute path of an app-owned `0400` file the host exposes by a read-only bind mount. The format is `rose-signin-keyring/v1`: `{"format","current","keys":[{"version","key"}]}`.
    - Strict UTF-8 JSON of at most 16 KiB, with no unknown or repeated field. Whitespace between tokens is allowed.
    - 1 to 64 keys with unique `u32` versions, and `current` among them.
    - Each key is standard padded base64, with no whitespace, of exactly 32 bytes.
    - The path must be absolute. The opened file must be a regular file that the server's process can read, and neither the group nor others may read it (mode bits `0o044` clear). Its owner isn't checked, so an app-owned `0400` file behind a read-only bind works.
    - The file is opened without blocking, so a FIFO is refused rather than waited on. It is read once.
    - The loader zeroes its own buffers. It can't zero the JSON parser's internal copies, or rose-signin's `Secret`, which redacts `Debug` but isn't zeroed on drop.
    - Its path and contents are never logged. Any problem leaves the server without a key, and a reason word says why.
  - **Server.** `Started::server` attaches the data and marks the migrations done.
- **The creator's invite** (D7). `rose_app_runtime_signin::invite(&config, account, now)` issues the token a default `invite` app needs to admit anyone.
  - **The token.** It is the 52-character text the invitee enrolls with: single use, valid 7 days, admitting exactly that account. It comes back as `InviteToken`, whose `Debug` is `<redacted>` and which has no `Display`. Only `as_str()` gives the text.
  - **The store.** It opens only a sign-in store that a server has already set up under `DATA_DIR`, at exactly this build's schema. It never creates, initialises or migrates one. A missing, empty or uninitialised file is `not_found`, and another schema version is `store_unavailable` (start the server first). It reads no keyring: `CHARIOT_SEALING_KEY_FILE` is never consulted, because an invite is only its hash and expiry. It waits for a busy store, so it runs safely beside a serving server.
  - **Restore.** It refuses while a restore marker is present, by the start-up rule, so no token is issued into a store that is about to be replaced.
  - **Refusals.** `InviteError` (`invalid_name`, `already_enrolled`, `not_found`, `restore_pending`, `store_unavailable`) names the problem, never the account or a token. Admission isn't consulted.
  - **No route.** It is to be exposed only as the generated server's `invite <account>` command. That command belongs to Home, which owns the generated `main.rs` invite dispatch, its exit codes and its tests (S8). It is agreed but not in §7.5 yet. Until it lands, no generated server can issue an invite.
  - **The token is a credential.** It never appears in an error, a log or `Debug`. The command's stdout must go only to the person who ran it, never into the service's log capture (§7.7 item 8). How the creator runs the command on the host, and how the token reaches the invitee, are outside the adapter.
- **Restore marker** (Chariot `e592c4b1`). At start the adapter looks up `CHARIOT_RESTORE_STATE_PATH` once, without following a link. When the variable is unset, the path is `DATA_DIR/.chariot-restore.json`, and a value that is set must be absolute.
  - **Absent** (`NotIndicated`): no restore is indicated, and `restore_done` is set.
  - **Anything present** (`Indicated`): a file, directory or link, even a dangling one. The server stays `503`.
  - **Any other lookup error** (`Unknown`): the server stays `503`.
  - The marker is never read, parsed, run, removed or completed, because no restore executor or completion protocol is agreed.
- **Readiness.** A server is ready once a keyring is delivered, its migrations are done and no restore is indicated. No host delivers a keyring yet.
- **Dependencies.** The lock is rose-signin's 52 crates plus the two path crates, and a test checks this. Its CI (`.github/workflows/rose-app-runtime-signin.yml`) follows rose-signin's route.
- **Build note.** The bundled SQLite C source needs a musl C compiler on the builder.

### 7.5 Client sign-in and generated code (slice 8)

`rose/app_builder/codegen.py` turns a contract (§7.3) into source on both
sides. The same contract always gives the same bytes, whatever the order of its
keys.

**Server: `server_files(contract)`.** This returns `rose/api.rs`,
`rose/routes.rs`, `rose/wire.rs` and `rose/main.rs` (the entry point), all
matching `server_build.GENERATED`. The builder adds its own `rose/meta.rs`
(`APP`, `ISSUER`, `ADMISSION`), `rose/migrations.rs` (`ACCOUNT`, `SHARED`)
and `rose/assets.rs` (`get`, the writer's `server/assets/`, §7.6), and
codegen never writes those.
- `api.rs` has:
  - one struct per contract type, with `from_value` and `to_value` over the
    runtime's `json::Value`;
  - the `Handlers` trait, one function per route, taking `&Ctx` and the typed
    request and returning the typed response or a `Refusal`;
  - `Refusal`, a 4xx status and an error word. The word must be lower-case
    letters and underscores; the runtime answers any other word with a 500.
- Contract types named `Self`, `Handlers` or `Refusal` become `Self_`,
  `Handlers_` and `Refusal_`.
- `routes.rs` has `routes::<H>()`, the runtime's `Vec<Route>`. A route takes a
  body exactly when it has a request type, and public routes are
  `RouteAuth::Public`. A missing or malformed request body is refused with
  `400 bad_request` before the handler runs. That is the word the runtime uses
  for malformed JSON and on the sign-in routes.
- `wire.rs` checks the §7.4 wire shapes. Unknown fields are ignored. An absent
  optional is left out, and null reads as absent. `max` on a string counts
  characters, not UTF-8 bytes.
- **Bytes** travel as standard base64 with padding, both ways. Reading is
  strict: a character outside the alphabet, missing or extra padding, unused
  bits that are set, or any whitespace is `400 bad_request`. `max` on `bytes`
  counts decoded bytes.
- **The writer's code** is `server/src/handlers/mod.rs` (appprofile
  `HANDLERS`). It defines `pub struct App;` and
  `impl crate::api::Handlers for App`. The builder copies it to
  `<server>/src/handlers/mod.rs`.
  - **Data.** Handlers reach the app's databases through `ctx.data`, which
    is the adapter's `AppData`. The adapter re-exports its own SQLite
    binding (rusqlite 0.32, bundled), so the server crate never declares
    rusqlite itself.
  - **Errors.** Handlers return `Result<_, crate::api::Refusal>`, so
    storage errors are mapped rather than passed on with `?`. Any status
    outside 4xx is answered `500 internal`.
  - **Shared data.** `AppData::shared()` works the same way and refuses with
    `DataError::NoShared` when the app declares no shared data.

    ```rust
    use rose_app_runtime::server::Ctx;
    use rose_app_runtime_signin::AppData;
    use rose_app_runtime_signin::rusqlite::{params, Connection};
    use crate::api::Refusal;

    const STORAGE: Refusal = Refusal::new(500, "internal");

    fn account_db(ctx: &Ctx) -> Result<Connection, Refusal> {
        let data = ctx.data.downcast_ref::<AppData>().ok_or(STORAGE)?;
        // Always present on an account route.
        let principal = ctx.principal.ok_or(Refusal::new(401, "signed_out"))?;
        data.account(principal).map_err(|_| STORAGE)
    }

    // In a handler:
    let conn = account_db(ctx)?;
    conn.execute("INSERT INTO notes (body) VALUES (?1)", params![body])
        .map_err(|_| STORAGE)?;
    ```
- **`rose/main.rs`** is the start-up, over OS Core's adapter
  (`rose_app_runtime_signin::{start, Config, Keys, Migrations}`, slice 7b).
  - It declares the generated modules, `assets`, `meta`, `migrations` and
    `#[path = "../src/handlers/mod.rs"] mod handlers;`. So handlers read a
    bundled data file with `crate::assets::get("data/x.json")`, which
    returns `None` when there is no such file.
  - It reads only `PORT` (decimal, 1 to 65535, default 8080). The adapter
    reads and checks `DATA_DIR`, `CHARIOT_APP_ID`,
    `CHARIOT_DEPLOYMENT_SHA256`, the optional `CHARIOT_REVISION_SHA256` and
    the keyring file named by `CHARIOT_SEALING_KEY_FILE`, and looks up the
    restore marker (`CHARIOT_RESTORE_STATE_PATH`, by default
    `DATA_DIR/.chariot-restore.json`).
  - It calls `Config::from_env(meta::APP, meta::ISSUER, meta::ADMISSION,
    Migrations { account: migrations::ACCOUNT, shared: migrations::SHARED })`,
    then `let (keys, why) = Keys::from_env();`, then `start(config, keys)`,
    then `.server(routes::routes::<handlers::App>())` and
    `.with_client_header("X-Chariot-Client-Address")`, and serves on
    `0.0.0.0:$PORT`.
  - **Client address.** Chariot's gateway overwrites
    `X-Chariot-Client-Address` on every request, and guest ingress admits
    only the gateway (§7.7 item 6). So rate limits count by that header
    when it holds exactly one IP address, and by the peer otherwise. A
    generated server is meant to run only behind that gateway.
  - **Keys.** Only the adapter's loader reads the keyring
    (`rose-signin-keyring/v1`), and its result never stops start-up. When
    there is no usable keyring, `main.rs` prints
    `rose server: sealing keys unavailable: <word>`. The word names the
    problem, never the path or a byte of the file. Sign-in then answers
    `auth_unavailable` and `/health` answers 503.
  - **Readiness** is decided by the adapter, not by `main.rs`. `/health`
    answers 200 only when three things hold:
    - the keys are loaded;
    - the migrations are done;
    - no restore is indicated: nothing is at the marker's path, and only "not
      found" counts as absent.

    A marker of any kind, or a lookup that fails, keeps the server at 503 for
    that whole run. Nothing in `main.rs` or the adapter runs, reads or
    removes a restore (§7.7 item 5).
  - **Failures.** Any failure before serving prints `rose server: <reason>`
    and exits 1. The reason names the variable, never its value.
  - **Invites.** `invite <account>` uses OS Core's `invite` (§7.4).
    - It builds the same `Config` serving does, then asks the adapter for one
      single-use invite (7 days, bound to the account).
    - It prints the invite once, with a newline, on stdout and exits 0.
    - It reads no keyring, binds no port and never runs a restore.
    - The invite is a credential. Stdout goes only to the person who ran the
      command, never into the service's log capture.
    - A refusal prints `rose server: no invite: <word>` and exits 1. The
      words are `invalid_name`, `already_enrolled`, `not_found` (no sign-in
      store a server has set up; nothing is created), `restore_pending` and
      `store_unavailable`. The word never contains the account or the
      invite.
    - Anything other than no arguments or exactly `invite <account>` prints
      a usage line and exits 2. That includes a missing account, extra
      arguments, another command or an account that isn't text.
  - **Crates.** The runtime and the adapter come in as builder-owned path
    crates. Codegen writes no Cargo files.

**Client: `client_files(contract, package)`.** Everything goes under the
reserved `src/<package>/rose/`:
- `RoseJson`: a strict JSON reader and writer. It refuses repeated keys,
  trailing data, lone surrogates and nesting deeper than 64.
- `RoseTypes`: one class per contract type, the same shapes as the server.
- `RoseApi`: `Client(transport, tokens)` with one method per route.
  `RoseApi.serverUrl()` returns the builder's hosting record, its own
  `RoseServer.URL` (§7.6), and codegen never writes that file.
  - It returns null until the app is hosted. The app then says the server is
    not set up yet and never guesses one.
  - The value is fixed at build time, so the first hosted version needs a
    build after the record exists.
  - The caller passes the value to `HttpsTransport(baseUrl)`. That accepts
    `https` only, with no user info, query or fragment, and does not follow
    redirects.
  - Errors come back as `RoseApi.Error(status, word, retryAfterSeconds)`. A
    401 `session_invalid` forgets the token.
- `RoseSignin`: enroll, confirm, sign in, recover authenticator, recover
  passphrase and sign out, over the `/signin/v1/*` routes.
- `RoseSession`: of the session it keeps only the bearer token, in
  `getNoBackupFilesDir()`. It also opens, copies or draws the enrollment URI;
  `qr(uri, pixelsPerModule)` returns it as a QR code `Bitmap`.
- `RoseQr`: the QR encoder `qr` draws with, a generated port of
  `rose/qr.py` (byte mode, level M, versions 1 to 20).
- `RoseSigninCore.java`, copied byte for byte at the pin above. The builder
  refuses to run if the file's SHA-256 has changed.

**The sign-in flow.**
- The `otpauth://` URI is checked with `RoseSigninCore.parseOtpAuth`. It is
  shown as copyable text and opened with `ACTION_VIEW`. OS Core will build a
  RoseOS system authenticator for that scheme, and on stock Android any
  authenticator app claims it, so the flows are the same.
- **QR rendering** is generated: `RoseSession.qr(uri, pixelsPerModule)` draws
  the enrollment URI as a QR code (`RoseQr.java`, a port of `rose/qr.py`,
  which Rose decodes with ZXing; a test compares the two module for module).
- The user types the code and the passphrase.
- **Naming the app (#824).** OS Core's RoseOS authenticator
  (`dev.pixel.rose.authenticator`) names the sending app only when Android
  vouches for it (API 34+). So on API 34+ the client starts the `ACTION_VIEW`
  with `ActivityOptions.makeBasic().setShareIdentityEnabled(true)`. Without
  that, the dialog says "an unidentified app" and still works.
- **Names.** Issuer and account names must have no control, bidi or
  line-separator characters, and at most 128 UTF-16 units. rose-signin's name
  rules already require this.

**Tests (`rose/tests/test_app_codegen.py`).**
- The Rust side builds against `workspace/rose-app-runtime` with no warnings,
  with the writer's `App` laid out as above.
- The Java side compiles with `--release 17 -Xlint:all -Werror` and runs on the
  JVM. The Android parts compile against android-35.
- A Java request goes through the generated Rust and back.
- With `ROSE_REQUIRE_CARGO` and `ROSE_REQUIRE_APP_BUILD`, a missing tool or
  runtime crate fails instead of skipping.
- **Start-up.**
  - The port and variable rules are compiled and run on their own, with only
    `std`.
  - The whole `main.rs` builds against the adapter only where the adapter's
    crates (rose-signin's lock, with rusqlite) are already in the offline
    cache. Those crates come only by the adapter's own route. Elsewhere,
    including the app-tests job, which downloads nothing, the test is skipped
    and says why. `ROSE_REQUIRE_ADAPTER_BUILD` turns that skip into a
    failure.
  - When the build does run, it checks every refusal before anything
    listens, including the keyring reason printed before a refusal and a
    relative `CHARIOT_RESTORE_STATE_PATH`. No server is started, and no
    restore marker is created.
  - The argument rules are also compiled and run on their own.
  - Where the adapter build runs, every usage case exits 2. The invite
    refusals (`not_found` for a fresh `DATA_DIR`, `invalid_name` and
    `restore_pending`) exit 1 with nothing on stdout, and no store is
    created. No invite is issued in these tests.

### 7.6 Builder (wired in the job; each host-side part injected)

**The job** (`service.Builder`; source, run by nothing on a host yet). Every
project's snapshot is checked by `appprofile.check`, which is
`template.check` for a plain app. For a `client-server-v1` app, the job also
runs these steps before signing:
1. **Contract.** `contract/api.json` is checked against every delivered
   version's contract, which the ledger now records with each
   client-server version. Its normalised form must fit what the ledger
   keeps (512 KiB); a larger one is the writer's (`compile`), refused
   before anything is built.
2. **Migrations.** They are checked against the delivered lock and in the
   sandbox, which applies them as the adapter does (SQLite's schema-change
   procedure): the pending ones together, with foreign keys off, in one
   transaction, then `foreign_key_check` over every table and the
   adapter's check that no foreign key names a table that no longer exists
   (a dropped or renamed parent, which `foreign_key_check` misses while the
   child table is empty), under a step budget. A fresh
   install is one batch. An upgrade is the delivered set, then rows seeded
   in every ordinary table on a fresh connection (parents first, so a
   child's foreign keys point at its parents' rows), then the new set. So
   a rebuilt table that loses rows its children point at is refused here,
   as the adapter refuses it at start-up, whenever the seed can fill the
   child table. Seeding is best effort: a child table whose constraints
   refuse every sample row stays empty, and the adapter's check on a
   phone's real rows is then the only one.
   They are then embedded in builder-owned files, which a generator may
   not write (it is the builder's failure, `toolchain`); the builder writes
   its other files with them:
   - `rose/migrations.rs`, in the adapter's shape: `ACCOUNT: &[&str]` and
     `SHARED: Option<&[&str]>` (`None` unless the app declares shared
     data). The statement at index `i` takes a database from version `i`
     to `i + 1`. Numbers run 1, 2, … with no gap, so position is the
     number. Each statement is an escaped string literal, never a raw
     string, so the embedded SQL is byte for byte what was checked and
     locked (rustc rewrites a raw string's CRLF and refuses a bare CR).
   - `rose/meta.rs`:
     - `APP`: rose-signin's app name, which is the app's package (stable
       and unique to the project);
     - `ISSUER`: the project's name, as an authenticator shows it, with no
       `:` or control character, at most 128 bytes, and `Rose app` when
       nothing is left;
     - `ADMISSION`: `rose_app_runtime::signin::Admission`, from
       `rose-app.json`.
   - `rose/assets.rs`: `pub fn get(path: &str) -> Option<&'static [u8]>`,
     the writer's `server/assets/` files by their path under it (with `/`),
     each `include_bytes!` of the build's own copy, so the bytes are exact.
     It is written on every client-server build (with no assets, `get`
     answers `None`), so the generated `main.rs` always declares it
     (`mod assets;`, §7.5). At
     most 1024 files and 64 MiB in all, within the app's own 64 MB; more is
     the writer's (`compile`, `assets_too_large`). A path under
     `server/src` or `server/assets` longer than 1024 bytes is the
     writer's too (`layout_invalid`), so the build's copy always fits.
   - `src/<package>/rose/RoseServer.java` (the client's):
     `static final String URL`, the app's hosted https base URL, or `null`
     when it is not hosted yet. It comes from the injected `server_url`
     (the hosting record, which only owner tooling writes; slice 10), never
     guessed. A record the generated client would refuse fails the build as
     the builder's (`toolchain`): the builder takes only https with a
     lower-case DNS name whose last label starts with a letter (as
     `java.net.URI` reads a host; no IP address), an optional port and a
     plain path, and a test checks the generated `HttpsTransport` accepts
     each of those. It is baked into the APK, so the first hosted version
     needs a build after the record exists. The generated
     `RoseApi.serverUrl()` returns it (§7.5).
3. **Generated code.** The injected `codegen` writes the server's `rose/*.rs`
   files and the client's files under `src/<package>/rose/`. A generator
   that fails, or writes outside that folder (a `..`, `.` or empty path
   part included), is the builder's failure (`toolchain`). The writer may
   not use that folder, or put a file where one of its parent folders
   goes (`compile`, `reserved_path`).
4. **APK.** It is built from the client's tree: the writer's checked app
   without `rose-app.json`, `contract/` or `server/`, plus the builder's
   files.
   - Builder-written Java may keep its own package. The sign-in core is the
     case: byte-identical, package `dev.pixel.rosesignin`.
   - That exemption covers only those files and their packages. The
     writer's Java and classes still have to be in the app's package.
5. **Server.** It is built by `server_build` with the injected isolation,
   Rust toolchain and crate set. The builder's own crates, the runtime
   and its adapter, are read-only path dependencies, remapped out of the
   binary; a binary that still holds one of their folders is refused like
   any other builder path.
6. **Delivery.** The ELF is stored by its digest (`artifacts/<sha256>.server`).
   The version is delivered with its server, migration lock, crates,
   `Cargo.lock` and contract, all in one transaction.

With no `codegen`, no runtime or adapter crate (`server_crates` without
`rose-app-runtime` or `rose-app-runtime-signin`, which the builder's own
generated code names), no isolation or no toolchain, a client-server job
fails as the builder's (`toolchain`) before anything is half built or
delivered. So does a builder crate set up wrong: missing, a folder holding
another package, a path dependency reached through a symlink or outside the
given crates, a folder the binary's remapped crate names would contain, or a
binary that names a builder crate's folder. A crate set is needed by an app
that declares crates (without one it fails as `dependency`) and by builder
crates that use crates.io crates, as the adapter does (without one, as
`toolchain`). A generator's client file that cannot be written is the
builder's too. A plain app's job is unchanged.

- **The writer's policy** (`workspace/rose-agent/server/app-build.mjs`, the
  fixed `app_build` instructions, §4A) describes a client-server app in the
  shapes the builder checks and generates, so a Codex writer can produce one
  when the owner's app needs its own server:
  - `rose-app.json`, the contract and the migrations, with the rules above;
  - `server/src/handlers/mod.rs` against the generated `Handlers`, with
    the adapter's `AppData` and `rusqlite`, `Refusal` and `crate::assets`;
  - the Android side through the generated `RoseApi.serverUrl()`, `Client`,
    `RoseSignin` and `RoseSession`, off the main thread, with
    `android.permission.INTERNET`;
  - on a revision, the delivered migrations kept and the contract
    compatible unless `api` rises.

  The same text goes to every job, so it offers the profile only when the
  turn says the builder builds client-server apps: `CodexGenerator(
  client_server=True)`, which a host sets only once it injects the
  Builder's code generator, isolation, Rust toolchain and builder crates
  (the standalone host does so only with explicit `clientServer`, §6).
  Otherwise the writer builds the app without a
  server and says what would need one. It also tells the writer that
  `RoseApi.serverUrl()` is null until the app's host is recorded, so the app
  must work with null and its reply never claims the server runs or syncs,
  and that pending migrations share
  one transaction with foreign keys off before every reference is checked
  (so `ON DELETE` does not fire in a migration, a rebuild is fine when the
  referenced rows stay, and a broken reference refuses the update at
  start-up with nothing lost). It limits nothing about what the app does.
  `rose/tests/test_app_writer_policy.py` reads the policy through node and
  checks each rule and name against the builder's own constants and its
  generated code; `app-build.test.mjs` checks the source is one plain
  template literal.
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
  | `build_isolation_unavailable` | toolchain | The host supplied no isolation, its `command()` raised, or its program is missing |
  | `build_isolation_unavailable` | toolchain | A cargo config sits in a folder above the job |
  | `rust_toolchain_unavailable` | toolchain | No cargo or rustc, no standard library for the target, no linker, or cargo wrote no lock or binary |
  | `crate_set_invalid` | toolchain | The set can't be read, or a crate in it fails the checks above |
  | `no_crate_source` | dependency | Crates are declared and the host has no set |
  | `crate_unavailable` | dependency | A declared crate, or one it needs, isn't in the set, or no version in the set fits |
  | `crate_changed` | dependency | A crate differs from the set's digest or from a delivered version's |
  | `crate_compile` | dependency | A crate, or its build script, failed |
  | `build_path_embedded` | dependency | The binary holds the job's or the set's path |
  | `server_not_static` | compile with no crates, else dependency | The binary needs a program interpreter or a shared library (§7.7) |
  | `server_binary_invalid` | toolchain | The binary is not a 64-bit x86-64 Linux executable (§7.7) |
  | `server_compile` | compile | The writer's server code didn't compile |
  | `build_isolation_failed` | toolchain | The isolation's own launcher failed (for example the kernel refused a namespace), as its `failed()` says |
  | `server_build` | toolchain | Any other cargo failure: with no compiler error in the app's own code, it is never the app's |

  Logs have the job's, the toolchain's and the set's paths redacted.
- **One version** (the ledger; source, called by the job's delivery):
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
  - **A sandbox.** The migrations are applied in an in-memory SQLite as
    the adapter applies them: the pending ones together in one transaction
    with foreign keys off, then `foreign_key_check` and the check for
    references to a table that no longer exists, under a step budget
    (step 2 above). Temporary data stays in memory (`temp_store = MEMORY`), and a
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

> **Delivery path superseded (2026-11-16).** Generated backends go to the
> Chariot agent container, set up over the Chariot CLI and SSH (§7.7a). The
> native-host calls below (`/v1/app-hosts`, the deploy plan, host records,
> invites over that API) are not part of the new delivery path. They stay in
> source only until §7.7a replaces them. These still apply:
> - the server ELF and manifest;
> - its runtime environment (`PORT`, `DATA_DIR`, sealing keyring, restore
>   marker);
> - its health and readiness rules;
> - the data and migration protections.

App Core accepts this boundary. The server is one Rust ELF plus a manifest,
with nothing Chariot-specific inside the app. The fake host in tests checks
the same manifest.

**The manifest** (`app_builder/server_manifest.py`; source). This is App Core's description of one version's server. It is not
Chariot's own wire format, because further manifest fields are still open
(item 1 below). Its keys follow the host's snake_case, as the readiness keys
do:
- `schema_version: 1`, `project_id`, `version_code`;
- `elf: {sha256, size, target}`, with target `x86_64-unknown-linux-musl`;
- `listen: {env: "PORT", default: 8080}`;
- `health: "/health"`;
- `data_dir: {env: "DATA_DIR"}`: the only writable path, holding `TMPDIR`;
- `signin: {revision: "23062a04"}`;
- `schema: {account, shared}`: the highest migration number per scope, with
  `shared` only when the app declares a shared database.

It names no host identity and no secret: no `app_id`, no deployment hash,
and nothing about the sealing key (item 3). The manifest's bytes are
canonical JSON, so one manifest has one digest. Decoding is strict: no
repeated key, no float or NaN, at most 16 KB, and only the canonical bytes. `check` holds a manifest to exactly this schema, and, when
given them, to its project, its version and its binary. That function is
what the fake host checks now, and what the Chariot adapter will check later.

**Recorded with each delivered version.** When a client-server version's
server is built for the agreed target (`x86_64-unknown-linux-musl`), the
job builds its manifest from the job's own facts and the ledger records the
canonical bytes with the version, in the same transaction
(`server_manifest(project, versionCode)`).
- **Matching:** the ledger refuses a manifest that names another project,
  version or server, so nothing is half recorded.
- **Other targets:** a server built for any other target, such as a build
  machine's own target in CI, has no manifest. It is still delivered, but
  it is not one the host runs.
- **What a host is handed:** the ELF (`artifacts/<sha256>.server`) and that
  manifest. Slice 10's fake host test holds the two to `check`, as the
  Chariot adapter will.
- **Not Chariot's wire format:** the fields stay App Core's schema version 1.
  Chariot's deploy takes its own body, which the deploy plan (below) maps from
  this manifest.

**The binary check** (`check_elf`, also run by every server build). It
reads the ELF's own headers, every offset bounded by the file, and accepts
only a static 64-bit little-endian x86-64 Linux executable:
- `ET_EXEC`, or `ET_DYN` with a dynamic section and no `DT_SONAME` (static
  pie);
- an entry point and an executable segment;
- no program interpreter and no `DT_NEEDED`.

A binary that needs an interpreter or a shared library is
`server_not_static`. With no crate in the build, it is the writer's own code
(a link section, say), category `compile`. With crates, the builder can't
tell a crate asking for a shared library from the writer. It reports
`dependency`, the usual cause. Any other mismatch is
`server_binary_invalid`, category `toolchain`. The build passes
`-Ctarget-feature=+crt-static`, the musl target's default, so every target
the builder runs links the same way. The check decides nothing about what
the server does.

**Agreed with the Chariot owner (relayed by Chief, 2026-11-15):**
- **Binary:** a static x86-64 musl ELF, taking `PORT` and `DATA_DIR`, behind
  a stable HTTPS endpoint. A stopped or unhealthy server answers `503` with
  `Retry-After`.
- **Readiness** is served on the app's own listener (default `8080`), with
  no separate liveness check. `/health` returns `200` only once the sealer is
  ready, migrations are done and any restore has run, and `503` until then.
  It carries the same identity fields either way, and no secrets, request
  data or account information. The wire keys are the host's own, in
  snake_case (Chariot's guest contract, `native-app-hosts.md`), and the
  runtime writes them as they are, with no adapter in between:
  - `status`: `ok` when ready. When not ready it is `not_ready`; that word
    is App Core's, since the contract names only the ready body.
  - `app_id`: the Chariot host's UUID, from `CHARIOT_APP_ID`. It is not the builder's `projectId`.
    The builder's ledger keeps two separate, explicit mappings from
    `projectId`: to this native host id (`record_native_host`, below), and
    to the hosted APK app id that §3.3 `share` reads (`record_hosted_app`).
    Both are written only from owner tooling or publisher results, never
    guessed.
  - `revision_sha256`: measured by the server itself, by hashing its own ELF
    at start. If the host's `CHARIOT_REVISION_SHA256` differs, the server
    stays not ready.
  - `deployment_sha256`: the authoritative value is the
    `CHARIOT_DEPLOYMENT_SHA256` the host passes, which the server echoes. Any
    hash the server computes itself over its args, port and schema is
    reported separately and never replaces it.
- **Publication** uses Chariot's existing creator auth, by sha256, never the
  phone or the APK. The publication adapter isn't finished, and there is no
  live URL or credential.
- **Client base URL (Chariot owner's answer, relayed by Chief,
  2026-11-15).** The builder bakes the native host's `HostView.endpoint`,
  as recorded from owner tooling, into each client-server APK as
  `RoseServer.URL` (§7.6).
  - **Stability:** the endpoint is stable through deploys, rollbacks,
    restarts and sleep while the native host id and its domain stay the
    same. A replaced host or a changed domain can need the client rebuilt.
  - **Before the first deploy:** Chariot's authenticated
    `POST /v1/app-hosts` returns the endpoint before any binary is
    deployed, but it provisions a VM. It is **held**: nothing here calls
    it, and no host is provisioned.
  - **Not from public metadata:** Chariot's public APK metadata has no
    native host or server mapping. The builder never derives the endpoint
    from it, or from anything else.
  - **The record** (the ledger; source; no real binding recorded). Owner
    tooling records `record_native_host(project, hostId, endpoint)`: the
    host's lower-case UUID (`CHARIOT_APP_ID`, above) and its endpoint,
    checked with `HOSTED_URL` (what the generated client accepts), at most
    2048 characters, and in one spelling: no trailing `/` and no default
    port `:443`, since the client reaches the same server either way.
    - It is its own record, never the hosted APK app id (§3.3 `share`).
    - The same record again changes nothing. A different one is refused
      unless `replaces` names the current record exactly, so a replaced host
      or a changed domain is an explicit change, never a silent move.
    - A host id or endpoint another project holds is refused, and so is an
      endpoint another project holds as its agent container URL (§7.7a), so
      no two projects' records name the same host or server. Where a domain
      actually routes is Chariot's to decide.
    - The host passes `backend_endpoint(project)` as the Builder's
      `server_url`. That is the agent container's URL when one is recorded,
      else this endpoint, and the build checks the value again. Versions already
      built keep the endpoint they baked until they are rebuilt.
  - **The writer** (`app_builder/owner_records.py`; source, run by no one
    yet) is the one source path for owner tooling. It records, or shows, a
    project's records in a builder's ledger:

    ```
    python -m rose.app_builder.owner_records show --ledger DIR --project ID
    python -m rose.app_builder.owner_records native-host --ledger DIR --project ID \
        --host-id UUID --endpoint URL [--replaces-host-id UUID --replaces-endpoint URL]
    python -m rose.app_builder.owner_records hosted-app --ledger DIR --project ID --hosted-app-id UUID
    ```

    **Caller contract, for Chariot's owner tooling:**
    - Pass the values exactly as Chariot's own systems returned them. The
      tool derives, guesses, fetches and provisions nothing, opens no
      connection and reads no credential.
    - `DIR` must already hold a builder ledger; none is created.
    - A replaced host or a changed domain names the record it replaces
      (`--replaces-*`, both together).
    - Output is one line of JSON with a `status`. `ok` and `refused` also
      carry `projectId`, `nativeHost` and `hostedAppId`; every status but
      `ok` carries `message`. Status and exit code:
      - `ok`, 0: recorded, or already so;
      - `usage`, 2;
      - `refused`, 3: the ledger refused the value;
      - `not_found`, 4: no such ledger or project;
      - `busy`, 5: another writer held the ledger; nothing recorded; try
        again;
      - `error`, 6: the ledger could not be opened or written (read-only,
        full, I/O); nothing recorded; not a retry.
    - Each record is one write transaction (`BEGIN IMMEDIATE`), so the tool
      is safe while the builder runs. A build already running keeps the
      endpoint it read.
  - **Not done:** nothing runs the writer yet. Chariot owns its receipt
    caller, and the call that gives the endpoint is held. The standalone
    builder host passes its ledger's endpoint reader as `server_url` (#861);
    with no recorded binding it still yields `null`. This does not enable
    hosting. Other Level 2 inputs are composed only when explicit
    `clientServer` configuration is supplied (§6). Production DNS, TLS and
    ingress for an endpoint are unverified.

- **The deploy plan (App Core's handoff to Chariot's deploy).** Chariot's
  agreed owner calls (ProtocolsBackend #2564, `chariot/docs/native-app-hosts.md`)
  deploy a native server in two steps. First
  `PUT /v1/app-hosts/{host}/artifacts/{sha256}` with the raw static musl ELF
  (at most 64 MiB). Then `POST /v1/app-hosts/{host}/deploy` with
  `{sha256, args, port, health_path, data_schema}`. Chariot computes its own
  `deployment_sha256` from those; App Core's manifest is not sent.
  `python -m rose.app_builder.server_delivery plan --ledger DIR --project ID
  [--version N]` reads the builder's records and prints that plan for one
  delivered version, the newest by default:
  - `hostId` and `endpoint` come from the native host record.
  - `artifact` is `{path, sha256, size, target}`. The stored ELF is re-hashed,
    and must still be the one the version recorded.
  - `deploy` is `{sha256, args: [], port: 8080, health_path: "/health",
    data_schema}`. `args` is always empty, because the generated server
    serves only with no arguments. `data_schema` is
    `1 + auth × 100000000 + account × 10000 + shared`, as agreed with
    Chariot (#2566):
    - `account` and `shared` are the manifest's highest migration numbers,
      each 0–9999 (`shared` is 0 with no shared database).
    - `auth` is rose-signin's own store schema (`SCHEMA_VERSION`, `auth_*`
      migrations in `auth.sqlite3`) at the manifest's pinned sign-in
      revision. It is 2 for `23062a04`, the only revision a manifest admits.
      `server_delivery.AUTH_SCHEMA` maps the revision to it, and a test holds
      that map to `workspace/rose-signin/src/store/sqlite.rs`, which the
      server is built against. So a sign-in store change can't ship under an
      unchanged tag. A revision not in the map is refused.
    - It is one integer for the schema of all three databases, and at least
      `100000001` because `auth` ≥ 1. The producer keeps it at or below
      2³¹ − 1 until Chariot confirms a wider integer.
  - **Earlier tags.** Before #2566 the plan sent `1 + account × 10000 +
    shared`, which is at most `100000000`, so no new tag equals an earlier
    one. Every manifest ever recorded names `23062a04` (auth 2), so an
    earlier tag `T` describes exactly the same data as the new tag
    `T + 200000000`. A host whose active deployment recorded an earlier
    tag would otherwise get a 409 on its next same-schema deploy. Whether
    any such deployment exists is in Chariot's records, not App Core's.
    The plan tool records nothing.
  - **Refused (exit 3):** a version with no server, or with no manifest (built
    for another target); a server over 64 MiB; no native host recorded; or a
    stored binary that is missing or changed. The other statuses follow
    `owner_records`: `usage` 2, `not_found` 4 (no such ledger, project or
    version), `error` 6.
  - **It only reads.** It opens the ledger read-only (`Ledger.reader`,
    SQLite `mode=ro`: no directory mode, pragma or schema is touched). It
    uploads, deploys, records and contacts nothing, and holds no credential.
    Chariot's owner tooling makes both calls with its own creator
    authentication and its own Idempotency-Key, then reads `HostView`; a
    running host is not a ready app.
  - **Readiness needs the host's sealing key (D9).** The server reports
    ready only with its keyring loaded, so a host with no sealing-key binding
    stays at 503 and the deploy never becomes ready. That is the designed
    fail-closed path, not a plan error.
  - **No data snapshot yet.** Chariot v1's pre-deploy `.backup` covers only
    `CHARIOT_SQLITE_PATH` (`DATA_DIR/app.sqlite`), which the generated server
    never creates. Its data is `auth.sqlite3`, `accounts/*.sqlite3` and
    `shared.sqlite3` under `DATA_DIR`. So a deploy today replaces the code with
    no snapshot of that data, and a rollback restores no database (item 9).
  - **Start-up (agreed, #2566).** Chariot allows 120 s from start for
    `/health` to answer 200, then reports the deployment unavailable. It
    makes no automatic rollback and no automatic restore. The server binds
    its listener only after every migration, so until then a health check
    gets connection refused, and the time grows with the number of
    accounts.
  - **Health fields (agreed, #2566):** the snake_case `status`, `app_id`,
    `revision_sha256` and `deployment_sha256`.
  - **Schema changes.** Chariot v1 refuses (409) a deploy whose
    `data_schema` differs from the active one. So a version that adds a
    migration can't be deployed over an older one until §7.7 item 4
    (schema-changing deploys) is done.
  - `rose/tests/test_app_server_delivery.py` checks each case, and that every
    plan's `deploy` body is within Chariot's `DeployNative` bounds.
- **Creator invites (agreed source contract; the guest channel is
  unbound).** Chariot's source is ProtocolsBackend #2564 at `814fd66c`
  (`chariot/docs/native-invites.md`, `hosting/native_invites.py`). It runs the
  generated server's `invite <account>` command (Home, §7.5) on a native host
  for the creator. The command calls OS Core's `invite` (§7.4).
  - **Call.** `POST /v1/app-hosts/{host_id}/invites`, under Chariot's existing
    owner authentication. It takes a nonzero UUID `Idempotency-Key` and
    `{account, revision_sha256, deployment_sha256}`. `host_id` is the native
    host record (above), never the hosted APK app id. The account is passed
    exactly as given, 1 to 128 UTF-8 bytes, and OS Core's adapter judges it.
  - **Execution.** Under the host's execution lease, Chariot refuses a pending
    operation, a host that isn't running, or a revision or deployment other
    than the request's. It never wakes, deploys or retries.
    - It measures the recorded executable's SHA-256 and checks it against the
      active revision before running anything. The command itself runs no
      revision check.
    - It runs `[executable, "invite", account]` with the service's own UID,
      GID, working directory and exactly its environment: `DATA_DIR`,
      `CHARIOT_APP_ID`, `CHARIOT_DEPLOYMENT_SHA256`, and
      `CHARIOT_REVISION_SHA256` and `CHARIOT_RESTORE_STATE_PATH` with their
      original values when present. Absent ones stay absent, and the restore
      path is never rebuilt from `DATA_DIR`.
    - stdin is null, with no TTY and no keyring. Output is captured privately,
      never into the journal or any log.
  - **Limits:** 30 s; stdout exactly the 53-byte token line
    `^[A-Z2-7]{52}\n$` with empty stderr; stderr capped at 256 bytes.
  - **Result:** `{outcome, reason, exit_code, replayed, token}`, sent
    `no-store` with no `Retry-After`.
    - `issued` (200): the token is in the first delivery only. A replay of the
      same key gets `token: null`; a token can't be retrieved or reissued.
    - `refused`: the adapter's words. `invalid_name` is 422;
      `already_enrolled`, `not_found` and `restore_pending` are 409;
      `store_unavailable` is 503. Chariot's own lease refusals
      (`operation_pending`, `deployment_changed`, `host_unavailable`) are 409
      or 503. The command's usage refusal is 502.
    - `outcome_unknown` (503): any other exit or output, including
      `the invite could not be written`, a timeout, a signal or an inconsistent
      receipt. A token may exist, so nothing is reissued automatically; a new
      invite needs a new explicit request.
    - Chariot keeps a non-secret attempt record only: no account, token or
      output.
  - **Fixture.** Chariot's `chariot/test/fixtures/native_invites/results.json`
    is copied byte for byte to
    `rose/tests/fixtures/chariot/native-invites-results.json` (git blob
    `925b85f8`). `rose/tests/test_app_native_invites.py` holds it to OS Core's
    refusal words and the token's shape. Home's generated command is checked by
    Home's own tests.
  - **Not done.** The real inspector and runner (the authenticated guest
    channel) are unbound, the router is unmounted, and no invite has been
    created anywhere.
- **Client address.** Chariot's gateway overwrites `X-Chariot-Client-Address`
  on every request, and guest ingress admits only the gateway (Chariot's
  boundary to enforce). A server given `Server::with_client_header` counts
  rate limits by that header when it holds exactly one IP address, and by
  the peer otherwise. Without it, only the peer counts, so the header is
  never trusted by default.
- **Sealing key (D9): agreed, in source, enabled nowhere.** Chariot resolves
  a pinned Secret Manager version on the host. It hands the payload,
  opaque, to the server as an app-owned 0400 regular file, bound read-only
  into the service's namespace at the absolute path in
  `CHARIOT_SEALING_KEY_FILE`, outside `DATA_DIR`, backups and the ELF. The
  server never sees the descriptor or its credentials.

  The file is `rose-signin-keyring/v1` (App Core's spec; OS Core's loader):
  - **Shape:** `{"format":"rose-signin-keyring/v1","current":<u32>,"keys":[{"version":<u32>,"key":"<base64>"}]}`.
  - **Encoding:** strict UTF-8 JSON, at most 16 KiB. Unknown or repeated
    fields are refused.
  - **Keys:** 1 to 64, with unique u32 versions and `current` among them.
    Each `key` is standard padded base64 with no whitespace, decoding to
    exactly 32 bytes.
  - **The file:** the loader takes it only as a regular file the process can
    read and no group or other user can read.
  - **Reading:** once, at start, never logged. The loader wipes its own
    buffers. The JSON parser's copies and rose-signin's `Secret` are not
    zeroized; `Secret` only redacts its `Debug` output.
  - **Hand-off:** the keys go to `AeadSealer` as current plus retained
    versions.
  - **Failure and rotation:** a missing or bad file means not ready (`503`),
    with no fallback key. Rotation is a restart.

Still open for the Chariot owner (App Core's proposal in brackets):
1. Further manifest fields [resources, shutdown grace].
2. App id variable: settled, `CHARIOT_APP_ID`.
3. Sealing key: settled, as above. A real key, its permissions and its
   delivery on a live host are not exercised.
4. Schema-changing deploys, which return a conflict today [the manifest
   states `schema: {from, to}`; the owner confirms; Chariot snapshots
   `DATA_DIR` first; the server migrates forward only and is ready only
   afterwards; rollback restores the snapshot and the previous ELF].
5. Restore: a v1 rule is agreed, and reconciliation stays open. The
   marker is `CHARIOT_RESTORE_STATE_PATH`, which defaults to
   `DATA_DIR/.chariot-restore.json`.
   - **Chariot** blocks startup while anything is at that path, malformed
     or a dangling link included. It has no executor.
   - **The adapter (OS Core)** checks for presence only, without following a
     link. Nothing there means no restore is indicated, so `restore_done`
     is set. Anything there means it stays not ready. It never reads,
     removes or completes a marker.
   - [Reconciliation, once an executor exists: the executor writes the
     marker before it replaces a database. The adapter runs rose-signin's
     `restore_after_backup(now)` and writes a completion receipt binding
     the marker's sha256 to `revision_sha256`. Chariot verifies the receipt
     and removes the marker.]
6. Client address: the header is agreed, `X-Chariot-Client-Address`
   (above). Every generated server counts rate limits by it, because the
   generated `main.rs` calls `.with_client_header(..)` (§7.5).
7. Rollback [keep the previous ELF; report the running sha256].
8. Process and logs [SIGTERM within a grace period; no secrets in logs].
9. Pre-deploy data snapshot [with the service stopped, back up every regular,
   non-symlinked `*.sqlite3` under `DATA_DIR` (`auth.sqlite3`,
   `shared.sqlite3`, `accounts/*.sqlite3`) with `sqlite3 -readonly .backup`
   into `backups/pre-<deployment>/<relative path>`. Use the symlink refusal
   Chariot already applies to `app.sqlite`, and refuse a symlinked
   `accounts/`. Item 4's "snapshot `DATA_DIR` first" is the same need for
   schema-changing deploys].

Older APKs on phones keep working against a newer server version. That is
the generated server's job, through a versioned API and the stable base URL.

### 7.7a Backend in the Chariot agent container (in source, off unless `agentHost` is configured; no live target yet)

**The settled architecture (Nathan, 2026-11-17).** The building agent chooses
the backend's stack and installs and builds it itself, in the app's own Chariot
agent container:
- It is language-agnostic. Python is supported and Rust is an author choice where
  it fits; neither is required.
- There is no extra VM, no imposed per-app toolchain, and no local Rust or
  bubblewrap build of the backend.
- The builder compiles no backend. It switches to the agent's release with
  the backup, health check and rollback below.

**Agent-built backends (`client-backend-v1`).**
- **The writer's files.**
  - `rose-app.json` is `{"schemaVersion": 1, "profile": "client-backend-v1"}`.
  - `contract/api.json` is the API. The builder generates the Java client
    from it, as before: RoseApi, RoseTypes, sign-in, RoseInvite and
    `RoseServer.URL`.
  - `backend/` holds the backend's own source, in any language. It is kept in
    the project for later revisions, never compiled or checked by the
    builder, and never part of the APK. The APK's file-name rules (Java only,
    no scripts) don't apply there; links, modes and sizes still do.
- **The one tool.** The writer stays exactly as isolated as before: no
  network, no credentials, its own shell only in the project source. For
  this profile it gets one host-run tool, `agent_container {command,
  stdin?}`:
  - The bridge offers it only when the builder opens the session with
    `containerTool` (codex-voice-bridge.mjs, app-build.mjs
    `AGENT_CONTAINER_TOOL`).
  - The Builder answers each call (`generator.CodexGenerator._tool`). It
    runs `/chariot/bin/sh -c COMMAND` in the project's own recorded agent
    container, through the owner's `agentHost` Chariot CLI and its HOME
    (`AgentHosting.exec`).
  - The model never chooses the container, and never sees or holds the CLI's
    certificate or token.
  - The first call records the container from the owner-listed pool and
    makes the release folder, so a client-only app takes no agent.
  - Before any of the agent's commands run, the agent is checked to be the
    recorded one and to hold this app or nothing. A pool agent holding
    another app's server or data is never recorded for this one.
  - Each call stops, and its `chariot ssh` is killed, on a cancel or at the
    writer's deadline.
  - The tool is offered only to projects that are new or already
    `client-backend-v1`.
- **The release.** The agent builds this attempt's release in place, in
  `$HOME/workspace/.chariot-app/releases/<release id>`. The id is a sha256
  of the project, job and attempt. The release is never moved, so absolute
  paths inside it stay valid.
  - It must contain `start`: a POSIX sh script run by `/chariot/bin/sh
    ./start` from that folder, ending by exec-ing one foreground server.
  - That server listens on `127.0.0.1:$PORT` and answers `/health` with 200,
    with `$ROSE_RELEASE` in the body. Its data lives only under `$DATA_DIR`,
    migrated forward in place.
  - An optional `invite <account>` prints one opaque token (printable ASCII,
    no whitespace, at most 512 characters). A backend with no creator
    invites ships none, and the route then answers `invites_unsupported`.
- **The switch** is the same procedure as below, over release folders.
  - The launcher's `start` is a fixed shim. It sets `PORT`,
    `ROSE_LISTEN_HOST`, `DATA_DIR`, `CHARIOT_SEALING_KEY_FILE` when a keyring
    exists, and `ROSE_RELEASE` / `CHARIOT_DEPLOYMENT_SHA256` (the release
    id), then runs the release named by `current`.
  - Health is a 200 from `/health` whose body names the release, so a
    process left on the port never passes for it. A port that already
    answers before the launch is refused (`port-busy`).
  - Old releases are pruned only after the switch has succeeded, whatever
    modes their files have, so pruning never fails a switch. Preparing a
    release also removes attempts that were never switched to; only the
    current and previous releases stay.
  - `deployed.json` names the release. Its `dataSchema` is null for an
    agent-built backend, which migrates its own data.
  - A release without `start` is refused before anything is touched.
- **The job.**
  - The writer runs and builds the release.
  - The builder generates and builds the client, then signs it.
  - It switches to the release (`AgentHosting.deploy_release`).
  - It delivers the version with `{release}` and its contract
    (`ledger.CLIENT_BACKEND`; no ELF, lock, crates or manifest).
  - The version gets `versions[].hosting`, and creator invites run the
    release's `invite`.
  - A failed switch delivers nothing, and an undelivered release is reverted.
- **Turning it on:** `agentHost` alone. `clientServer` (the builder-built Rust
  path below) is retired and ignored.
- **Tests:** `test_app_agent_backend`, plus the node app-build tests.

The remaining builder-built text in this section is historical compatibility
context. Existing apps migrate as described in APP-BACKEND-MIGRATION.md;
new Rust binary builds are no longer offered.

Nathan's direction (2026-11-16) puts each generated app's backend in the
existing Chariot agent container. Rose's existing Codex obtains it with the
Chariot CLI, then installs, configures and runs the server over SSH. That
replaces the native-host deploy API in §7.7 for the delivery path:
`/v1/app-hosts`, `server_delivery plan` and `owner_records native-host` are
not part of it.

**Which Codex does it.** Neither existing Codex process changes:
- The writer (`app_build`, §4A) stays network-off and read-isolated. It
  writes untrusted-request code and must not hold Chariot credentials.
- The voice worker keeps its shell disabled.

**Who runs the setup.** The builder itself, in the one Rose server's build
worker (`rose/app_builder/app_host.py`). It runs fixed scripts over the existing
Chariot CLI (`chariot ssh AGENT -- /chariot/bin/sh -s`, script on stdin). No
model turn is involved, so no Codex process gains a shell, network or
credentials. The `app_host` bridge purpose proposed earlier is not needed.
Which binary, data and keys are touched is the builder's code, not a model's
choice, and the data-safety rules below are enforced by that code.

**Turning it on** (builder config, beside `clientServer`, which it requires):
```
"agentHost": {"chariot": "/abs/path/chariot", "home": "/abs/path/chariot-home",
              "agents": ["<agent name>", ...], "generateKeyring": false, "port": 8090}
```
- `chariot`: the Chariot CLI.
- `home`: the `HOME` where its owner ran `chariot login`. Only `HOME` is
  passed; the CLI keeps its certificate there. The builder never reads it.
- `agents`: 1 to 16 agents the owner has authorized for apps, one app each.
  This list is the whole pool. A project is given the first listed agent not
  already holding another project's app, by the identity that agent reports
  (`CHARIOT_APP_ID`, `CHARIOT_APP_URL`). No agent is discovered, listed or
  created.
- `generateKeyring` (D9): whether the setup may write a new sealing keyring
  (`rose-signin-keyring/v1`, one 32-byte key, mode 0400) into an agent that
  has none.
  - Only a builder-built (`client-server-v1`) server needs one: with `false`,
    an agent without one is refused for it before anything is touched.
  - An agent-built (`client-backend-v1`) backend needs none and starts
    without one; `CHARIOT_SEALING_KEY_FILE` is set only when a keyring
    exists. So D9 does not gate agent-built backends.
- `port`: the loopback port the server binds and `app.json` names. The
  default is 8090; 8088 and 42617 are refused.

**The job.** For a client-server version whose server is built for
`server_manifest.TARGET` (musl):
1. Before the client is generated, `ensure` records the project's agent
   container (`record_agent_container`, below), so its URL is baked. A hosted
   build without a record is refused.
2. After the APK is signed and placed, and before the version is delivered,
   `deploy` sets the server up. A failure fails the job (`toolchain`,
   retryable) and delivers nothing.

A server for another target is not delivered while `agentHost` is set.

**The setup** (`deploy`, following `chariot/docs/agent-app-hosting.md` at
30349c92):
1. **Probe.** The agent reports `CHARIOT_APP_ID` and `CHARIOT_APP_URL`. It must
   be the recorded agent, with every applet the scripts use in `/chariot/bin`.
2. **State.** The deployed record (`deployed.json`: ELF sha256, data
   schema, version, project) is read. The agent must hold this project's
   app or nothing:
   - another project's record is refused;
   - a server, a start script or data the builder did not set up is
     refused, and left untouched;
   - an older `data_schema` than the deployed one is refused, so older code
     never runs on newer data;
   - a deployed release whose version was never delivered (the job failed
     or stopped after its setup) is reverted first (see below).
3. **Prepare.** An incoming folder is made under
   `$HOME/workspace/.chariot-app`.
4. **Transfer.** The server ELF and its start script go as a tar on stdin.
5. **Switch.** Every tool is called by its `/chariot/bin` path, which is not
   on PATH, and only with options BusyBox's applets take (its `flock` takes
   `-s -x -u -n` only). In order:
   - The keyring is checked first. A builder-built server without one, and
     without `generateKeyring`, is refused before anything is touched; an
     agent-built backend goes on without one.
   - If an earlier switch was cut off (its `switching` marker is left), that
     is undone before anything else: the deployed release is started again,
     after `latest.tar` is put back if the marker says another binary may
     have run. If the data cannot be put back, nothing is started and the
     marker stays.
   - A server byte-identical to the deployed one (a version that changed
     only its client) is just started and published if needed: no backup,
     no new previous release, and nothing to undo.
   - `app.json` is removed and the server stopped. A server runs while
     Chariot's launcher holds `run.lock`; it gets TERM, then KILL, and only
     while the lock is held, so a stale `server.pid` is never signalled.
   - The data directory is archived to `backups/taking.tar`. Only once that
     is whole does it become `latest.tar`; the archive before it is kept as
     `previous.tar`.
   - The `switching` marker (written before the stop, stage `stopped`) moves
     to stage `installing`. The release is kept as `releases/<sha256>/` and
     made the running pair, and it is started with
     Chariot's own launcher (`launch_agent_app.sh`, sha256 pinned in source).
   - It must answer `/health` 200 with the new `revision_sha256` within
     60 s.
   - Then `deployed.json` is written (the one before it kept as
     `deployed.prev.json`), the marker removed, and `app.json`
     (`chariot-agent-app/v1`, the port, `/health`) published. Releases other
     than these two are pruned.
6. **On any failure after the stop** (an EXIT trap), the app goes back to
   `deployed.json`'s release. When the marker says another binary got as
   far as running, its data comes from `latest.tar` first. That data is
   unpacked whole before it replaces anything, and the data it replaces is
   moved to `backups/replaced`. It is relaunched and published, and the job
   fails.
7. **Commit point.** The job stops at a cancel only before the setup. Once
   the server is set up, the version is delivered even if a cancel came
   meanwhile, so the agent and the library never disagree. If it is not
   delivered after all (an error), the setup is reverted:
   - the data goes back to `latest.tar`, and what the undone server wrote
     is kept as `backups/replaced`;
   - the release before it is started and published again;
   - if the revert fails after the stop, the deployed release is started
     again.
   - One setup, revert or invite runs at a time. An invite during a setup is
     refused as `server_unavailable`, retryable.
8. **Check against the baked URL.** The URL the client baked is checked
   against the record at setup time, so a version is never delivered for a
   container other than the one it was built for.

**The start script** exports `PORT`, `ROSE_LISTEN_HOST=127.0.0.1` (the
generated server accepts only `127.0.0.1` or `0.0.0.0`, default `0.0.0.0`),
`DATA_DIR`, `CHARIOT_SEALING_KEY_FILE`, `CHARIOT_REVISION_SHA256` and
`CHARIOT_DEPLOYMENT_SHA256`. `CHARIOT_APP_ID` is the container's own. The
script then runs `exec ./server`.

**Tests:** `test_app_host_agent`. A fake agent runs the real scripts in a
local `sh` with the applets linked into a temporary `/chariot/bin` and a fake
health server. It covers:
- first setup, update and rollback;
- refusal without a keyring, of an older schema, of another agent, and of a
  missing applet;
- BusyBox-only `flock` options and an empty PATH;
- a switch killed mid-way, then a failing attempt;
- a switch killed during its backup;
- data that cannot be put back;
- a failed backup, twice;
- the same server again;
- a revert that fails;
- an invite during a setup;
- another app's agent, or leftovers not set up by the builder;
- reverting an undelivered release;
- a stale pid;
- delivery as the commit point;
- the pinned launcher;
- the pool;
- the job: URL baked, setup before delivery, a failed setup delivering
  nothing.

`test_app_host_level2` covers the config.

**The client URL.** `RoseApi.serverUrl()` is baked into the APK, so the
container's public `https` endpoint must be known before the client build.
The order is: obtain the container and its endpoint, build and sign, then set
up the server.

**Recorded in source: the agent container whose URL is baked.**
- **The record.** `Ledger.record_agent_container(project, agent_id, app_url)`
  stores the agent id (32 lower-case hex) and the app URL Chariot returns
  for that agent (`CHARIOT_APP_URL`). It is recorded only from owner tooling,
  and the URL is never derived from the id.
- **The rules match the native-host record:**
  - the same record again is a no-op;
  - a different record needs `replaces` naming the current one;
  - an agent or URL another project holds is refused. This holds across both
    records: one project's native endpoint can't be another project's
    container URL, so no two projects bake the same server;
  - the URL is in its one https spelling, with no trailing `/` and no
    `:443`.
- **What a build bakes.** `Ledger.backend_endpoint` returns the container's
  URL, ahead of a native host. The builder's `server_url` uses it, so a
  client-server build writes it into `RoseServer.URL`. Versions already built
  keep the URL they baked.
- **Recording it:**
  ```
  python -m rose.app_builder.owner_records agent-container --ledger DIR
    --project ID --agent-id HEX --app-url URL
    [--replaces-agent-id HEX --replaces-app-url URL]
  ```
  With `agentHost` the builder records it itself; this command is for an owner
  recording one by hand. The command contacts nothing.
- **Status.** The record tracks Chariot's proposed contract
  (`https://agent-<agent UUID hex>.apps.chariots.sh`, not yet confirmed).
  It checks only the id format and the https spelling, not that domain.
- **Tests:** `test_app_ledger_level2.AgentContainer`,
  `test_app_owner_records`, and `test_app_host.NativeHosts`, which includes a
  job baking the container URL.

**Builder API v1 additions** (agreed with Home/App, #881; recorded in
`rose/tests/fixtures/builder-hosting-v1.json`):
- **Backend status: `versions[].hosting`.** It sits beside `server`, not
  inside it: a phone reads the server summary with its exact keys.
  - Shape: `{"state": "set_up", "appUrl", "setUpAt"}`, with `setUpAt` in unix
    ms. It appears only on the delivered version whose server is the one set
    up in the agent container. That server answered its health check there
    before the version was delivered.
  - It is not a claim that the server is running now.
  - The record is cleared when a setup starts and written only after the
    version is delivered. A server replaced by a version that is never
    delivered is therefore never claimed for an older one.
- **Creator admission: `POST /builder/v1/projects/{id}/invite`.** Signed by the
  paired device; the body is `{"schemaVersion": 1, "account"}`. Admission stays
  `invite`.
  - The owner types `account`. It must pass rose-signin `valid_name`, and it
    reaches the server exactly as typed, single-quoted in the script.
  - The builder runs the set-up server's `invite <account>` in the agent with
    the start script's `DATA_DIR` and the deployment's sha256.
  - **200:** `{projectId, versionCode, account, invite, expiresInS: 604800}`,
    no-store. The token (`[A-Z2-7]{52}`) is read only from a successful run
    and is never stored, logged or put in a job. Each call issues a fresh
    single-use invite.
  - **Refusals:**
    - 400, `reason: invalid_account`;
    - 409 `{error, reason, retryable}`, with `reason` one of `not_hosted`,
      `already_enrolled` or `server_unavailable` (retryable);
    - 404 for an unknown project.
- **The hand-off: `<pkg>.rose.RoseInvite`** (generated client).
  `RoseInvite.take(activity)` returns `{account, invite}` once, from the
  launching intent's extras `rose.signin.account` and `rose.signin.invite`, and
  removes them. Nothing is stored and `toString` is redacted. The writer is
  told to pre-fill the sign-up screen from it and keep the fields editable.
  - When the app is already open, the hand-off can arrive in `onNewIntent`
    instead of `onCreate`; `RoseInvite.take(intent)` reads it there.
- **Who may ask for an invite:** any device paired with this Rose server,
  since pairing is the owner's own act. An invite admits exactly the account
  it names, once, for 7 days.

**What it still needs to run for real** (inputs, not source work):
1. **The gateway domain.** `CHARIOT_AGENT_APP_DOMAIN=apps.chariots.sh` live on
   Chariot (Saba). Until then `CHARIOT_APP_URL` has no public route.
2. **An authorized agent with credits.** The `agents` list and a logged-in
   `home` (Local Worker, once Nathan funds the account).
3. **The applet set.** The probe refuses any agent whose `/chariot/bin`
   lacks one of `app_host.APPLETS`; Chariot's doc confirms only some of them.
4. **D9.** Whether `generateKeyring` may be `true`. This applies only to
   builder-built (`client-server-v1`) servers; agent-built backends need no
   keyring.

Agent-built backends need no Linux build host: the building agent builds in
the app's own agent container. A qualified Linux host (§5.2) matters only for
the builder-built `client-server-v1` path, kept for apps already on it.

**Separate, not a gate on this work:** Saba's report of unenforced network
policy and node service-account scope is tracked by Chariot and Saba. Any IAM,
network, security, credential or spending action still needs its own
confirmation when it is taken.

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
6. **The manifest and the binary check (§7.7).** Done:
   `rose/tests/test_app_server_manifest.py`. Real binaries come from the
   machine's own toolchain: a static-pie one passes, and a dynamically
   linked one is refused. The rest are made byte by byte. Every server
   build runs the check.
7. **The runtime (§7.4).**
   - **7a, the standard-library core:** done
     (`workspace/rose-app-runtime`, with its own offline CI).
   - **7b, the adapter onto rose-signin `23062a04`** and per-user SQLite:
     next. It builds only where rose-signin's locked crates are available.
8. Codegen and the client sign-in overlay.
9. **The library wire summary and its fixture for Home/App (§3.3
   `server`).** Done: `rose/tests/test_app_server_summary.py`, with
   `rose/tests/fixtures/server-summary-v1.json`. The phone's parsing is
   Home/App's.
10. The hosting seam: a fake host, and a Chariot adapter that stays blocked
    until §7.7 is answered.
    - **The delivered version's manifest and the fake host's check
      (§7.7).** Done: `rose/tests/test_app_level2_pipeline.py` (`FakeHost`)
      and `rose/tests/test_app_ledger_level2.py` (`Manifests`). The Chariot
      adapter stays blocked on §7.7 item 1.
    - **The native host record (§7.7 client base URL).** Done:
      `rose/tests/test_app_ledger_level2.py` (`NativeHost`) and
      `rose/tests/test_app_level2_pipeline.py`, which builds with the
      ledger's record as `server_url`. Synthetic fixtures only.
