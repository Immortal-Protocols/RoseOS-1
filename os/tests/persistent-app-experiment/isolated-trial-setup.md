# Isolated SystemUI trial runner: tools and setup manifest (EXP006-ISOLATED-TRIAL-001)

This is the source-side manifest for the approved two-hour trial. The workflow side is the `runner: isolated-trial`
input of `.github/workflows/systemui-persistent-experiment.yml`, guarded by `trial_preflight.py`. The Mac operator
alone creates and tears down the VM and runner. Nothing here grants access, and no step below runs from CI.

## What the workflow requires of the host

The trial path runs only when the `runner` input is `isolated-trial`; the default `hosted` path is unchanged. On that
path:

- **Runner selection:** `runs-on` is group `roseos-systemui-test` with labels `self-hosted` and
  `roseos-systemui-test`. The group must admit only private RoseOS-1 (repository id 1428996648) and
  `Immortal-Protocols/RoseOS-1/.github/workflows/systemui-persistent-experiment.yml@refs/heads/main`.
- **Guard before checkout:** the job must be `workflow_dispatch` on `refs/heads/main` of that repository and
  workflow, and `kvm_acl=true` is refused. `kvm_access.py` separately refuses any runner that is not
  GitHub-hosted.
- **`trial_preflight.py`, right after checkout:**
  - the job user is non-root, `sudo -n true` fails, and the user is in no sudo, admin, wheel, docker, root, lxd or
    libvirt group;
  - `/dev/kvm` is a character device, not world-writable, that the user can already open read/write;
  - the checkout persisted no git credential;
  - `/etc/roseos-systemui-test/deadline` holds one Unix time, is root-owned with mode `0444` (or stricter), and sits
    in a root-owned, non-group/other-writable directory. At least 2100 s (35 min) must remain and at most 2 h 5 min.

  It changes nothing. Its fixed-word record is `trial-preflight.json` in the uploaded evidence.
- **Limits:** one job at a time (concurrency group, `cancel-in-progress: false`), `timeout-minutes: 30`,
  `contents: read`, no secrets and no `id-token`.
- **Unchanged experiment behavior:** verified boot (the seed stays unavailable under `veritymode=enforcing`), stage
  reporting and cleanup.
- **No reinterpretation:** PR270 does not turn a foreign-signer `exit 0` into a refusal. That stage reports what
  happens.

## Pins

