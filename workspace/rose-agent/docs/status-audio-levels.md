# Status-bar audio signal

The production ElevenLabs front end now forwards `getInputVolume()` and
`getOutputVolume()` to `Native.trayAudio()` every 80 ms while connected. These are
independent normalized measurements from the SDK's local and remote WebRTC track
analysers. The installed SDK describes the scalar as the mean normalized spectral
magnitude in the voice-frequency range; it is not calibrated sound pressure or a
literal waveform. The SystemUI flowing-line view visualizes changes in these
levels.

No new audio capture, microphone permission, server request, speech transcript,
or connection-state animation is used for this signal. The existing SDK analysis
can return zero if a track analyser is unavailable. The app deliberately does not
invent activity in that case.

- Muting immediately publishes zero input, while output can continue.
- Disconnecting immediately publishes two zeros and clears native values.
- Non-finite values, detached tracks and getter failures produce zero.
- Native values stay bounded in [0, 1]; the observation time uses Android elapsed
  realtime, shared with SystemUI. Existing IPC ignores samples older than 1.2 s.
- Only the protected SystemUI service can receive this level state. It contains
  no audio samples or conversation contents.

## Verification, October 11

`node rose-agent/tests/status-audio-levels.cjs` passed measured independent
levels, silence, mute/unmute, malformed values, detached tracks, connection
lifecycle and bounded sampling. The existing ElevenLabs UI lifecycle test passed.
The complete production APK compiled and passed signature verification.

An isolated emulator APK kept the production front end and injected test-only
SDK return values. Its real WebView-to-Java bridge reported:

| SDK input | SDK output | State | Native input | Native output |
|---|---|---|---|---|
| 0.2 | 0.7 | connected | 0.2 | 0.7 |
| 0.8 | 0.3 | muted | 0 | 0.3 |
| 0 | 0 | connected silence | 0 | 0 |
| 0.9 | 0.9 | disconnected | 0 | 0 |

No crash-buffer entries appeared. The previously installed emulator preview APK
was restored, and its synthetic waveform override was cleared. No physical Pixel
install, live session, microphone capture, or paid API call was performed.

This verifies the plumbing with controlled inputs, not live acoustic performance.
A supervised actual ElevenLabs conversation remains necessary before claiming
physical-device audio responsiveness or Bluetooth routing is verified.

The probe receiver exists only in the emulator source tree and is included only
when building with `PIXEL_EMULATOR_FRONTEND=production`; it is absent from the
production APK manifest. The default emulator front end and waveform fixture are
unchanged.
