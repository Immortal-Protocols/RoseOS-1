# Local voice spike on a Mac

Handoff plan for an agent (for example Claude Code) running on the owner's Mac,
started from a RoseOS-1 checkout. Goal: prove that a real-time open-weights voice
model and a 30B "brain" can run together on one Apple Silicon Mac, and that the
voice model can hand work to the brain. Nothing here touches a phone, a cloud
account or a deployment.

## Target architecture (context)

```
Phone (later)  ⇄ audio ⇄  Voice model (real-time, listens while talking)
                              │ tool call: ask_brain(question) / do_on_phone(request)
                              ▼
                 Orchestrator (on the phone in the product; a local stand-in here)
                              │ OpenAI-style /v1/chat/completions
                              ▼
                          Brain (30B, ~3B active, 4-bit)
```

- Voice model, first choice: NVIDIA NemotronLabs VoiceChat 11B (OpenMDW 1.1
  license, English, has tool calling). Fallbacks: NVIDIA PersonaPlex 7B (no tool
  calls), Kyutai Moshi (no tool calls).
- Brain: a ~30B mixture-of-experts model with ~3B active parameters, 4-bit,
  served with an OpenAI-compatible API (`mlx_lm.server` or `llama-server`).
  Candidates: NVIDIA Nemotron 3 Nano 30B-A3B, Qwen 30B-A3B.
- Design A: the voice model decides when to hand off, via its own tool calls.
- Design B: a decider (the brain) reads a transcript of each user turn and
  chooses `chat`, `think` or `act`; the voice model never waits on a tool.

The Mac ports below are community projects. The commands in their READMEs change
often: read each project's current README and follow it, rather than trusting
command lines remembered from elsewhere.

## Ground rules

- Work in an isolated checkout or worktree on a new branch, for example
  `spike/local-voice-mac`. Do not change other branches.
- Put spike code in `prototypes/local-voice-spike/`. Never commit model weights,
  caches, recordings, logs with personal speech, tokens or keys.
- Install Python tools into a project virtual environment (`uv` or `venv`), not
  system-wide. Ask before installing anything system-wide (Homebrew packages,
  Xcode components).
- Bind every server to `127.0.0.1`. Do not open ports to the network.
- Do not run phone, ADB, provider or deployment scripts from this repo.
- Stop and report if free disk falls under 20 GB or memory pressure turns red.
- Record exact versions: macOS, chip, memory, each repo's commit or release, each
  model's Hugging Face repo id and revision, and the quantization used.

## Phase 0: inventory (15 min)

1. Record chip, memory, macOS version, free disk (`sysctl hw.memsize`,
   `sw_vers`, `system_profiler SPHardwareDataType`, `df -h ~`).
2. Pick a tier:
   - 16 GB: voice model only (4-bit). Skip Phase 2 or use a small brain.
   - 32 GB: voice 4-bit + brain 4-bit, tight. Close other apps.
   - 48 GB or more: voice 8-bit or bf16 + brain 4-bit.
3. Expect about 40–60 GB of downloads in total.

## Phase 1: voice model alone (half a day)

Try in order and stop at the first that works well:

1. **speech-swift** (github.com/soniqo/speech-swift, v0.0.25 or later): its
   local VoiceChat terminal demo (INT5 build).
2. **mlx-vlm server** with `mlx-community/NemotronLabs-VoiceChat-11B-bf16`
   (or its 8-bit / 4-bit variants). One model card says this exposes a
   full-duplex WebSocket endpoint; another mlx-community repo says the encoder and
   duplex loop are not implemented yet. Find out which is true.
3. **PersonaPlex 7B**: speech-swift, or `personaplex-mlx` / a 4-bit MLX build
   with a local web UI.
4. **Moshi**: Kyutai's official `moshi_mlx`.

For the one that works, measure and record:

- Time from end of user speech to start of reply (aim: under 1 s).
- Real-time factor or ms per step, and whether it ever falls behind.
- Peak memory.
- Interruption: talk over it mid-sentence. Does it stop and listen?
- Background noise: a TV or music at normal volume.
- Five-minute conversation: does quality hold past the ~2-minute audio window
  NVIDIA documents for VoiceChat?

## Phase 2: brain alongside it (half a day)

1. Serve the brain with `mlx_lm.server` (or `llama-server` with a GGUF build) on
   `127.0.0.1`.
2. Check the OpenAI-style API with a plain chat request, a tool-call request and
   a request whose output is forced to a JSON schema (an enum of
   `chat` / `think` / `act`). Note which server supports forced output.
3. With the voice model running at the same time, measure brain time to first
   token and tokens per second, and re-check the voice model's latency. The
   question is whether both fit on this Mac without the voice model stuttering.

## Phase 3: the handoff (1–2 days)

Write a small local orchestrator stand-in (Python or Node, in
`prototypes/local-voice-spike/`) that plays the phone's role:

1. Opens the voice session and streams the Mac's mic and speaker.
2. Registers two tools with the voice model, each with one plain-English field:
   - `ask_brain(question)`: calls the brain, returns a short ASCII answer.
   - `do_on_phone(request)`: **mocked**. Calls the brain to plan steps, prints
     them, returns a fake result such as `"Sent to Mom"`. No real phone.
3. Design A test: say ten scripted requests (five chit-chat, three thinking,
   two actions). Log whether the voice model called the right tool, the
   arguments it passed, and end-to-end latency.
4. Design B test: get a transcript of each user turn (from the voice model's
   stream if it provides one, otherwise a local streaming Whisper via MLX),
   send it to the brain with forced `chat` / `think` / `act` output, and start
   the handoff from the orchestrator. Log agreement with design A on the same
   ten requests.
5. Find out, and write down with evidence:
   - Can a client receive the voice model's tool calls and send results back?
   - Can a client inject a message the voice model didn't ask for (needed for
     design B and for long tasks that finish in the background)?
   - Can you interrupt the voice model while a tool call is pending?

If the working voice model has no tool calls (PersonaPlex, Moshi), skip step 3:
design B is the only option.

## Deliverable

`prototypes/local-voice-spike/REPORT.md` with:

- Machine, versions and model revisions (see ground rules).
- Which voice runtime worked, which didn't, and why.
- The measurements from Phases 1–3 in a table.
- Answers to the three questions in Phase 3 step 5.
- A recommendation: voice model, brain model, design A or B, and the minimum
  Mac memory to tell users.
- Exact setup steps that reproduce the working setup from a clean Mac.

Commit the report and spike code to the spike branch and push it. Do not open a
pull request unless the owner asks.

## Out of scope

Connecting a real phone, the Rust `rose-inference` gateway, the hosted GPU VM,
Android app changes, and coding workflows. Those come after this spike answers
whether the local setup is good enough.
