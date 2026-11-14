# Bluetooth audio callback fixture

`BluetoothAudioWearHostTest.java` compiles the real signal adapter,
`GlassesWearHook` and customization configuration against these small Android
stubs. The test runner records hook decisions; it never launches an activity.
The selected offline suite runs it immediately after `GlassesWearHookTest`.

The regression starts without Bluetooth audio, delays the initial callback,
connects a headset after five seconds, then delivers the callback at six seconds.
The old adapter re-reads the current list for its first observation, considers
the new connection a startup baseline, and loses the transition. Sampling the
baseline synchronously before registration preserves it and fires exactly once.
An already-connected startup still never fires.

The asynchronous registration premise was checked against Android 15's official
[AudioManager source](https://github.com/aosp-mirror/platform_frameworks_base/blob/android-15.0.0_r1/media/java/android/media/AudioManager.java)
(blob `a4d0c1714e2336cf27a837f4777f7026eed2499d`): registration captures the device
list and posts it through `Handler.sendMessage`, then the handler calls
`onAudioDevicesAdded`. The fixture intentionally lets the latest device list
change before that captured initial callback is delivered.

Additional checks cover supported output types, registration idempotency,
unavailable-manager retry, normal connection, multiple devices, reconnect blips,
disable and action changes. The production execution runner is replaced by a
test recorder, so these are not activity-launch, Android-build or phone results.
The startup race is demonstrated independently; it is not yet proven to explain
Nathan's observed Bluetooth-hook failure.

## Steady-state UI backlog

The adapter now uses a process-lifetime HandlerThread for observations. Previously
its handler used the UI looper. A long UI task spanning a disconnect and reconnect
queued both callbacks; when they finally ran, both re-read the final connected list,
so the disconnect was lost even after startup. The new fixture blocks only the UI
looper across a five-second disconnection and verifies exactly one reconnect
decision, with no duplicate after UI recovery. Reconnect blips and disabling remain
effective while the UI is blocked. The prior adapter fails this regression.

This is a deterministic scheduling model, not a test of real Android thread timing.
The existing action runner and its launch-request reporting are unchanged. The
change removes UI congestion as a source of lost observation; it does not claim
that all possible audio-service delays are eliminated or that the reported phone
failure has been independently reproduced.
