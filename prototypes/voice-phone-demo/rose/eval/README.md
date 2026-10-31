# rose.eval: end-to-end scenarios against the real pipeline

Drives the whole Rose v3 pipeline in one process (runtime, brain, listener,
speaker, phone, tasks, conductor, wired the way `rose.app` wires them,
including the GPU keepalive) with a `FakeTransport` in place of the browser.
A scripted user speaks through the fake mic and a fake client plays Rose's
audio in real time. Every event is recorded, then each scenario is judged
from that record.

## Run

```bash
cd prototypes/voice-phone-demo
.venv/bin/python -m rose.eval.run --list                     # scenarios, no models loaded
.venv/bin/python -m rose.eval.run                            # all of them, about 6-8 minutes
.venv/bin/python -m rose.eval.run --only barge_in,backchannel --repeat 3
```

| Option | Meaning |
|---|---|
| `--only a,b` | run these scenarios (default: all) |
| `--repeat N` | run the selection N times (latency percentiles get meaningful from ~3) |
| `--out DIR` | output directory (default `out/eval_v3`) |
| `--voice NAME` | Rose's TTS voice (default `zoey`) |
| `--user-voice NAME` | macOS voice for every user line (default: per step, `Samantha`) |
| `--transcribe` | also transcribe the audio the client played (Parakeet) and compare it with Rose's text events |
| `--no-warmup` | skip the untimed "Hello there." turn that warms kernels and caches |
| `--no-keepalive` | run without the app's GPU keepalive |

`HF_HUB_OFFLINE=1` is set automatically; every model must already be in the
HF cache. GPU work runs in this process, so do not run it next to `rose.app`
or another GPU job: the latencies would be meaningless. Exit code 0 when
every hard check passed, 1 when one failed, 2 when a pipeline module is
missing (the message names every missing or broken piece).

Outputs under `--out`:

- `<scenario>.jsonl` (`<scenario>.rN.jsonl` with `--repeat`): every event of
  the scenario, one JSON object per line, with `t_rel` (seconds since the
  scenario started), `mono` (monotonic time) and `t` (unix time).
- `results.json`: per scenario, every check (pass/fail, soft, detail), the
  derived metrics, the user's lines, what Rose said, mic feeder health.
  Written after every scenario, so an interrupted run keeps what it did.
- `summary.md`: pass/fail table, latency p50/p90/max over all user turns,
  barge-in timings, task durations, behaviour counters, every check.
- `_say_cache/`: the synthesized user lines (16 kHz WAV), reused across runs.

## How the user is simulated

- Each `say` step is synthesized with macOS `say` at 16 kHz and fed through
  `FakeTransport.mic` in 512-sample (32 ms) frames, one frame every 32 ms of
  wall time. Between utterances a background thread keeps feeding a -70 dBFS
  noise floor in real time: the mic stream never stops, as with a real
  microphone, because the listener's endpointing counts audio samples.
- The first and last voiced frame of each line (10 ms RMS above 5 % of the
  peak) are timestamped when they are pushed. "The user stopped speaking" is
  the push time of the frame that holds the last voiced sample.
- The fake client plays Rose's audio in real time and reports `played` /
  `idle` / `stopped` like the browser worklet; the harness logs every piece
  sent, every control message (duck, unduck, stop) and every report.

## Metrics

Per user turn (rows in `results.json` -> `metrics.turns`; an utterance's turn is
the last `user_turn` before the next utterance, so a turn committed too early
and resumed is judged by the reply that answered the whole utterance):

| Metric | Definition |
|---|---|
| `voice_to_voice_ms` | last voiced mic frame fed -> first Rose audio of the reply *played* at the fake client. A reply segment's start is `report time - samples / 24 kHz` of its earliest client report, never before the piece was sent. Negative means Rose spoke before the user finished. |
| `turn_end_after_voice_ms` | last voiced frame fed -> listener's `turn_end` event |
| `endpoint_ms`, `asr_ms`, `smart_turn_p` | from the listener's `turn_end` |
| `llm_first_token_ms`, `first_speech_text_ms`, other `*_ms` | from the conductor's `metrics` event for that reply |
| `tts_first_audio_ms` | conductor `voice_to_voice_ms - first_speech_text_ms` (first speakable text -> first audio sent) when the conductor does not report it |
| `conductor_v2v_ms` | the conductor's own voice-to-voice (listener's end-of-speech estimate -> first audio *sent*) |
| `speaker_latency_ms` | `rose_audio_start.latency_ms` |
| `asr_wer` | word error rate of the user turn text against the script |

