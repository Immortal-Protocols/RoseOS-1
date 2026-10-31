# EXP006 isolated SystemUI trial: QA pins, licence basis and offline setup plan

Infra verifier, 2026-10-31. These files complete PR273's open setup items
(`os/tests/persistent-app-experiment/isolated-trial-setup.md`) without
editing them. **Nothing here is real setup proof**: no VM, runner group,
token, key, firewall or trial was created or run.

| File | What it is |
| --- | --- |
| `tool-manifest.json` | Official pins with sha1 (Google manifest) and sha256 (measured) |
| `trial_plan.py` | T0 plan as data: deadline file, teardown timer/script, outbound bound, cleanup; `validate`; `scan-leak` |
| `test_trial_plan.py` | 20 offline tests (run as root and as non-root here) |

## Pins (official provenance)

- **Source:** Google's `repository2-3.xml` and `sys-img2-3.xml`, read 14:01
  UTC. Each archive was downloaded once and matched the manifest sha1.
- **Pins:** cmdline-tools **22.0**, platform-tools 37.0.1, emulator 37.2.13
  (stable), `system-images;android-35;default;x86_64` r02, plus the repo's
  existing platform 35 r02 and build-tools 35.0.0.
- **Not `cmdline-tools;latest` (23.0):** its `sdkmanager` is a deprecated shim
  that downloads an unpinned `android-cli` from
  `dl.google.com/android/cli/latest` into `~/.android/bin` at run time, as
  observed. 22.0 is the newest stable revision with the Java `sdkmanager`.
  Unpack its `cmdline-tools/` as `$ANDROID_HOME/cmdline-tools/latest`, which
  is the path the experiment calls.
- **Revision drift:** `sdkmanager --install` takes the newest stable
  revision at install time. Setup checks `sdkmanager --list_installed`
  against these revisions. On any drift it stops and the manifest is updated,
  never a silent pass.
- **Runner:** `actions/runner` **2.338.2**, tag commit `d1eb12d4…`, from
  anonymous `git ls-remote`. **sha256 missing:** the release page returned
  HTTP 403 to this session's proxy and was not retried. The Mac operator
  records it from the official release page before T0.

## Licence basis

- **One licence:** every pinned package uses only `android-sdk-license`, the
  "Android SDK License Agreement" dated January 16, 2019 (text sha1 as served
  `efa68a6b…`). That is the same licence as the platform and build-tools
  archives the repository already downloads.
- **No new term**, as long as the emulator stays on the stable channel. The
  dev-channel emulator 37.3.6 would add `android-sdk-preview-license`.
- **The `licenses/` file:** `sdkmanager` writes it on the VM, and its value is
  recorded then. It is never copied or invented. If an existing accepted SDK on
  the same Mac holds `android-sdk-license`, reuse is valid only if the VM's
  `sdkmanager` computes the same file value.

## Non-root compatibility (local container, not the VM)

Real `sdkmanager` 22.0 was run as a non-root user against a root-owned
`0755` SDK built from the verified archives:

- **Reads work:** `--version` and `--list_installed` succeed.
- **No-op install:** `--install system-images;android-35;default;x86_64` with
  stdin closed exits **0**, and no licence is needed for this no-op.
  - It tries to create `$SDK/.knownPackages`, plus `package.xml` for
    packages unpacked without one. Both fail with EACCES and are tolerated.
  - So a root-owned SDK is compatible with the experiment's image stage.
- **Direct unzip isn't recognized:** packages unpacked straight from an archive
  (the emulator here) are not seen as installed.
  - The experiment's `--install emulator` then reaches the licence prompt.
  - It prints "Skipping … license is not accepted" and still **exits 0**.
  - Setup must therefore install every package with this `sdkmanager` as root.

## Offline validation of the setup plan

`trial_plan.py plan --t0 T0` emits the plan, and `validate` refuses any
broken invariant:

- **Timeline:** SSH key removed by T+55m, expires at T+60m, last job start
  T+75m, stop accepting and teardown at T+110m, GCE STOP at T+120m
  (`maxRunDuration` 7200 s, STOP, no restart).
- **Deadline file:** `/etc/roseos-systemui-test/deadline` = T+110m, written
  once by root at `0444`. The merged `trial_preflight.deadline_facts`
  (unchanged) accepts jobs from setup until T+75m. It refuses one second
  later, and refuses a deadline written beyond the trial or left writable.
- **Teardown:** a root oneshot timer, `Persistent=false`, installed before
  the runner accepts work.
  - It kills `rose-test` processes and removes the runner credential files,
    `_work`, `~/.android` and rose-test's temp files.
  - It writes a receipt with `"runnerUnregistered": "not-by-timer"` and
    disables itself.
  - It never handles a token. The browser operator unregisters and verifies.
  - Tested against a fake root: it removes all of these, and reports
    `incomplete` if processes survive.
- **Outbound:** an nftables output policy:
  - default drop, IPv6 dropped;
  - only the metadata server, TCP 443 and established replies allowed;
  - 443 bounded by a named quota (default 1 GiB, which stays within the
    USD 1 reserve at USD 0.12/GiB).
  - `nft -c` accepts it.
  - It bounds host egress only. The GCE firewall still owns ingress.
- **Cleanup:** every created item has an owner (vm-timer, job cleanup, Mac
  operator, browser operator, GCE) and a verification step.

## Same-Mac masked-token harmless-input check (prepared, NOT executed)

Run this before any real token exists, on the SAME Mac's authorized browser
and its host-verified IAP SSH TTY. A cloud browser clipboard is not the Mac
clipboard.

1. On the Mac, generate a harmless sentinel locally, at least 12 characters,
   for example `HARMLESS-NOT-A-TOKEN-` plus 8 random characters. Do not
   generate a registration token.
2. As `rose-test`, in the verified runner directory, run
   `./config.sh --url https://github.com/Immortal-Protocols/RoseOS-1 --runnergroup roseos-systemui-test --labels roseos-systemui-test --name roseos-systemui-test-01`
   with no `--token`, so it prompts. Type the sentinel at the prompt. Expect
   no echo or a masked echo, then an authentication failure; nothing
   registers.
3. Run `history -w`, then
   `read -rs S; printf '%s\n' "$S" | python3 trial_plan.py scan-leak ~/actions-runner ~/.bash_history`.
   This takes the sentinel from stdin, never argv. Expected:
   `{"result": "absent", …}`.
4. Record the echo observation and the scan's fixed-word result, then remove
   `_diag`. If the sentinel echoed or the scan says `present`, stop: no real
   token is issued.

## Proposed hunks for OS Product (owner applies; not edited here)

In `isolated-trial-setup.md`:

- **Pins table:** replace the three "no reviewed pin" rows with
  `tool-manifest.json`. That means cmdline-tools 22.0 (not `latest`) unpacked
  as `cmdline-tools/latest`, platform-tools 37.0.1, emulator 37.2.13 stable,
  image r02, and runner 2.338.2 / `d1eb12d4` with its sha256 recorded before
  T0.
- **"SDK licences: decision needed":** replace with the licence basis above
  (one existing licence, no new term, stable emulator only).
- **Host layout:** add "install every package with that `sdkmanager` as root
  so each has `package.xml`. Direct unzip is not recognized, and
  `--install emulator` would then exit 0 without installing."
- **Optional wiring** (Release/OS owners): add
  `python3 qa/systemui-trial/test_trial_plan.py` to `scripts/test-local.sh`.
