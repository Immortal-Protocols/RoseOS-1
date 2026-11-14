# Rose v3: design

A local voice assistant that feels fast, attentive and smart, and controls a
fake phone, on one Apple M4 Max (128 GB) with MLX. Replaces the full-duplex
Nemotron VoiceChat design in `demo/` (kept for comparison).

## Why the change

The duplex speech model decided on its own when to talk (fillers, loops,
silence after results), used 55-70 ms of every 80 ms frame forever, and left
the brain ~10 tok/s of prompt processing. A cascaded pipeline lets the strong
model talk directly, uses the GPU only when there is work, and makes every
behaviour explicit and testable.

## Measured building blocks (this Mac, 2026-10-30, alone on the GPU)

| Part | Model | Cost |
|---|---|---|
| VAD | `mlx-community/silero-vad` weights, numpy forward pass on the listener thread | 0.15 ms per 32 ms frame |
| End of turn | `mlx-community/smart-turn-v3` | 4-7 ms GPU, ~20 ms CPU |
| Live ASR | `mlx-community/nemotron-3.5-asr-streaming-0.6b` (cache-aware, 160 ms lookahead) | ~1 ms/step, final text 30-40 ms after audio ends, WER 4.8 % on test set |
| Final ASR | `mlx-community/parakeet-tdt-0.6b-v3` | 24-64 ms per utterance, WER 4.4 % (number formatting only) |
| Brain | `mlx-community/Qwen3.6-35B-A3B-4bit` (hybrid DeltaNet/attention MoE, 3B active) | decode 82-91 tok/s; prefill ~1,500-1,900 tok/s; 33-token prompt TTFT 66 ms; 22 GB |
| Task brain (opt.) | `mlx-community/Qwen3.8-27B-4bit` (hybrid, dense) | decode 25-27 tok/s; prefill ~220 tok/s; turn TTFT 150-280 ms; screenshot 1.4 s; 15 GB |
| TTS | `mlx-community/pocket-tts` (24 kHz, streaming) | first audio ~20 ms, RTF 0.05 |
| TTS (alt) | `mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit` | first audio 70-140 ms, RTF 0.21 |

Kokoro was dropped: its espeak front end has a hard-coded data path from the
wheel's build machine on this Mac.

## Architecture

```
browser (getUserMedia: echoCancellation/noiseSuppression/AGC)
  AudioWorklet -> 16 kHz mono PCM16, 512-sample frames ----ws /audio---->  Listener
  AudioWorklet player <- 24 kHz PCM16 chunks (segment id) <--ws /audio--  Speaker
     reports {played samples per segment}, ducks/stops on command
                                                                        |
Listener (CPU VAD every frame; GPU: streaming ASR steps, final ASR, smart-turn)
  events: speech_start, partial(text), pause(candidate), turn_end(text), speech_resume
                                                                        v
Conductor (asyncio; the dialogue manager: who speaks when)
  - turn taking, barge-in, backchannel tolerance, announcements, task events
  - talker: one persistent LLM sequence (prompt cache), streams speech + tool calls
  - background tasks: phone agent / deep think, each its own LLM sequence
                                                                        v
Runtime (ONE MLX thread, cooperative priority scheduler)
  P0 listen (ASR, smart-turn) > P1 speak (TTS) > P2 talk (talker LLM) > P3 background
  jobs are generators; each next() is one bounded GPU unit (a decode token,
  a 128-256 token prefill chunk, one TTS chunk, one ASR step)
```

Everything runs in one Python process: `python -m rose.app` (FastAPI +
uvicorn on 127.0.0.1:8091). The dashboard is served at `/`.

### Runtime (`rose/runtime.py`)

- One thread owns all MLX models (MLX streams are per thread: load and run on
  the same thread). No MLX calls happen on any other thread, not even
  `mx.stream(mx.cpu)`: entering it switches the process-wide default device,
  which silently moved the runtime's work to the CPU (Parakeet 30 -> 120 ms).
  VAD is therefore a numpy port of Silero (`rose.listener.SileroCPU`, 0.15 ms
  per frame, max difference 3.6e-7 from the MLX model).
- `Runtime.submit(make_gen, priority, name, on_item=None) -> Job`: `make_gen()`
  is called on the MLX thread and returns a generator. Each `next()` must do
  one bounded unit of GPU work and `mx.eval` it. Items yielded are passed to
  `on_item` (called on the MLX thread; must be cheap and thread-safe).
  A job may yield `Sleep(seconds)` to step aside (e.g. TTS that is far ahead).
