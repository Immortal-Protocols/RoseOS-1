# Bro 1 voice transport

Rose Agent now uses the ElevenLabs client SDK over WebRTC. The native home, Bluetooth routing, media-button handling, wireless phone controls, APK delivery and Mac Codex executor remain in place. The hosted conversational agent calls `delegate_task`; the task executor retains its existing Luna route.

The restricted ElevenLabs key is read only on the Mac from `~/.config/pixel-voice/elevenlabs-api-key`. The authenticated `/eleven/session` endpoint gives the phone a temporary conversation token. The hosted agent is private and uses the saved Bro 1 voice. No API key is packaged into the APK. Audio recording is disabled in the agent settings. Recent locally stored transcript text is passed as continuity context; the service still stores recognized text locally as before.

`/eleven/task` uses an idempotent client request ID; `GET /eleven/task/:id` returns progress and verified final results. Client tools wait up to 100 seconds; longer work continues on the Mac, with its final result added to conversation context and displayed in the task area. Ending voice does not stop a task already accepted by the Mac.

Unlike the old transport, no paid voice session is pre-opened while idle. Native Talk, automatic glasses connection, and media play/pause start a fresh conversation. Mute disables the SDK microphone track. End closes the SDK connection and native audio route. Existing physical media-button mappings remain unchanged.

## Build and validation

Run `bash rose-agent/build.sh` (bundles the pinned npm client from the lockfile), `node --test rose-agent/server/elevenlabs.test.mjs`, and `node rose-agent/tests/eleven-ui.cjs`.

Opt-in `node rose-agent/server/test-eleven-phone.mjs` runs a real Bro 1 text conversation using the same agent and client tool configuration; it actually opens Vanadium on the paired Pixel. Do not run it when changing the foreground app would interfere with a user task.

Verified on the physical Pixel: two-way Bro 1 speech with the intended greeting, SDK connection, native microphone route, media-button mute state, and End returning to microphone-off. The same hosted agent successfully called the Mac executor to open Vanadium and reported the verified foreground result. Physical glasses gestures still need a user wearing the glasses for end-to-end hardware verification; their native handlers were not changed.

Prior source and APK are in `rose-agent/backups/pre-elevenlabs/` for rollback. Restoring old APK alone is insufficient if the server is changed incompatibly; legacy `/session` and `/event` endpoints remain supported.
