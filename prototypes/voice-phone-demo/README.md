# Rose: local voice assistant + phone control (Mac prototype)

Talk to Rose and she answers in about a third of a second, does things on a
(fake) phone in the background while you keep talking, and stops the moment
you talk over her. Everything runs on one Apple M4 Max (128 GB): no cloud,
no API keys.

```
browser mic (echo cancellation) ──► listener ──► conductor ──► speaker ──► browser speaker
                                    VAD, ASR,     who speaks     Phonon TTS
                                    end of turn   when; memory
                                                      │
                                    brain: Qwen3.6-35B-A3B (talks, decides, calls tools)
                                                      │ phone_task / think / calculate
                                    background tasks: phone agent drives the fake phone UI
```

The first prototype used a full-duplex speech model (Nemotron VoiceChat 11B)
plus a separate brain. It was chatty, hard to steer and slow when both models
shared the GPU. That design and its measurements are in [DUPLEX.md](DUPLEX.md).
The design notes for this version are in [rose/DESIGN.md](rose/DESIGN.md) and
the module contracts in [rose/CONTRACTS.md](rose/CONTRACTS.md).

## Run

```bash
bash scripts/setup.sh    # once: venv + ~22 GB of models
bash scripts/rose.sh     # starts Rose (~7 s) and opens http://127.0.0.1:8091
```

Click **Start microphone** (or press space) and talk. The page uses the
browser's echo cancellation, but headphones are still the safest choice. With
`--local-audio`, Rose uses the Mac's default mic and speakers instead of the
browser (headphones required). Without a mic, type into the box at the bottom:
it is spoken with macOS `say` and fed through the same listener.

The fake phone has Messages, Clock (alarms), Calendar, Settings, Contacts,
Music (playlists, play/pause/next) and Maps (invented places nearby, hours,
walking and driving directions). Every change is made by the phone agent
tapping through the phone's screens, shown live on the dashboard.

Things to try:
- "Text Sam that I'm running ten minutes late", then keep chatting while it happens.
- "Where's the nearest pharmacy?", "Get me walking directions to the nearest coffee shop", "Play my chill mix".
- "What's on my calendar this afternoon?", or "What did Alex ask me?" and then "Reply that I'll send it tonight."
- "If a recipe for four needs one and a half cups of flour, how much for seven?"
- Ask for a story, then interrupt ("actually, what time is it?"), or say "mm-hmm" (she keeps going) or "wait" (she stops).
- "Text Mom I'll call her tonight", then "actually, make that tomorrow."
- The **Simulate incoming text** buttons: Rose mentions the text at the next quiet moment.

## Models (all local, MLX, one process)

| Part | Model | Cost on this Mac |
|---|---|---|
| Voice activity | Silero VAD (`mlx-community/silero-vad` weights, numpy on the CPU) | 0.15 ms per 32 ms frame |
| End of turn | `mlx-community/smart-turn-v3` + transcript rules | 3–7 ms |
| Live transcript | `mlx-community/nemotron-3.5-asr-streaming-0.6b` (cache-aware streaming) | ~1 ms per step |
| Final transcript | `mlx-community/parakeet-tdt-0.6b-v3` | 25–65 ms per utterance |
| Brain and voice of Rose | `mlx-community/Qwen3.6-35B-A3B-4bit` (3B active) | ~85 tok/s; a turn's prompt in 45–65 ms with the cache |
| Speech | `gradium/phonon-31m-en` (Phonon 31M, 9 voices) | first audio ~6 ms, 25× real time |

One MLX thread runs every model through a cooperative priority scheduler
(`rose/runtime.py`): listening first, then speech, then Rose's reply, then
background tasks. Each unit of GPU work is small (one token, one 128–256
token prompt chunk, one 80 ms audio frame), so a phone task can never delay
Rose's next word by more than about 60 ms. A tiny GPU op after every 250 ms
without work keeps macOS from idling the GPU: without it, a turn's prompt
after a 3 s pause takes ~250 ms instead of ~60 ms.