- `Runtime.call(fn, priority) -> concurrent.futures.Future`: one-shot unit.
- `Job.cancel()` takes effect before its next unit. `Job.future` resolves
  with the generator's return value or exception.
- Scheduling: highest priority runnable job first; round robin within a
  priority. Metrics: per-priority busy time, queue wait p50/p95.

### Brain (`rose/brain.py`)

- Loads Qwen3.6-35B-A3B (or `--brain`/`--task-brain`, e.g. Qwen3.8-27B: same
  hybrid DeltaNet/attention design, 64 layers, no deepstack) via `mlx_vlm.load`
  and drives its language model
  directly. Images go in with `Sequence.append_image`: the vision tower runs in
  groups of blocks and the merged embeddings prefill in 32-token chunks, each
  unit under ~60 ms. Qwen's M-RoPE gives image patches 3-D positions, so a
  `Sequence` keeps its own next position (`pos`, saved in marks), which falls
  behind the token count after an image.
- `Sequence`: a cache list for one conversation. `prefill(tokens)` (chunked
  generator), `decode(sampler, max_tokens)` (generator of token ids),
  `snapshot()` / `restore(snap)` (KV caches trimmed by offset, linear-attention
  states copied), `fork()` (deep copy, used to start phone tasks from a
  pre-filled agent prompt and deep-think from the talker's context).
- Prompt rendering is ours, append-only, matching the model's chat template
  (`<|im_start|>role\n...<|im_end|>\n`, non-thinking assistant prefix
  `<|im_start|>assistant\n<think>\n\n</think>\n\n`), so the cache never needs
  recomputation. System + tools block is produced once with the tokenizer's
  `apply_chat_template` and cached.
- Tool calls use the model's XML format:
  `<tool_call>\n<function=NAME>\n<parameter=P>\nVALUE\n</parameter>\n</function>\n</tool_call>`.
  A streaming parser splits generated text into speech text and calls.

### Listener (`rose/listener.py`)

- Every 32 ms frame: Silero (numpy) -> speech prob. Start of speech: p >= 0.5 for
  2 frames (3 frames and p >= 0.6 while Rose is audible). Pause candidate:
  p < 0.35 for 200 ms.
- Streaming ASR (Nemotron) fed continuously while in speech -> partials.
- Final ASR (Parakeet, 300 ms pre-roll) starts 64 ms into a dip; its text is
  emitted at once as a `tentative` event so the conductor can start thinking.
- At the pause candidate, smart-turn on the last <= 8 s, then (leaning early,
  because the conductor merges a turn that resumes before Rose really spoke):
  ends on "and", "the", "to", a comma -> wait 2.0 s; a question, or smart-turn
  >= 0.5 -> commit now; ends on "could", "I", "was" -> commit only if smart-turn
  >= 0.9, else wait 1.2 s; a lone "Hi Rose." / "Okay so" -> wait 0.7 s;
  anything else -> 0.5 s.
- A transcript faster than 7.5 syllables/s of speech never commits early:
  Parakeet sometimes invents words for a short clip ("Right," became "Right,
  I'm not sure."), and those turns wait like "anything else". In 1,070
  recorded eval turns, real speech topped out at 6.3 syllables/s and only the
  five invented transcripts were held.
- Measured: turn_end 225-240 ms after the user stops (p50).

### Speaker (`rose/speaker.py`)

- Text chunks from the talker (first chunk at the first clause boundary with
  >= 3 words, then sentences) -> normalized text (numbers, times like 6:30,
  symbols) -> TTS (P1) -> 24 kHz PCM16 chunks to the client with a segment id
  and the text of that segment.
- Tracks what was actually played from client reports, so an interrupted
  reply is recorded as what the user heard (word-level estimate by time).

### Conductor (`rose/conductor.py`)

State: `listening | user_speaking | thinking | speaking`, plus background tasks.

- **Reply**: on the `tentative` transcript, start generating speculatively
  (speech held, tools not run); on turn_end with the same words, release it,
  otherwise rewind and start over. The user message carries a context line:
  the time, the calendar relative to now (only when the turn is about time or
  plans, or something starts within 15 minutes), running tasks, pending
  events, and a tool hint for clear phone requests or corrections. Speech
  streams to TTS as it is generated; if the model goes straight to a tool
  call, Rose says "One sec." meanwhile.
- **Barge-in** while Rose is audible: duck to 25 % at the first speech frame,
  pause (silent, position kept) after 0.2 s of voiced speech, stop for good on
  2 non-backchannel words, a stop word ("wait", "no", "actually"...) or 0.6 s
  of voice (1.1 s while the words are only backchannels). A backchannel,
  noise or Rose's own echo resumes her where she paused. A stopped reply is
  rewound in the cache and re-appended only as heard, ending with a dash.
  "Wait" / "hold on" while she talks just stops her (no reply). Speech that
  resumes within 1 s of Rose starting is treated as the same turn: her draft
  is dropped and the turn merged. Background tasks are never cancelled by
  barge-in.
- **Tools** (talker): `phone_task(goal)`, `update_task(task_id, instruction)`,
  `cancel_task(task_id)`, `think(question)`. The talker says a short
  acknowledgement before a call. The immediate tool response is "started
  task N"; completion arrives later as a new tool response.
- **Announcements**: when a task finishes (or a phone notification arrives) and
  nobody is speaking and no turn is pending for >= 0.8 s, Rose speaks a short
  update. A single finished task is announced with its own result sentence
  (scripted: fast and always specific); anything else is generated. If the
  user is mid-turn, the event is included before their turn, and re-announced
  later if her reply skipped it or was cut off before it.
- **Never** speaks unprompted otherwise. No filler turns, no repeated calls
  (a task with the same goal that is running is refused by the conductor).
  Announcements cannot call tools. A reply that acknowledges an action
  ("Sure, setting an alarm...") but ends without the call gets the call
  written for it (forced `phone_task` / `update_task` prefix). Read-only
  "tasks" are refused (the data is already in context). A runaway reply (a
  repeated sentence or more than 14 sentences) is cut.
- **Semantic end of turn**: the talker answers "..." (never spoken) when the
  user's words are clearly unfinished; the draft is dropped and Rose keeps
  listening, or says "Go on?" after 2 s of silence. One- or two-word turns
  hold Rose's first audio until 0.5 s of silence ("Yes, ... Sam").
- **Recipients**: names in messaging requests are matched to contacts
  (misheard "Texam" -> Sam); an unknown name gets a scripted question ("I
  don't see Pam in your contacts. Did you mean Sam?") and sends are refused
  until the user answers. Advice questions ("Should I ...?") never change the
  phone.
- **Smarter answers**: `calculate` for exact arithmetic (synchronous; the
  reply continues after it), `think` for hard problems (background, thinking
  mode).

### Background tasks (`rose/tasks.py`)

- Phone agent: forks a pre-filled agent sequence (system prompt + action
  tools), observes the compact UI tree plus, on the real phone, a screenshot
  (vision; `tap_point` for things the tree lacks),
  acts with `open_app`, `tap`, `type_text`, `back`, `home`, `finish(summary)`;
  several actions per step allowed. P3 priority, prompt cache across steps.
  Taps are animated on the dashboard phone (~250 ms each).
- Calculations (real phone): `press_keys("50*51/2=")` taps a calculator's keys
  by name, one normal tap each (only digits and operators, so never a Send),
  and the prompt asks for the shortest expression with the same result. A
  success finish for a calculation goal is refused while its summary claims a
  number the screen does not show (`_unseen_number`): on the Pixel 10 the agent
  once "calculated" 1+...+50 by typing 1225 into nothing and reported it.
- Deep think: forks the talker context and reasons with thinking on, then
  returns a short answer for Rose to say.
- Every task: id, goal, status, steps (for the dashboard), cancel event,
  amendments (update_task) appended to its sequence before its next step.

### Phone (`rose/phone.py`)

The fake phone from `demo/phone.py` with UI-tree ids, plus incoming
notifications (dashboard button: "Mom texts you") so Rose can be proactive,
per-task views (two tasks can drive the phone at once), and Music
(playlists, search, play/pause/next) and Maps (invented places nearby with
distance, address and hours; walking and driving directions). The talker's
context includes what is playing and any active directions; places are only
known by looking them up in Maps.

### Rose's workspace (`rose/background.py`, real phone)

RoseOS phones carry a background controller (`dev.pixel.backgroundcontrol`):
Android ComputerControl runs one app on a separate virtual display, with a
floating viewer and an ongoing notification whose Stop belongs to the owner.
Rose's phone tasks run there by default, so the screen in the user's hand
stays theirs. `Workspace` is a port of the Node backend's `BackgroundPhone`
(`workspace/rose-agent/server/background-phone.mjs`), reached with the
ordinary `app_command` over the existing link (no phone-side change):

- One target app per session; another app starts a new one, `home` closes it.
- The controller needs the latest snapshot id (< 30 s) for every action, and a
  screenshot or an accepted action clears its node map. So each node action
  re-inspects and re-finds the same node by its identity (refusing
  `stale_screen` if it is gone or ambiguous), `back` gets a fresh snapshot,
  and an agent step's screenshot is taken before its tree.
- Schema 3 tags a session with the `taskId` given at start: Rose uses one id
  per workspace generation (`rose-ws-<nonce>-<n>-<package>`, the nonce new per
  process because the controller remembers owner-stopped ids across Rose
  restarts) so a follow-up task ("open Calculator", then "add 1 to 50")
  continues in the same workspace.
- Lifetime: kept open after a task; closed by home, another app, the owner's
  Stop (latched: never reopened for that task), 180 s unused
  (`WORKSPACE_IDLE_S`), and reset. A Stop pressed while the workspace sat open
  between tasks only closes it (never charged to the next task); a Stop around
  a link reconnect is still matched to the session held before it. The
  controller's own deadline, and a session Android closed or failed, close the
  workspace (`workspace_closed`: "open_app opens the app there again"); a reused
  session is renewed at open_app before it nears 200 operations / 600 s.
- Two slots (controller schema 4, `capabilities` says `multiSession` and
  `maxSessions`): `Workspaces` keeps one `Workspace` per slot. Each unit starts
  with `mode "alongside"`, names its own session on get_state and stop (an
  unnamed stop would be slot 0's, maybe the other unit's), and finds its
  session in the `sessions` summary by its taskId nonce. A task view uses one
  unit at a time. `open_app` picks the unit already showing the app, then a
  free one, then the least recently used one no running task holds, and last
  the view's own unit. An app another running task holds is refused
  (`app_in_other_session`). Rose's sessions from before a restart (`rose-ws-`
  with another nonce) are stopped only when a start finds no free slot. Rose
  Agent's own sessions are never stopped. With one slot, a single shared unit
  behaves as before.
- The probe decides only on a definite answer: a controller state (workspace)
  or "unavailable" (visible screen). A timeout or an error is "don't know":
  the task stays off the user's screen and the next read probes again.
- Sends: the runner judges the real control (text, description, view id such
  as `ic_send`; for `tap_point` what is under the point), never taps money or
  commitments (payments, "Send $20", transfers, rides, bookings), and a
  confirmed Send needs the exact approved draft in the field on the same
  workspace session. A draft is kept 170 s (`READY_TTL_S`), inside the idle
  close.
- Refusals are said plainly and never worked around: the OS allowlist
  (`target_not_allowed`: "Android doesn't let Rose open Amazon in her
  workspace; nothing was opened."), consent, a blocked session (kept), and the
  lock ("The phone is locked; unlock it and I'll continue."). Nothing then
  happens on the visible screen.
- A task uses the visible screen only when started `on_screen`
  (`RealPhone.session(name, on_screen=True)`) or when the phone has no
  controller (older phones, the Fold). Alarms, timers, music and media keys
  stay direct intents (audio and alarms are the user's), directions too
  (navigation is for the user); `search_places` opens Maps in the workspace.
- Surface for the talker and the agent: `background_available()`,
  `workspace_app()`, `look_workspace()` (next to `look()`, the visible
  screen), `RealSession.background`. The fake phone has no workspace
  (`False`/`None`), so the eval is unchanged. `/api/realphone` shows the
  workspace state; `ROSE_WORKSPACE=off` keeps every task on the visible screen.

### Dashboard (`rose/web/`)

Status orb (listening / thinking / speaking), mic button (getUserMedia needs a
click), live partial transcript, conversation with Rose's words highlighted as
spoken, background task cards with steps, the phone with tap animation,
latency HUD (end of speech -> first audio, ASR, LLM TTFT, TTS TTFA), voice
picker, "simulate incoming text" buttons, and a text box that injects a
spoken turn via macOS `say`.

## Targets

- p50 end of user speech -> first Rose audio <= 600 ms, p90 <= 900 ms.
- Barge-in: playback ducked <= 150 ms, silent (paused) <= 500 ms after speech start.
- Backchannels ("mm-hmm") never stop Rose.
- Phone tasks: <= 0.8 s per agent step; "text Sam I'm late" done < 4 s.
- Zero unprompted turns; zero repeated tool calls.

## Testing

`rose/eval/` drives the real pipeline with synthesized speech through a fake
client that plays Rose's audio in real time: latency, barge-in, backchannel,
task-while-chatting and correctness scenarios. GPU work runs one process at a
time.
