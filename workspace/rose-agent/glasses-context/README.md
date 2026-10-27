# Glasses photo context

The user explicitly requested that pressing the glasses capture button share a photo with their current agent conversation. Glasses Camera still saves the photo to Gallery. Its `Share new photos with agent` switch controls sending subsequent photos; it remains an optional foreground camera session.

## Data path

1. Glasses Camera obtains a photo via Meta DAT and saves Gallery output.
2. With sharing enabled, it makes a bounded JPEG (maximum edge 1280 pixels, maximum 512 KB), and calls the Rose Agent content provider. Glasses Camera has no INTERNET permission.
3. The provider verifies the caller package and signing certificate, requires connected voice, then uses Rose Agent's existing certificate-pinned HTTPS and pairing authentication. The camera app receives neither credentials nor other app data.
4. `/glasses/photo` checks capture time, payload size and session identity, saves the image privately on the Mac, and emits metadata for that same voice session. Duplicate capture IDs cannot overwrite later captures.
5. The voice model receives a silent notice that an image is available to the backend. On the next backend request in that session, the actual file is attached as a Codex `localImage` input. Uploading alone does not call a model or interrupt existing work. Image text is treated as untrusted content.

The most recent photo is available for ten minutes within its originating voice session. It is not added to personal memory, and a new call cannot inherit it automatically. The Mac stores up to twenty captures per server process for review; files can persist across restarts. Existing images in the ongoing Codex task remain part of that task's history. Gallery storage and any gallery-backup settings are separate.

## Rollback

Turn off `Share new photos with agent` in Glasses Camera to stop new delivery. The gallery and physical shutter still work. Full pre-change sources and the Rose Agent APK are in the private rollback directory printed during installation. `integration.patch` records changes outside this repository. Restore those sources and rebuild with a higher versionCode to preserve app data when rolling back. Do not uninstall or wipe either app.

## Validation

`node --test rose-agent/glasses-context/*.test.mjs rose-agent/server/session-lease.test.mjs rose-agent/voice-providers/*.test.*`

Automated checks cover matching-session image delivery, expiry, invalid input, duplicate capture IDs, native image parts on new and steered turns, and existing voice integrations. On-device image receipt and a real backend visual answer must also pass before reporting end-to-end completion.

Verified on Pixel: camera version 11 / Rose Agent 135. The camera-to-provider-to-Mac path delivered a 181,636-byte JPEG. The normal task endpoint attached that image and the backend correctly described its visible objects. Turning sharing off still saved a gallery photo without creating another server image. Sharing was restored on. The physical microphone follow-up remains a user test; voice was reconnected and left muted as before.
