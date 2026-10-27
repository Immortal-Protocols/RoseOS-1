# Rose Agent OS integration

How the OS image and Rose Agent fit together for an update to an existing phone.
This describes source and offline checks, not a built, installed or device-tested
release. See [VALIDATION.md](VALIDATION.md) and [OS-RELEASE.md](OS-RELEASE.md).

## What the OS provides

| Piece | Source | Enabled by |
| --- | --- | --- |
| Status tray (agent/mic glows, tap to talk, mute, end) | `os/patches/frameworks-base.patch`, `os/overlays/.../wm/shell/AgentTray*.java` | `persist.wm.debug.pixel_agent_tray=1` (`apply-os-changes.py --agent-tray`) |
| Separate agent workspace | Background-control framework patches and the computer-control extension | `--background-policy` with `--background-agent` |
| Viewer and Stop | `dev.pixel.backgroundcontrol` (`os/background-control/agent-pip`): ongoing notification with Stop, floating viewer | Privileged prebuilt `PixelBackgroundControl`, pinned by the policy |
| Rose Agent app | `dev.pixel.voice`, built and signed separately | Not preinstalled; see below |
| Update channel | Pinned `packages/apps/Updater`, whose `url` is `https://releases.grapheneos.org/` | `--updater-url` with the installed phone's channel |

The tray binds `dev.pixel.voice/.AgentTrayService` (guarded by
`STATUS_BAR_SERVICE`) and only accepts replies from that package's UID. The
controller's provider accepts only callers with its own signing certificate, so
Rose Agent and the controller must share the app signing identity, as the policy
already checks.

Before `--agent-tray`, the repository applied the tray code but never enabled it.
Only the historical Mustang scripts and the Fold patch set the property for one
device. The new patch sets it in `generic_system.mk`. Confirm that the target
product inherits that file by checking the built `system/build.prop` for the
property, as the historical Mustang verification did.

The repository does not change the Updater, so a build made without
`--updater-url` would poll GrapheneOS, whose releases this phone cannot verify,
and lose the existing channel. Pass the base URL the installed phone already uses;
`frankel-stable` metadata and OTA assets must be served under it. The URL is a
device and release-channel fact to obtain first, not something to infer from
`pixel-ota` asset names.

## Stop contract (controller schema 3)

The owner's Stop (notification action or controller screen) must end the task,
not just the workspace. Previously the backend's next `open_app` silently started
a new session after a Stop. The controller now records why a session ended:

- `get_state` includes `stopReason` (`""`, `"user"`, `"agent"` or `"deadline"`),
  `stoppedSessionId`, `taskId` and `stoppedTaskId`. Status `"stopped"` with
  `stopReason: "user"` acknowledges the owner's Stop. `"cleanup_failed"` does not.
- `start` accepts an optional `taskId` (1–128 characters of `[A-Za-z0-9._:-]`).
  If the owner stopped that task, `start` opens nothing and returns
  `{"status": "user_stopped", "actionExecuted": false, "rejectedTaskId": ..., "stopReason": "user", "current": {...}}`.
  The top level names only the rejected task. Any workspace still running for a
  newer task is reported, unchanged, under `current`; it was not stopped. A start without `taskId` behaves as before, which old backends
  rely on.
- `command` and provider `stop` also accept an optional `taskId`. When it names a
  task other than the one holding the workspace (or the workspace has no task),
  nothing runs and the result is `{"status": "stale_task", "actionExecuted": false,
  "rejectedTaskId": ..., "current": {...}}`, or the `user_stopped` refusal if the
  owner stopped that task. A late cleanup from task A therefore cannot close task
  B's workspace. Requests without `taskId` behave as before.
- Agent `stop` or `home` records `"agent"` and never replaces the owner's report
  for the same session. The ten-minute limit records `"deadline"`.

- The owner's Stop also sends broadcast
  `dev.pixel.backgroundcontrol.action.OWNER_STOP` to package `dev.pixel.voice`
  with `sessionId`, `taskId` and `stoppedAt` (epoch milliseconds), so Rose Agent can
  end the task even if the agent makes no further phone call. The sender shares its
  identity (Android 14 `BroadcastOptions.setShareIdentityEnabled`). The receiver
  must be exported, check that `getSentFromPackage()` is `dev.pixel.backgroundcontrol`
  and that its signature matches, and ignore anything else. Delivery is best effort;
  `get_state` remains authoritative.

The backend must send a `taskId` per user request and end the task on
`user_stopped` or `stopReason: "user"`. Only the `agent-pip` viewer variant
implements schema 3; the split and original controllers remain schema 2.

## Update compatibility for an existing phone

The update must keep app data and identities, so these facts come from the
installed phone and current release channel, not from this repository:

1. Installed OS build number, device, and that the OTA signer and AVB key match the
   existing chain. The new build number must be greater.
2. The Updater's configured channel URL (to pass as `--updater-url`) and the
   metadata it accepts. The published `frankel-2026102401` notes give the feed as
   `https://github.com/Immortal-Protocols/pixel-ota/releases/latest/download/`
   with channel `frankel-stable`; because the feed follows GitHub's "latest"
   release, a candidate is offered only once it is the latest release.
3. Installed `dev.pixel.voice` and `dev.pixel.backgroundcontrol` version codes and
   signing certificates. The controller prebuilt must have a greater version code
   than any installed copy, or Android keeps the installed one. `agent-pip` is now
   27 (`2.1.4-pip`). The policy must pin the same certificate as Rose Agent.
4. Which app holds the HOME role. Android keeps the role holder across an OTA;
   expected, not yet observed on this phone.
5. How Rose Agent reaches the existing Pixel Voice VM (endpoint and pinned
   certificate already configured in the installed app, or a new app build).

Published baseline (user reported as installed, not measured on the phone):
`frankel-2026102401`, fingerprint
`google/frankel/frankel:17/CP3A.261005.005/2026102401:user/release-keys`, AVB
rollback index `1791158400` (2026-10-05). The pinned `os/pinned-manifest.xml`
source has security patch level `2026-10-05` (`build/release` `cp2a`) but
`BUILD_ID` `CP2A.260605.016`, so the installed build was not produced from exactly
this pinned tree. Reconcile the candidate's source with the tree that built
`2026102401` before building; keep the security patch level and rollback index at
or above the installed values.

## Preinstall and default HOME (proposal, not implemented)

Rose Agent stays an independently updated app for this update. Preinstalling it
does not help a phone that already has it and adds risk. An installed copy with a
higher version code shadows the system copy. A different signer would conflict
with existing data. For new installs later, add a presigned `android_app_import`
pinned to the policy's Rose Agent certificate, and set the HOME default
deliberately. This Permission module's HOME role has no configurable default; it
falls back to the highest-priority HOME activity, and a tie produces a chooser
(`HomeRoleBehavior.getFallbackHolderAsUser` at the pinned revision).
