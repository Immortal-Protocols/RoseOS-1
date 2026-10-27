# Isolated emulator integration test

This harness does not use the physical Pixel or the production server's session state. It has a separate pairing token, no delivery catalog, no email/calendar access, no Codex child, and rejects task execution. It uses the existing protected API project's key only when the emulator requests an actual live voice session. No transcript is stored. Diagnostics retain only current connection/microphone flags in memory.

Build with `PIXEL_EMULATOR_DIR="$HOME/.config/pixel-voice-emulator" bash rose-agent/emulator/build.sh`. Output is a signed `pixel-voice-emulator.apk` in that private directory. Original app assets/APK remain untouched. The package identity stays `dev.pixel.voice` so the real SystemUI contract is exercised; never install this candidate on the physical phone.

Run on Mac with `PIXEL_EMULATOR_DIR="$HOME/.config/pixel-voice-emulator" node rose-agent/emulator/server.mjs`. It binds loopback port 19444. A forwarded TLS connection uses the existing pinned server certificate but a distinct test-only bearer token. Do not expose the production port 9443 to this emulator.

## Remote emulator transport

The emulator must reach Mac loopback19444 through an SSH reverse tunnel on its Linux host, then ADB reverse. Example shape (replace the SSH destination with the verified build host; do not use a guessed host):

```sh
ssh -N -R 127.0.0.1:19444:127.0.0.1:19444 VERIFIED_BUILD_HOST
```

Run ADB on that Linux host, explicitly targeting its emulator serial. First verify `ro.kernel.qemu` returns `1`. Then `adb -s EMULATOR_SERIAL reverse tcp:19444 tcp:19444`. A desktop emulator can reverse directly to the Mac host. Do not use an unqualified `adb install` when a physical Pixel is connected.

## Test sequence after custom SystemUI boots

1. Confirm target is an emulator; install the isolated APK there. Grant RECORD_AUDIO and applicable Bluetooth/notification permissions. Do not provision accounts or device owner merely for IPC tests: the tray does not need device-owner privileges.
2. Start Rose Agent once, then open a stock Settings page to reveal the reserved tray. Confirm REGISTER returns Ready/Disconnected before starting and display changes arrive without polling from the client.
3. Tap the real tray's Talk. Confirm test server diagnostics show connected/open and a live enabled microphone track; tray shows Listening. This is a real voice API connection, not a mocked state.
4. Tap Mute. Confirm track.enabled=false and tray Unmute. Repeat explicit MUTE via a privileged test if needed; it must remain muted. Tap Unmute; track.enabled=true again.
5. Tap End. Confirm capture track disappears and tray stops saying Listening; prepared connection may remain but no active microphone. No unexpected automatic restart.
6. Verify ordinary shell/app attempts to bind the AgentTrayService are denied by STATUS_BAR_SERVICE; verify tray reconnects after emulator-only Rose Agent force-stop/relaunch and SystemUI restart. Repeat registration should not duplicate clients.
7. Lock emulator: tray hides, then restores appropriately after unlock. Open keyboard: reserved tray stays above IME. Navigate Home: full agent UI replaces tray. Return to Settings: no overlap and controls remain usable.
8. Shut down test server/tunnel and emulator when finished. Do not restart production services or publish this test APK.

Current checks: isolated APK builds and signature verifies; health returns200; empty apps catalog returns200; task execution returns403; malformed voice session returns400; unauthenticated requests return401. Actual emulator voice/IPC and geometry tests await boot.

## Verified WSL transport

Because SSH terminates on Windows while the emulator runs in WSL, a Windows loopback reverse tunnel alone is insufficient. `python3 rose-agent/emulator/tunnel.py` runs a small framed TCP relay over SSH stdio to `quantus-rtx`, invoking `/home/pixelbuild/pixel-os/emulator-tunnel.py remote` in Ubuntu-24.04. WSL listens only on127.0.0.1:19444; traffic reaches Mac127.0.0.1:19444 with TLS intact. It changes no firewall, Windows portproxy, production service, or physical phone configuration. The remote script must match the local script. Stop the local process to remove the listener.

Verified from WSL: authenticated health200 with isolated=true, unauthenticated401, and validation against the pinned server certificate. After boot, ADB reverse from the explicitly verified emulator to WSL19444 completes the path.

## Runtime verification, 2026-10-09

On custom image `emulator-5556` (qemu=1), the real SystemUI tray successfully controlled an actual isolated voice session:

- Talk: connected data channel and live microphone track with enabled=true; tray displayed Listening/Mute.
- Mute: enabled=false and muted=true; tray displayed Muted/Unmute.
- Unmute: enabled=true and muted=false.
- End: no microphone tracks, ready=false. Prepared connection may subsequently return without recording.
- Unprivileged test APK explicitly queried `dev.pixel.voice`, then received SecurityException when binding AgentTrayService. This avoids mistaking Android package visibility rejection for permission enforcement.
- Force-stop/relaunch exposed an uncaught background foreground-service-start denial in StartupReceiver on a non-device-owner install. Receiver now handles IllegalStateException/SecurityException and waits for user activation. The emulator-only candidate was rebuilt and installed; relaunch then real tray Talk/End passed with no new crash record.

Geometry/keyboard verification is owned separately by the OS tray implementation. Physical Pixel and production service were not touched by these tests.

SystemUI process restart recovery also passed on the updated emulator image: old PID905 killed, replacement PID2581 rebound and showed Ready. Actual tray Talk enabled the mic, Mute disabled it, and End released tracks. Crash buffer was empty. ADB reverse was restored after emulator-only adbd root restart. Mic left off for subsequent geometry tests.


## Status-bar waveform preview, 2026-10-11

The emulator frontend now sends measured microphone and reply audio levels through the protected SystemUI IPC. It does not automatically open a paid voice session. The emulator build also contains `WavePreviewReceiver`, a deliberately synthetic signal fixture for visual checks on the remote headless emulator. Broadcast `mode=left`, `right`, `both`, `mute`, or `off` to that receiver to preview those states for up to ten minutes. This receiver and IPC override are inserted only into the isolated build copy, never the production APK. A simulated preview verifies placement and rendering, not microphone capture.
