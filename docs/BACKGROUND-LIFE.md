# Rosie's background life and activity indicator

November 10 plan 1F/1G, source implementation. **Not delivered or enabled.**
No deployment, credentials, cloud provisioning or release lane is changed.

## Implemented

`rose/life.py` supplies a private SQLite diary under
`ROSE_CONFIG_DIR/rosie-life/diary.sqlite3` (0700 directory, 0600 database),
separate from identity, conversations and owner memory. It persists interests,
source references, verified phone-note receipts, results, unfinished work and
activity status. A failed/unreadable diary does not prevent conversation startup.

The conversation's `rosie_life` tool supports status, diary recall, interests,
pause, resume and targeted Stop. Greeting instructions permit one genuinely
completed result, never fabricated experiences or unsolicited announcements.
All diary/source prose is untrusted reference data, not instructions.

The bounded scheduler defaults to 120 seconds idle, a 120-second session budget,
and at least 30 minutes between starts. SQLite admits only one active session,
including across processes. It saves the claim before dispatch. An expired
unresolved claim pauses research for explicit inspection/resume, never silently
replaying a possibly completed phone write. Owner speech/typed input sets the
cancellation latch before submitting the conversation turn. A slow interruption
acknowledgment never holds up the user's request. Unknown stop results remain
visible, not reported as confirmed stops.

Normal Codex startup now installs a capability-gated `PhoneResearchExecutor`.
Research remains paused by default. Explicit resume with an interest admits one
bounded session only after the paired phone proves the private notebook and
schema-4 alongside controller contracts. Missing capabilities report unavailable
without opening owner Notes, taking over Home, or inventing a workspace ID.

The existing Codex app-task stream now carries additive version-1 activity
receipts: a monotonic sample and `executing` boolean. A running, accepted background
app turn sends one receipt per second; an idle retained workspace never does.
The existing JavaScript projection transports these receipts; duplicate samples do
not refresh native freshness, and a late running event cannot resurrect a terminal
task. Older producers retain their old task projection and produce no motion.

The native Home switcher consumes these receipts. Its existing 64x28 window glyph
moves by at most 0.6dp while tasks execute, settles for 240ms on observed completion,
and becomes still immediately for waiting, malformed, disconnected or expired
state (3 seconds). Concurrent tasks keep it moving until all stop executing.
Reduced-motion, hidden and released views are still. The hit target and ordinary
switcher tap are unchanged. The drawable and color path are unchanged, preserving
the Glacier owner's theme changes instead of duplicating them.

## Private research executor (1F source)

`rose/life_executor.py` uses the existing paired `app_command` transport and a
separate ephemeral Codex ChatGPT research process. It sends only the selected
public interest, never the owner's conversation or phone content. The existing
Terra Fast account/model checks remain. Research has no dynamic phone tools.
Shell, apps/plugins, multi-agent, image reads, hooks and memories are disabled;
environment access and project instruction bytes are empty. Inherited base
instructions/instruction files and enabled or unknown ambient MCP entries are
refused before thread creation. Already-disabled named entries are checked in the
thread snapshot, never through wildcard discovery that could connect other servers.
The effective configuration is rechecked after creation before a topic is submitted.
Unsupported checks fail closed. Codex has no atomic per-thread MCP deny-all field,
so this guard requires stable backend configuration during the session; it is not
a new OS-level isolation sandbox or a guarantee against concurrent host changes. This does
not change persistent account configuration or create credentials. The stricter
inventory contract was inspected in local Codex 0.167.0-alpha.4 offline schemas;
research requires Codex 0.167 or newer and compatible runtime evidence. Older
versions are refused before starting the research process; ordinary voice is unchanged.

The model is asked for at most six public web calls; observing a seventh or an
unexpected native tool rejects the result and interrupts the thread. An already
started read may finish. The hard host bound is the session deadline, also checked
with a monotonic clock. Only completed native HTTPS page-open events become
source receipts. They prove observed page-open operations, not that every generated
claim is correct or that an opened page was readable. Model-written citations or
phone receipts are never substituted for these events.

The new `workspace/rosie-notes/` ordinary app (`dev.pixel.rosienotes`) owns only
Rosie's immutable research notes. It has no permissions, Internet/WebView, owner
Notes access or backup. Its exported command provider checks same signing identity;
its read-only launcher surface shows stored notes and refreshes when one arrives.
See [the exact notebook contract](PRIVATE-LIFE-NOTES.md). This separate package is
necessary because the existing controller correctly refuses Rose Agent's Home
package as a background target. That Home safety gate is unchanged.

