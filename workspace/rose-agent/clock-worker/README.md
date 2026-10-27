# Clock fast-worker trial

The existing model resolves timer intent, duration and label, then calls:

    python3 rose-agent/phone/control.py clock_timer_start 300 --label Tea

No timer-specific phrase matching or extra decider model is added. Unsupported actions remain on the existing general agent path.

Execution sends one signature-protected local broadcast from Rose Agent to Pixel Clock. The Clock model creates and starts the timer and returns verified state through a private ResultReceiver. The command ID is stored before starting, so uncertain execution cannot silently replay. The model is instructed not to retry uncertain results.

Integration points: `server/phone.mjs`, `phone/control.py`, `app/src/dev/pixel/voice/VoiceService.java`, and `voice-providers/app-worker-policy.mjs`. The native implementation snapshot is in `src/`. These changes do not modify Bluetooth routing, microphone setup, provider connectors, or music ducking.

Validation: device instrumentation verifies timer creation, duplicate-operation reuse and invalid-duration rejection. Three normal task-agent requests (tea five minutes, coffee two minutes, pasta three minutes) each called the worker exactly once with the correct duration. Successful on-phone command times in that run ranged from 53 milliseconds to 1.55 seconds; these exclude speech recognition and agent reasoning, and are not a voice latency benchmark.

Rollback: `rollback/pixel-voice-before-clock.apk` is the installed pre-trial app; `rollback/phone.mjs` restores the previous server command registry. The provider policy is separately versioned in `../voice-providers`. Reinstall the saved APK, restore the registry/policy, and restart the Mac server. Stock Clock is untouched.

## Expanded Clock controls (2026-10-12)

The optional expansion adds `clock_control` to the same on-phone execution mechanism. It does not replace the timer-start worker or add another model. Simple explicit stopwatch/timer commands can dispatch from the existing voice command resolver; the existing task model resolves richer requests, dates, target IDs and ambiguous language using the shared worker policy.

Examples:

```sh
python3 rose-agent/phone/control.py clock_control stopwatch_start
python3 rose-agent/phone/control.py clock_control stopwatch_lap
python3 rose-agent/phone/control.py clock_control stopwatch_pause
python3 rose-agent/phone/control.py clock_control timer_list
python3 rose-agent/phone/control.py clock_control timer_add --params '{"timerId":12,"seconds":60}'
python3 rose-agent/phone/control.py clock_control alarm_create --params '{"hour":7,"minute":30,"label":"Wake up","days":[1,2,3,4,5]}'
python3 rose-agent/phone/control.py clock_control world_time --params '{"city":"London"}'
```

Operations and argument validation are in `commands.mjs`. Timer controls include read, pause, resume, reset, cancel, extend and rename. Stopwatch includes read, start/resume, pause, lap and reset. Alarm includes list/read/create/edit/enable/disable/delete and snooze/dismiss of a ringing instance. World clocks include catalog search, current local time, list/add/remove saved cities. Alarm times are local phone time; weekdays use ISO Monday=1. Arbitrary dated alarms and new timezones aren't represented by this API; the agent must not silently reinterpret them.

`ClockCommandReceiver.kt` in the Pixel Clock repo uses the app's actual DataModel and alarm scheduler. Mutations persist an uncertain operation record before executing and reuse recorded results for duplicate IDs. Outcomes are verified by reading the model/provider. Timer extension preserves elapsed running time. Alarm updates preserve sound/vibration settings. Ambiguous targets return candidates without mutation. Snooze/dismiss select only ringing alarms.

The server validates parameters and requires the independent `clock-worker` flag. `GET/POST /clock-worker/config` reads/sets `{enabled: boolean}`. No settings UI is added. To roll back immediately:

```sh
bash rose-agent/clock-worker/rollback-controls.sh
```

This stops new expanded commands; already-dispatched commands may finish. Existing timers/alarms and the original timer-start, music and messages workers remain. `baseline.json` points to the private backup of both installed APKs and integration sources for a full binary rollback if needed. The new code is separately committed in the Clock, clock-worker and voice-providers repositories.

Validation: `node --test rose-agent/clock-worker/test.mjs` covers the flag, validation, routing isolation, and non-replay. `ClockControlsTest` runs against the real receiver: temporary timers, ambiguity, duplicate extension, running-time preservation, stopwatch start/lap/pause/reset when initially reset, alarm scheduling/edit/disable/duplicate prevention, modeled ringing-instance snooze/dismiss, and world-city add/time/remove. Test records are deleted and original counts checked. `test-live.mjs` uses isolated synthetic speech with the normal Live provider connector and actual phone worker, without saving synthetic speech into personal history. Spoken stopwatch start and pause each dispatched once to Clock without a general-agent call; this is not a latency benchmark for the entire voice conversation.

The final ringing-state tests exposed a pre-existing standalone-app crash in `AsyncRingtonePlayer`: telephony call state requires a permission the Clock app does not request. The Clock now falls back to system audio mode when that read is denied, without adding phone permissions or changing the voice service. Instrumentation exercises that denied-permission path as well as the Clock controls.

## Ringing timer stop correction

`timer_stop` handles expired timers explicitly: with no selector it resets all currently ringing timers, leaving unexpired countdowns alone. If none are ringing it pauses the unique running timer; multiple running timers need clarification. If none are running or ringing, `alreadyQuiet` is true. `timer_silence` only resets ringing timers. Neither operation deletes saved timers. Voice maps “stop the timer” to `timer_stop` and “stop the beeping” to `timer_silence`; the shared model policy explains that an expired timer may still be sounding.

Regression test: two silently expired test timers plus a separate running countdown. One `timer_stop` resets both expired timers and preserves the running countdown. The timer ringtone is restored after testing. Isolated synthetic “Stop the timer” speech dispatches once to the local Clock worker with no general-agent call.
