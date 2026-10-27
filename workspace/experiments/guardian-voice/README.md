# Rose Agent → Manager Guardian

Implemented 2026-10-13. Native Pixel build 166. Normal Pixel calls still use the existing task path. “Talk to <member>'s Guardian” opens a fresh voice session, using the actual per-manager/per-member Guardian thread through the signed-in A-LIST Manager app. “Back to Pixel” opens a clean normal session. Home options also has “Talk to a Guardian”; the active name and Back button appear in the native Home header.

## Design

- `bridge.mjs`: authenticated loopback-only queue on 127.0.0.1:8797. Main Pixel HTTPS service exposes selection and request/status APIs to the paired phone. Generation checks reject late replies across member switches. Stable request IDs go to A-LIST's existing idempotence token.
- `GuardianVoiceBridge.swift` in `alist-manager-mac`: the already-signed-in app owns all A-LIST credentials and delegated access. Loads assigned clients, verifies selected thread, sends and reads replies using ManagerGuardianService. No A-LIST token is exported to Node or Pixel.
- `client.js`: narrowly recognizes explicit spoken switch/back commands. In Guardian mode all other spoken requests go to Guardian instead of Luna or local media tools. Audio remains GPT-Live/WebRTC and existing glasses controls.
- Voice startup waits for cancellation of an in-flight previous start. A late old server session is explicitly closed; it cannot overwrite the new session ID.
- Guardian voice sessions receive no personal Pixel memory, previous Pixel transcript, or automatic book/sky/task context. Replies and requests persist in the real Manager thread; they are not fed into Pixel's personal-memory collector.
- An unlinked assistant reply is accepted only after the exact outgoing row, before another user row, excluding pre-send IDs and validating the client stamp. This matches the live backend's reply shape.

## Runtime

The signed-in Manager app must be running. Its local bridge key is in the app bundle's Resources (locally generated, excluded from Git), matched to `~/.config/pixel-voice/guardian-voice.json`. No cloud backend deployment or new A-LIST auth account is required.

Guardian work retains its normal server latency. Ending speech or switching back does not undo an already delivered Guardian request. No automatic resend after an uncertain result. No claim that the underlying Guardian supports native audio streaming.

## Verification

- JavaScript tests: `node --test rose-agent/voice-providers/providers.test.cjs experiments/guardian-voice/*.test.*` (17 passing at implementation).
- Mac tests: `swift test --filter GuardianVoiceBridgeTests` (two passing).
- Signed Mac app and Android APK build.
- Installed Pixel v166 and signed Mac build verified. Holland Sleeper selected via the real Pixel Home options; header and Back to Pixel button visually checked. Synthetic speech through GPT-Live produced a real Guardian request and reply in the Mac chat, returned as 969,600 bytes of audio with spoken identification of Holland. Physical glasses-microphone test remains for the user.
- `live-test.mjs` explicitly sends one synthetic spoken question through GPT-Live to the real currently selected Guardian. Run only when a user authorizes a test of that client. It requires a live Pixel Guardian session. No record mutations are requested by the test.

## Rollback

Immediate: tap Back to Pixel or say “Back to Pixel.” This stops routing new requests to Guardian; normal voice remains available.

Full code rollback: backup/ contains the original server, provider, HTML, native Java, build script, manifest, previous signed Manager bundle, and Pixel APK v161. Restore only these feature-touched source files, quit Manager, restore its bundle with ditto, and restart `local.pixel-voice.server`. Android does not allow ordinary version downgrades: rebuild the backed-up Pixel sources with a version code higher than the installed version and deliver through the existing publisher. Do not uninstall or wipe user data. Preserve any subsequent unrelated changes before restoring files.
