# Rose Agent prototype

A directly installed Android app that connects GPT-Live voice to a persistent Codex app-server running on this Mac. No Play Store is required.

## Components

- `app/`: native Android microphone permission/audio routing and a locally bundled WebView voice interface. Talk, Mute, End, transcript, task progress, typed requests, and configurable Mac endpoint.
- `server/server.mjs`: authenticated HTTPS service, Live WebRTC session creation, transcript/delegation routing, progress queue.
- `server/codex.mjs`: Codex app-server JSON-RPC integration using the existing ChatGPT sign-in, persistent thread history, and steering of active turns.
- `build.sh`: compiles/signs the APK with the existing local development signing key.

The OpenAI API key remains in `~/.config/pixel-voice/openai-api-key` (mode 0600). The phone creates a hardware-backed, non-exportable key in Android Keystore and pairs with the Mac using a one-time code. Voice/task, clipboard receiver, and screen-viewer connections authenticate the paired TLS client identity. Connection assets contain only public addresses and certificate pins. The rotated `pair-token` is for local Mac helpers only and is not included in the APK. See [secure pairing](security/README.md) for migration, revocation, and tests.


## Run and install

```sh
node rose-agent/server/server.mjs
rose-agent/build.sh
tools/platform-tools/adb -s YOUR_DEVICE_SERIAL install -r rose-agent/app/pixel-voice.apk
tools/platform-tools/adb -s YOUR_DEVICE_SERIAL reverse tcp:9443 tcp:9443
tools/platform-tools/adb -s YOUR_DEVICE_SERIAL shell am start -n dev.pixel.voice/.MainActivity
```

Initial endpoint: `https://127.0.0.1:9443`, using ADB reverse over USB. For wireless use, the phone must be able to reach the Mac service over a private network/VPN; change the endpoint in Connection settings. Network reachability is separate from wireless ADB used for APK installations. Do not expose unauthenticated ADB to the internet.

## Validation

- Live API WebSocket session started and closed successfully.
- Pixel WebRTC connected with microphone capture and inbound/outbound audio packets.
- Codex created and verified a real test file; result reached the Pixel.
- An active Codex turn accepted a correction; final test file contained CORRECTED.
- A synthesized spoken request crossed the Pixel WebRTC channel, was transcribed and delegated to Codex, and produced `runtime/spoken-proof.txt` containing `spoken request succeeded`. A user microphone utterance was also transcribed and answered.
- Mute disabled the actual microphone track. End received session closure and released the microphone and peer connection.
- Unauthenticated server requests returned 401; authenticated health checks passed.
- Final build disables WebView debugging.

## Current limitations

- Prototype foreground voice UI; keep it open. Background service, locked-screen audio, Bluetooth routing and hardware-button integration are not implemented/verified.
- Wireless routing and unattended remote installs are not yet verified. USB is currently the tested control path.
- Voice and Codex threads are distinct from the parent Codex desktop conversation. Relevant transcript is passed to the task backend, which retains its own thread history.
- The user authorized host and physical-phone control. Codex uses full host access for ADB/build operations; task instructions still require explicit authorization for messaging, destructive actions, publishing, or credential changes. The original workspace-only sandbox blocked ADB during the stopwatch test and was corrected.
- Local development certificate and per-install pairing token; no multi-user account system or production deployment hardening.

The Mac backend runs as user LaunchAgent `local.pixel-voice.server`, with logs in `~/.config/pixel-voice/`. It resumes the same Codex thread on restart. Restart with `launchctl kickstart -k gui/$(id -u)/local.pixel-voice.server`.


## Wireless delivery

Publish a signed APK with `node rose-agent/delivery/publish.mjs APK_PATH APP_LABEL`.
The publisher verifies its Android signature and copies immutable SHA-256 addressed
bytes into the private Mac delivery directory. Authenticated `/apps` and
`/apps/<sha256>/apk` routes serve the catalog and APK. Rose Agent automatically
downloads new entries, verifies their hash, and submits a PackageInstaller session.
Rose Agent is now registered as device owner (dev.pixel.voice/.PixelAdmin) on this test phone. It uses USER_ACTION_NOT_REQUIRED and performs automatic installs without an Android confirmation. The Install button retries a canceled
or failed delivery.

