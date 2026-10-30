# Persistent-app replacement experiment (OS-COMPONENT-EXP-004)

A synthetic `android:persistent="true"` app, `dev.roseos.test.persistentfixture`, is used to observe on one
disposable AOSP emulator what the pinned package-manager source (OS-COMPONENT-EXP-003A) predicts:

| Stage | Expected |
| --- | --- |
| ordinary `adb install` of v2 over the image copy | rejected: "Persistent apps are not updateable" (`InstallPackageHelper`) |
| `adb install --staged` of the same v2, then reboot | applied; `/data` codePath, versionCode 2, v2 process running |
| `pm uninstall-system-updates`, then reboot | image copy (`/system/app/PersistentFixture`, v1) running again |

- `fixture/` holds the app: its Application logs `marker version=N pid=P sentinel=…`.
- `experiment.py build` compiles v1 and v2 with the SDK tools and signs both with one job-local test key. That key is
  never a production identity, and it is deleted after the run.
- `experiment.py run` performs the stages. Each stage records `pass`, `fail`, `unavailable` or `not-run` with a reason.
- `test_experiment.py` holds the static and unit checks. It runs locally and as the workflow's first step.

The workflow `.github/workflows/systemui-persistent-experiment.yml` is manual only (`workflow_dispatch`): one
ubuntu-24.04 job, 30 minutes, `contents: read`. It uploads `results.json` and small text excerpts only.

## SystemUI stages (opt-in)

`experiment.py run --systemui` adds `systemui_stages.py` after the fixture stages:
- identity of the image's own `com.android.systemui`;
- a refusal of the same APK re-signed with the job-local test key;
- a staged same-version reinstall through `scripts/systemui-component-update.py`;
- that tool's recovery to the image copy.

These stages need only a booted emulator, not the fixture seeding. The workflow does not pass `--systemui`.

## What the run does not do

- **No security or licence changes:**
  - It accepts no licence; `sdkmanager` runs with stdin closed.
  - It does not change `/dev/kvm` access, SELinux or verity. If verity is enforcing, seeding is reported as
    unavailable instead of disabling it.
  - It grants no installer or rollback permission.
- **No production targets:** without `--systemui` it does not touch `com.android.systemui`. It never touches a phone, a builder, alpha or any release.

## What a pass shows

A pass shows only this package-manager mechanism on the named emulator image. It says nothing about GrapheneOS
SystemUI's shared UID or platform signature, privileged installer authority, real SystemUI restart safety, automatic
rollback, OTA precedence or Pixel behaviour. "Restore" is a manual return to the image copy, not platform rollback.