| Item | Pin | Source of the pin |
| --- | --- | --- |
| VM image | `ubuntu-os-cloud/ubuntu-2404-noble-amd64-v20261024`, image id `4575951068918267275` (exact image, not the family) | packet's verified read-only catalog |
| `platforms/android-35` | `platform-35_r02.zip`, sha256 `0988cacad01b38a18a47bac14a0695f246bc76c1b06c0eeb8eb0dc825ab0c8e0` | `scripts/setup-android-sdk.sh` |
| `build-tools` 35 | `build-tools_r35_linux.zip`, sha256 `bd3a4966912eb8b30ed0d00b0cda6b6543b949d5ffe00bea54c04c81e1561d88` | `scripts/setup-android-sdk.sh` |
| `cmdline-tools/latest` (sdkmanager, avdmanager) | **no reviewed pin in this repository** | record the exact archive name and sha256 from the official `https://dl.google.com/android/repository/repository2-3.xml` before T0 |
| `platform-tools` (adb), `emulator`, `system-images;android-35;default;x86_64` | **no reviewed pin in this repository** (hosted runs used the runner image's SDK and `sdkmanager --install`) | install with that sdkmanager, which checks the repository manifest's checksums; record `sdkmanager --list_installed` revisions and archive sha256s before T0 |
| GitHub Actions runner | **no reviewed pin in this repository** | the official `actions/runner` linux-x64 release, with the version and sha256 published on its release page, recorded before T0 |
| Ubuntu packages | official Ubuntu 24.04 archive, apt-verified | record `dpkg-query -W` versions |

Any pin still missing at T0 is reported as missing, never filled with a guess.

## Ubuntu packages (root, official archive only)

- **Base:** `openjdk-17-jre-headless` (cmdline-tools needs Java 17 or newer), `python3` (3.12 in the image), `git`,
  `unzip`, `curl`, `ca-certificates`.
- **Emulator:** `libpulse0` is the library this emulator was observed to need on ubuntu-24.04 (runs 41109233214 and
  41122307845). Any further library: run `emulator -version` and `ldd` on the emulator's qemu as `rose-test`. Install
  only the official package that provides a library the loader names, and record it. No library is guessed.
- **Not installed:** docker, sudo rules for `rose-test`, desktop packages, `qemu-kvm`. The Android emulator brings its
  own qemu.

## SDK licences: decision needed before T0

- **Hosted runs:** they used the SDK licences the GitHub runner image had already accepted. This repository records no
  accepted licence file, and a clean Ubuntu VM has none.
- **What sdkmanager needs:** installing `cmdline-tools`, `platform-tools`, `emulator` and the system image requires
  acceptance of the Android SDK License (`android-sdk-license`) in `$ANDROID_HOME/licenses`. The direct
  platform and build-tools archives are distributed under the same terms.
- **Not done here:** the trial approval covers installing the official tools. It names no licence acceptance, so this
  manifest does not accept one.
- **How to proceed:** the Mac operator shows the exact text with `sdkmanager --licenses`, on a TTY, as root. Accept
  only `android-sdk-license`, and only with the owner's explicit acceptance of that text. Record the licence id and its
  file hash as generated.
- **Never:** copy licence hash files from elsewhere, pipe `yes`, or accept any other licence silently. If acceptance
  isn't given, stop before installing.

## Host layout

- **SDK:** `ANDROID_HOME=/opt/android-sdk`, root-owned `0755`, with
  `cmdline-tools/latest`, `platform-tools`, `emulator`, `platforms/android-35`, `build-tools/<35>` and
  `system-images/android-35/default/x86_64`. It is set only in the runner's `.env`; no other variable or secret goes
  there.
- **Verify before T0:** as `rose-test`, `sdkmanager --install "system-images;android-35;default;x86_64"` must exit 0
  and change nothing, because the experiment's image stage runs it. If it needs to write to the root-owned SDK, that
  stage reports `unavailable`. Report it rather than making the SDK writable.
- **Job-local state:** the AVD, test key, emulator home and outputs stay under `$RUNNER_TEMP` and are removed by the
  existing cleanup. Only the read-only SDK is shared.
- **KVM:** a udev rule makes `/dev/kvm` `root:kvm 0660`. `rose-test` is the only intended member of `kvm`, with no
  other privileged group and no sudo.
- **Deadline:** `/etc/roseos-systemui-test/` is `root:root 0755` and `deadline` is `root:root 0444`. It holds one line,
  the Unix time of T+110m (stop accepting jobs), written once at setup. A job can start only while at least 35 min
  remain before it, so the last start is T+75m.
- **Runner:**
  - name `roseos-systemui-test-01`, group `roseos-systemui-test`, label `roseos-systemui-test`, keeping the default
    `self-hosted` label;
  - installed under `rose-test`'s home and started manually with `./run.sh`, never as a service, with no autostart on
    boot;
  - the registration token entered only at the masked interactive prompt;
  - the credential files are `0600` and owned by `rose-test` until cleanup.

## Checks before the runner accepts work (as `rose-test`)

- `id` shows no sudo, admin, docker, lxd or libvirt group, and `sudo -n true` fails.
- `python3 -c "import os; os.close(os.open('/dev/kvm', os.O_RDWR))"` succeeds, and `/dev/kvm` is `crw-rw---- root kvm`.
- `$ANDROID_HOME/emulator/emulator -accel-check` reports KVM usable, and `emulator -version` succeeds.
- `sdkmanager --list_installed` shows exactly the recorded revisions.
- The deadline file has the mode, owner and value above.

These checks don't replace the job's own preflight. A failure stops the setup, and nothing is changed to make it pass.
