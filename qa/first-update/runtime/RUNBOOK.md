# First Update — runtime acceptance runbook (QA, prepared, not executed)

Prepared under Chief assignment CHIEF-STAGING-001-QA. Nothing here has been run against a VM,
device, provider or deployment. Each step runs only at the authorized checkpoint and by the role
that owns that access: Infra for VMs and the hub, Nathan for the phone. QA analyses the evidence
offline and stays independent.

Frozen offline baseline: App `2749a76358b1e97412bd47ae8419202513ac1e17` + OS
`9ce7e3d23d202b1b0944303c7e61da19497f5d49`, QA harness on `claude/focused-volta-e07218`.
Any change to those heads, or a packaging or version change, requires the offline harness
(`qa/first-update/run.sh`, including `startup/startup.test.mjs`) to be re-run first.

## Inputs QA needs (from Infra/OS/App, with identities)

| Item | Owner | Identity to record |
| --- | --- | --- |
| Candidate source revision(s), merged App+OS | Chief/App/OS | Exact commits; resolved OS manifest plus local-change inventory (lineage vs `CP3A.261005.005`) |
| Candidate OTA, target-files, channel metadata | Infra/OS | SHA-256 of each; build number greater than 2026102401; OTA cert DER `2ce41f9e…268a`; AVB blob `7c19d7fa…8822` |
| Bundled APKs: Rose Agent `dev.pixel.voice`, controller `dev.pixel.backgroundcontrol`, Calculator `dev.pixel.calculator.agent` (Chief PACKAGING-001: delivered in the OTA) | OS/App/Infra | Package, versionCode (greater than the installed version, including data-partition overrides), versionName, full signer SHA-256 (existing identities; no platform-signing swap), APK SHA-256 |
| Version table: installed vs candidate per package | OS/App with Infra | Source facts so far: fork export app 297 / controller 25; PR app 297 / controller 27. Installed values are unknown |
| Backend deployment for the run | Infra | Code revision deployed (must contain App PR #3 head), `app-tools-trial.json` with Calculator, endpoint and pin (`https://100.68.248.87:9444`, pin `f703219f…75ca` for the Pixel 10 service), and whether the live provider is used |
| VM access | Infra | Cuttlefish runner, ADB serial, audio capture method, and a guest image that carries the candidate (the retained historical guest is not the candidate) |

## Tools in this directory

- `device-facts.sh SERIAL OUT`: read-only props, package versions and signers (pulled APKs with
  `apksigner`), HOME holder, focus, displays, screenshot, Updater URL resource and SHA256SUMS.
- `logcat-capture.sh SERIAL FILE`: epoch-stamped logcat for `PixelOwnerStop`, `BackgroundControl`
  and `PixelVoice`.
- `stop-evidence.mjs LOGS…`: offline analyzer of backend `evaluation-recordings/**/*.jsonl` and
  `decisions-routing.jsonl`. It reports a violation for any phone action, tool, web lookup or
  worker message after `task_stop_acknowledged`, until the next `request.started`. It is
  self-tested in `stop-evidence.test.mjs`.
- `audio-window.py WAV START ACK`: 50 ms RMS segments relative to the Stop acknowledgment, with a
  threshold of 0.008 to match App’s OpenAI quiet threshold.

Time base: record the epoch offset between the backend host, the VM (`adb shell date +%s.%N`)
and the audio recorder at the start and end of every run.

## Scenarios

Each scenario captures `device-facts.sh` (before and after), `logcat-capture.sh`, backend logs
copied after the run, screen recording or screenshots, and audio capture where audio is
involved. Record the exact task request text and task IDs from the logs.

| ID | Scenario | Steps | PASS rule |
| --- | --- | --- | --- |
| RT-6 | Candidate backend starts with Calculator app tools | Start the deployed service; capture its start log and `/health` (authenticated by the paired app or operator) | `PIXEL_VOICE_READY`; `/health` `appTools` lists `calculator_*`; no startup errors. Offline equivalent: `startup/startup.test.mjs` |
| RT-7 | Voice → Calculator in a separate workspace | On the VM with the candidate image, open another app (for example Notes) and type in it. Ask Rose: “What is 84.50 plus a 15% tip?” (typed `/task` acceptable for the VM gate; voice for RT-1/2) | Focus stays on Notes (`focus.txt`); a Calculator session on a non-default display (`displays.txt`); the controller notification with Stop is visible; PiP viewer opens; the spoken or returned result equals Calculator’s displayed value and history entry (97.175 → as displayed) |
| RT-5 | App-server turn binding | Same run: inspect backend logs | Every tool call and message carries a `turnId` bound to the request’s task; no `worker.turn_end_rejected` for the active task; steering creates no new binding |
| RT-3 / RT-4 | Owner Stop via the notification | Start a longer Calculator task (several operations). Press Stop in the controller notification mid-task | Logcat: `OwnerStopReceiver` forwards (`stop forwarded status=200`); the controller reports `stopped`/`stopReason:user`; the backend shows `task_stop_acknowledged`; `stop-evidence.mjs` verdict is PASS; `user_stopped` refusal if the stale task restarts |
| RT-1 / RT-2 | Audio after Stop, for each provider (ElevenLabs and OpenAI) | Voice request whose result is long; press Stop while the result is being spoken, and also during work before speaking | `audio-window.py`: speech after ACK is only the Stop acknowledgment (check against the transcript); `lastSoundAfterAckSeconds` reported; no task-result words after ACK. Playback restores only after provider listening or quiet, never by timer |
| Fresh task | Recovery | After Stop, a new explicit request “What is 12 times 7?” | Completes normally; the old task’s late events are rejected (`*_rejected`) |
| RT-9 | App identity/version continuity | `device-facts.sh` on the device before the update (authorized checkpoint) and in the candidate VM | The candidate versionCode for each of the 3 packages is greater than installed; signer equals the installed signer (expected `650d47f9…0bfb` for the app set, to be verified); no data wipe |
| RT-10 | Built image configuration | `device-facts.sh` on the candidate VM, plus target-files inspection | `persist.wm.debug.pixel_agent_tray=1`; Updater `url` = `https://github.com/Immortal-Protocols/pixel-ota/releases/latest/download/`; fingerprint build number greater than 2026102401; independently verified OTA/AVB identities as above |
| RT-8 | Pixel 10 service runs the candidate | Infra deploy receipt at the authorized checkpoint | Deployed revision equals the candidate; app-tools config present; rollback recorded |
| RT-11 | Phone update and experience | Nathan, authorized checkpoint: updater discovers, downloads and installs; reboot; then RT-7, Stop and a fresh task by voice while using another app | Build equals the candidate; apps, data and pairing intact; the one-pager experience works; `device-facts.sh` before and after |

## Reporting

For every run, QA records the exact artifact hashes, revisions, time offsets, verdicts per row,
and anything not executed. A VM PASS never substitutes for RT-11. Buffered-audio acceptance needs
RT-1 evidence for both providers; otherwise it stays open (Chief STOP-001).
