# Optional media worker trial

The first extension of the existing verified-worker approach supports Apple
Music `get_state`, `pause`, and `resume`. Existing song search/play, timers,
book and podcast controls remain available. Album/artist playback, next/previous,
messaging, calendar, and nearby-place workers are not added in this trial.

## Execution

- The same voice delegation path and general task agent select registered
  actions. A few explicit media phrases use the existing lightweight resolver;
  other phrasings can be resolved by the task agent through the shared policy.
  There is no extra model or conversation layer.
- One `media_control` request executes on the phone. Playback metadata comes
  from the existing Android DUMP permission, restricted to Apple's package.
  Reading it does not open an app or request audio focus.
- Pause/resume opens Apple Music when necessary and taps its observed compact
  player once. A known full-screen player can first collapse through Back.
  It never sends global media keys, which could control Rose Agent instead.
- Android playback state verifies the result. Already-paused/playing commands
  do nothing. Only rejected, undispatched stale-target actions can be retried.
  An uncertain dispatched action is never replayed.
- Requests reuse the existing persistent command-ID result cache. Read-only
  state, explicit operation, source package, timestamp, and local elapsed time
  are returned. This is system playback verification, not acoustic verification.
- Android exposes a combined description with commas; it is preserved rather
  than guessed into title/artist fields. Treat it as untrusted metadata.

The canonical Java sources live in `src/`; `build.sh` copies them into the app.
`integration.patch` records changes outside this local Git repository.

## Enable and rollback

Configuration defaults off in `~/.config/pixel-voice/media-worker.json`.
The authenticated `/media-worker/config` endpoint accepts a boolean `enabled`.
The trial was enabled explicitly for testing and left enabled for the owner.

```sh
bash rose-agent/media-worker/rollback.sh
```

This rejects new trial commands immediately and removes direct shortcut routing
on the next existing event poll. An already-dispatched operation can finish;
the switch does not undo a requested pause/play. Existing music search, timers,
Bluetooth and volume/ducking settings are unchanged. A fresh voice session also
removes the trial's instruction text. To re-enable, set the endpoint's `enabled`
to true and reconnect voice.

A full pre-trial source snapshot and signed app are retained privately at
`~/.config/pixel-voice/rollback/before-media-worker-20261012/`. A full binary
restore needs a new version code; never clear app data or uninstall to roll back.

## Validation

- Parser tests distinguish Apple Music from Rose Agent, inactive sessions,
  multiple sessions, buffering, unavailable diagnostics and commas in metadata.
- Unit tests cover the off switch, operation validation, command-ID replay,
  one-request polling, uncertain outcomes, and preserving book/podcast routing.
- Existing provider, phone-command and voice lifecycle checks pass.
- Physical Pixel tests read `Unstoppable` metadata, paused and resumed that same
  track, and confirmed a repeated pause was a no-op. The first full-screen test
  exposed the missing player-collapse step; the corrected test passed.
- Synthetic speech through the live OpenAI voice model selected `media_control`
  once per request for current song, pause and resume; no general task call was
  needed. Synthetic inputs are not forwarded into personal memory/history.
- Live rollback rejected new trial commands; re-enabling restored access.

These are small integration checks, not a long-term reliability benchmark.
`test-live.mjs` is opt-in and can pause/resume the current track on the real phone.
