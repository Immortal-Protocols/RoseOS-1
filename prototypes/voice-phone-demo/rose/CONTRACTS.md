# Rose v3 module contracts

Read `rose/DESIGN.md` first. Existing, working modules (do not rewrite; extend
only if a contract below says so): `rose/runtime.py`, `rose/brain.py`,
`rose/bus.py`, `rose/textnorm.py`, `rose/web/audio.js`.

Conventions: Python 3.12, `from __future__ import annotations`, dataclasses,
module docstrings that explain *why*, sparse comments, no new third-party
packages (numpy, scipy, soundfile, mlx, mlx-audio, mlx-vlm, fastapi, uvicorn,
pillow, sounddevice are installed). All MLX model loading and inference runs
on the runtime thread (`runtime.run(fn)` / `runtime.submit(gen, ...)`), never
on asyncio or audio threads. No MLX calls on other threads at all, including
`mx.stream(mx.cpu)` (it changes the process-wide default device). Silero VAD
runs as numpy on the listener thread.

Times: `time.monotonic()` for durations, `time.time()` in bus events.
Audio: mic float32 [-1, 1] 16 kHz mono; Rose's speech float32 24 kHz mono.

## Bus events (kinds and fields)

Emitted with `bus.emit(kind, **fields)`. The dashboard and eval harness rely
on these names.

| kind | fields | emitter |
|---|---|---|
| `vad` | `p` (speech prob), `speaking` (bool) - at most 10/s | listener |
| `speech_start` | `t_audio` (s since stream start) | listener |
| `partial` | `text` | listener |
| `speech_pause` | `t_audio` | listener |
| `speech_resume` | `t_audio` | listener |
| `noise` | `text`, `t_audio` (voiced audio with no words; ends the utterance) | listener |
| `tentative` | `text`, `t_audio` (final-ASR text ~64 ms into a pause, before the commit) | listener |
| `speculation` | `stage` ("start", "confirm", "drop"), `id`, `text` / `head_start_ms` / `reason` | conductor |
| `turn_end` | `text`, `t_audio`, `endpoint_ms` (silence before commit), `asr_ms`, `smart_turn_p` | listener |
| `user_turn` | `text`, `id` (`id` 0 and `hold: true` for "wait"/"hold on" while Rose talks: no reply) | conductor |
| `rose_partial` | `id`, `text` (text generated so far, speech only) | conductor |
| `rose_say` | `id`, `seg`, `text` (a chunk sent to TTS) | speaker |
| `rose_audio_start` | `id`, `latency_ms` (end of user speech -> first audio sent) | speaker |
| `rose_heard` | `id`, `text` (what the user actually heard), `interrupted` (bool) | conductor |
| `rose_turn` | `id`, `text`, `interrupted` | conductor |
| `barge` | `stage` ("duck", "pause", "stop", "resume", "abandon"), `reason`, `ms` | conductor |
| `tool_call` | `id`, `name`, `arguments` | conductor |
| `task` | `task` (dict from `Task.to_dict()`) | tasks |
| `phone` | `version`, `what` | phone |
| `notification` | `app`, `title`, `text` | phone |
| `metrics` | `turn` (dict of per-turn latencies) | conductor |
| `stats` | runtime / audio stats, ~1/s | app |
| `status` | `state` ("loading", "listening", "user_speaking", "thinking", "speaking") , `detail` | conductor |
| `warning` / `error` | `message` | anyone |

## Transport (`rose/transport.py`)

