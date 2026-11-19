# One backend path: agent container

The backend builder now uses `client-backend-v1` only. Rose's building agent
chooses the stack and installs dependencies, builds and tries the backend in
its existing Chariot agent container through `agent_container` → Chariot CLI
→ SSH. Python, Node and other stacks require no Rust. Rust is an app-author
choice. Client-only apps take no container. No extra builder VM or per-app
preconfigured toolchain is introduced.

The Android compiler/signing path, persistent project signing identities,
optional `requireKeyBackup:false`, writer read isolation, compiler-feedback
repair, and generic `agentHost` semantics stay in place. Generated Android API
clients, contracts and sign-in client code are unchanged; their future is a
separate decision. Android Gradle/Kotlin/Compose work belongs to App Core.

## Removed and retained

Removed: `server_build.py` (prescribed Cargo manifest/config, offline binary
compilation), `crates.py` (curated crate store), `prepare.py`, `qualify.py`,
`providers/host.py`, their host configuration wiring and active writer rules.
An old `clientServer` JSON key is accepted but inert: no paths are opened,
validated or executed. Remove it from deployed config after inventory. It
must not force a Python app to install Rust or prevent a server from opening
its existing ledger.

Retained for existing apps: source Git history, APK and `.server` artifacts,
all ledger tables (signer, package, versions, source revisions, contract,
migration/crate/Cargo locks, manifests, endpoint and hosting records), legacy
artifact verification/read-only deploy-plan utilities, and agent rollback
support. Nothing garbage-collects these files or edits a delivered version.
The Rust runtime libraries and optional codegen are still available to authors
and historical readers; the builder never compiles them. Old deployment
helpers are compatibility tooling, not a second new-build route.

## Usage inventory and evidence limit

Source inspected at `c0496d600a2083192183143d05b03836ab5c2fb7`:

- The old pipeline was live source reachable through `host.clientServer`,
  `Builder._client_server`, signing/delivery, and `AgentHosting.deploy`.
- `Ledger.project_profiles` binds `client-server-v1`; `version_servers`,
  `version_migrations`, `version_crates`, `version_cargo_locks`,
  `version_contracts`, and `version_manifests` retain old builds.
- Both `project_native_hosts` and `project_agent_containers` can identify an
  old backend. A ledger record alone is not deployment or data evidence.
- Checked-in examples of old use are synthetic tests. No tracked
  `builder.sqlite3`, `rose-app.json` or `deployed.json` inventories were found.
  **Deployed usage is unknown, not zero.** No owner container, account or phone
  was accessed for this change.

The existing Local Server Worker can run this against the authorized builder
ledger, using the source revision in this PR and the normal server Python:

```sh
PYTHONPATH=prototypes/voice-phone-demo python3 -m rose.app_builder.legacy_backend \
  --ledger /EXISTING/BUILDER/ROOT
```

It opens `Ledger.reader` read-only, does not instantiate a Builder, run recovery,
connect to Chariot, or modify a profile. Missing path exits 4 with
`status: unknown`, not an empty project list. Its output covers that ledger
only. Return the output privately with observation time and running source SHA;
project identifiers, URLs and public signer hashes need not be posted publicly.
Do not send the ledger, project descriptions, secrets or data rows.

For **each reported project**, the minimal additional authorized read-only
runtime evidence is:

1. Which recorded native host or agent currently serves the baked endpoint;
   recorded agent ID and URL versus actual `CHARIOT_APP_ID`/`CHARIOT_APP_URL`.
2. Sanitized `deployed.json` fields `project`, `release`, `versionCode`,
   `dataSchema`; `current` release ID; whether `switching` exists; current
   server artifact hash; health status and matching release ID only.
3. Persistent storage location/identity, whether the original data directory
   and sealing keyring exist, aggregate data size and file/database counts,
   schema-version summaries, and an existing backup/restore receipt if one
   exists. No keyring content, tokens, account names or database rows.
4. For a native host, whether the **same existing endpoint** can be retained
   while its data, auth identities and keys are preserved in an already
   authorized agent. Do not allocate a replacement or change routing to gather
   this evidence. If not established, report the missing fact.

Inventory alone authorizes no cutover. This PR performs no live migration,
release, server restart, deployment, login, provisioning or phone operation.

## Legacy revision path

An app already recorded in an agent container migrates during its next ordinary
revision, never merely by starting the upgraded server:

1. Check out the exact last delivered source. Move `server/` into
   `backend/legacy/server/`, retain the original declaration, Cargo.lock,
   manifest and a record of the delivered identity/history. Change only the
   working declaration to `client-backend-v1`. Original commits remain.
