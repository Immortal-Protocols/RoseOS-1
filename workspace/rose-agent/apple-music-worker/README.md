# Apple Music learned-route prototype

An isolated, manually invoked worker for one learned procedure: **search for an
exact song and artist, play that result, and verify the player**. It is not wired
into Rose Agent, has no decider, and never calls a language model. Learning was
performed through live UI inspection on Apple Music 6.5.3; this is not yet an
autonomous app explorer or a system that learns new procedures by itself.

```sh
python3 rose-agent/apple-music-worker/worker.py 'Dreams' 'Fleetwood Mac'
python3 rose-agent/apple-music-worker/worker.py 'Dreams' 'Fleetwood Mac' --pause-after
cd rose-agent/apple-music-worker && python3 -m unittest -v
```

Uses the existing authenticated wireless phone bridge. No voice-service restart,
Bluetooth changes, app installation, or new permissions are required. Requires
an unlocked paired phone, Apple Music signed in, and a working Mac connection.
It temporarily takes over the app screen. Do not run it concurrently with another
phone-control task. It changes the current track and search text.

The map is in `map.json`. The worker:

1. Opens the exact installed Apple Music package.
2. Dismisses the full player if necessary and finds the known Search interface.
3. Focuses the search field, enters the supplied title and artist, submits through
   its exposed keyboard action, and inspects fresh results.
4. Matches title and artist on the **same** clickable song row. Exact titles take
   priority; a standard remaster suffix is allowed when uniquely matched. Live
   versions, videos, covers by other artists, and ambiguous duplicates stop the run.
5. Taps that result once and checks the matching player title and Pause control.
   Artist attribution comes from the uniquely selected result; the compact
   player itself does not expose the artist. No full-lyrics navigation is needed.
6. Optionally returns to the compact player and pauses for a test. The compact
   player avoids the continuously changing elapsed-time text in the full player.

All target IDs come from the immediately preceding live observation. No saved
coordinates, generated navigation decisions, search APIs, or Apple credentials.
The existing device freshness check is unchanged. One explicitly rejected stale
**text entry** can be retried only after reading and re-identifying the search
field. A possibly completed song tap is never repeated to verify it. Unexpected
screens, app switches, protected fields, ambiguous songs, stale taps and timeouts
return `needs_assistance`. This prototype does not automatically call Luna; its
caller can decide how to recover. It does not subscribe, purchase, or download.

Each run saves a private JSON result in
`~/.config/pixel-voice/apple-music-worker/` with elapsed time, actions and outcomes.
`elapsedToVerifiedPlayingMs` is worker invocation to confirmed player state,
including phone round trips; it does not measure speech recognition, any future
router, or acoustic playback at the listener. `modelCalls: 0` refers to this
worker, not a future outer agent. Partial success during cleanup is explicit.

Disable/rollback: stop invoking this directory. No existing voice routing or
phone-control implementation was modified. Source is versioned in this local
Git repository; private run reports are outside it. A broader autonomous mapping
service and dispatcher remain out of scope until this one route proves useful.

## Initial live validation

On this paired Pixel, using cellular transport and existing Apple Music sign-in:

- Dreams — Fleetwood Mac: 11,639 ms to verified player state, then paused.
- Here Comes the Sun — The Beatles: 10,156 ms, launched from the phone home
  screen, then paused.
- Both runs used zero model calls. Eight unit tests pass. These are two controlled
  runs, not an established success rate or a matched comparison against Luna.
- Earlier trials stopped on stale snapshots and a full-lyrics screen. The final
  procedure accounts for hidden player controls and focuses/submits search.

The normal voice agent has not been changed to invoke this worker automatically.

## On-device experiment

```sh
python3 rose-agent/apple-music-worker/local.py 'Dreams' 'Fleetwood Mac' --pause-after
```

