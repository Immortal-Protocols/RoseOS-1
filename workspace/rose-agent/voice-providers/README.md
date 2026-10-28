# Shared Rose Agent providers

## Simplified prompt and restored spoken progress

The redundant Live handoff block is removed; the original voice prompt retains action delegation and result verification. `prompt-simplification.patch` records the server change. The optional `test-live-prompt.mjs` uses synthetic audio and simulated outcomes; `VOICE_TEST_PROGRESS=1` checks spoken intermediate and final updates without operating any device or writing history.

OpenAI task delegation now supplies a progress callback tied to the originating delegation. The phone forwards changed backend progress during task polling, separately from the final result. Repeated progress is suppressed and a closed voice connection receives no further updates. `progress-integration.patch` records the phone HTML/version changes; the native audio and Bluetooth implementation is unchanged.

The prompt takes effect on a new voice connection after the Mac service reload. The progress callback requires the updated phone APK. Both changes have a saved pre-change rollback copy.

The app uses a common conversation contract rather than provider SDK calls:

- `close()` — end this connection and release microphone/audio resources.
- `setMuted(bool)` — local microphone mute (also server mute for OpenAI).
- `sendContext(text)` — silent app or memory context.
- `sendMessage(text)` — a user or explicitly authorized app request.
- `inputLevel()` / `outputLevel()` — independent measured audio levels.

`voice-providers.js` contains the ElevenLabs and OpenAI Live implementations.
ElevenLabs uses its existing WebRTC SDK. OpenAI uses `gpt-live-1` over WebRTC,
with the pre-existing Marin voice; the account key remains on the Mac. Native
Bluetooth routing, media buttons, phone control and the music runner are unchanged.
The app's book, podcast, sky, memory, and UI code use only this shared contract.
`build.sh` copies the two browser assets from this repository into the app APK.

## Tool dispatch

ElevenLabs supplies structured named client tool calls. OpenAI Live supplies a
client delegation and audio transcript; its public delegation event contains no
function arguments. Its connector resolves narrow explicit commands through
`voice-commands.js`, then invokes the same registered tool functions:

- “Play TITLE by ARTIST” → the existing one-command on-phone music routine.
- “Keep reading” → book resume when a book is active.
- “Keep playing” → podcast resume when a podcast is active.
- “Pause” → the active podcast/book control.

Other wording and broader requests go to the existing Luna/Codex task handler.
There is no added model call in the clear music path. This does mean natural
phrasing outside the small deterministic matcher can take the general agent
route. Album and playlist requests remain general tasks. Duplicate delegation
events and repeated delegation of the same spoken segment do not replay actions;
a new spoken request can run again. Distinct task corrections are forwarded even
while earlier work is pending. No uncertain action is automatically retried.

OpenAI audio transcript fragments are grouped for the saved conversation. Its
first input transcript fragment triggers the shared interruption handler; unlike
ElevenLabs it does not provide this integration with a numeric VAD score. This
is not speaker identification, and neither provider is guaranteed to reject TV
speech. Background-audio behavior still needs a same-volume user comparison.

## Configuration and rollback

The authenticated `/voice/config` endpoint stores the next-session provider in
`~/.config/pixel-voice/voice-provider.json`. Missing configuration defaults to
ElevenLabs. Existing calls are not silently moved to another provider.

```sh
python3 rose-agent/voice-providers/provider.py           # inspect selection
python3 rose-agent/voice-providers/provider.py openai    # OpenAI on next Start
python3 rose-agent/voice-providers/provider.py elevenlabs # rollback next Start
```

End the current call and Start again after changing providers. Neither command
changes the hosted ElevenLabs agent or its prompt. Both providers receive the
same recent saved conversation and active personal-memory snapshot. Switching
starts a new provider connection, not a transfer of hidden model state.

The broader pre-change source/APK rollback is saved privately under
`~/.config/pixel-voice/rollback/before-voice-providers-*`. `integration.patch`
records the changes outside this isolated Git repository. Revert that patch and
rebuild with a newer version code if a full source rollback is needed; avoid
uninstalling the app or erasing user data. Existing `/eleven/*` and legacy
OpenAI endpoints remain available for compatibility.

## Validation

```sh
node --test rose-agent/voice-providers/*.test.* rose-agent/personal-memory/voice-context.test.mjs
node rose-agent/tests/eleven-ui.cjs
node rose-agent/tests/book-companion.cjs
node rose-agent/tests/podcast-companion.cjs
node --test rose-agent/tests/page-regressions.test.mjs
node rose-agent/tests/reconnect.mjs
```

Tests cover both connectors' lifecycle contract, cancellation during microphone
acquisition, context delivery, memory revisions/off, deterministic tool dispatch,
duplicate suppression, forwarding concurrent task corrections, and existing app
context/control behavior. These do not simulate all Android audio hardware.

Opt-in live tests use account quota and the music test changes Pixel playback:

```sh
say -o /tmp/pixel-live-music-test.aiff 'Play Dreams by Fleetwood Mac.'
afconvert -f WAVE -d LEI16@24000 -c 1 /tmp/pixel-live-music-test.aiff /tmp/pixel-live-music-test.wav
node rose-agent/voice-providers/test-live-music.mjs /tmp/pixel-live-music-test.wav
node rose-agent/server/test-reconnect-live.mjs
```

