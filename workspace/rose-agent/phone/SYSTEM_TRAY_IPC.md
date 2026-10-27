# System tray IPC, version 1

SystemUI binds explicitly as the current user to `dev.pixel.voice/.AgentTrayService` using `BIND_AUTO_CREATE`. This is a Messenger service, not a public intent action. It stays usable while bound even when the voice engine is stopped.

The exported service requires platform signature permission `android.permission.STATUS_BAR_SERVICE`. Each incoming Messenger message also verifies the framework-supplied sending UID belongs to the same Android user, has that permission, owns `com.android.systemui`, and that package has the platform signature. Rose Agent itself need not be platform signed. Ordinary apps, shell, and cross-user callers cannot control it. Do not replace this with an unprotected broadcast or an app-defined normal permission.

| Message | Code | Behavior |
| --- | ---: | --- |
| REGISTER | 1 | Supply `replyTo`; immediately receives STATE, then changes at up to 12.5 checks/second. Register once per bound connection. |
| UNREGISTER | 2 | Supply same `replyTo` before unbinding. |
| START | 3 | Start or resume voice; does not open an activity. |
| MUTE | 4 | Explicitly mute an active conversation; repeated calls must not toggle. |
| UNMUTE | 5 | Explicitly unmute an active conversation; repeated calls must not toggle. Does not create a new conversation. |
| END | 6 | End conversation using existing restart protection. |
| STATE | 100 | Service-to-client Bundle below. |

STATE fields: `protocolVersion` (int, 1), `available`, `connected`, `listening`, `muted` (booleans), `status` (canonical UI string). `available=true` means IPC is available, not that a remote voice session is ready. `listening` means active and unmuted; a muted conversation has `muted=true`, `listening=false`. Status is Ready, Connecting, Listening, Muted, or Disconnected. Optional waveform fields are `inputLevel` and `outputLevel` (floats normalized to 0–1), and `audioObservedAt` (elapsed-realtime milliseconds). Levels older than 1.2 seconds are zeroed; muted input is zero. No audio samples, task descriptions, transcripts, email contents, tokens, or error details cross this interface.

SystemUI should clear stale state and disable controls when binding is lost, rebind on package/user changes, and show Unmute when `muted=true`. Commands are requests; render the returned state rather than optimistically claiming recording has started. The existing service handles Android microphone/foreground-service restrictions. This interface does not bypass the lock screen or grant access to other applications' UI.

The v14 candidate builds and signs successfully. Explicit mute/unmute idempotence and existing mute/End lifecycle tests pass. Not deployed yet. End-to-end verification requires the custom platform-signed SystemUI consumer; stock SystemUI has no tray client.

## Current profile scope and lifecycle

The intended deployment is the primary Android user (user 0). Android SystemUI normally runs under user 0 even when it binds into a secondary user. The deliberate same-user caller check therefore rejects secondary-user controls; supporting those profiles requires an explicit trust-policy change, not merely changing the bind target. Do not claim multi-profile support.

The service links client Binder death and unregisters dead subscribers, so a SystemUI restart cannot retain an idle status poll indefinitely. SystemUI must still register again after reconnecting.
