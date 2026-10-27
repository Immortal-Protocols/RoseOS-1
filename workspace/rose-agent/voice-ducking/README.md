# Optional voice ducking trial

System mode requests Android transient-may-duck focus only while Rose Agent output is active. Light mode applies a smaller media-stream volume reduction. Both use the existing provider-neutral output-level bridge without changing communication routing or media buttons. A 450 ms release delay avoids flutter between words; a 2 s watchdog releases a stalled signal. Ships disabled by default; explicitly enabled on the test Pixel.

Off switch (immediate, no reinstall or reconnect):

```sh
python3 rose-agent/phone/control.py voice_ducking --enabled off
```

Enable or inspect:

```sh
python3 rose-agent/phone/control.py voice_ducking --enabled on
python3 rose-agent/phone/control.py voice_ducking
```

`--test-seconds 1..10` performs a bounded focus probe without synthesizing speech. It does not test the whole voice conversation.

Validation: controller tests cover default-off, one acquisition per burst, gap hold, immediate disable, no repeated denied requests, stale-meter watchdog, probe expiry, disconnect. Existing provider-neutral audio bridge tests passed. Signed Android build passed.

On the physical Pixel, an Apple Music audio player remained started on the same player instance before/during/after the probe. The Android audio dump listed that player as ducked only during the probe, and removed it afterward. Its focus loss was transient-can-duck, not pause. This verifies system behavior, not subjective sound quality or a real spoken-reply test.

`baseline/` holds exact pre-trial touched sources; `baseline-v101.apk` is a local ignored APK snapshot. `integration.patch` records glue changes; `VoiceDucking.java` is the new controller. Leave existing unrelated changes alone when reverting. Standard off switch is preferable to source/APK rollback.

## Lighter adjustable mode

```sh
python3 rose-agent/phone/control.py voice_ducking --mode light --reduction-db 4 --enabled on
```

This uses the current route’s media-volume dB curve to select the closest quieter step. It leaves the voice-call level alone, does not acquire system ducking focus, and restores the media baseline after speech. Hardware volume updates change the baseline while retaining the offset; direct user changes win over stale restoration. Pending recovery levels are stored by route type for interrupted sessions. A detected coupled voice/music stream disables the trial instead of changing both. No Bluetooth routing code is modified. The discrete Android volume steps mean the realized reduction may differ from the requested value.

Return to the original system-selected depth without reinstalling:

```sh
python3 rose-agent/phone/control.py voice_ducking --mode system --enabled on
```

Physical glasses test: voice stayed at stream step 15; music moved from 25 to 23 during the light probe and returned to 25 afterward. Apple Music and WebRTC tracks remained started on the glasses output. This confirms routing and volume restoration, not subjective loudness. The requested four-decibel drop is quantized to Android’s available stream steps.