```python
class Transport(Protocol):
    def send_audio(self, seg: int, pcm: np.ndarray) -> None: ...   # float32 24 kHz chunk
    def send_control(self, msg: dict) -> None: ...                 # {"type": "duck"|"unduck"|"stop"|"end", ...}
    on_mic: Callable[[np.ndarray], None]                           # set by app: float32 16 kHz, any length
    on_client: Callable[[dict], None]                              # played/stopped/idle reports

class BrowserTransport:  # bound to the /ws/audio websocket (see web/audio.js protocol)
    def attach(self, websocket, loop) / detach()
class FakeTransport:     # for tests/eval; simulates a client that plays audio in real time
    def __init__(self, clock=time.monotonic, speed=1.0)
    def mic(self, samples: np.ndarray) -> None   # inject user audio (calls on_mic in 32 ms frames, paced in real time by caller)
    played_audio(self) -> np.ndarray             # everything the fake client "played", 24 kHz
    # Emits {"type":"played","seg":N,"samples":S,"done":bool} every 100 ms of simulated playback,
    # {"type":"idle"} when its queue drains, and on "stop": fades (40 ms) then {"type":"stopped",...}.
    # "duck" scales played samples by gain; "unduck" restores.
```

## Listener (`rose/listener.py`)

```python
class Listener:
    def __init__(self, runtime: Runtime, bus: Bus, on_event: Callable[[ListenEvent], None]): ...
    def load(self) -> None            # blocking; loads models (runtime thread for GPU models)
    def start(self) -> None           # starts its thread and the ASR job
    def push(self, samples: np.ndarray) -> None   # mic audio, float32 16 kHz, any length; thread-safe, never blocks
    def set_rose_audible(self, audible: bool) -> None   # stricter speech-start while Rose plays (echo guard)
    def stop(self) -> None
    vad_p: float; in_speech: bool; utterance_started_at: float | None

@dataclass
class ListenEvent:
    kind: str     # "speech_start" | "partial" | "speech_pause" | "speech_resume" | "turn_end" | "noise" | "tentative"
    text: str = ""
    t: float = 0.0            # monotonic time of the event
    speech_ms: float = 0.0    # length of the current utterance's voiced audio
    data: dict = field(default_factory=dict)  # turn_end: endpoint_ms, asr_ms, smart_turn_p
```

Behaviour (see DESIGN.md "Listener"): Silero VAD (numpy) every 512 samples,
streaming ASR (`mlx-community/nemotron-3.5-asr-streaming-0.6b`; set
`model.default_att_context_size = [56, 0]`; trained values are 0, 3, 6, 13) for partials, final transcript
from Parakeet (`mlx-community/parakeet-tdt-0.6b-v3`) on the utterance audio
plus 300 ms pre-roll, smart-turn (`mlx-community/smart-turn-v3`) at each pause
candidate. Endpointing: pause candidate after 200 ms below threshold; commit
when smart-turn p >= 0.5 and the text does not end in a dangling word; else
commit after 1.2 s (2.0 s if dangling). Resumed speech before commit continues
the same turn. Also emits bus events listed above. `on_event` is called on the
listener thread.

## Speaker (`rose/speaker.py`)

```python
class Speaker:
    def __init__(self, runtime: Runtime, bus: Bus, transport: Transport): ...
    def load(self, voice: str = "zoey") -> None      # Phonon 31M (gradium/phonon-31m-en) via mlx-audio
    def set_voice(self, voice: str) -> None
    voices: dict[str, str]                             # name -> phonon-voices id
    def utterance(self, reply_id: int, t_user_end: float | None) -> Utterance
    def duck(self) / unduck(self) / stop(self) -> None # immediate client commands
    def pause(self) / resume(self) -> None             # fade to silence keeping the position / continue
    audible: bool                                      # client is currently playing Rose audio
    def on_client(self, msg: dict) -> None             # wire to transport.on_client

class Utterance:
    def add(self, text: str) -> None    # stream of LLM speech text; splits into chunks:
                                        # first chunk at first clause/sentence boundary with >= 3 words
                                        # (or 12 words), later chunks per sentence; textnorm.speakable() each
    def finish(self) -> None            # no more text
    def cancel(self) -> None            # stop synthesis; does NOT send stop to client (conductor does)
    def flush(self) -> None             # speak buffered text now (before a tool call)
    def retract(self, text: str) -> bool  # take back text still in the chunker (runaway cut)
    def heard(self) -> str              # text the user has heard so far (word-level estimate from played samples)
    done: threading.Event               # set when playback completed (all audio played) or cancelled
    first_audio_at: float | None        # monotonic time first audio was sent
    interrupted: bool
```

