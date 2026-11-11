# My Dude: continuing character and real app work

Current implementation guide for the November 11
[implementation brief](https://app.notion.com/p/4157c646c81b81f2b753dd5f0799d205).
The existing chosen assistant name/voice remain authoritative. Physical outings
and companions are fictional; app observations/actions are real. This distinction
is established in conversation, with honest answers to direct reality questions.

## Runtime and memory

Normal `--brain codex` startup now opens `WorldStore`/`WorldLife` at the existing
`config_dir()/rosie-life/diary.sqlite3`. The version-2 migration is additive:
legacy `settings`, `interests`, `activities` and the immutable server notebook
are preserved, including failed attempts and receipts. `life_research_legacy.py`
exists for regression/rollback compatibility, not production execution. There is
no phone notebook or native server-search replacement.

The database adds stable random owner/agent IDs, a versioned world (persona,
cast, preferences, plans, directions, conversation continuity), personal events,
operational wakes, actions, per-input checkpoints and host-created observations.
Rename, pairing and fresh ephemeral conversations do not change these IDs.
New turns receive current committed memory; `rosie_life memory/diary` and an
optional `query` retrieve older events before history claims. `remember` uses an
expected revision and a proposal containing `changes` and optional `events`.
App and diary prose are contextual data, never developer instructions.

Each event records separate occurrence/recording times. Future intentions belong
in plans. Digital events must reference actual stored worker observations; the
model cannot mint a receipt. A receipt proves a bound tool result/screenshot,
not the truth of every model interpretation of it. Source prose and summaries
retain that distinction. Empty proposals create operational no-ops, without
forcing a personal diary entry.

## Wakes, ownership and recovery

Defaults are 09:00, 14:00 and 19:00 in America/Los_Angeles. Use the existing
`rosie_life schedule` tool with morning/afternoon/evening HH:MM values. These
are internal events, never fabricated user messages. Plans do not create timers.
Only the latest elapsed slot today is eligible; missed slots/days are coalesced.
Pause/resume establishes a new schedule floor, with no fictional catch-up.

SQLite uniquely claims agent/local-date/slot and saves intention before app
dispatch. One OS lifetime lease and a database writer fence protect the profile.
A copied database cannot become a second live writer on another host/path.
Wake revision and cancellation epoch are rechecked before UI actions and atomic
world/event/outcome commit. Stale models retrieve/replan against existing receipts;
identical app goals within a wake are not dispatched twice.

The internal coordinator uses the existing Codex bridge and registered memory/app
tools. Invocation-scoped configuration disables native search, shell, delegation,
project instructions and the known ambient Node MCP; runtime checks refuse other
enabled/unknown MCP entries. Shared Codex configuration/authentication are unchanged.
Terra Fast/ChatGPT checks remain. Character sessions require the existing compatible
Codex 0.167+ runtime checks and confirmed process close.

App phases use `CodexConductor` → RealPhone/ScopedWorkspace → paired app_command.
The shared allocator retains two slots and one app per session. Autonomous work
uses only its own workers, yields to foreground conversation/user tasks, and
finishes quietly. Explicit user takeover changes ownership, so a wake cannot
later stop that worker. The existing task feed carries actual app/workspace
bindings and running receipts for inspection/Stop. Quiet wakes produce no fake
workspace or animation. No independent Agent APK/OS release is included.

Workers have 200-step/600-second bounds instead of the old 50-step owner prompt;
the wake also has a 600-second monotonic deadline. Deferred work and observations
remain available to later wakes. Inputs are checkpointed before dispatch and
receipted afterwards. A crash with an unfinished action retains uncertainty.
Recovery reads/stops only recorded exact controller session/task pairs; an unknown
start reply stays uncertain and blocks further work in that app. No unscoped Stop
or automatic input replay is used. Offline fiction/conversation remain available.
Stop also cancels deferred continuations after a wake has settled, with stopped
intentions retained in context. In-flight receipts retain their original wake/task
binding through user takeover. The writer lease stays held through worker cleanup.
After a locked-session refusal, the phase cannot repeat inputs; a later start in
that app requires fresh authenticated controller metadata showing it unlocked.
Coordinator close has a separate confirmed/uncertain receipt. An uncertain close
blocks replacement wakes; a cancelled wake that never started a process does not
invent a cleanup failure. Pending dynamic-tool replies drain before child close.

Keep `--phone real --vision-control` for screenshot-only execution. The new
`submit_search` taps a visibly identified Search/Go key or magnifier in a fresh
frame after typing. It sends no generic Enter, reads no accessibility tree, refuses
send/purchase targets and verifies the next screenshot. Existing sends, purchases,
publishing, deletion and security changes remain owner actions. Installed app
inventory and deployed capabilities are timestamped at wake time; missing
capabilities produce a pending/blocked outcome, never an accessibility fallback.
App workers can retrieve read-only character memory without creating phone evidence.
Screenshot pixels use documented [same-turn image input](https://learn.chatgpt.com/docs/app-server),
bound to the tool's expected turn. The installed Codex 0.167.1 preflight reported
NO_IMAGE for dynamic image output; the repaired bridge read the fixture correctly
and emitted a delivery acknowledgement. A failed delivery stops that app phase.

## Private backup, rollback and explicit transfer

Directories are 0700 and databases/backups 0600. Backups are local private
artifacts, not Git assets. From `prototypes/voice-phone-demo` with its environment:

```sh
.venv/bin/python -m rose.life_profile backup /private/config/rosie-life /private/backups/pre-my-dude
```

SQLite backup is coherent and includes the immutable notebook and chosen identity
when present. It excludes pairing, TLS credentials, authentication and transcripts.
Before deployment, record the old source revision, service arguments, identity/TLS
hashes, pairing authorization and legacy row/note counts. Stop the existing backend
before migration/deployment; start one replacement using the same config directory.

For code rollback, stop the backend, first back up the **current v2 store** to a new
private location, then restore the recorded source revision. The old Diary reader
ignores additive tables: keep the v2 database in place so new history survives.
Set legacy `settings.enabled=0` before starting old code so it cannot resume the
retired research executor. Do not overwrite new records with the pre-migration
snapshot. A catastrophic database restore is a separate explicit recovery choice;
retain both snapshots and reconcile new history before replacing anything.

Mac/cloud share a schema, not automatic data synchronization. To transfer authority,
stop the source backend first (the lease refuses a live export):

```sh
.venv/bin/python -m rose.life_profile export /private/source/rosie-life /private/transfer
.venv/bin/python -m rose.life_profile import /private/transfer /private/destination/rosie-life --accept-agent-id AGENT_ID
```

Export permanently fences that source profile as exported. Import verifies hashes
and accepted agent identity, requires an empty destination, binds it to that host,
and leaves life paused until explicit resume. Name/voice are provided as
`transferred-identity.json` for deliberate restoration; existing destination
identity/pairing is never overwritten. Do not activate a pre-export backup as
another writer while the transferred profile is authoritative. A failed transfer
leaves the source fenced; inspect and recover explicitly instead of auto-reclaiming.

## Checks and operational evidence

`bash scripts/test-background-life.sh` includes synthetic Friday–Sunday continuity,
changed hours/suggestions, quiet/offline wakes, duplicate claims, stale edits,
atomic rollback, invented/future evidence rejection, crash receipts, writer
handoff, user priority/takeover and conversation/reset integration. Run the existing
Codex, voice identity/catalog, punctuation, tailnet and vision suites too.

The loopback dashboard exposes GET `/api/life` and POST `/api/life/control`
(pause/resume/stop/schedule). An operator can trigger an idempotent internal event
with POST `/api/life/wake`, supplying `conversation_id` and a bounded `event_id`.
The existing loopback Host/Origin restriction and stale-conversation checks apply;
these routes are not on the remote phone listener. No event enters user chat.

Record real model/app observations separately from synthetic weekend fixtures.
`python -m rose.eval.life --live` is an explicit real-Terra behavioral check using
synthetic dates and app screens in separate private profiles. It compares open
and closed café decisions, applies a user suggestion, advances a Friday–Sunday
story and checks a legitimate quiet wake. It never reads the live store/phone.
For live acceptance, verify a committed intention, actual paired app session and
screens, host receipts, atomic commit, quiet completion, greeting recall after
restart, targeted Stop and unchanged pairing/name/voice. An HTTP acceptance reply
or a model-written summary alone is insufficient evidence.
