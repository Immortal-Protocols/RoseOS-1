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
| `HttpsInferenceClient`, `EndpointPolicy` | HTTPS-only POST of the agent's request body, on a worker thread with platform trust. No redirects, a 4 MiB response cap, cancel by disconnect. Reports the raw status and body, or `connect` / `timeout_connect` / `timeout_response` / `tls` / `response_too_large` |
| `Json` | A small JSON codec with no Android dependency |
| `MainActivity` | Programmatic UI, plus Settings (HTTPS endpoint and exact model id, no defaults) and the Apps picker |

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
  anchors only, an `https://` endpoint with no embedded credentials, and no auth header.
- There is no default endpoint or model. Send stays disabled until Settings has both.

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
identity. `versionCode 1 / 0.1.0-dev-unsigned` is a placeholder. CI:
`.github/workflows/models-android.yml`.

The native library is stored uncompressed and the APK is zip-aligned for 16 KiB pages. Without
`ROSE_MODELS_NATIVE_LIB` the APK has no native code and cannot start a chat.

## Evidence levels

The JVM tests run Core's real agent through the JNI adapter, built for the test machine (x86_64),
not on Android. The arm64 library is checked as a file (ELF, alignment, exports), not loaded on a
device. The unsigned build proves packaging only. No Android runtime, emulator, phone install or
model has been exercised, and no phone-reachable inference endpoint exists yet.
