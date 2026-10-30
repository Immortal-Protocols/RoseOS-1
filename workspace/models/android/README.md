# Rose Models Android app (`dev.pixel.models`)

A distinct, small Android text-chat app: transcript, Send, Stop, Continue, status and the last
app-action result. The conversation and agent loop run on the phone; the server only does
inference. It is **not** Rose Agent (`dev.pixel.voice`), does not take the HOME role and asks
for no accessibility, overlay, microphone, device-owner or background-control authority.
MODELS-ANDROID-APP-001.

The conversation, history, action validation, limits, fencing, Stop and background rules are
**Core's local-agent-v1 agent** (`workspace/models/rose-chat-core/src/agent.rs`, contract
`workspace/models/contracts/local-agent-v1.md`), loaded through the thin JNI adapter
`workspace/models/rose-chat-android/`. This app is only its Android host. It adds no agent logic.

## Layout

| Path | Role |
| --- | --- |
| `LocalAgentHost` | Forwards Send/Stop/Continue/background as agent inputs and carries out its outputs: HTTPS calls, `cancel_inference`, `need_tool`, render and turn end |
| `NativeAgent`, `AgentPort` | JNI wrapper. Java owns the native handle and destroys it exactly once; steps after close answer `rejected` |
| `AgentConfigJson` | The local-agent-v1 config: exact model id, `Reasoning strength: low` and the allowlist ids and labels |
| `AppAllowlist`, `AllowlistLauncher` | `open_app {app_id}` policy: user-selected allowlist (`[a-z][a-z0-9_]{0,31}` ids), installed and unambiguous launcher component, keyguard check |
| `AndroidAppLauncher` | Platform calls only: list launcher activities, read the keyguard, start one explicit component (adapted from the narrow launch path in Rose Agent's `PhoneControl` at 9664372b) |
| `HttpsInferenceClient`, `EndpointPolicy` | HTTPS-only POST of the agent's request body with the bearer from `CredentialSource`, on a worker thread with platform trust. No redirects, a 4 MiB response cap, cancel by disconnect. Reports the raw status and body, or `connect` / `timeout_connect` / `timeout_response` / `tls` / `response_too_large`; a credential it cannot get is reported as the auth server's status |
| `DeviceAuth`, `KeystoreDeviceKey`, `ModelsService` | service-auth-v1 (`workspace/models/contracts/service-auth-v1.md`) against the fixed `https://models.a-list.com` / `muse-glimmer`: a hardware-attested Keystore P-256 key enrolls once with an admin invite, then each bearer (at most 1 h) is renewed by signing a fresh challenge. Bearer and invite stay in memory; prefs keep only the key id and state |
| `InviteLink` | The admin QR: exactly `dev.pixel.models://enroll?invite=rgi1.<43 base64url>` (VIEW, cold start or `onNewIntent`), read once and cleared from the intent. No camera or storage permission: any scanner opens the link |
| `Json` | A small JSON codec with no Android dependency |
| `MainActivity` | Programmatic UI. No inference settings: Settings shows the fixed service and offers **Reset sign-in**; the Apps picker stays. Old endpoint/model prefs are removed on start; chat and app data are kept |

## Host duties (from the contract)

- Immediately before an Intent is sent, the host checks that the turn is still the live one, the
  activity is in the foreground and not destroyed, and (in `AllowlistLauncher`) the phone is
  unlocked. Otherwise it reports `cancelled` or `locked`. Exactly one `tool_result` is sent per
  `need_tool`.
- `launch_requested` means the Intent was dispatched, not that the app rendered.
- Leaving the foreground sends `backgrounded`; the agent decides whether the turn is cancelled or
  parked. **Continue** is offered only for a parked turn, back in the foreground.
- **Stop** sends `cancel`. An app already opened stays open, and the UI says so.
- No cleartext: `usesCleartextTraffic=false`, a network security config with system trust
  anchors only. No secret ships in the APK; the bearer goes only to its own origin.
- Sign-in is automatic and never blocks Send. Each turn has at most two HTTP inference attempts:
  one 401 renews and resends once only while an attempt is left, so there is never a third.
- The invite goes only with the first enrollment. A key is recorded as pending before that request;
  a timeout, 5xx or lost reply re-sends it with the same key and chain on a fresh challenge (no
  second code), and after a restart the key renews or, if never committed, waits for a rescan. Only a
  401 `challenge_mismatch` during a rescan allows a new key. 403 `invite_required`/`invite_invalid` waits for a new scan;
  any other 403 is terminal (key and state kept) until **Reset sign-in** is chosen. 429 (Retry-After) and 5xx retry at most
  three times; 400/401 and TLS failures never retry. Stop cancels sign-in and inference.

## Build and test

```sh
bash workspace/models/android/tests/run.sh          # JVM host tests over the real agent (JDK + cargo)
lib="$(bash workspace/models/android/build-native.sh)"   # arm64 .so; needs an existing NDK
bash scripts/setup-android-sdk.sh                    # pinned SDK, if not already present
ROSE_MODELS_NATIVE_LIB="$lib" bash workspace/models/android/build.sh   # unsigned APK + receipt in out/
bash workspace/models/android/tests/check-apk-policy.sh out/rose-models.*/apk-metadata.txt --require-native
```

`build-native.sh` installs nothing. Without an NDK or the `aarch64-linux-android` Rust target it
stops with the exact missing piece (exit 3). It links with 16 KiB page alignment, and
`tests/check-native-lib.sh` verifies the result (AArch64, LOAD alignment, the three JNI exports).

The APK is **unsigned**: not installable, not published. Infra owns the version and signing
identity; `versionCode 2 / 0.2.0` upgrades signed v1 in place. CI:
`.github/workflows/models-android.yml`.

The native library is stored uncompressed and the APK is zip-aligned for 16 KiB pages. Without
`ROSE_MODELS_NATIVE_LIB` the APK has no native code and cannot start a chat.

## Retained signing inputs (CI artifact)

`models-android.yml` keeps one **private** workflow artifact per run for 7 days, named
`rose-models-unsigned-<sha12>-run<run id>-<attempt>`. `stage-artifact.sh` copies exactly six files
into a new directory: `rose-models-unsigned.apk`, `librose_chat_android.so`, `apk-metadata.txt`,
`build-receipt.txt`, `artifact-receipt.txt` (source commit, event/ref/run, Models trees, both
Cargo.lock hashes, rustc/cargo/JDK/NDK/SDK identities, badging, native check) and `SHA256SUMS`.
It fails if the library inside the APK is not byte-identical to the staged one.

A second job downloads that artifact into a fresh directory and runs `tests/verify-artifact.sh`
(exact inventory, every hash, source/run binding, library-in-APK, unsigned `dev.pixel.models`,
arm64-v8a, INTERNET only, badging re-read with aapt) and `tests/artifact-negative.sh` (a positive
control plus six damaged copies that must be rejected). Downloaded files are data only.

Only an artifact from a successful **main** run is a signing candidate; pull-request runs build
merge-test commits and are kept separate by name and receipt. The artifact is unsigned and not
installable; Infra signs a separately hashed output.

## Evidence levels

The JVM tests run Core's real agent through the JNI adapter, built for the test machine (x86_64),
not on Android. The arm64 library is checked as a file (ELF, alignment, exports), not loaded on a
device. The unsigned build proves packaging only. Sign-in is tested against a scripted server and a
software key; Keystore attestation, the live service and the model are not exercised by CI.