The live synthesized-speech test uses real OpenAI Live audio/transcription and
this connector's actual event/tool handler with a simulated WebRTC wrapper,
then invokes the real on-phone worker. It verified Dreams with one music call,
zero general-agent calls, and 2,461 ms local worker time. This is not total
speech-to-sound latency. The history test recalled the synthetic Marigold
project. On the physical Pixel, OpenAI WebRTC connection and personal-memory
delivery were separately confirmed. Book/podcast interaction coverage here is
automated; listening quality and background-music rejection need user testing.

## Handoff regression with restored history

The first physical test heard the request and spoke an acknowledgment without
emitting a delegation event. This also reproduced in a live synthesized-speech
test when real saved history was supplied as alternating user/assistant input
turns; the earlier empty-history test missed it. Strengthening only the handoff
prompt did not fix that test.

OpenAI startup now uses `openai-session.mjs` to carry the same bounded history as
clearly labelled, untrusted historical context, rather than active assistant
turns to imitate. Memory is still provided independently. The same live test
then delegated, and a subsequent spoken Ruins by Cat Stevens test with history
loaded invoked the music worker once and verified playback in 2,709 ms locally.
These are controlled results, not a guarantee of every handoff.

Handoff diagnostics record event/dispatch/result stages and status only, with no
transcript or credentials. The phone is version 101; this history-format fix is
on the Mac and applies on the next voice connection.

Opt-in history comparison (uses account quota; only omit DRY_RUN to play music):

```sh
VOICE_TEST_HISTORY=1 VOICE_TEST_DRY_RUN=1 node rose-agent/voice-providers/test-live-music.mjs
VOICE_TEST_HISTORY=1 VOICE_TEST_CONTEXT_HISTORY=1 VOICE_TEST_DRY_RUN=1 node rose-agent/voice-providers/test-live-music.mjs
```

## Shared workers from the general agent

`app-worker-policy.mjs` is the shared worker capability catalog. Both Codex's durable developer instructions and every task request receive it. Registered workers cover Apple Music songs, media controls, Clock operations, and recent Messages text lookups. They are optional shortcuts: intent resolution, tool selection, and references stay with the model. Direct computer, browser, and phone controls remain available, and an explicit request for computer use must not be substituted with a limited reader. Worker limits are not global capability limits. Authorization, verification, and duplicate-action checks apply across every execution path. This is model-guided tool use, not a hard router or an additional model call. Future app workers can be added to this catalog.

The general backend previously lacked this capability in its tool guidance and manually reconstructed search. On the live paired Pixel, a correction to Bron-Y-Aur Stomp now invoked exactly one `apple_music_run` command, with 3.200 seconds inside the worker and about nine seconds through the task endpoint. “Play that again” also invoked exactly that command once, with 3.415 seconds inside the worker and about eight seconds through the endpoint. Neither general-agent run issued individual UI commands. These were direct calls to the same task endpoint used by voice, not fresh microphone recordings. Existing direct voice commands and twelve-decibel ducking were unchanged.

`worker-routing-integration.patch` contains the two server integrations. Revert these imports/policy insertions to disable this guidance; no phone install is required. The original files are in the private before-worker-routing rollback snapshot.

## Sequential music handoff recovery

Observed on the phone: repeated “Play Tangerine by Zeppelin” was transcribed, but no client delegation or music-worker call arrived. Corrected music results now use `session.thinking.append` with the originating delegation ID and a correlated event ID, rather than an unscoped “stay silent” update. OpenAI documentation: https://developers.openai.com/api/docs/guides/live-delegation .

A narrow fallback handles complete, settled, explicit title-and-artist playback commands if no model delegation arrives after 2.2 seconds. It rechecks microphone silence, generation, connection/mute state and exact accumulated request before acting. Title-only requests, references, corrections and other actions still require the model. A late delegation shares the fallback operation's promise/result and cannot replay playback. Closing clears the timer; uncertain outcomes are reported without retry. This is application-owned execution of low-impact music commands, not speaker identification.

Validation: three successive real Live audio requests with simulated worker results each delegated once and received correlated result acknowledgments. A separate two-request run deliberately suppressed delegation events; the fallback handled both once. Unit checks cover late delegation deduplication, explicit repeats, title-only exclusion, mute and disconnect. The old connector also had isolated runs with transcription and no delegation, so this is not relying on prompt changes alone.

Opt-in sequence test (uses API quota, no phone playback):

```sh
VOICE_TEST_HISTORY=1 VOICE_TEST_CONTEXT_HISTORY=1 node rose-agent/voice-providers/test-live-sequential-music.mjs first.wav second.wav third.wav
VOICE_TEST_DROP_DELEGATIONS=1 node rose-agent/voice-providers/test-live-sequential-music.mjs first.wav second.wav
```

The exact “Play Tangerine by Zeppelin” request was then tested through real Live audio and the actual Pixel worker. Live omitted delegation, the fallback invoked one music command, and Led Zeppelin playback was verified in 3.183 seconds inside the worker. This isolated audio test does not measure the full glasses microphone path. APK version 106 carries the fix; the original version 105 APK is retained privately. Twelve-decibel light ducking is unchanged.