TTS runs as one runtime job per utterance at priority SPEAK, one Mimi frame
(80 ms audio) per unit, yielding `Sleep` when more than ~1.5 s of audio is
buffered ahead of playback. Reset the Phonon flow cache to the voice prompt
before every chunk (`model._slice_flow_cache(state, base_frames)`; the stock
`generate_audio_stream` does not, so text leaks between calls). Trim leading
silence of the first chunk. Send audio in ~80-160 ms pieces with a monotonically
increasing segment id per text chunk.

## Phone (`rose/phone.py`)

Port `demo/phone.py` (keep its behaviour and data) and add:
- `notify(app, title, text)` -> appends an incoming message to the thread when
  app == "Messages", shows a banner, calls listeners with `("notification", {...})`.
- `listeners`: callables `(what: str, info: dict)`.
- `ui_tree()` compact text: one line per element `[id] kind "label" value=...`.
- `snapshot()` dict for the dashboard (route, toast, banner, last_tap box).
- `summary()` (as in demo) for the talker's context.

## Tasks (`rose/tasks.py`)

```python
@dataclass
class Task:
    id: int; kind: str ("phone" | "think"); goal: str
    status: str ("running" | "done" | "failed" | "cancelled")
    steps: list[dict]; result: str; started: float; finished: float | None
    def to_dict(self) -> dict

class TaskManager:
    def __init__(self, runtime: Runtime, brain: Brain, phone: Phone, bus: Bus,
                 on_done: Callable[[Task], None]): ...
    def prepare(self) -> None                 # pre-fill the phone agent's system prompt sequence once (runtime thread)
    def start_phone(self, goal: str) -> Task  # forks the prepared sequence; job at BACKGROUND priority
    def start_think(self, question: str, context: str) -> Task   # thinking on, budget ~1200 tokens
    def update(self, task_id: int, instruction: str) -> bool     # appended before its next step
    def cancel(self, task_id: int) -> bool
    def running(self) -> list[Task]
    def describe(self) -> str                 # one line per task for the talker context
```

Phone agent: observes `phone.ui_tree()` (text), acts with tools
`open_app(app)`, `tap(id)`, `type_text(id, text)`, `back()`, `home()`,
`finish(summary, success)`; may issue several calls in one step (executed in
order, ~250 ms apart so the dashboard animates); loop detection; max 10 steps;
summary is one short sentence Rose can say. Greedy decoding. Measure and
report per-step latency in `steps[i]["ms"]`.

## App (`rose/app.py`)

`python -m rose.app [--port 8091] [--voice zoey] [--no-browser-audio] [--local-audio]`.
Routes: `/` (web/index.html), `/audio.js`, `/phone.png`, `/ws/audio` (binary
mic in / speech out, see audio.js), `/ws/events` (bus events as JSON, history
first), `POST /api/say` {text} (synthesize with macOS `say` and inject as mic
audio), `POST /api/notify` {app,title,text}, `POST /api/reset`,
`POST /api/voice` {voice}, `GET /api/state`.
`--local-audio` uses sounddevice mic/speaker instead of the browser (headphones).

## Conductor (`rose/conductor.py`) - written by the lead

```python
class Conductor:
    def __init__(self, runtime, brain, listener, speaker, tasks, phone, bus): ...
    def start(self) -> None
    def on_listen(self, ev: ListenEvent) -> None
    def on_task_done(self, task: Task) -> None
    def on_phone(self, what: str, info: dict) -> None
    def inject_text_turn(self, text: str) -> None   # typed input path for tests
```