The host starts one actual alongside controller session with the diary task ID,
checks the returned task/session/package, and carries that real session ID to the
notebook. The provider independently validates the ready controller binding before
staging and publishing a new note. One immutable put is followed by exact-body,
SHA-256 and identity readback. A lost reply never causes a second put. Cancellation
or deadline during a possible write still gets one bounded read-only reconciliation
and targeted workspace Stop. Unknown cleanup pauses future admission. A verified
note may remain on an interrupted/failed diary entry without claiming task completion
or confirmed Stop. Shutdown waits boundedly for cleanup and persists uncertainty.

Research tasks use the existing task feed, inspect binding and targeted Stop path.
Only actual execution emits working receipts. No unsolicited speech is generated.
Diary recall and suggested-interest rotation remain separate from owner memory.
The combined synthetic test covers research → actual controller-ID-shaped fixture
→ note put/readback → diary restart → recall, plus isolation, interruption, missing
capabilities, uncertain writes, cleanup and source rejection.

## Still required for 1F device delivery

Publish/install the notebook signed with the existing authorized app/controller
identity and update the backend/client through their authorized delivery owners.
No signing key, provisioning, app install or deployment was performed here.
Verify with the real paired cloud backend and phone: public hobby research,
visible saved note, restart recall, interest follow-through, task inspection/Stop,
owner priority and all unavailable/uncertain paths. No live Codex provider, native
Android compilation or physical-device acceptance was run for this source slice.

## Still required for 1G delivery

Home and the **raised Launcher3 tray** now consume truthful receipts. Rose Agent
projects the feed into `working`, `terminal` or still plus the original monotonic
expiry. Its existing SystemUI-only Messenger STATE includes that projection in
change detection. SystemUI validates it and mirrors only phase, expiry and boot
count into `rose_background_activity_v1`, using its existing Settings.Global
authority. No task keys, goals, notes or conversation content are mirrored.

Launcher3 observes that setting only while the edge is attached. Duplicate reads
never extend expiry; a missing, malformed, expired or other-boot receipt is still.
SystemUI clears the mirror on startup, screen-off, disconnect and user change;
unclean process death can retain motion only until the original three-second
expiry. Only owner-user receipts are mirrored. Waiting and idle open workspaces
are still, concurrent work continues until the final executing task ends, and an
observed terminal transition settles over 240ms. Hidden, detached and reduced-motion
views stop movement. The existing glyph moves at most 0.6dp inside its unchanged
box; theme colors, geometry, hit targets and tap/Stop routing are unchanged.

This source deliberately leaves the stopped Launcher3/WMShell compile contracts,
shared theme renderer and release infrastructure untouched. Their pins require a
separately authorized refresh before a native build can claim this source.

Verify both themes on the phone, visual smoothness, normal tap/inspection/Stop,
concurrent completion, stale and disconnected sockets, process restart, hidden
views and reduced motion. A host-JVM/source check is not native phone acceptance.

## Offline checks

- `python3 -m unittest discover -s os/tests -p test_raised_tray_activity.py`: 34 host-JVM projection/mirror/lifecycle assertions plus source seam checks.

- `bash scripts/test-background-life.sh`: synthetic scheduler, executor, diary and
  conversation integration, restricted research IPC/config, private notebook restart/
  binding tests and real Java lifecycle/feed model checks.
- `PYTHONPATH=prototypes/voice-phone-demo python3 -m unittest rose.tests.test_codex`
- `node --test workspace/rose-agent/voice-providers/local-tasks.test.cjs
  workspace/rose-agent/tests/local-task-feed.test.mjs
  workspace/rose-agent/tests/home-launcher.test.mjs
  workspace/rose-agent/tests/staged-copies.test.mjs`
- `bash scripts/test-local.sh`: existing aggregate, with the new checks registered.

All tests use synthetic data. They do not call a provider, access a phone, alter
credentials, publish a release, or prove perceptual/native acceptance.

## Historical validation receipt for the initial foundation

On base `2532ddc54ced172107d13f45854d0f1f49b81d70` in an isolated Linux checkout:
20 new Python tests, 112 existing Codex tests, 23 Java lifecycle assertions and
57 focused Node tests passed. Python compilation and `git diff --check` passed.

The aggregate initially stopped because this environment has no `javac` command.
A temporary, out-of-repository launcher invoked the already installed JDK compiler
module, allowing the unchanged aggregate to continue. It then stopped in
`scripts/tests/test_release_pipeline.py`: 45 test-fixture errors because
`/usr/sbin/mke2fs` is absent. The pipeline and APEX fixture source are unchanged
from the base. This is an environment-blocked aggregate, not a full-suite pass.
No Android SDK/app build, native renderer run, visual acceptance, remote CI,
publication or device acceptance was performed.
