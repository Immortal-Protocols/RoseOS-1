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
| Rose Agent and Calculator | `dev.pixel.voice`, `dev.pixel.calculator.agent`, signed separately | `--preinstall-apk` (product partition); see below |
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

### Release channels (alpha, beta, prod)

With `--updater-url`, the patcher also applies `os/patches/updater-rose-channels.patch`
and copies `RoseChannels.java` into the Updater. It also applies
`os/patches/updater-product-build-make.patch`, because upstream lists the `Updater`
module only for `OFFICIAL_BUILD` (`media_system.mk`), so RoseOS builds shipped
without it (candidate 2026102803). The patch adds `PRODUCT_PACKAGES += Updater` next
to that block; `OFFICIAL_BUILD` stays unset. A signed build must then contain
`SYSTEM/priv-app/Updater/Updater.apk` (`app.seamlessupdate.client`) and its
`SYSTEM/etc/{permissions,default-permissions,sysconfig}/app.seamlessupdate.client.xml`.
A `--keep-upstream-updater` build includes no Updater. The selector offers:

| Label | Stored `channel` | Wire channel (`DEVICE-channel`, metadata token) |
| --- | --- | --- |
| Prod | `prod` | `prod` (default for new installations) |
| Beta | `stable` | `stable`: the existing Pixel feed `frankel-stable` |
| Alpha | `alpha` | `alpha` |

Beta keeps the legacy `stable` wire channel, so existing phones and existing
metadata keep working; nothing requests `frankel-beta`. Only channels listed with
`--updater-published-channel` (default `stable`, the feed Infra verified) are
fetched. Any other choice is labelled "not available yet" in the selector, a
manual check reports it, and periodic checks make no request. The Updater's
device, channel and newer-build checks are unchanged, and an explicit
`sys.update.channel` override keeps its stock behaviour.

The stock Updater stores `channel` only after its settings screen is opened, so
a missing key does not mean a new installation. On the first boot of a build with
these rules, a phone that arrived through an OTA
(`PackageManager.isDeviceUpgrading()`) and has no stored channel is given `stable`
(Beta). A new installation stores nothing and follows the Prod default. A marker
makes this run once, so a later OTA never moves a phone. An explicit choice,
including a legacy `stable`, always survives. A leftover upstream `beta` value has
no RoseOS feed and shows as unavailable until the owner picks another channel.
Publish `prod` or `alpha` metadata before listing them as published.

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
   metadata it accepts. The published `frankel-2026102401` notes state the feed as
   `https://github.com/Immortal-Protocols/pixel-ota/releases/latest/download/`
   with channel `frankel-stable`; because the feed follows GitHub's "latest"
   release, a candidate is offered only once it is the latest release. This is
   the documented value, not a reading of the phone's configuration.
3. Installed `dev.pixel.voice` and `dev.pixel.backgroundcontrol` version codes and
   signing certificates. The controller prebuilt must have a greater version code
   than any installed copy, or Android keeps the installed one. `agent-pip` is now
   28 (`2.1.5-pip`); the verified draft 2026102802 carried 27. The policy must pin the same certificate as Rose Agent.
4. Which app holds the HOME role. Android keeps the role holder across an OTA;
   expected, not yet observed on this phone.
5. How Rose Agent reaches the existing Pixel Voice VM (endpoint and pinned
   certificate already configured in the installed app, or a new app build).

Planning baseline: `frankel-2026102401`, which the owner reports as installed (not
measured on the phone). Its release notes, whose bytes match the published
`SHA256SUMS`, state fingerprint
`google/frankel/frankel:17/CP3A.261005.005/2026102401:user/release-keys` and AVB
rollback index `1791158400` (2026-10-05); these are release-author statements,
not checks of the OTA payload. The pinned `os/pinned-manifest.xml` source has
security patch level `2026-10-05` (`build/release` `cp2a`) but `BUILD_ID`
`CP2A.260605.016`, so the published `2026102401` build was not produced from
exactly this pinned tree. Reconcile the candidate's source with the tree that
built `2026102401` before building; keep the security patch level and rollback
index at or above the baseline's.

## Preinstall and default HOME

