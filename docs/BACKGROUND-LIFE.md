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

Execution is intentionally unavailable in normal startup until a verified adapter
is supplied. Resume saves intent but reports `waiting_for_phone_executor` and
executes nothing. There is no placeholder success, guessed endpoint, copied login,
new Models/cloud-QR service, Mac dependency or production fallback.

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

## Still required for 1F delivery

Implement and verify an adapter for the **existing authorized cloud-to-phone
route**. `Life(executor=...)` expects:

- `research(job, cancelled, started)`: access only public research and Rosie's own
  isolated phone notes. Honor the absolute deadline and check cancellation before
  every action. Call `started()` only when actual execution begins, and abort if it
  returns false. Do not read owner screens, personal tabs, messages, files or memory.
- Return observed `public_research` receipts for this exact task: bounded summary
  and note body, HTTPS source/title/read times, and the phone's verified note ID,
  workspace ID, save time and SHA-256 of the exact saved note body. The adapter must
  verify these against actual phone replies, not merely model claims. The scheduler
  validates and stores complete receipts, but cannot prove an adapter's honesty.
- `cancel(task_id)`: scoped interruption; return True only after confirmed stop.
  A dispatched write may already have happened. Never retry an uncertain write.
- No messages, purchases, real-money trades, external sharing, arbitrary shell or
  unbounded browsing. Keep research separate from owner-request task sessions.

The adapter, its cloud service route and real phone research → saved phone note →
restart → conversational recall demo are **not implemented/verified here**. The
scheduler emits execution-state callbacks, but autonomous research task cards and
inspect/stop UI binding to the existing workspace list still require adapter work.
Saved interests alone are not completed research.

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

- `bash scripts/test-background-life.sh`: synthetic scheduler, diary and
  conversation integration plus real Java lifecycle/feed model checks.
- `PYTHONPATH=prototypes/voice-phone-demo python3 -m unittest rose.tests.test_codex`
- `node --test workspace/rose-agent/voice-providers/local-tasks.test.cjs
  workspace/rose-agent/tests/local-task-feed.test.mjs
  workspace/rose-agent/tests/home-launcher.test.mjs
  workspace/rose-agent/tests/staged-copies.test.mjs`
- `bash scripts/test-local.sh`: existing aggregate, with the new checks registered.

All tests use synthetic data. They do not call a provider, access a phone, alter
credentials, publish a release, or prove perceptual/native acceptance.

## Validation receipt for this source slice

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