The authenticated `apple_music_run` command sends title, artist and the optional
pause-after-test flag once. The installed app performs the same narrow procedure
locally with fresh observations and the existing action validation. It checks the
package, screen completeness, title/artist row, and playback state at each relevant
step. A short handler callback advances one step, then yields while Apple Music
updates; there is no blocking sleep or long-running loop on the UI thread.

Only one local routine may run at once. Competing automation mutations receive
`routine_busy`; read-only diagnostics remain available. Unexpected app changes,
lock, missing permission, ambiguity, or a 20-second deadline stop the routine.
The command result is cached by ID before execution starts, so repeated delivery
never repeats the routine. Restarting the service interrupts an unfinished run;
its old cached running ID is not automatically replayed.

The existing command poll reports the final result. `localElapsedMs` measures the
phone's complete routine including optional test pause. The separately reported
`requestToFinalResultMs` includes command delivery and final reporting. The
phone's `elapsedToVerifiedPlayingMs` measures local start to verified player
state, not physical audio. None of these includes voice recognition or a decider.

Local source is `app/src/dev/pixel/voice/AppleMusicRoutine.java` and the pure matcher
`AppleMusicMatch.java`; `local-integration.patch` records app/server integration.
The pre-change files and signed APK are preserved in
`~/.config/pixel-voice/rollback/before-local-music-20261011-230225/`.

### Local-run results

On the same phone, from Home, with the same account:

- Dreams: 2,380 ms on-phone to verified playback; 4,379 ms request to final
  response including a verified test pause (3,086 ms total on-phone).
- Here Comes the Sun: 2,309 ms on-phone to verified playback; 4,363 ms request
  to final response including a verified test pause (2,956 ms total on-phone).
- Dreams, normal play without automatic pause: 2,638 ms on-phone; **3,572 ms**
  request to the verified result. It was paused separately afterward.

These are small controlled trials, not a general benchmark. The remaining local
time includes accessibility observations, UI focus/transition waits and Apple
Music search/playback readiness. The old Mac-driven prototype took 10.2–11.6 s
to verified playback, but these comparisons are not randomized or cold-cache
controlled. Do not claim acoustic latency or guaranteed one-second performance.

## Normal voice requests

The user subsequently authorized normal Rose Agent requests to invoke the local
worker. `wire-voice.mjs` adds `apple_music_play` to the existing ElevenLabs agent,
with a narrowly appended prompt exception to `delegate_task`. It preserves the
selected LLM, voice, other tools, and existing prompt. The normal conversational
model chooses this tool; no additional routing/decider model is introduced.
The app client callback sends one native routine command and awaits its outcome.
It does not speak over successful music playback or retry uncertain outcomes.

Voice integration was checked with a fresh real hosted conversation: “Play Here
Comes the Sun by The Beatles on my Pixel” selected `apple_music_play` once,
selected `delegate_task` zero times, and verified playback on the phone. This test
covered text input to the same agent/tool configuration, not speech recognition.
The test track was paused and Rose Agent was reopened for user testing.

To remove only the conversational integration, explicitly run
`node rose-agent/apple-music-worker/unwire-voice.mjs` and reconnect Rose Agent.
The original hosted prompt/tool IDs and the new tool ID are backed up privately
under `~/.config/pixel-voice/apple-music-worker/`. No API keys are stored here.

## Intermittent search failure: reproduced and corrected

The on-device runner on v92 returned `needs_assistance` after `ime_enter`
returned `stale_screen`. The suggestions changed between inspection and the
keyboard action. `PhoneAccessibility` rejects this before dispatch, so it is
safe to re-inspect and retry that rejected Search press. The old worker stopped
instead. A second observed bug advanced to results even when Search could not
be submitted because the field had lost focus; the old results then stayed on
screen despite new search text.

Version 94 / 0.86 fixes only this workflow: re-establish focus and the advertised
keyboard action, require an accepted submission before reading results, and
permit up to three re-inspected retries of a rejected stale Search press within
2.5 seconds. Accepted or uncertain actions and song taps are not retried. Shared
accessibility guards, Bluetooth, audio routing and voice settings are unchanged.
Step results now include the phase and the exact submit failure status.

