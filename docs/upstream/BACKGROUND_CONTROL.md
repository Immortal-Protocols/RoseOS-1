# Background control and viewers

The agent runs Android apps in a virtual display on the phone while you keep using its physical screen. This requires the included custom OS changes. Installing the APK on stock Android or Samsung does not add this capability; the Samsung launcher and status glow remain supported separately.

The current source includes Pixel Voice v289, PiP controller v26, and split-pane controller v23. They use the same controller package name: choose one variant per device. The original v6 controller remains as a no-viewer reference.

## Build the controller

Use the same app signing identity for Pixel Voice and Background Control. Use your own JDK and Android SDK. The source compiled against API 35; the custom OS supplies the ComputerControl extension at runtime.

```sh
export JAVA_HOME=/path/to/jdk
export PATH="$JAVA_HOME/bin:$PATH"
export ANDROID_JAR=/path/to/android-sdk/platforms/android-35/android.jar
export BUILD_TOOLS=/path/to/android-sdk/build-tools/35.0.0
# Supply passwords securely in the environment, not as command-line values.
export BACKGROUND_KEYSTORE_PASSWORD
export BACKGROUND_KEY_PASSWORD
python3 scripts/build-background-agent.py \
  --viewer pip \
  --android-jar "$ANDROID_JAR" --build-tools "$BUILD_TOOLS" \
  --keystore /private/path/app-signing.jks --alias your-key-alias \
  --out /private/path/background-build
```

Choose `--viewer split` for the Fold viewer or `--viewer none` for the original controller. Omitting the key performs an unsigned compile check only. Signed builds produce `Agent.apk` and a V4 `.idsig`; keep both for GrapheneOS package updates. Installing an APK does not update OS policy.

## Let the agent follow the task between apps

The working prototype can follow a task into another app, including Android's installer, and press its ordinary buttons. It does not need an app-name exception for each install. Screenshot taps and typing into a focused field remain available when semantic controls are missing.

Generate policy from your actual signed controller and Pixel Voice APKs:

```sh
python3 scripts/prepare-background-policy.py \
  --prototype-control \
  --agent-apk /private/path/background-build/Agent.apk \
  --voice-apk /private/path/pixel-voice.apk \
  --sdk TARGET_OS_SDK_NUMBER \
  --aapt "$BUILD_TOOLS/aapt" --apksigner "$BUILD_TOOLS/apksigner" \
  --output /private/path/background-policy.json
```

Use the target OS's actual `ro.build.version.sdk`. This authorizes your controller signing identity for cross-app control without publishing the key. The historical restricted mode remains available by omitting `--prototype-control` and supplying `--target-apk` for each target; that does not reproduce the current broad-control prototype.

## Apply the matching OS changes

Follow [OS.md](OS.md) for the pinned source, device/vendor inputs and your own signing chain. On a clean checkout:

```sh
python3 scripts/apply-os-changes.py /path/to/grapheneos \
  --background-policy /private/path/background-policy.json \
  --background-agent /private/path/background-build/Agent.apk --check
python3 scripts/apply-os-changes.py /path/to/grapheneos \
  --background-policy /private/path/background-policy.json \
  --background-agent /private/path/background-build/Agent.apk
```

The first command checks every patch in order in a temporary copy. Both check pinned revisions, clean source, overlay conflicts and the verified APK hash. They never build, install or flash anything. `--emulator` also includes the original Goldfish patch.

The changes include the extension, feature flag, privileged controller, recipient signing policy, cross-app access, screenshot timing, and reconnect window refresh. Synchronous removal of the status-bar window fixes the Android crash previously triggered by reopening the viewer. The original SystemUI work stays included. Existing explicit device policy can override product defaults; inspect it when diagnosing a mismatch.

For an original Pixel Fold, see [port notes](../os/fold-port/README.md). The optional `--reference-display-address` comes from your device's physical cover display; no original address is embedded here. It chooses phone-sized dimensions before rendering and input are created so the mobile layout and taps agree.

Build with the `cur` release configuration, a valid new release number, matching vendor inputs, and your own OS keys. Verify signed images and the target device before installation. The complete portable OS export has not had a fresh independent build; the original machines' successful boots do not prove another phone is ready.

## Pair and enable

Follow [BACKEND.md](BACKEND.md) or [LINUX_BACKEND.md](LINUX_BACKEND.md). Obtain your phone's enrollment ID with `node workspace/pixel-voice/security/pair.mjs status`. With the backend stopped, save this private configuration as `~/.config/pixel-voice/background-phone.json`, then restart:

```json
{"enabled": true, "deviceId": "YOUR_OWN_64_CHARACTER_PAIRED_DEVICE_ID"}
```

This is an enrollment ID, not a USB serial. Do not copy another deployment's pairing files. Keep the separate Artemis foreground experiment disabled. Select Background Control as the assistant where required by the OS setup. Check authenticated health, then complete a harmless voice or typed task through Pixel Voice. Verify the actual result and that the user's visible app remains usable; health alone is not acceptance.

## Viewer behavior

- **Pixel / PiP:** opening a background app automatically opens its floating live viewer once per session. Moving or dismissing the viewer leaves that session running. Tapping the controlled app in Pixel Voice reveals the existing viewer.
- **Fold / split:** agent left, user right, no diagnostic header or footer. The orange edge glow pulses while working and becomes steady when idle. The agent pane is currently view-only for the user.
- **Fold closed:** the live test kept the same agent session running while the user's app filled the outer screen. Locking is a separate case and is not established by this test.
- **Locked or screen off:** background control continues only with the opt-in `--locked-control` OS build and the owner's PIN or biometric arming in Pixel Voice. Owner gates such as installs, permissions and settings still wait for the owner to unlock. See [Screen state and locked-phone control](LOCKED_CONTROL.md); this is not yet device-validated.
- **Fast native commands:** some work without opening a screen and therefore do not show a floating window. The proposed always-open fast-tool change is deliberately not included.

These describe the original devices. Repeat showing, hiding, typing, installation and folding on your target. Apply the OS window-removal fix before testing repeated PiP reopen cycles; one successful calculation is not enough.

Normal installer screen control is the general path. The older [optional fast-install helper](../os/optional-installer/README.md) is included separately; its remaining name-matching case is not declared fixed by this publication.

To disable background mode, finish the task, stop the server, set `enabled` to `false`, and restart. Reverting framework code requires an appropriate signed forward OS update; a blind downgrade is not a rollback procedure.
