# GrapheneOS build

The OS base is GrapheneOS tag `2026100600`, Android 17. The latest verified original-device build is `2026102101`, for Pixel 10 Pro XL (`mustang`). These historical identifiers identify the source; they are not reusable release numbers for future updates.

The handoff includes a pinned source manifest, our SystemUI changes, and the build/release scripts. Full Android source is fetched through `repo`; it is not duplicated inside this repository. Build on x86_64 Linux with the resources required by [GrapheneOS's build guide](https://grapheneos.org/build).

Select the target supported Pixel, fetch matching vendor/device inputs, apply the included changes, and compile. Signing and packaging are separate. Generate your own OS keys and verify the resulting image for that target. The original production keys and device backups are not supplied.

Existing release scripts retain the original target as a reference. They must not be used to flash a different Pixel. The currently tested update workflow uses recovery sideload; a custom unattended OS updater is not part of this handoff. App APK updates are independent.

The complete clean OS rebuild and a port to Nathan's device have not been run as part of source packaging. See VALIDATION.md for the exact checks completed.

## Reconstruct the source

The flattened manifest pins every project to the commit recorded on the build machine. From a new Linux source directory, use this private repository as the manifest source:

```sh
repo init -u https://github.com/Immortal-Protocols/pixel-workspace.git -b main -m os/pinned-manifest.xml
repo sync -c -j4 --fail-fast
```

Git must be authenticated for the private manifest repository. The manifest's remotes point to the corresponding public GrapheneOS/AOSP sources. Its base was the official GrapheneOS `2026100600` tag; follow upstream instructions to verify that tag independently.

After checkout, run `python3 /path/to/pixel-workspace/scripts/apply-os-changes.py /path/to/grapheneos`. Add `--emulator` only for the included Goldfish emulator change. The script verifies base revisions and checks patches before modifying source; it does not compile, sign, or flash anything. Follow upstream device-specific vendor preparation and build instructions next.

## Pixel Voice screen and lock behavior

The "Pixel Voice screen" Quick Settings tile and the matching home option switch between keep screen on and allow screen off. Keep screen on sets `FLAG_KEEP_SCREEN_ON` only while the app activity is resumed and clears it when the activity pauses. It does not disable Android's keyguard. Physical-screen accessibility, screen-capture and gesture commands still check the lock state and stop with `locked`.

Owner-authorized app control while the phone is locked or dark runs only in the background workspace. It needs the opt-in `--locked-control` build option, which adds `locked-control-frameworks-base.patch` on top of the background-control patches, and the owner arming it in Pixel Voice with a PIN or strong biometric. Installer, permission, settings, system and secure screens stay owner gates while locked. See [Screen state and locked-phone control](LOCKED_CONTROL.md).

## Background control addition

Follow [Background app control](BACKGROUND_CONTROL.md) for the additional framework and build patches, controller APK, generated signing policy and required release flag. The original statusbar/SystemUI patches remain intact. The new phone build was verified on the original Pixel; the portable export has patch and app-build checks, not a completed independent full OS build.
