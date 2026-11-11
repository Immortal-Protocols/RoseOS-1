# Glacier deferred-rebuild regression and reporting fix

Implemented locally over retained PR #744 head
`0496e8cc7ad234a107f0b2cd7aff64205a1f2037` and assessment commit `6c3cca5`.
No new network read, Android runtime, phone, deployment, release, permission or
compile-contract change was involved.

## Confirmed source gap and minimal correction

The former command result reported only Home and SystemUI. Both could acknowledge
Glacier while the independent conversation activity still deferred rebuilding for
Return, Stop or Background Work. The producer nevertheless said “Glacier everywhere.”
The assessment's expected-failure regression now passes normally.

`CustomizationAgent` adds `conversation` evidence from `ConversationThemeState`:

- Each activity registers an opaque, per-instance token before building its views.
- Its first `OnDrawListener` posts acknowledgment after the draw. Reading the
  preference, constructing views and scheduling `recreate()` do not acknowledge it.
- Destruction removes the token. A late callback cannot resurrect it or acknowledge
  the replacement. No activity reference or evidence is saved persistently.
- `requested`, `shown`, `applied` and `state` describe the exact requested theme and
  all registered conversation instances. A mixed/undrawn set has no common `shown`.
  Missing, closed, recreating and deferred screens never manufacture completion.

The Python producer requires exact Home revision plus conversation and SystemUI
acknowledgments for the same theme. It retains the three-second read-only polling
window, never repeats a write, and does not combine evidence from different reads.
A deferred screen gets an explicit “after Return, Stop or Background Work closes”
message. Older clients missing the field receive a partial outcome. A new theme
or newer customization revision during polling is reported as superseding the
request rather than promised to draw an obsolete revision later.

## Preserve active flow state

The existing `themeBusy()` guard remains unchanged. It protects Return in flight,
Return's failure sheet, Background Work, Stop confirmation, Stop in flight and Stop
awaiting its task's terminal state. Closing all protected flows allows the next
existing refresh tick to schedule one rebuild. A `themeRecreating` guard also
prevents duplicate listener callbacks from scheduling it twice.

Only a theme-triggered recreation retains settled context through Android's
non-configuration-instance handoff: the exact `ReturnModel.State`, selected task
ID and that task's Stop/watch message. A live operation, dialog or callback is never
transferred. The new activity restores those bindings before its first refresh;
the task projection itself remains owned by `VoiceService`.

## Executable evidence

`workspace/rose-agent/customization/test_deferred_theme.py` executes the actual
production `themeBusy`, `maybeRetheme` and retention methods in a minimal JVM probe,
using production `ReturnModel`, `TaskControl` and `ConversationThemeState`.
Its 87 assertions cover the six protected flows, repeated ticks, reverted requests,
concurrent flows, finishing/destroyed screens, exact settled-context handoff,
wrong-theme frames, first-draw gating, recreation gaps, stale callbacks and multiple
activity instances. Static wiring assertions bind those checks to the Android
lifecycle and first-draw hook.

`test_conversation_tools.py` includes bounded deferred/rebuilt-frame polling,
missing/malformed/wrong-theme acknowledgments, both themes, independent snapshots,
newer theme or same-theme revisions, and read-only state evidence. The CLI catalog
still emits the same command through the same signed provider.

## Checks and limits

- Focused deferred test: 3 tests passed, including 87 JVM assertions; no expected failures.
- Conversation customization: 31 tests passed.
- CLI and staged-copy checks: 12 passed.
- Existing JVM checks: CustomizationOutcome 25; ReturnModel 27; TaskControl 249;
  CustomizationConfig 13,619; RoseTheme 13,269 passed.
- Python compile and `git diff --check`: passed.
- Aggregate `bash scripts/test-local.sh`: not a full pass. It first stops because
  `javac` is absent from PATH. A temporary, non-repository launcher for the installed
  Java compiler module allows it to reach unchanged release-pipeline fixtures,
  which stop with 45 setup errors because `/usr/sbin/mke2fs` is absent. No test,
  fixture, compiler contract or system installation was changed to bypass this.
- Independent source review re-ran the final 31 Python tests, 3 focused tests with
  87 JVM assertions and diff check; all passed, with no remaining source finding.

The probe verifies control flow and the retained data contract, not Android's
actual recreation, frame presentation or native layout. No Android SDK/app build,
scroll-position retention, rendered-color acceptance, physical-phone validation or
remote CI is claimed. Phone checks must cover each protected flow, newer requests,
and failed/uncertain Stop notes through real recreation in both themes.

The name-change commit `29cda290cf59084277f00378406fa34d9b4a83e4` is not present in
this local object database. Its checkout is on another computer. The name/header
paths are untouched here, but a final merge/integration check remains pending;
no denied route was retried to obtain it.