Verified on the Pixel through the iPhone hotspot: Moon Lander was downloaded by
Rose Agent, updated through Android PackageInstaller, and launched. Package
metadata identifies `dev.pixel.voice` as installer and initiating package. USB
was used only to operate the UI during this verification, with no ADB reverse
forwarding; the APK transfer used the public HTTPS endpoint. A physically
unplugged user confirmation test remains useful. Router forwarding currently
uses a temporary lease; this is not yet an unattended long-term internet service.

Silent-install validation: Memory was published through the Mac service and
automatically downloaded and installed by Rose Agent over the hotspot, without
any installer taps. Android package metadata confirmed dev.pixel.voice as the
installer and initiating package, and Rose Agent reported Installation complete.
The device-owner role was assigned via ADB without resetting the phone.

## API authentication

The LaunchAgent sets `PIXEL_CODEX_API_KEY_FILE` to the private API-key file already
used for voice. Codex app-server uses ephemeral credential storage and authenticates
with `account/login/start` (`apiKey`) before resuming the existing coding thread.
It verifies `account/read` reports `apiKey` and refuses a ChatGPT fallback. The
shared desktop/CLI login is not overwritten. Authenticated `/health` reports
`codingAuth` so the active mode can be checked without revealing credentials.

requests through that project; it is not a certification of the entire prototype.
Local transcripts, files, and service logs remain on the Mac as the user requested.

Verified after activation: `/health` reports `codingAuth: apiKey`; a Responses
request with the replacement key returned HTTP 200 and `store: false`; Live
started and closed successfully. A task submitted through the running Pixel
Voice backend wrote and read back `runtime/api-auth-proof.txt` with the exact
expected text. The connected Pixel UI received that successful result.

## Local Rose over Wi-Fi or Tailscale (remote link v1)

With local mode on (the default; `local-rose` preference `enabled`), the voice and phone links
talk to Rose on the Mac (`prototypes/voice-phone-demo`). Before pairing they use the USB
developer path, plain `ws://127.0.0.1:8091` over `adb reverse tcp:8091 tcp:8091`. After pairing
they never use that unauthenticated loopback.

- Pair once: on the Mac choose **Pair phone** in the Rose dashboard (or `python -m rose.pair`),
  then on the phone choose **Pair with Mac** in the Home menu and scan the code (or scan it with
  the Camera app: `roseagent://pair` opens `LocalRosePairActivity`). The screen asks before it
  sends anything. The same screen shows the pairing and offers **Unpair** (the device key stays).
- The QR code carries the Mac's addresses (MagicDNS name, Tailscale IPs, LAN IPs), its TLS port
  and the SHA-256 pin of its self-signed certificate. The phone trusts only that certificate.
  `LocalRoseKey` keeps an EC P-256 key (alias `rose-local-device-v1`) in StrongBox or the TEE and
  refuses to pair without secure hardware. Every websocket upgrade is signed (`X-Rose-Device`,
  `-Time`, `-Nonce`, `-Signature`; canonical string in `LocalRoseAuth.canonical`).
- `LocalRoseConnection` tries the address that worked last first, then the others, with backoff
  of 0.5 to 8 s. A default-network change (Wi-Fi, mobile, the Tailscale VPN) retries at once. A
  401/403 means the Mac removed this phone: the page shows "Not paired" and retries stop until the
  user pairs again. Pairing state lives in the app's `local-rose` preferences, never in Git.
- Build input: ZXing core 3.5.4 (Apache-2.0, notice in `app/assets/licenses/`), fetched by
  `bash scripts/setup-zxing.sh` (run by `scripts/bootstrap.sh`) into `.tools/zxing/` and checked
  by SHA-256 in `build.sh`.
- Offline test: `node --test workspace/rose-agent/tests/local-rose-auth.test.mjs` runs
  `app/tests/LocalRoseAuthTest.java` and cross-checks signatures with the Mac's Python
  (`prototypes/voice-phone-demo/.venv`).