2. Before the first writer container command, read the recorded agent's
   identity/state. Require the exact latest delivered legacy release, schema
   and version, no interrupted switch, and existing data/keyring. The artifact
   is checked against its retained manifest. Recheck before switching.
   An empty, changed or reset agent is not treated as a fresh app.
3. The writer uses the same agent and endpoint. For a client-only change it
   can copy the current server binary and invite script into the new release,
   set `CHARIOT_REVISION_SHA256="$ROSE_RELEASE"` in its own start script and
   run it. Copies are required; references to old release directories break
   when those releases are pruned. For backend changes, the author owns the
   stack, build files and dependencies. Preserve auth identity, database paths,
   schema history and data; test forward migrations on synthetic data. The
   writer must not access live data or keys.
4. Validate the retained migration hashes and Cargo.lock against the ledger,
   and the API contract against every delivered contract. Compile/sign the
   Android client with its existing package, signer and higher versionCode.
5. Switch using the existing backup/health/rollback mechanism, with the same
   `DATA_DIR` and keyring. Deliver the APK and release record and change the
   bound profile in **one ledger transaction**. A cancelled/failed build keeps
   the old profile/source/version. A failure after switch calls the existing
   release rollback. Historical records remain intact after success.

An app recorded **only on a native host**, or with missing/inconsistent runtime
state, is held at revision with a specific migration message. Its existing
APK, running server and retained data remain untouched. The Local Server
Worker must first prepare a project-specific cutover using the evidence above:
retain the client endpoint, stop writes for a consistent backup, preserve all
auth/account/shared data plus the keyring outside model access, restore and
verify in the existing agent, and retain the old service/storage for rollback.
The agent's legacy release/state must match the ledger before the normal
revision can proceed. This task does not execute that runbook. If the old
endpoint cannot be preserved, a separately reviewed client/server transition
is required; do not replace it silently or strand older clients.

## Runtime handoff after source review

Local Server Worker owns installation of the reviewed source and restart of the
existing Rose server when separately authorized. Preserve the builder root,
ledger, project repositories, artifacts, keys, config, Chariot HOME, named
writer permission profile and existing agent mappings. Keep `agentHost` and
its configured CLI/SSH access. No new Rust, musl, crate store or qualification
host is needed. `clientServer` can be removed; `requireKeyBackup` remains the
owner's current value. Nothing changes signing isolation.

For runtime acceptance, use an authorized test app in the existing agent:
create a small Python backend, seed data, revise with a forward schema change,
verify old data/auth and API access, and exercise a failed-health rollback.
For each inventoried legacy app, first verify a same-binary/client-only
transition, then its seeded-data update and rollback. Verify Android package,
signer, higher versionCode and user data after an ordinary update through the
existing phone owner; this engineer does not operate the phone. Do not equate
scratch-process tests, compilation or signing with that runtime acceptance.

## Source verification (2026-11-18)

Local checks used Python 3.12, the pinned Android 35 SDK from the repository's
bootstrap, JDK 21.0.13+2 and netcat-openbsd for the scratch agent process.
Cargo and rustc were explicitly absent from PATH for backend acceptance.

- 47 cases passed: `test_app_agent_backend`, `test_app_backend_migration`,
  `test_app_share`, `test_builder_routes`. Covers Python releases, Android
  compilation/signing, cancellation after switch, failed-delivery rollback,
  retained identities/history and existing API wire shapes.
- After making legacy observation strictly read-only, all 11 migration cases
  passed again. A real scratch SQLite database retains its row through a
  forward schema update; a failed-health update restores both schema and row.
  The sealing keyring stays byte-identical. No real Chariot is involved.
- All 24 existing `test_app_host_agent` cases and 22
  `test_app_ledger_level2` cases passed. Their combined run included an initial
  migration fixture setup error, corrected before the final 11-case rerun.
- Node `app-build.test.mjs`: 24 passed. Profile/writer-policy/migration quick
  checks: 19 passed. One-server wire and codegen checks: 16 passed, four optional
  Rust cases skipped; generated Android client compilation passed.
- A broad `test_app_*.py` discovery ran 379 cases. It exposed the corrected
  migration fixture error and two host-JDK setup errors; all affected targets
  passed their subsequent focused runs with the complete JDK. This is not a
  claim of a completely green broad discovery run.
- `bash scripts/test-local.sh` stopped on seven Launcher3 compile-contract
  assertions. The same seven fail on untouched base
  `c0496d600a2083192183143d05b03836ab5c2fb7` (26-case module): existing
  patch/blob/treePin/overlay contract drift. Those OS files are outside this
  change. `git diff --check` passed.

These are source and scratch-process checks. Live inventory, deployment,
auth/data acceptance in Chariot and physical Android update acceptance remain
with the existing runtime owners.
