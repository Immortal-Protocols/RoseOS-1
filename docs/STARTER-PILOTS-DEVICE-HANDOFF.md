# Starter pilots on API 34 and 37 emulators: source handoff to the emulator workflow's owner

**To:** OS Product, owner of `.github/workflows/systemui-persistent-experiment.yml` and `os/tests/persistent-app-experiment/`.
**From:** App Core. **Basis:** main 707f5ee5, prepared 2026-11-19.

- Nothing here has been dispatched or granted, and no runner, VM or phone has been touched.
- The only approval it needs is the existing `kvm_acl=true` delta (§7).
- It proves compiled starter trees with a scripted writer install, launch and run on an AOSP emulator (§9). It does not prove writer-generated apps.

## 1. Owner and coordination

| Area | Owner (from session trailers and merge titles) |
|---|---|
| Workflow file and drivers (`experiment.py`, `setup_emulator.py`, `kvm_access.py`, `report.py`, tests) | **OS Product** (session_015TFqkUvLjBgRPN1soK4VYh). Every change since #242: 19657ceb, e214ec24 (#255, KVM-001), cb47c4de, 1d8ff4a2, bba26b31, 395c86e5, 475febbf, b17bb72f (#273), 2069a7c7 (#281) |
| Original author | OS Core (session_015TFqkUBnNiWWDvKDuGw3hi): 59f0b726 EXP-004 through 93169785 |
| `qa/systemui-trial/tool-manifest.json`, `trial_plan.py`, `sdk_revisions.py` consumer | QA / Infra verifier (session_015TFqkR8dnr135pgmRQQCU7), 1bc52bbf (#278) |
| `.github/workflows/starter-pilots.yml`, `rose.app_builder.{starter_pilots,device_check}` | **App Core** (us), #899 |
| Dispatch and KVM approval | Repository owner (Nathan). `kvm_access.py:3-4` names him; the earlier finding is that all 11 runs were dispatched from that account (not re-checked here) |

App Core makes its one change (§5.1) in the PR that adds this document, so your PR can depend on it. Everything else in §5 is your source.

## 2. What exists (App Core side, merged in #899)

- **`.github/workflows/starter-pilots.yml`**
  - `workflow_dispatch` only, with input `pilots` (default `all`). Lines :11-17.
  - ubuntu-24.04, `permissions: contents: read`, `timeout-minutes: 60`.
  - Concurrency group `starter-pilots-${{ github.ref }}` (:22-24).
  - Builds 3 templates (feed, player, sprite-game) × 3 themes = 9 pilots. Gradle runs inside bubblewrap with no network, and each pilot is signed with a throwaway key that never leaves the runner.
  - Steps that touch the disposable runner: :45 `sudo apt-get install bubblewrap` (only if `bwrap` is missing) and :48 `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`.
  - :64 runs `sha256sum -c SHA256SUMS`.
  - :65-72 upload artifact `starter-pilots-${{ github.run_id }}` with `if: always()`, `if-no-files-found: ignore` and `retention-days: 3`.
  - `starter_pilots` exits 1 unless every pilot is ready (`starter_pilots.py` docstring). The build step then fails, so a partial set never counts as success.
- **`starter_pilots.py`:** `LABEL = 'compiled starter tree, scripted writer (not writer-generated)'` (:35), `SCHEMA_VERSION = 1` (:36).
- **`device_check.py`**
  - Usage: `python -m rose.app_builder.device_check --pilots DIR --serial SERIAL --evidence OUT [--adb ADB] [--keep]`.
  - It imports no third-party module: I checked under Python 3.13 here. It boots nothing and changes no device or host permission.
  - Per ready pilot (`check_one`, :119-178):
    1. `adb install -r`, then reads `versionCode` back from `dumpsys package`.
    2. `logcat -b crash -c`, then `am start -W` and needs `Status: ok` with no `Error`.
    3. Waits the 3 s settle, then checks `pidof` and that the activity is resumed.
    4. Takes an `exec-out screencap -p` PNG.
    5. Reads `logcat -b crash -d`. The pilot fails if its package is named there.
    6. `am force-stop`, then `uninstall` unless `--keep`.
  - Exit codes (`main`, :197-225):
    - 0: every pilot passed, and at least one ran.
    - 1: a pilot failed, or no pilot was ready.
    - 2: `Refused` (bad input, or adb `get-state` is not `device`), or an argparse error such as OUT already existing.
    - A refusal that comes after OUT is created (the adb state check) leaves OUT without `evidence.json`.

## 3. Input artifact contract

**Folder** (the `starter_pilots --out DIR` output, which is the same as the artifact contents):

```
apks/<template>-<theme>.apk     signed APKs
pilots.json                     {schemaVersion, label, pilots:[...]}
SHA256SUMS                      "<64 hex>  <path>", one line per APK plus pilots.json (starter_pilots.py:159)
```

**What `device_check.load()` enforces** (:59-94). Any failure refuses the whole folder with exit 2.

- **`SHA256SUMS`:**
  - each line is 64 lowercase hex characters, two spaces, then a non-empty path;
  - no path appears twice;
  - `sha256(pilots.json)` equals its entry.
- **`pilots.json`:** `schemaVersion == 1` and `label == LABEL` exactly.
- **Every record:** `label == LABEL`, otherwise the folder is refused. Records with `state != "ready"` are skipped.
- **Ready records:**
  - `apk` matches `apks/[a-z0-9-]+\.apk`;
  - `packageName` is a dotted package;
  - `versionCode` is an int;
  - `launchableActivity` matches the activity pattern;
  - `apkSha256` equals both the SHA256SUMS entry and the file's own sha256.
- **Carried but not enforced by `load`:** `template`, `theme`, `signerSha256`, `minSdk`, `targetSdk`. The starters declare `minSdk = "23"` and `targetSdk = "37"` (`workspace/rose-starters/gradle/libs.versions.toml:16-17`), so both API 34 and API 37 can install them.

**Output** (`--evidence OUT`, which must not exist):
- `OUT/evidence.json`: `{schemaVersion:1, label, device:{sdk, release, abi, fingerprint}, pilots:[{template, theme, label, packageName, versionCode, apkSha256, signerSha256, launchableActivity, passed, steps:{install, launch, running, resumed, screenshot, crash}}], passed}`.
- One `OUT/<template>-<theme>.png` for each pilot that reached the screenshot step.

**Labels:**
- **Main has exactly one label.** `compiled starter tree, scripted writer (not writer-generated)` means Rose's starter tree, built and signed by the real builder job, with a scripted writer that changed nothing. **It is not an app Rose's writer generated.**
- **Writer-handoff labels.** `rose.app_builder.writer_handoff` (this PR) adds three, and `device_check` accepts them; every other label is refused:
  - `TEST_LABEL`: an imported tree from a test writer;
  - `TRANSPLANT_LABEL`: one run's template code under another theme;
  - `WRITER_LABEL`: writer-generated source with an attested digest, built on Linux.

  A manifest whose records carry more than one label is labelled `mixed`. Apps mode for compiled starter pilots needs only `LABEL`.
- Your report should map labels through an exact-string table, so an unknown label shows as `invalid`.

## 4. Image pins (measured 2026-11-19; re-verified)

**Sources:**
- `https://dl.google.com/android/repository/sys-img/android/sys-img2-3.xml`: sha256 `7b119fdc282a65432aab52e8bbc64546f7fb6ec74e79e235633c60d06564b111`. I re-fetched it in this pass and got the same digest, which also matches the Oct 31 basis in `tool-manifest.json`.
- `repository2-3.xml`: sha256 `8db7cc1bd65bf8c9dffc3678a70d6dc98b02781a24d1ef0d31ecf111a55cc2d5`. It differs from the basis, but its stable emulator is still 37.2.13 with the same sha1. That is what `setup_emulator.py` resolves today. The hosted path does not pin it; it is only the newest stable.

**Re-verification in this pass:**
- I parsed both entries again from the re-fetched manifest: path, revision, `android-sdk-license`, `channel-0`, size, sha1, archive name and the emulator `min-revision` all match the table below.
- I streamed the API 34 archive again with `curl -sSL … | tee >(wc -c) | sha256sum`. curl exited 0, and I got 720747116 bytes and sha256 `b57f6386…2b3`, the same as the first measurement. Nothing was stored.
- The API 37 sha256 is from the first measurement only; its size and sha1 match the manifest.

| API | SDK path | Rev | Archive (`sys-img/android/`) | Size | sha1 (= manifest) | sha256 (measured) | Emulator min |
|---|---|---|---|---|---|---|---|
| 34 | `system-images;android-34;default;x86_64` | 4, channel-0 | `x86_64-34_r04.zip` | 720747116 | 5f6a249f9bc3b1b4c459b13ce2eb646c9680bed1 | b57f6386af25b0fe5488e77196b43c03778c6760e6f4311611d090391b9cb2b3 | 29.1.11 |
| 37 | `system-images;android-37.0;default;x86_64` | 6, channel-0 | `x86_64-37.0_r06.zip` | 907680404 | 8842a57a6b146423debb46c1496a9c0986aa1b9e | a80ad9f0f8b487df146df1c1412ee4744ba95dde471b601843b8039616be28d2 | 37.2.11 |

- **Licence:** both use `android-sdk-license`, the same licence as every package already pinned, so there is **no new licence term** and no preview licence.
- **API 37 path:** it exists only as `android-37.0` (api-level 37.0, extension 22), never `android-37`. The repository already uses this spelling (`gradle_stack.py` `PLATFORM = 'android-37.0'`).
- **Flavour:** `default` x86_64, the same as the API 35 pin (`experiment.py:40`). `boot()` needs `ro.debuggable=1` (`experiment.py:449-452`). That is expected on `default` images but unverified for 37.0 r6.
- **Revisions are not pinned at install:** `sdkmanager --install` takes the newest revision, so the pin is enforced afterwards by a revision check (§5.3). sdkmanager itself verifies the sha1. The measured sha256 is a reference; an optional streamed pre-check can use it.

**Entries in the `sdk.packages` format** (`installDir` is a key `sdk_revisions.pinned()` already reads):

```json
{"path": "system-images;android-34;default;x86_64", "revision": "4", "channel": "stable",
 "archive": "x86_64-34_r04.zip", "size": 720747116,
 "sha1": "5f6a249f9bc3b1b4c459b13ce2eb646c9680bed1",
 "sha256": "b57f6386af25b0fe5488e77196b43c03778c6760e6f4311611d090391b9cb2b3",
 "license": "android-sdk-license",
 "note": "sys-img/android/sys-img2-3.xml (sha256 7b119fdc...b111), measured 2026-11-19; api-level 34; needs emulator >= 29.1.11"},
{"path": "system-images;android-37.0;default;x86_64", "revision": "6", "channel": "stable",
 "archive": "x86_64-37.0_r06.zip", "size": 907680404,
 "sha1": "8842a57a6b146423debb46c1496a9c0986aa1b9e",
 "sha256": "a80ad9f0f8b487df146df1c1412ee4744ba95dde471b601843b8039616be28d2",
 "license": "android-sdk-license",
 "installDir": "system-images/android-37.0/default/x86_64",
 "note": "sys-img/android/sys-img2-3.xml (sha256 7b119fdc...b111), measured 2026-11-19; api-level 37.0, extension 22; needs emulator >= 37.2.11 (stable 37.2.13 ok)"}
```

**Where the entries go:** **not** into `tool-manifest.json` `sdk.packages`. `sdk_revisions.py` treats that list as the exact installed set on the isolated trial: a pinned package that is not installed fails ("is not installed exactly once", :71), and so does an installed package that is not pinned (:79). There are two options, and either way only the hosted path reads the entries:
- **(a) Recommended, in your own directory:** a new `os/tests/persistent-app-experiment/app-images.json`, `{"schemaVersion":1, "basis":{the two manifest digests}, "images":[the two entries]}`.
- **(b) QA-owned:** a separate top-level section of `tool-manifest.json`, such as `"hostedAppImages"`.

## 5. Source changes needed

The design adds an opt-in **apps mode**, default off, to the existing hosted job. The KVM grant, trap and cleanup stay unchanged. The pilots are built **in the same run** through a reusable call of `starter-pilots.yml`, so the experiment job downloads an artifact from its own run. A same-run download uses the runner's artifact token, which needs no `actions: read` and no `github-token` or `run-id` input.

### 5.1 `.github/workflows/starter-pilots.yml` (App Core, ours)

Replace the trigger block at :11-17 with:

```yaml
on:
  workflow_dispatch:
    inputs:
      pilots: {description: 'Space-separated TEMPLATE:THEME pairs, or "all"', required: false, default: all}
  workflow_call:
    inputs:
      pilots: {type: string, required: false, default: all}
```

- When called, the workflow's `github` context is the caller's, so the artifact becomes `starter-pilots-<experiment run id>`.
- The caller's `contents: read` caps the called workflow's token, and starter-pilots asks for nothing more.
- We will also write `sha256sum SHA256SUMS` to `$GITHUB_STEP_SUMMARY` after :64.
- **Unverified:** how the called workflow's own top-level `concurrency:` (:22-24, group `starter-pilots-<ref>`) behaves under `workflow_call`. It is a different group from the caller's `systemui-persistent-experiment`, so it should not deadlock. A standalone starter-pilots run on main at the same moment could queue it, though. If that happens, we move it to job level.

### 5.2 `.github/workflows/systemui-persistent-experiment.yml` (yours)

- **Inputs (:21-41):** add the two inputs below. Their descriptions must not contain `self-hosted` (`test_trial_preflight.py:142` counts exactly 3).
  ```yaml
      apps:
        description: 'Install and launch Rose starter pilots (built in this run) with device_check instead of the fixture stages'
        type: choice
        options: [none, starter-pilots]
        default: none
      apps_api:
        description: 'Emulator API level for apps mode (pinned default x86_64 image)'
        type: choice
        options: ['34', '37']
        default: '34'
  ```
  - One API per dispatch keeps the existing single-emulator job and the single evidence artifact name.
  - A matrix is possible, but it needs per-leg artifact names. Your call.
- **New job before `experiment` (:50).** Do **not** add `secrets: inherit`: no secret is needed, and `secrets.` is a banned string.
  ```yaml
    pilots:
      if: inputs.apps == 'starter-pilots' && inputs.runner == 'hosted'
      uses: ./.github/workflows/starter-pilots.yml
      with:
        pilots: all
  ```
- **`experiment` job (:51-53):**
  - Add `needs: pilots` and `if: ${{ !cancelled() && (inputs.apps == 'none' || needs.pilots.result == 'success') }}`.
  - With `apps=none` the pilots job is skipped and the experiment still runs, including on the trial runner. A failed or partial pilots build skips the experiment, so no KVM grant happens.
  - Leave `runs-on` (`test_trial_preflight.py` asserts its exact text) and `timeout-minutes: 30` unchanged. The apps-mode wall time is unmeasured; see §8.
- **New steps after checkout (:66-68), apps mode only:**
  ```yaml
      - name: Starter pilots from this run (apps mode)
        if: inputs.apps == 'starter-pilots'
        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0
        with:
          name: starter-pilots-${{ github.run_id }}
          path: ${{ runner.temp }}/pilots-in
      - name: Starter pilots integrity (apps mode)
        if: inputs.apps == 'starter-pilots'
        run: cd "$RUNNER_TEMP/pilots-in" && [ -z "$(find . -type l)" ] && sha256sum -c --strict SHA256SUMS
  ```
  - That SHA is the pin already used in this repository (`yogi-ota.yml:240`, `os-sign.yml:88` and others).
  - `device_check` checks the digests and labels again.
- **Run step (:91-110):**
  - Add `APPS: ${{ inputs.apps }}`, `APPS_API: ${{ inputs.apps_api }}` and `RUNNER_KIND: ${{ inputs.runner }}` to `env:`. Never inline inputs into `run:`; the test at `test_report.py:203` already enforces this for `systemui`.
  - Insert this after the startup-diagnostic block (:100-104) and **before** the `kvm_acl` block (:105):
    ```bash
    if [ "$APPS" = starter-pilots ]; then
      if [ "$SYSTEMUI" = true ] || [ "$STARTUP_DIAGNOSTIC" = true ] || [ "$RUNNER_KIND" != hosted ]; then
        echo "::error title=apps mode::not with systemui, startup_diagnostic or the trial runner"; exit 1; fi
      case "$APPS_API" in 34|37) ;; *) echo "::error title=apps mode::bad api"; exit 1;; esac
      flag=(--apps "$RUNNER_TEMP/pilots-in" --api "$APPS_API")
    fi
    ```
  - Leave the trap and grant (:105-109) and the run line (:110) unchanged. The run line already passes `"${flag[@]}"`.
- **Upload (:111-120):** no new path is needed. Apps evidence goes under `pfx-evidence/`, which is already allowlisted. `pilots-in/` is outside it, and no `.apk` is ever written under it.
- **Summary (:135-143):** add `APPS` and `APPS_API` to `env:`. Cleanup (:121-134) needs no change.

### 5.3 `os/tests/persistent-app-experiment/experiment.py`

- **Constants (:40):** keep `IMAGE`, and add:
  - `ROOT = HERE.parents[2]`
  - `IMAGES = {34: ('system-images;android-34;default;x86_64', '4'), 37: ('system-images;android-37.0;default;x86_64', '6')}` (or load these from `app-images.json`)
  - `EMULATOR_MIN = {34: (29, 1, 11), 37: (37, 2, 11)}`
- **`run()` (:370):**
  - New signature: `run(sdk, work, evidence, systemui=False, startup_diagnostic=False, apps=None, api=None)`.
  - Before anything is written (as :371-372 does), raise `Unavailable` when `apps` comes with `systemui` or `startup_diagnostic`, when only one of `apps`/`api` is given, or when `api` is not in `IMAGES`.
  - In apps mode, set `results['mode'] = 'apps'`, `results['api'] = api`, and scope `'Rose starter pilots on an AOSP API <n> x86_64 emulator; per-pilot labels; not a phone'`.
- **`environment()`:** unchanged. It still reads `work/'apks.json'`, so the fixture build step stays.
- **`image()` (:405-416):**
  - `pkg = IMAGES[api][0] if apps else IMAGE`.
  - Replace the hard-coded root at :415 with `Path(sdk)/'system-images'/Path(*pkg.split(';')[1:])`.
  - In apps mode, parse the revision column of the `--list_installed` row (:413-414). If it is not `IMAGES[api][1]`, raise `Failed(f'drift: installed r{x}, pinned r{y}')`.
  - Parse the `emulator -version` line already captured in `environment()` (:398) and refuse below `EMULATOR_MIN[api]`.
  - The existing install timeout is `timeout=600`, against archives of 0.72 and 0.91 GB.
- **`boot()` (:418-453):**
  - Pass `-k pkg` at :424.
  - In apps mode, leave `-writable-system` out of the launch argv at :441-443; nothing is seeded into /system.
  - After the facts at :449-450, which already include `sdk`, refuse with `Failed` if `facts['sdk'] != str(api)`.
- **New stage `apps()`:**
  - Copy only `pilots.json` and `SHA256SUMS` into `evidence/'apps-input'`.
  - Then run:
    ```python
    subprocess.run([sys.executable, '-m', 'rose.app_builder.device_check', '--pilots', str(apps), '--serial', SERIAL,
                    '--evidence', str(evidence/'device-check'), '--adb', device.adb],
                   cwd=ROOT/'prototypes/voice-phone-demo', stdin=subprocess.DEVNULL, capture_output=True, text=True,
                   timeout=max(60, budget(20) - time.time()))
    ```
    (`device.adb` is set at :299; `budget` caps at run start + 24 min, :379.)
  - Map the result:
    - exit 0, `evidence.json` `passed: true`, and every pilot label equal to `LABEL`: **pass**;
    - exit 1: `Failed`, naming each failing pilot and its first failing step;
    - exit 2: `Failed('refused: ' + the bounded first "device_check: refused:" line)`;
    - a missing `evidence.json`: `Failed`.
  - Stage facts: `{api, pilots:[{pilot, label, passed, failedStep}]}`, never APK paths.
- **Stage loop (:536-542):**
  - In apps mode, record `seed`, `ordinary`, `staged` and `restore` as `{'result':'not-run','reason':'excluded: apps mode'}`.
  - Then run `stage('apps', apps_stage, ['environment','image','device'])`.
  - The `finally` block at :551-581 already kills the emulator and removes the key, AVD and emulator home.
- **`main()` (:584-606):** add `--apps DIR` and `--api {34,37}` (`type=int`). Call `p.error` on the same combinations, mirroring :594-595, and forward both values at :602.
  - `main()` returns 0 whatever the stage results are. A pass or fail comes only from `results.json` and `report.py`, never from the run step's colour.

### 5.4 `os/tests/persistent-app-experiment/report.py`

- **`verdict` (:62-82):** when `results.mode == 'apps'`, the required stages are `('environment','image','device','apps')` instead of `FIXTURE` (:36). A green step is never a pass.
- **Rows:** next to the startup-diagnostic branch (:129), add one row per pilot, built only from fixed values:
  - pilot name matching `^[a-z0-9-]{1,80}$`;
  - passed: yes or no;
  - failed step from `{install, launch, running, resumed, screenshot, crash}`;
  - label through an exact table `{'compiled starter tree, scripted writer (not writer-generated)': 'starter (scripted writer)'}`, with anything else shown as `invalid`.
  - Hard-code the label string in `report.py`. Its code is stdlib-only, and tests ban `subprocess`, `secrets` and similar strings in it. The test then checks that the string equals `starter_pilots.LABEL`.
- **Header and tail:** "API <n> AOSP emulator; compiled starter trees with a scripted writer; not writer-generated; not phone acceptance".

### 5.5 Tests to update or add

- **`test_experiment.py:74-85`:** update the exact `on:` block string to include `apps` and `apps_api`.
- **`test_experiment.py:86-100`:**
  - Keep `permissions:\n  contents: read`, `timeout-minutes: 30`, the checkout and upload pins, the banned strings (`secrets.`, `environment:`, `emulator-runner`, `reactivecircus`, and the rest) and `.apk\n` out of the upload (:100).
  - Add assertions for the download-artifact SHA, `name: starter-pilots-${{ github.run_id }}`, `uses: ./.github/workflows/starter-pilots.yml`, no `actions:` permission, and the apps refusal placed before `kvm_access.py grant`.
- **`test_trial_preflight.py:136-161`:** `self-hosted` must still count 3, and the exact `runner` input and `runs-on` strings stay as they are.
- **`test_report.py:196-226`:** that `WorkflowTest` also asserts `timeout-minutes: 30`, `contents: read` and the summary-step env lines. Keep them true, or update them deliberately.
- **New `AppsModeTest`,** using the `fake_sdk` / `run_fake` pattern (`test_experiment.py:470-526`):
  - only environment, image, device and apps run;
  - the launch argv has no `-writable-system`;
  - `-k` names the API 34 or `android-37.0` path;
  - revision drift fails the image stage;
  - an emulator below the minimum refuses;
  - an `ro.build.version.sdk` mismatch fails;
  - device_check exits 0, 1 and 2 map to pass, fail and "refused";
  - a foreign label fails;
  - `apps-input/` holds exactly the two files;
  - no `.apk` lands in the evidence;
  - the private paths are removed.
- **Refusal matrix** for `ex.run` and `ex.main`: `--apps` with `--systemui`, `--apps` with `--startup-diagnostic`, `--apps` without `--api`, `--api` without `--apps`, and an unknown api.
- **`test_report.py`,** following `VerdictTest` (:125-158) and the invalid-value pattern (:102-113):
  - the apps verdict ignores the fixture stages;
  - values outside the fixed sets show as `invalid`;
  - the label table key equals `rose.app_builder.starter_pilots.LABEL`, imported via `prototypes/voice-phone-demo`.
- **Static-checks step (:74-80):** add `(cd prototypes/voice-phone-demo && python3 -m unittest rose.tests.test_app_device_check)`.
- **Optional:** `README.md:37` still says the experiment does not change `/dev/kvm` access. That is true of `experiment.py` itself, but the workflow can now grant it. Consider clarifying.

### 5.6 Pin file

Add the two §4 entries to `os/tests/persistent-app-experiment/app-images.json` (yours) or to a separate section of `tool-manifest.json` (QA's). Never to `sdk.packages`.

## 6. Commands (after §5 merges)

The owner dispatches once per API level:

```
workflow: systemui-persistent-experiment.yml   ref: main
  systemui=false  startup_diagnostic=false  runner=hosted
  kvm_acl=true    apps=starter-pilots       apps_api=34
then the same with apps_api=37
```

Command line, for the owner only:

```
gh workflow run systemui-persistent-experiment.yml --ref main -f systemui=false -f startup_diagnostic=false -f runner=hosted -f kvm_acl=true -f apps=starter-pilots -f apps_api=34
```

Each run, in order:

1. **Job `pilots`:** the called `starter-pilots.yml`, on its own disposable ubuntu-24.04 VM.
   - Checkout, then `scripts/setup-android-sdk.sh` and `scripts/setup-android-gradle.sh`.
   - `sudo apt-get install bubblewrap` (only if missing) and `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`.
   - `python3 -m rose.app_builder.starter_pilots --out $RUNNER_TEMP/starter-pilots`, then `sha256sum -c SHA256SUMS`.
   - Upload `starter-pilots-<run_id>`.
2. **Job `experiment`:** a separate disposable ubuntu-24.04 VM.
   1. Checkout (`persist-credentials: false`).
   2. Download into `$RUNNER_TEMP/pilots-in`, then the no-symlink check and `sha256sum -c --strict SHA256SUMS`.
   3. The five existing unit-test files, plus `test_app_device_check`.
   4. `experiment.py build --sdk "$ANDROID_HOME" --work "$RUNNER_TEMP/pfx"`, the fixture build, unchanged.
   5. `setup_emulator.py …`, unchanged.
   6. The run step:
      - the apps refusals;
      - the trap, then `kvm_access.py grant --state "$RUNNER_TEMP/kvm-state" --evidence "$RUNNER_TEMP/pfx-evidence"`;
      - `experiment.py run --sdk "$ANDROID_HOME" --work "$RUNNER_TEMP/pfx" --evidence "$RUNNER_TEMP/pfx-evidence" --apps "$RUNNER_TEMP/pilots-in" --api 34`.
   7. Inside the run:
      - the environment stage;
      - `sdkmanager --install 'system-images;android-34;default;x86_64'` (or the `android-37.0` path), stdin closed, then the revision pin check;
      - `avdmanager create avd -n fixture -k <pkg> -d pixel -p …`;
      - `emulator -avd fixture -port 5580 -no-window -no-snapshot -no-boot-anim -no-audio -gpu swiftshader_indirect`;
      - the boot wait, the qemu and debuggable checks, and the sdk check;
      - `python3 -m rose.app_builder.device_check --pilots $RUNNER_TEMP/pilots-in --serial emulator-5580 --evidence $RUNNER_TEMP/pfx-evidence/device-check --adb $ANDROID_HOME/platform-tools/adb`, from `prototypes/voice-phone-demo`;
      - `emu kill`, and removal of the AVD, key and emulator home.
   8. The trap runs `kvm_access.py restore`.
   9. Upload `persistent-experiment-<run_id>`: `pfx-evidence/` and `pfx/apks.json`, kept 7 days.
   10. Cleanup: `emu kill`, `kvm_access.py restore` again (idempotent), and `rm` of the keystore, AVD and emulator home.
   11. `report.py` writes the step summary.

## 7. Exact approval delta: `kvm_acl=true` (EXP006-KVM-001, unchanged; checked against `kvm_access.py:159-253`)

**Grant**

- **Preconditions:**
  - Linux, with `GITHUB_ACTIONS=true` and `RUNNER_ENVIRONMENT=github-hosted`;
  - the job user (`os.getuid()`) is non-root;
  - `/dev/kvm` is a root-owned character device, not a link;
  - `getfacl`, `setfacl` and `sudo` already exist; nothing is installed.
- If the user already has rw, it records `not-needed`, writes `mutated: false` state and changes nothing.
- Otherwise:
  1. `sudo -n true`.
  2. `getfacl --numeric --omit-header --absolute-names -- /dev/kvm`, retried with `sudo -n` only if the first attempt fails.
  3. It refuses if the ACL already names this uid, if any entry kind is not user/group/other/mask, or if the mask (or, without one, the owning group's rights) lacks rw.
  4. It saves the device identity (dev, ino, rdev, uid, gid, mode) and the exact ACL to `STATE/kvm-acl-state.json` (directory 0700, file 0600).
  5. **The only change:** `sudo -n setfacl --no-mask --modify u:<uid>:rw -- /dev/kvm`.
  6. It verifies the result:
     - the device identity and mode are unchanged;
     - the only new entries are `user:<uid>:rw-`, plus a mask equal to the group's rights if there was none;
     - no original entry is gone;
     - no principal's effective rights changed;
     - the user has rw, and `KVM_GET_API_VERSION` returns 12.

     Any mismatch restores at once and refuses.

**Restore**

- It runs from the run-step trap (`EXIT INT TERM`) and again from the always-run cleanup step.
- It is a no-op when nothing was recorded or nothing was changed, and it never touches a replaced device.
- Only if the current ACL differs from the saved one, it writes `STATE/kvm-acl-restore.txt`, runs `sudo -n setfacl --set-file=STATE/kvm-acl-restore.txt -- /dev/kvm`, and deletes the snapshot.
- It then requires the exact ACL and identity (owner, group, mode) again.

**Scope**

- This job's own non-root user, on the hosted experiment VM, for the run step.
- No chmod, chown, group, udev rule, package or persistent setting.
- Evidence: `pfx-evidence/kvm-access.json`.
- Residual risk: a hard cancellation or VM loss can prevent the restore; then only GitHub's disposable VM bounds the grant (:23-24).
- The isolated-trial guard refuses `kvm_acl=true` (workflow :65), and apps mode refuses the trial runner.

**Which runs:** two dispatches on `main` after §5 merges (`apps_api=34`, then `apps_api=37`). Each grants once, on its own disposable VM.

**Precedent** (from the earlier read, not re-fetched here): run 41161860755 (systemui=true, kvm_acl=true). The grant and both restores were ok, emulator 37.2.13 with the API 35 image booted, and the run step took about 75 s.

**Nothing else needs approval**

- No new runner or runner reuse, VM, secret, token scope (still `contents: read`, no `actions: read`), licence term or phone.
- The approver should still know what else the same dispatch runs: the called `pilots` job runs `starter-pilots.yml`'s existing, merged steps (`sudo sysctl` at :48 and the conditional bubblewrap install at :45). They run on that job's own disposable VM, exactly as in a standalone starter-pilots dispatch, never on the KVM job.
- The rejected alternative, downloading from a separate starter-pilots run with `run-id` and `github-token`, would **add** `permissions: actions: read`, which is a second delta.

## 8. Missing source work vs missing permission

**Missing source work**

1. *OS Product:* apps mode in `experiment.py` (§5.3). Today it hard-codes the API 35 image (:40, :415, :424) and never calls `device_check`.
2. *OS Product:* the workflow inputs, the `pilots` call job, the same-run download and integrity steps, and the apps refusals before the grant (§5.2).
3. *OS Product:* the apps verdict and per-pilot rows in `report.py` (§5.4).
4. *OS Product:* the tests in §5.5.
5. *OS Product, or QA for option (b):* the image pins, kept outside `sdk.packages` (§4, §5.6).
6. *App Core:* `workflow_call` on `starter-pilots.yml`, plus the SHA256SUMS digest in its step summary (§5.1).
7. *App Core, in progress:* writer-generated pilots come from `writer_handoff build` on a Linux build host, not from a workflow artifact. Their route to a device is a separate step, after a stage-1 writer run exists. None of this blocks the compiled-starter runs.

**Missing permission**

1. The owner's approval of the existing `kvm_acl=true` delta (§7), for the two dispatches. **That is the only one.**

**Unmeasured or unverified, recorded as such**

- **`android-37.0` on the hosted tools:** whether the runner's own, unpinned `cmdline-tools;latest` sdkmanager and avdmanager accept the path. This sandbox has no SDK to test with. If they refuse, the image stage records `unavailable`. Recording `sdkmanager --version` in the environment facts makes this visible, and pinning cmdline-tools 22.0 on the hosted path removes the risk.
- **The API 37.0 image's properties:** its `ro.build.version.sdk` (expected "37") and `ro.debuggable` (expected "1").
- **Apps-mode wall time:** the image download (0.72 or 0.91 GB), a cold boot on swiftshader, and 9 pilots × (install, launch, 3 s settle, screenshot), plus the `pilots` job (up to 60 min) before the experiment job starts. The experiment job is expected to fit its 24-minute internal budget (:379) and 30-minute timeout, but this has not been measured. If the first run times out, raise both, or call the pilots job with a subset.
- **Concurrency:** how the called `starter-pilots.yml` concurrency group behaves under `workflow_call` (§5.1).

## 9. What the evidence will and will not prove

**Will prove,** per pilot and per API level, on an AOSP `default` x86_64 emulator (API 34 r4 or API 37.0 r6, revision checked against the pin):
- this exact signed APK installed with its own versionCode (sha256 bound through `SHA256SUMS` and the copied `apps-input/`);
- its launchable activity started with Status ok;
- its process ran and the activity was resumed;
- a screenshot was taken;
- no crash naming its package was logged;
- the KVM grant was restored, per `kvm-access.json`.

**Will not prove:**
- that Rose's writer can generate these apps. They are compiled starter trees with a scripted writer, and every record says so;
- equivalent behaviour across themes (#901's JVM evidence, a separate question);
- acceptance on a physical phone, or anything on a Pixel;
- anything about RoseOS's own OS image, SystemUI or update path;
- performance;
- behaviour on any API level other than the one dispatched.

A failed or refused stage proves only that the stage did not pass. A green job step is never a pass by itself: `experiment.py run` exits 0 whatever its stages show, and the verdict comes from `report.py`.
