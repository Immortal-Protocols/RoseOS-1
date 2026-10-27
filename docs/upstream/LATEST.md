# Latest handoff — October 22, 2026

## Included

- Pixel Voice v287, including server selection/fallback, automatic viewer launch,
  launcher reuse of the active workspace, and multi-device live updates.
- Pixel PiP controller v25 and Fold split controller v22 as separate build choices.
  The Fold viewer fills its pane and shows the orange task-activity glow.
- Cross-app prototype control, normal installer interaction, focused-field typing,
  screenshot/control refresh, and the Android window-removal/reconnect fixes.
- Headless Linux backend setup, a user-service example, portable FFmpeg selection,
  and generation of the model catalog from the recipient's own Codex cache.
- Current delivery, Play Store operation, provider-switching, Notes and Memories
  changes. The 14 app-fork revisions remain unchanged because their exported
  sources already match the latest canonical app source.
- Original Pixel Fold port reference patches and the optional older install
  helper source. Neither is presented as a newly validated standalone OS release.

Nate's Samsung adaptive launcher, status glow and stock-Android fallback code are
preserved. No phone was updated or erased by preparing this handoff.

## Start from your existing installation

Pull the repository, read [setup](SETUP.md), then choose [Mac/backend pairing](BACKEND.md)
or [Linux](LINUX_BACKEND.md). For the background viewer, follow
[background control](BACKGROUND_CONTROL.md); the controller APK and OS must match.
Keep your own signing keys and private configuration. Do not overwrite a working
deployment's key or pairing directory with newly generated material.

## Scope still open

The fast-install helper's remaining Google Fi name mismatch is not reported as
fixed. The change to open windows for otherwise screenless native tools remains
on hold. A complete independent OS rebuild, recipient phone acceptance and a full
Linux VM reboot test remain to be done. [VALIDATION.md](VALIDATION.md) separates
export checks from live tests on the original devices.
