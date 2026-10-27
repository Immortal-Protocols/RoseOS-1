# First Update — independent QA harness

Test-only evidence for the "RoseOS — First Update to Nathan's Pixel 10" milestone.
Nothing here changes product code, and it is not product acceptance. It produces
offline evidence and an independent Stop gate that App/OS fixes must satisfy.

```sh
npm ci --prefix workspace/rose-agent/server --ignore-scripts --cache "$PWD/.tools/npm-cache" --no-audit --no-fund
bash qa/first-update/run.sh
```

`run.sh` runs the repository's default offline suite, criterion-relevant offline
tests that `scripts/test-local.sh` omits, the `agent-app-trust` Java check with the
system JDK (the original test hardcodes a macOS JDK path), and `stop-gate.test.mjs`.
It writes per-check logs and `receipt.json` (source commit, worktree state, tool
versions, results) under the ignored `out/qa-first-update/`.

`stop-gate.test.mjs` drives the real `BackgroundPhone` against a fake controller that
follows `Controller.java` and `StopLedger` (the stopped `sessionId` is kept; a later agent
or deadline stop of the same session keeps the owner's `user` reason). Every case runs
against both a schema-2 controller (01c5d8f, and the controller on a phone before the OS
update) and a schema-3 controller (OS PR #1: `stopReason`, `stoppedSessionId`,
`stoppedTaskId`, `user_stopped` refusal by `taskId`). After an owner Stop it requires no
Calculator command, replacement workspace, workspace UI action, physical phone-display
action, or `home`-then-reopen. It also requires that an agent-closed workspace may reopen,
that a fresh explicit task runs after an owner Stop or a deadline (no lockout), and that a
stale start for a stopped task is refused while the current task keeps its workspace.
Between tasks the harness calls `close()`, as `server.mjs` does when a turn ends.
Implementation hooks (`taskId` option, `beginTask()`) are optional adapters.

`server-stop-gate.test.mjs` exercises the real `server.mjs` `fastPhone` and the `/task/stop`
and `/phone/commands` route bodies (extracted from source, like App's own test) over the real
`TaskStop`, `LauncherTask` and `BackgroundPhone`. It covers: acknowledged Stop, then terminal
state, then late canceled-task commands; denial while stopping; and a duplicate Stop that must
not be acknowledged before queued work is barred. It skips on trees without `task-stop.mjs`.

Generation identity (Chief decision CHIEF-GENERATION-001): `server-stop-gate.test.mjs` also
requires that after task A is stopped and fresh task B runs, A-tagged commands (direct, queued
before Stop, or via `/phone/commands`) are denied, B-tagged commands run, unknown generations
fail closed, and an intentional non-task command still works. The field is not prescribed;
only the `tag()` adapter changes once App documents its interface.

`speech-gate.test.mjs` runs the real worker-message handler and `finishAgentTurn` from
`server.mjs`: after the Stop acknowledgment a late result must not be shown or spoken, and only
the Stop acknowledgment may be spoken. Cancelling audio already playing is an explicit `todo`
(needs App's client contract or VM audio evidence).

Real voice audio, the Android
controller itself and real device behaviour are outside this harness and need a VM run.

None of these checks builds Android, uses the Pixel Voice VM, touches a phone,
release channel or signing, or counts as device acceptance.