## What makes it feel fast and attentive

- **Rose's memory is a live cache.** The conversation is never re-processed;
  each turn only adds its new tokens (~40 ms). Interrupted replies are rewound
  and rewritten to exactly what the user heard, ending in a dash.
- **Speculative replies.** About 64 ms into a pause, the final transcript is
  already known and Rose starts thinking. Her words are held until the turn
  is committed, and thrown away if you keep talking.
- **End of turn in ~200 ms** when the sentence sounds finished, longer after
  "and", "the", a comma, or "Hi Rose," (also when it is misheard as "High
  rose") so she doesn't cut you off. A transcript faster than anyone talks
  (7.5+ syllables a second, e.g. "Right," heard as "Right, I'm not sure.")
  has invented words, and never ends the turn early.
- **Barge-in in three steps:** her voice ducks on your first syllable
  (~100 ms), pauses with her place kept at ~0.2 s of speech, then stops for
  good on real words, or resumes if it was just "mm-hmm", a cough or her
  own echo. The live transcript often writes "mm-hmm" as "And n" or "MM Ham
  Hum", so hum-like words don't count as real words, and the better final
  transcript of the dip has the last say. If she did stop for an "mm-hmm",
  she picks up where she left off instead of answering it.
- **Work happens in the background.** "Sure, texting Sam now", and the phone
  agent runs (1.5–2 s, animated on the phone) while you keep talking. Results
  are announced at the next quiet moment, never over you.
- **No filler, no unprompted turns, no fake actions.** She only speaks when
  spoken to or with news. A reply that says "on it" or "I'll open it" but
  forgets the tool call gets the call written for it, however the request was
  phrased ("Right, play some music", or "Great, do that" after she offered).
  Advice questions ("should I turn off Bluetooth?") are answered, not acted
  on, and a plain question ("what time is it?") never starts a phone task:
  if she already said she would, she takes it back. Notifications are
  reported, never acted on without being asked. Facts she can't know
  (places, hours, prices) come from Maps or she says she doesn't know. The
  calendar is only given to her when the question is about time or plans,
  so "how are you today?" at 1 AM doesn't get the day's schedule read out.
- **Smarts where it matters:** exact arithmetic (`calculate`), a
  thinking-mode background task for formula questions (savings, loans,
  probability), the calendar given relative to the current time (including
  what "this afternoon" held once it is over), and the user's exact words in
  texts.
- **Names are checked, not guessed.** A misheard recipient is matched
  against the contacts ("Texam" becomes "text Sam"). A name that isn't a
  contact gets a question ("I don't see Pam in your contacts. Did you mean
  Sam?"), and the answer sends the message.
- **Patience for short turns.** After a one- or two-word turn ("Yes,"),
  Rose waits half a second of silence before speaking, because people often
  go on ("Yes, Sam.").
- **She knows when you're not done.** If the words so far are clearly
  unfinished ("So I was thinking that maybe we could"), the brain answers
  with a silent "..." (decided in ~0.1 s, never spoken) and Rose keeps
  listening. If you stay quiet for 2 s, she says "Go on?".

## Measured (M4 Max, 2026-10-31)

End-to-end runs of the real pipeline (`rose/eval`): synthesized speech fed
into the listener in real time, a fake browser playing Rose's audio in real
time. Latency is from the user's last voiced audio to Rose's first audio
played. 22 scenarios, twice each, in three macOS voices (Samantha, US;
Karen, Australian; Daniel, British): 132 runs, 151 user turns.

| | p50 | p90 | max |
|---|---|---|---|
| Voice to voice | 394 ms | 540 ms | 981 ms |
| End of turn after the user stops | 254 ms | 341 ms | 551 ms |
| LLM first token after the user stops | 228 ms | 291 ms | 808 ms |
| Rose silent after you start talking over her | 356 ms | 413 ms | 462 ms |

Only two turns took over 600 ms. The slowest (981 ms) was "Actually, make
that tomorrow." while the text it corrects was still being sent.

Phone tasks, from the call to the phone showing the result: Do Not Disturb
0.9–1.1 s; nearest pharmacy 1.1–1.2 s; play a playlist 1.2 s; text Sam
1.6–1.8 s; walking directions 1.6–1.7 s; alarm 1.9–2.0 s; two changes at
once 2.6 s. A savings question worked out in the background with thinking
mode: ~14 s, while Rose stays free to talk.

Reliability: 131 of 132 runs passed (Samantha 44/44, Karen 43/44, Daniel
44/44). In the failure, Karen asked "Should I turn off Bluetooth to save
battery?" and Rose said she would. The call was refused, but she didn't take
it back. That was fixed, and the scenario then passed 6 of 6 in Karen's
voice. Scenarios: greeting, knowledge, math, calendar, task while chatting,
alarm, Do Not Disturb, barge-in, backchannel, double backchannel, hold on,
amend a running task, incoming notification, 30 s of silence, advice
without action, two requests, context follow-up, unknown contact, a hard
money question, play music, nearest pharmacy and walking directions. See
`rose/eval/README.md`.

A 40-turn conversation (`scripts/long_session.py`) grew the cache from 2.1k
to 4.8k tokens. Time to first token stayed flat (p50 86 ms, max 183 ms), and
Rose still knew the names and plans from the first turns.

Run it yourself (it needs the GPU to itself, ~8 min per pass of all scenarios):

```bash
HF_HUB_OFFLINE=1 .venv/bin/python -m rose.eval.run            # all scenarios
HF_HUB_OFFLINE=1 .venv/bin/python -m rose.eval.run --only barge_in,backchannel --repeat 3
HF_HUB_OFFLINE=1 .venv/bin/python -m rose.eval.run --user-voice Karen  # another speaker
HF_HUB_OFFLINE=1 .venv/bin/python scripts/long_session.py --turns 40  # a long conversation
```

Module tests: `python -m rose.tests.test_listener`, `test_speaker`,
`test_tasks`, `test_conductor` (each loads the models it needs; the
listener and conductor tests start with quick checks of the text rules that
once went wrong).

## Not verified

- **A real person's voice through a real microphone.** Every test used
  synthesized speech (three macOS `say` voices). Smart-turn and the
  backchannel rules were tuned on it. Real voices pause, hum and mumble
  differently.
- **Echo cancellation on laptop speakers.** The fake client never feeds
  Rose's audio back into the mic. The browser's echo canceller and the echo
  check in the conductor are untested against real speakers.
- **Very long sessions.** A 40-turn text conversation (2.1k to 4.8k tokens
  of cache) kept its first-token time flat (p50 86 ms, max 183 ms) and
  recalled facts from the first turns. Longer ones were not tested; the
  cache grows with the conversation and is never trimmed.
- A physical phone or an on-device orchestrator. The phone is fake, and the
  numbers are from this Mac only.

## Files

- `rose/runtime.py`: the MLX thread and priority scheduler
- `rose/brain.py`: Qwen3.6 engine with rewindable caches, tool-call parsing
- `rose/listener.py`: VAD, streaming and final ASR, end of turn
- `rose/speaker.py`, `rose/transport.py`, `rose/local_audio.py`: Phonon TTS, browser/local/fake audio
- `rose/conductor.py`: turn taking, barge-in, tools, announcements, memory
- `rose/tasks.py`, `rose/phone.py`: phone agent, deep thinking, the fake phone
- `rose/app.py`, `rose/web/`: server and dashboard (`--ui-only` replays a scripted demo without models)
- `rose/eval/`: end-to-end scenarios and metrics; `rose/tests/`: module tests
- `scripts/rose.sh`, `scripts/setup.sh`, `scripts/bench_*.py`, `scripts/eval_talker.py`, `scripts/rose_drive.py`,
  `scripts/ws_client_test.py` (talks to a running server like the browser does), `scripts/long_session.py`
- `demo/`, `DUPLEX.md`: the earlier full-duplex prototype
