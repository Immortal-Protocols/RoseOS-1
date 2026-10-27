# Screen state and locked-phone control

Pixel Voice has two owner controls for the physical screen:

- **Keep screen on / allow screen off.** This is the "Pixel Voice screen" Quick Settings tile, with the same choice as **Keep screen on** in Pixel Voice's home options. When it is on, `FLAG_KEEP_SCREEN_ON` is set while Pixel Voice is resumed. The flag is cleared on pause and re-applied on resume, and a tile change applies immediately, even under the shade. The setting never dismisses or delays the keyguard.
- **App control while locked.** This option is off by default, in Pixel Voice's home options. When it is on, the assistant can keep working in the background workspace while the phone is locked or its screen is off. The lock screen stays up. The physical screen is never driven while locked.

## Requirements

Locked control needs both of these:

1. **An OS built with the owner opt-in:** `apply-os-changes.py --locked-control`. This needs `--background-policy`/`--background-agent` from a `--prototype-control` policy, and it adds `os/patches/locked-control-frameworks-base.patch`.
2. **The owner arming it on the phone.** Turning it on opens Android's own credential prompt (BiometricPrompt, PIN/pattern/password or strong biometric). Turning it off needs no prompt.

Without the OS opt-in, Android keeps refusing to create sessions while locked (`ERROR_DEVICE_LOCKED`), and Pixel Voice reports `locked`.

## Layers and what each one enforces

| Layer | Check |
| --- | --- |
| Framework, session creation (`ComputerControlSessionProcessor`) | Used only when the device is locked or the default display is not interactive. Then a session may be created only if all of these hold: `config_pixelComputerControlLockedSessions` is true; the agent matches the signing pin in `config_pixelComputerControlPrototypeAgents`; the user is unlocked (credential-encrypted storage available); the lock screen is secure; `LockPatternUtils.getStrongAuthForUser` is `STRONG_AUTH_NOT_REQUIRED` (so not after boot, in lockdown, after the strong-auth timeout, after failed attempts, or under a device-policy requirement); and Keystore's last PIN/password/pattern or fingerprint authentication is no older than `config_pixelComputerControlLockedAuthMaxAgeMs` (4 hours). If any check fails, the original `ERROR_DEVICE_LOCKED` and visible-window rules apply unchanged. |
| Framework, running session (`ComputerControlSessionImpl`) | While the device is locked, owner-gated packages cannot be launched, captured or operated. They are Android's system package, setup wizard, installer, uninstaller, verifier, permission controller, Settings, SystemUI, and any package holding `INSTALL_PACKAGES` or `DELETE_PACKAGES`. If one appears, the session moves to Android's own `Blocked` state and its virtual device sleeps. This applies to every ComputerControl session once the patch is applied, including sessions started before the phone locked. |
| Pixel Voice (`PhoneControl` → `LockedControl`) | When the device is locked or the physical display is not on, the controller's `start` and `command` operations need the owner's arming and a successful signature with an Android Keystore key. That key requires user authentication with a 4-hour timeout (`AUTH_DEVICE_CREDENTIAL` / `AUTH_BIOMETRIC_STRONG`) and is invalidated by new biometric enrollment, so Android Keystore enforces how recent the owner's authentication is. An ordinary PIN or fingerprint unlock renews the window. Removing the secure lock screen or changing biometrics turns the option off. `get_state`, `stop`, and app listing stay available. |
| Pixel Voice physical-screen paths | Unchanged. The keyguard checks in `PhoneControl`, `PhoneAccessibility`, `ScreenCapture`, `ScreenGesture` and `PhoneAppContext` still return `locked`. No ADB input, keyguard dismissal or `disableKeyguard` is used. |
| Controller (PiP v26, split v23) | Reports `deviceLocked` and a `reason` of `device_locked` (Android refused creation), `owner_unlock_required` (an owner gate was reached), or `target_in_foreground`. The lock-screen notification shows only "Pixel Voice is using an app". While locked, the split controller does not request installer recovery. The viewer is not launched while locked; it opens on the first observation after the owner unlocks. |
| Hub (`background-phone.mjs`) | Maps these results to `status: "locked"` with `lockedControl` set to `not_enabled`, `authentication_expired`, `invalidated`, `os_refused` or `owner_gate`, with `actionExecuted: false`. Sessions blocked at an owner gate are retained and reported as `background_blocked` with `ownerUnlockRequired: true`. The task policy tells the agent not to work around the lock screen or owner gates. |

Unchanged upstream protections still apply on the background display: `FLAG_SECURE` windows (`BLOCK_REASON_SECURE_CONTENT`), app authentication prompts (`BLOCK_REASON_AUTHENTICATION_PROMPT_REQUESTED`), password fields (excluded by the controller), per-agent consent, lockdown (the virtual device sleeps), the session timeout, and the controller's 10-minute and 200-operation limits.

## Why the session keeps running with the screen off

This is source analysis of the pinned GrapheneOS `2026100600` `frameworks/base` (`84183aff…`), not a device observation:

- The ComputerControl display is `VIRTUAL_DISPLAY_FLAG_TRUSTED | VIRTUAL_DISPLAY_FLAG_ALWAYS_UNLOCKED`. `KeyguardController` skips always-unlocked displays.
- Trusted displays owned by a virtual device join the device's own display group. The power button sleeps only the default group and groups flagged as adjacent to it, and an unset virtual-device screen-off timeout is effectively infinite.
- The virtual device sleeps when the session is `Blocked` without a mirror, and in lockdown.

## Operating notes

- Turn locked control off from Pixel Voice's home options. To disable it at OS level, rebuild without `--locked-control`, which needs a signed forward OS update.
- On GrapheneOS only PIN/password/pattern and fingerprint count toward the framework's recent-authentication check (it queries `PASSWORD` and `FINGERPRINT` Keystore auth tokens).
- After the window expires, unlocking the phone once renews it; no other action is needed.
- Same-signer apps can call the controller's provider directly. In this workspace only Pixel Voice does. Session creation remains gated by the framework for every caller.

## Validation status

The cloud merge smoke only boots the modified OS and opens Settings. Locked app actions, owner arming, screen-off behavior and the Quick Settings tile have not been validated end to end. They are not additional requirements of that smoke test. See [validation](VALIDATION.md) for the recorded results.
