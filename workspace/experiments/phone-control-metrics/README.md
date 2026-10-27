# Phone command measurements

Read-only instrumentation of the existing phone command path. It does not change polling, prompt instructions, command dispatch/order, deduplication, expiry, retry behavior, or action verification. No Android update is required. The authenticated server endpoints invoke the collector; failures in collection cannot fail phone commands.

Private rotating logs: `~/.config/pixel-voice/phone-command-metrics.jsonl` plus up to three numbered backups, each at most approximately 5 MB. Writes are queued asynchronously and dropped if the telemetry queue is overloaded. No command arguments, entered text, screen labels, image bytes or raw observations are logged. Observations are reduced to a SHA-256 digest and node count. Command IDs, action names, timestamps and task/model identifiers support correlation.

## Read a report

From the workspace root:

```
node experiments/phone-control-metrics/report.mjs
node experiments/phone-control-metrics/report.mjs 2026-10-13T19:20:00Z
node --test experiments/phone-control-metrics/metrics.test.mjs
```

- **Queue wait:** command created on the Mac until first offered in a phone poll. Includes the wait for polling and the poll's arrival at the server; it does not isolate pure transport latency.
- **Dispatch to result:** first offered until the result reaches the Mac. Includes transport, on-phone execution, existing post-action waits, and return transport.
- **Result pickup:** result received on the Mac until the caller next requests that completed result. Can reveal the CLI's polling overhead.
- **Between commands:** prior command completion until the next command is created. May include model/tool orchestration, user pauses and unrelated activity; never label it pure model reasoning time.
- **Possible redundant inspection:** an immediately following `inspect_ui` in the same task returns an identical complete observation to the preceding UI action, while the returned snapshot was still within its advertised lifetime. Changed, missing, truncated and expired observations do not count. These are candidates for review, not proof the check was unnecessary. Repeated inspections after inspections are separately recognized as matching screens but not counted as the proposed post-action optimization.

Polling savings overlap these command timings. Do not add the full time spent in candidate inspections to estimated polling savings. Screens may change transiently and return; a matching final tree does not prove nothing happened. A returned action observation is available only for some current commands; instrumentation records what exists without adding observations or taking screenshots.

## Rollback

`integration.patch` contains only the server hooks. Reverse it from the workspace root with `patch -R -p1 < experiments/phone-control-metrics/integration.patch`, then restart `local.pixel-voice.server`. Preserve unrelated later edits if the patch does not apply cleanly. The pre-change server is also saved in the private rollback directory created at deployment. Logs are independent of functional state; never delete command results, app data, memory or transcripts to disable measurement.
