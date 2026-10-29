# Rose Models Android app (`dev.pixel.models`)

A distinct, small Android text-chat app: transcript, Send, Stop, Continue, status and the last
app-action result. The conversation and agent loop run on the phone; the server only does
inference. It is **not** Rose Agent (`dev.pixel.voice`), does not take the HOME role and asks
for no accessibility, overlay, microphone, device-owner or background-control authority.
MODELS-ANDROID-APP-001.

**Current state: shell with a FAKE engine.** The banner says `FAKE ENGINE (local fixture), not a
model` until Core's `local-agent-v1` engine is wired in through the native adapter
(`workspace/models/rose-chat-android/`). Nothing here talks to a model yet, and this module
implements no second agent engine: `FakeEngine` only scripts the effect sequence for UI and policy
work.

## Layout

| Path | Role |
| --- | --- |
| `src/dev/pixel/models/TurnController.java` | One turn at a time: carries engine effects out, fences late/duplicate replies, enforces 2 model requests and 1 app action per turn, Stop/background/destroy handling |
| `AgentEngine`, `Effect` | The engine boundary, shaped like local-agent-v1 (StartTurn, InferenceResponse, ToolResult, Cancel → NeedInference, NeedTool, Render, terminal) |
| `AppAllowlist`, `AllowlistLauncher` | `open_app {app_id}` policy: user-selected allowlist, installed and unambiguous launcher component, keyguard check |
| `AndroidAppLauncher` | Platform calls only: list launcher activities, read keyguard, start one explicit component (adapted from the narrow launch path in Rose Agent's `PhoneControl` at 9664372b) |
| `HttpsInferenceClient`, `EndpointPolicy` | HTTPS-only POST on a worker thread with platform trust, 4 MiB response cap, cancel by disconnect. Not wired while the FAKE engine is in use |
| `MainActivity` | Programmatic UI and the Apps picker that builds the allowlist |
| `FakeEngine`, `FakeInferenceClient` | Labelled local fakes (no network) |

## Behaviour contract

- `open_app` accepts only an opaque `app_id` from the user's selection. Package names, components,
  Intent URIs, extras and shell strings are refused as `not_allowed`. A missing or ambiguous
  target is `not_found`; a locked phone is `locked`; a platform exception is `platform_error`.
- `launch_requested` means the Intent was dispatched, not that the app rendered.
- Immediately before an Intent is sent, the controller checks the turn is still current, not
  stopped, the activity is in the foreground and not destroyed; otherwise the result is `cancelled`.
- **Stop** cancels the request and ends the turn. Replies that arrive afterwards are ignored. An
  app already opened stays open, and the UI says so.
- **Background** (for example after opening another app) cancels any request and parks the turn.
  Back in the foreground, **Continue** re-sends the parked request; Stop ends it. Nothing runs in
  the background.
- No cleartext: `usesCleartextTraffic=false` and a network security config with system trust
  anchors only. The endpoint must be `https://` with no embedded credentials.

## Build and test

```sh
bash workspace/models/android/tests/run.sh          # JVM policy tests: JDK only, no Android SDK
bash scripts/setup-android-sdk.sh                    # pinned SDK, if not already present
bash workspace/models/android/build.sh               # unsigned APK + build-receipt.txt in out/
bash workspace/models/android/tests/check-apk-policy.sh out/rose-models.*/apk-metadata.txt
```

The APK is **unsigned**: not installable, not published. Infra owns the version and signing
identity. `versionCode 1 / 0.1.0-dev-unsigned` is a placeholder. CI:
`.github/workflows/models-android.yml`.

`ROSE_MODELS_NATIVE_LIB=…/librose_chat_android.so` adds the arm64 native library (stored
uncompressed, 16 KiB page alignment) once the native adapter exists.

## Evidence levels

JVM tests and the unsigned build prove source behaviour and packaging only. No Android runtime,
emulator, phone install or model has been exercised.
