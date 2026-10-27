# Immediate phone commands

An authenticated, certificate-pinned HTTPS event stream notifies Rose Agent as
soon as the Mac queues a command. Commands still go through `PhoneCommands`, the
same one-at-a-time phone executor, native persistent ID cache, expiry checks and
result endpoint. This does not change how Luna chooses actions, the voice
connection, Bluetooth, or any app-specific worker.

Polling remains a fallback: 800 ms when the stream is down and a five-second
safety check while it is connected. Reconnects offer the same pending IDs.
Results are retried without executing the action again. The event stream sends
individual bounded messages, and slow clients are disconnected and recovered.

## Immediate rollback

From the workspace:

```
python3 experiments/phone-command-push/toggle.py off
```

This closes the stream and restores polling without restarting voice or
installing another app. `on` re-enables it; `status` reports the flag and current
stream connections. The authenticated setting persists in the private
`~/.config/pixel-voice/phone-push.json`. Push is off if the file is absent.

`baseline/` is the pre-change source snapshot. `rollback-path.txt` identifies the
private backup containing the original installable APK. A full source rollback
also needs removal of the new stream source and rebuilt app with a higher
version code; the switch is the normal rollback path.

## Measurement, not behavioral changes

The existing bounded private metrics log now includes push/poll delivery,
phone-side execution and queue duration, input/output snapshot IDs, argument
shapes and text length. It does not add screenshot, label or field text logging.
Run:

```
node experiments/phone-control-metrics/report.mjs 2026-10-13T00:00:00Z
```

The report identifies identical fresh observations after actions and successful
short sequences worth reviewing. IDs can be joined to existing task logs for
offline screen/guard analysis. Candidate sequence timings are observed totals,
not a prediction of removable time; they include network, execution and model
gaps. Historical logs cannot prove what an unseen screen branch would do.

Neither redundant-read removal nor generalized local sequence execution is
enabled. Those remain separate experiments after enough real usage is recorded.

## Checks

```
node --test experiments/phone-command-push/*.test.* experiments/phone-control-metrics/*.test.mjs
bash rose-agent/build.sh
```

Tests cover shared execution, duplicate notification, lost result reply,
asynchronous native routines, server epoch replacement, fallback, disabled
push, reconnect, bounded stream buffering and measurement privacy/correlation.
