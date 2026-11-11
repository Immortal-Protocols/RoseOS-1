# Rosie's private phone notebook (plan 1F)

Source only. This is not a built, signed, installed, deployed or device-accepted
app, nor an independent Rose Agent release. The new ordinary Android package
`dev.pixel.rosienotes` is a dedicated, read-only workspace surface. It is separate
from both the owner's Notes app and Rose Agent's Home package. The existing
background controller deliberately rejects Home as a target; this implementation
does not weaken that restriction.

## Existing transport and scope

The Life adapter uses the existing authenticated cloud-to-phone transport and
`PhoneControl.app_command`, with `packageName: "dev.pixel.rosienotes"`. Android
resolves its `dev.pixel.rosienotes.agent` provider; both PhoneControl and the
provider enforce the existing application signing identity. The app must
therefore be installed with the same trusted app identity before this route can
work. No new credential, permission, network endpoint or signing policy is added.
Its manifest declares **zero uses-permission or permission elements**. Its only
exported components are its launchable read-only Activity and signature-checked
provider, following the existing ordinary-app provider pattern.

Before saving a new note, the provider independently checks the same-signer
`dev.pixel.backgroundcontrol.agent` provider's `get_state` reply for the exact
`sessionId`. It requires `status: "ready"`, the exact task and session, target
package `dev.pixel.rosienotes`, `blocked: false`, `foregroundFallback: false`, and
finite `elapsedMs` in `[0, 600000)`. A controller read taking over three seconds is
refused. These checks happen before staging and again immediately before atomic
publication. The provider never creates or stops sessions. The adapter owns
starting a real controller workspace, verifying it, and scoped Stop.

`workspace_id` is the **actual controller sessionId**, not a made-up notebook
namespace. `controller_task_id` must exactly equal the diary `task_id`; task aliases
are not allowed. A task has one immutable session binding. Existing exact notes
may be read back, or confirmed by an identical put, after the workspace closes.
The store does not claim an old receipt means its workspace is still executing.
Controller Stop and filesystem publication are not one cross-process transaction:
an already-published write remains saved if Stop races its final readback. The
adapter must reconcile the same task/session, not silently start another task.

## Version 1 wire contract

All results are JSON in the existing `Bundle.result` string, returned directly by
PhoneControl. No generic model tool is registered and no remote list, delete,
update, file path or owner-content operation exists.

`life_note_capabilities` (or ordinary `capabilities`) accepts `{}` and returns:

```json
{
  "status": "ok",
  "version": 1,
  "namespace": "rosie-life-v1",
  "target_package": "dev.pixel.rosienotes",
  "workspace_binding": "controller_session_id",
  "max_body_chars": 16000,
  "max_title_chars": 300,
  "max_id_chars": 128,
  "max_notes": 256,
  "character_count": "unicode_code_points",
  "id_pattern": "[A-Za-z0-9][A-Za-z0-9_-]{0,127}",
  "hash": "sha256_utf8",
  "saved_at_unit": "unix_seconds",
  "immutable": true,
  "owner_data_access": false,
  "operations": {
    "life_note_capabilities": [],
    "life_note_put": ["task_id", "workspace_id", "controller_task_id", "title", "body", "sha256"],
    "life_note_read": ["task_id", "workspace_id"]
  }
}
```

`life_note_put` requires exactly the six fields above. IDs are nonempty ASCII
strings matching the capability pattern; real controller session UUIDs fit it.
Title and body are nonempty strings bounded in Unicode code points. Unpaired
surrogates are rejected. Text is never trimmed or normalized. `sha256` is the
lowercase SHA-256 of the exact UTF-8 body. Unknown/missing fields, wrong scalar
types, duplicate JSON keys and malformed/oversized JSON are rejected.

`life_note_read` requires exactly `task_id` and `workspace_id`. A successful put
or read returns:

```json
{
  "status": "verified", "verified": true,
  "version": 1, "namespace": "rosie-life-v1",
  "task_id": "TASK", "workspace_id": "ACTUAL_CONTROLLER_SESSION_UUID",
  "controller_task_id": "TASK", "note_id": "life-TASK",
  "title": "Exact title", "body": "Exact body", "sha256": "LOWERCASE_BODY_HASH",
  "saved_at": 1794300000.123
}
```

Put also returns `created: true` for a freshly published and verified note or
`created: false` for identical readback. An identical put never changes bytes or
time. A different title/body/hash for the same task is `conflict`. A different
workspace is `scope_mismatch`, without disclosing stored text. Invalid
`controller_task_id` aliases are `invalid_request`.

Failures have `verified: false`, `version`, `namespace`, `code` and a bounded
`reason`; they contain no note body or fabricated receipt. Statuses are
`invalid_request`, `not_found`, `scope_mismatch`, `conflict`, `corrupt`,
`unavailable`, or `uncertain`. `workspace_not_ready`, `store_full` and `store_busy`
are specific codes. A missing/incompatible app is separately reported by
PhoneControl's existing unavailable behavior. A timeout or `uncertain` means
**read back the exact task/session**. Do not manufacture a different task or note
ID or use operationId receipt replay as proof.

## Persistence and read-only UI

Records live only under this app's `getNoBackupFilesDir()/rosie-life-v1`, with a
0700 directory and 0600 files. A deterministic per-task filename plus process and
file locking prevents competing puts from overwriting each other. A bounded
binary record includes task/session/controller-task binding, original text,
body hash, stable phone save timestamp, format version and a whole-record SHA-256.
A single locked staging slot bounds leftover staging data across crashes.
The store syncs staging bytes, atomically renames, syncs the directory and reads
and verifies the published bytes. There is no non-atomic fallback. Every read
validates the record and exact scope again, including after process restart.
Corrupt records are never overwritten. Symlink records and staging paths fail
closed. Storage is capped at 256 notes; reaching the cap refuses new saves rather
than deleting notes. There is no automatic retention or cleanup of saved notes.

The ordinary Activity displays at most 20 recent verified notes and lets the
owner read each exact body or locally refresh. A verified provider put also
notifies only the currently visible same-process Activity to refresh. Paused or
destroyed activities ignore late reads; no broadcast or navigation is used. It ignores intent payloads and
cannot edit, share, browse links or access other apps' storage. No WebView,
Internet permission, external-storage access or owner Notes dependency is present.
It uses a plain native surface; theme/visual/native acceptance remains unverified.

## Source validation and remaining acceptance

Run `bash scripts/test-private-life-notes.sh` for synthetic host-JVM storage,
controller-binding and source-wiring checks. It uses temporary synthetic files,
including a separate JVM restart. It does not invoke a real provider or Android
runtime. `workspace/rosie-notes/build.sh` is a source-only unsigned recipe matching
the existing ordinary-app recipe; it is **not run** as part of this source slice.
No build, signing, deployment, phone data, VM, release workflow or OS compile
harness was used or changed.

Still required before delivery: approved app/OS packaging and signing using the
existing identity, native compilation, installed same-signer provider checks,
actual controller admission and Stop, app-private durability on Android, and the
real public research → private phone note → restart → conversational recall demo.
Do not infer any of these from host tests or a source merge.