For this milestone the OTA carries Rose Agent, the controller and Calculator, so
the owner needs no separate installation; Rose Agent can still be updated as an
app afterwards. `apply-os-changes.py --preinstall-apk APK` (repeatable, with
`--background-policy`) adds each APK as a non-privileged, presigned
`android_app_import` on the product partition (`packages/apps/RosePreinstall/`)
and lists it in `target/product/handheld_product.mk`. Each APK must be
byte-identical to Rose Agent or a target pinned in the policy receipt, so the
image, the policy and the shipped apps cannot drift apart. The imports are
`preprocessed`, so Soong copies each APK unchanged; an ordinary presigned import
uncompresses native libraries and re-zipaligns (`build/soong/java/app_import.go` on
the builder). As that pinned validator requires, each APK must store `lib/**/*.so`
uncompressed and pass `zipalign -c -p 4`; compressed dex is accepted for these
non-privileged imports. The patcher checks this before writing anything. The
controller keeps its existing privileged, preprocessed `system_ext` prebuilt.
Nothing is platform-signed. Built-image bytes and signatures still need checking
against the receipt.

A preinstalled copy only takes effect when it is newer than the copy the phone
already has, and it must carry the same signer. With the same signer and an
equal or higher installed version, the installed copy keeps winning and the new
code is not delivered. With a different signer, the installed copy and its data
are at risk at first boot. Measure each package's installed version code, signer
and code path before building a frankel candidate, and give every bundled APK a
higher version code.

An existing phone keeps its HOME role holder across an OTA (expected, not yet
observed on this phone). This Permission module's HOME role has no configurable
default; it falls back to the highest-priority HOME activity, and a tie produces
a chooser (`HomeRoleBehavior.getFallbackHolderAsUser` at the pinned revision).

## Cuttlefish alpha product (no Updater)

The first emulator alpha targets `aosp_cf_x86_64_only_phone-cur-userdebug`
(`device/google/cuttlefish/vsoc_x86_64_only/phone/aosp_cf.mk`, pinned `283645aa`).
`aosp_cf.mk` inherits `generic_system.mk` → `handheld_system.mk` →
`media_system.mk`, `handheld_system_ext.mk` and `aosp_product.mk` →
`handheld_product.mk`, so the 2804 Rose integration (tray property, controller,
`VirtualDeviceManager`, Rose Agent and Calculator preinstalls) reaches it unchanged.

There is no emulator update feed, so the phone Updater is excluded:
`updaterMode` is `excluded-no-compatible-feed` and there is no endpoint. This is
also required for the build: `aosp_cf.mk` uses the Soong-defined
`aosp_shared_system_image`, which does not list `Updater`, and `file_list_diff`
fails on system files installed by make but missing from that image.

GrapheneOS `media_system.mk` also installs `Seedvault` on system, and no
`build/make` `Android.bp` lists it or its `required` closure (pinned
`packages/apps/Seedvault` `7cfffed7`: `LocalContactsBackup`, the three
`com.stevesoltys.backup_*` permission files and
`default-permissions_app.grapheneos.backup.contacts`), so `file_list_diff` would
fail on those too. The alpha keeps Seedvault and adds that six-module closure to
`aosp_shared_system_image` deps; the allowlist, `file_list_diff` and the Soong
image switch are unchanged.

`os/patches/cuttlefish-alpha-no-updater.patch` therefore makes two changes in
`build/make`: it removes only the RoseOS `PRODUCT_PACKAGES += Updater` block from
`target/product/media_system.mk` and adds the Seedvault closure to
`target/product/generic/Android.bp`.
Infra applies it with `git apply` to an already independent alpha source root that
holds the 2804 integration, never to the retained Pixel root; on any other base
`git apply --check` fails. `apply-os-changes.py` never applies it, so Frankel keeps
its Updater. The exact delta, base, app and policy hashes and the image
expectations are in `os/alpha/cuttlefish-product-contract.json`, checked by
`os/tests/test_cuttlefish_alpha_product.py` on pinned fixtures. That test evaluates
product variables only; image contents and boot need the alpha build.

The tray property needs no override for the Soong image: its `system-build.prop`
(`build/soong` `250710f7`) reads `:product_config`, which carries the product's
`PRODUCT_SYSTEM_PROPERTIES` (`core/soong_extra_config.mk`), and
`gen_build_prop.py` writes them into the system `build.prop`. The test runs the
pinned `gen_build_prop.py` on the evaluated product to show
`persist.wm.debug.pixel_agent_tray=1` reaches that output.
