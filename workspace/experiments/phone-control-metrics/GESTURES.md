# Gesture diagnosis

Physical accessibility taps, point taps, swipes, drags and screenshot gestures
keep their existing dispatch behavior. They now record Android's actual
`GestureResultCallback.onCompleted` or `onCancelled` result. A completed gesture
means Android finished the gesture; it does not prove the app performed the task.

Records contain command identity, target resource/class and bounds, coordinates,
requested duration, snapshot age, screen-change guard state, before-screen hash,
window identity and time since its last change. Subsequent existing `inspect_ui`
calls add up to eight screen hashes and focus observations over five seconds.
No extra screenshots, retries, sleeps, or background screen inspections occur.

The phone keeps the last 24 records, with a private local copy in the
`gesture-diagnostics` preferences. `inspect_ui` and `diagnostics` return them.
The Mac stores new revisions as `gesture_diagnostic` records in
`~/.config/pixel-voice/phone-command-metrics.jsonl`, using the existing bounded
log. Labels, entered text, screenshots and accessibility text are not logged.
The normal post-action observations are still the source for checking success.

For a failure, join the gesture's command identity with command timing records:

- Rejected dispatch: Android did not accept the gesture.
- Cancelled callback: Android cancelled an accepted gesture; the callback alone
  does not identify whether another touch, another service or another cause did it.
- Completed callback and unchanged screen: touch delivery completed, but the
  expected app transition was not observed. This does not by itself prove why.
- Changed window/target before dispatch or shortly afterward: possible race;
  compare the existing observations rather than asserting a cause automatically.

Missing callback or observations remain unknown, never successful or failed by
assumption. Instrumentation does not enable the currently disabled screen-change
guard, change gesture timing, or add recovery actions.