Talking over Rose (`metrics.barges`), measured from the first voiced frame of
the interrupting line: `duck_ms` (duck command sent), `stop_cmd_ms` (stop
command sent), `stop_ms` (client reported `stopped`, after its 40 ms fade),
`resumed` (unduck or a `barge` resume), and the interrupted reply's `heard`
and `said` text.

Behaviour counters: **unprompted turns** (a Rose turn with no `user_turn`,
finished task, `notification` or `event_queued` since her previous turn),
**repeated tool calls** (same tool, same normalized arguments, or a
phone_task/think goal with >= 80 % word overlap), **talk-overs** (Rose's
audio started between the first and last voiced frame of a user line),
task durations, steps and per-step ms from `task` events, and notification
-> first audio.

Content checks read Rose's text events (`rose_turn`, which carries what the
user heard when she was interrupted). A reply still playing when the final
settle times out has no `rose_turn` yet: it is judged on the chunks sent to
TTS so far and marked `unfinished` in `results.json`, so a long loop still
fails `no_loops`. `--transcribe` adds a soft check that
Parakeet's transcript of the played audio contains >= 70 % of those words.

## Scenarios

Every scenario starts from a reset phone and a fresh conversation
(`conductor.reset()`), waits for 1 s of quiet, runs its steps, then waits
until nothing has happened for a few seconds (no speech, no audio, no
running task) before judging. Hard checks decide pass/fail; soft checks are
the design targets in `rose/DESIGN.md`.

| Scenario | The user... | Hard checks | Soft checks |
|---|---|---|---|
| `greeting` | "Hi Rose, how are you doing today?" | one reply, no tool call | <= 30 words |
| `knowledge` | "What's the capital of Australia?" | says Canberra, no phone_task | |
| `math` | flour: 1.5 cups for 4 people, how much for 7? | says 2 5/8 (or 2.625, 21/8, "two and five eighths"), no phone_task | |
| `calendar` | "What's on my calendar this afternoon?" | mentions Alex and four, no phone_task (answered from context) | |
| `task_while_chatting` | "Text Sam that I'm running ten minutes late." then, as soon as Rose's acknowledgement has played, "How's your day going?" | one phone_task; Sam's thread ends with a sent message saying late and ten; task done; small talk answered; completion announced after the task finished | task <= 4 s; small talk answered while the task still ran |
| `alarm` | "Set an alarm for six thirty tomorrow morning." | one phone_task; a 6:30 AM alarm that is on; task done; Rose confirms after it finished | task <= 4 s |
| `do_not_disturb` | "Turn on do not disturb." | one phone_task; DND on; task done; confirmation | task <= 4 s |
| `barge_in` | "Tell me a bit about the history of Rome." then, 2 s into Rose's answer, "Actually, what time is it?" | Rose went silent (paused or stopped) within 500 ms and was stopped (client flushed) within 1.2 s; the reply is marked interrupted; what she recorded as heard is a word prefix of what she said; the next reply has the current time (+-3 min) | silent <= 400 ms, flushed <= 900 ms, duck <= 150 ms |
| `backchannel` | asks how volcanoes form, then "Mm-hmm." 1.5 s into the answer | Rose was speaking when it began (else the run is invalid), no stop, reply not interrupted, exactly one Rose turn | the duck was undone |
| `hold_on` | asks for a lighthouse story, then "Wait, hold on a second." 2 s in | Rose stopped; afterwards at most one turn of <= 2 words | stop <= 600 ms |
| `amend_task` | "Text Mom that I'll call her tonight." then, the moment the phone_task call appears, "Actually, make that tomorrow." | Mom's thread ends with a sent message saying tomorrow | nothing saying tonight was sent; update_task used; Rose says tomorrow |
| `notification` | silent; after 2 s Jordan texts "Are we still on for soccer Saturday?" | exactly one Rose turn; it mentions Jordan or soccer, once; no tool call | spoke within 3 s |
| `silence` | 30 s of room noise | zero Rose turns, zero tool calls, zero user turns | no speech_start at all |
| `advice_no_action` | "Should I turn off Bluetooth to save battery?" | a reply, no phone_task, Bluetooth still on | |
| `double_backchannel` | asks how hurricanes form, then "Mm-hmm, mm-hmm." 1.5 s in | Rose keeps talking, one Rose turn | |
| `two_requests` | "Turn on do not disturb and set an alarm for seven fifteen tomorrow morning." | DND on and a 7:15 AM alarm on | |
| `context_followup` | "What did Alex ask me?" then "Reply that I'll send it tonight." | mentions the deck; one phone_task; Alex's thread ends with "tonight" | |
| `unknown_contact` | "Text Pam that I'll be there soon." then "Yes, Sam." | asks about Pam and suggests Sam; one phone_task (after the answer); Sam's thread ends with "soon" | |
| `hard_question` | savings of $250/month at 5 % compounded monthly for 10 years | says about $38,800 (calculate or think); no phone_task | |
| `play_music` | "Right, play my chill mix." | one phone_task; Chill Mix is playing; task done | |
| `nearest_pharmacy` | "Where's the nearest pharmacy?" | one phone_task; after it finishes Rose names Valencia Pharmacy; no pharmacy named before Maps answered | |
| `directions` | "Get me walking directions to the nearest coffee shop." | one phone_task; walking directions to Sunrise Coffee Roasters are on | |