`bash rose-agent/apple-music-worker/test-routine.sh` exercises the real Java
routine with deterministic fake UI/time, including suggestions racing the
submit, loss of focus, retry exhaustion, and uncertain-action non-replay. It
fetches a checksum-pinned host JSON test dependency into a temporary directory.

Live validation on the Pixel reproduced a stale Search rejection, then recovered
on the next fresh inspection and verified Higher Love in 3,885 ms locally.
Diamonds verified in 2,505 ms, but its optional test-only pause was rejected as
stale; that result correctly remained partial success. Normal voice-route
validation called the music tool once, never the general agent, and verified
Here Comes the Sun in 3,687 ms locally. These are small controlled checks, not
a guarantee against every app-state failure.

`search-recovery.patch` records the changes from v92; apply after the original
`local-integration.patch` when reconstructing source. The pre-change APK and
source are saved under the private rollback directory. Reversal requires a
new version code to install without erasing app data.

## Current selection policy — top result (version 97 / 0.89)

The user superseded the earlier strict-version selection rules: play the first
song returned for the submitted title/artist search, including live versions,
remixes, and duplicate release listings. Apple Music ranks the results. Album
cards are skipped because this worker is for song requests. Selected title and
artist are recorded separately from the request; verification checks the selected
player title, not a claim that every search returns the exact requested edition.

Before selecting, the runner requires the submitted full-results screen and a
changed song list when changing queries, so it does not select the previous
search's still-visible results. If a row is rejected before dispatch with
`stale_screen` or `label_target_mismatch`, the next step rereads and reselects from
the new results. That recovery is limited to three retries within 2.5 seconds;
accepted and uncertain taps are never repeated. Album detail screens return to
Search through their visible navigation before entering the query.

The voice-route test for Hallelujah by Jeff Buckley passed from the album page,
selecting the top listing and verifying playback in 3,601 ms on the phone. Tests
also cover top-ranked live recordings, duplicates, same-row metadata, stale
selection recovery, retry exhaustion, and no retry after an uncertain tap.

The cumulative `search-recovery.patch` now includes these updates from v92.

## Song-title verification fix (version 98 / 0.90)

Some search rows include a lyric excerpt before the song title. The old first-text
selector treated the excerpt as the title, selected the correct row, and then
failed verification against the real player title. This was reproduced with
Just Can't Get Enough by Depeche Mode: the selected title incorrectly became
“And I just can't get enough,” and the worker timed out in verification despite
playback starting.

Selection still uses the first ranked song. Title extraction now uses the
observed `com.apple.android.music:id/title` field, excluding the `eyebrow` lyric
field. Result signatures also ignore lyric snippets. Java and Python regression
tests cover this exact row structure. The corrected hosted voice-tool test
verified playback of Just Can't Get Enough in 3,167 ms locally. An earlier
post-update test received Apple's No Results screen; the same query succeeded
on the next controlled test, so this is not a blanket reliability guarantee.

## Title-only song requests

The native routine and shared voice tool now accept an omitted artist. They search exactly the supplied title, select the first playable song, and obtain the selected artist from that result. The OpenAI voice matcher sends direct title-only play requests into the same routine; contextual phrases such as “play that” still use the general agent. Album, playlist, and known non-music control phrases do not enter the song shortcut.

The live OpenAI test recognized “Play Across the Universe,” made one music call and zero general calls, then verified The Beatles playback in 4.293 seconds inside the native routine. One stale-screen submit was rejected before dispatch and safely retried; there was one accepted search submission and one song selection. This uses synthetic speech into Live and the real Pixel for execution. The phone microphone still needs the user's ordinary test.

`title-only-integration.patch` contains changes outside this component. A pre-change APK/source snapshot is saved in the private rollback folder. The previous twelve-decibel ducking setting is preserved. The legacy standalone Mac worker remains title-and-artist; this extension applies to the current on-phone worker.