Every scenario with user speech also checks (hard): no unprompted turns, no
repeated tool calls, Rose never started talking while the user was
speaking, no reply repeats a sentence 3+ times (a degenerate loop); and
(soft) every voice-to-voice latency <= 900 ms. `barge_in` also has a soft
check that the time answer is <= 30 words.

## Writing a scenario

```python
from rose.eval.scenarios import Scenario, say, say_while_rose_speaks, wait_for, called, said, no_unprompted

Scenario("weather_barge", "Interrupting a long answer with a short question.",
         [say("Tell me about the weather on Mars."),
          say_while_rose_speaks("What about Venus?", after_ms=1500)],
         [said(r"venus", "talks about Venus"), no_unprompted()])
```

Steps: `say(text, voice)`, `wait(seconds)`, `wait_for(kind, predicate, timeout, since)`
(an event recorded after the last user line began, or after `since="step"`,
`"start"` or a `mark(name)`), `wait_idle()` (Rose has spoken and gone quiet),
`say_while_rose_speaks(text, after_ms)`, `backchannel(word, after_ms)`,
`notify(app, title, text)`, `mark(name)`, `settle(quiet, timeout)` and
`expect(name, fn)` (a check evaluated at that moment). A check is
`check(name, fn, soft=False)` where `fn(ctx)` returns a bool or
`(bool, detail)`; `ctx` (`rose.eval.run.Context`) offers `rose_turns()`,
`rose_text(since)`, `tool_calls(name)`, `turns()`, `barges()`, `tasks()`,
`unprompted()`, `talk_overs()`, `repeated_calls()`, `events(kind, ...)`, `phone_data()` and
`phone_start`.

## Limits

- No acoustic echo: the fake client's output never reaches the mic, so the
  listener's echo guard and the browser's echo cancellation are not tested.
- The user is macOS `say`: clean, evenly paced speech, kinder to ASR and
  endpointing than a real voice. `asr_wer` shows what the listener heard.
- Times are taken when 32 ms frames are pushed, so user-side timestamps
  have 32 ms granularity.
- One run of a scenario is one sample of a sampled LLM (temperature 0.6):
  use `--repeat` before drawing conclusions from a single failure.
