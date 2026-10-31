# Earlier design: full-duplex voice model (`demo/`)

This was the first prototype, kept for comparison. Rose v3 (`rose/`, see
README.md) replaced it. Run it with `bash scripts/setup.sh --duplex` once,
then `bash scripts/run.sh`.

A local proof of concept for the voice → orchestrator → brain architecture.
Everything runs on one Apple Silicon Mac; the phone is a fake rendered in the
browser.

```
Mic/speakers ⇄ Nemotron VoiceChat 11B (MLX, full duplex, 80 ms frames)
                    │  Design A: its own tool calls  ask_brain / do_on_phone
                    │  Design B: brain classifies each user turn chat / think / act
                    ▼
               Orchestrator (demo/orchestrator.py, stands in for the on-phone one)
                    │  OpenAI-style /v1/chat/completions
                    ▼
               Brain: Qwen3.6-35B-A3B, 4-bit GGUF + vision, llama-server :8081
                    │  screenshot + UI tree in, tap/type/open_app out
                    ▼
               Fake phone (demo/phone.py): Messages, Clock, Calendar, Settings, Contacts
```

## Run

```bash
bash scripts/setup.sh --duplex   # once: venv + models (needs `brew install llama.cpp`)
bash scripts/run.sh       # brain + demo; dashboard at http://127.0.0.1:8090
```

Wear headphones: the voice model listens while it talks and there is no echo
cancellation. Options: `--design B`, `--no-mic` (feed silence and use the
"Speak" box or the example chips, which synthesize speech with macOS `say`),
`--no-speaker`, `--input-device`, `--output-device`. The dashboard switches
designs live (each switch re-prefills the voice prompt, about 8 s).

## How the hand-off works

NVIDIA's NeMo reference writes tool calls on a separate function channel and
re-runs the whole clip offline with the tool response inserted. The MLX port in
`mlx-vlm` streams the function channel but has no way to return a result, so
`demo/voice_engine.py` adds it, following the NeMo training layout:

- After `<SPECIAL_21>` (end of call) the timeline freezes, the tool runs, then
  `<TOOL_RESPONSE>[...]</TOOL_RESPONSE>` is written onto the function channel as
  extra steps with silent user audio and no speech. The model then writes the
  end-of-response marker `<SPECIAL_22>` itself, which is a good sign the format
  matches training, and speaks about the result.
- The inserted steps are known in advance, so they run as one batched forward
  pass (37 tokens: 3.0 s one-by-one, 0.4–0.8 s batched; logits match within 0.01).
- While a call is being written the agent's speech channel is muted (NeMo
  splices that stretch out offline; live it otherwise leaks fragments).
- Only the first call is kept: the model tends to append an unrequested second
  call, so the list is closed as soon as the first object completes.
- Design B uses the same channel: the orchestrator writes a whole call +
  response the voice model never made (`inject_tool_exchange`), with the
  model's own function channel forced to pad.

## Findings (M4 Max, 128 GB, 2026-10-30)

Voice model speed, mean compute per 80 ms frame (below 80 = keeps up):

| Configuration | ms / frame | × real time |
|---|---|---|
| mlx-community 8-bit, stock mlx-vlm 0.7.11 | 87–92 | 1.09–1.15 (falls behind) |
| 4-bit LM, stock | 75 | 0.93 (drops words: "Didopuses") |
| 8-bit + speech path quantized + compiled RVQ loop (`demo/speedups.py`) | 55 | 0.69 |
| same, while the brain runs continuously | 106–138 | 1.32–1.72 |

The two models share one GPU. Running the brain at background priority
(`taskpolicy -b`, the default in `scripts/start_brain.sh`) brings contention
from 1.72× to 1.32×, but the voice still falls behind while the brain works;
the loop drops input to stay current, so audio can stutter during Design B jobs.

Other numbers: system prompt prefill 8 s (was 17–30 s before batching); brain
decider 0.6–1.7 s; phone tasks 3–7 agent steps at ~1.5 s each (5–10 s);
`ask_brain` with reasoning 6–15 s alone, 27 s under voice contention (without
reasoning ~1 s but it got the flour arithmetic wrong).

Behavior seen in the demo runs (simulated speech via `say`; the model's own ASR
was used to check what its audio actually said):

| Utterance | Design A | Design B |
|---|---|---|
| "Text Sam I'm running ten minutes late" | `do_on_phone` → sent → "I have texted Sam" | — |
| "Set an alarm for six thirty tomorrow" | `do_on_phone`, correct request | — |
| "How's it going?" | no tool, natural reply | — |
| "1.5 cups for 4 people, how much for 7?" | **no tool; answered 1¾ (wrong)** | decider → think; voice first said 3¾ (wrong), then the brain's 2⅝ |
| "What's on my calendar this afternoon?" | — | decider → act; voice improvised "what is your phone number…", then read all three events correctly |

Takeaways:
- **Design A** gives clean turns but its quality is capped by the voice model's
  judgment about when to call a tool, and the conversation freezes during the
  tool (5–15 s here).
- **Design B** catches what A misses, but the voice model talks first, so it
  can say something wrong or invented before the right answer arrives. It also
  needs GPU headroom the Mac does not have while both models run.
- A hybrid (A's calls when the model makes them; B's decider as a safety net
  that can also tell the voice model to hold) looks like the next experiment.

## Running voice and brain at the same time (one GPU)

`scripts/bench_shared_gpu.py` runs both models in one process: the voice in
real time (speech every ~8 s, so it talks a lot), the brain looping on
requests. `demo/gpu_gate.py` makes them take turns: the voice holds the GPU
for each 80 ms frame, and a brain forward pass may only start if it fits
before the next frame (or after waiting 0.4 s). 75 s runs, M4 Max:

| Setup | Voice ms / 80 ms frame | Frames late | Brain decode | Brain time to first token |
|---|---|---|---|---|
| Voice alone | 57.5 | 0% | — | — |
| Brain alone (MLX, `mlx-community/Qwen3.6-35B-A3B-4bit`) | — | — | 88–109 tok/s | 0.4–0.7 s (screenshot, 363 tok) |
| Both, no gate | 115 | 96% (up to 686 ms) | 62 tok/s | 0.8 s |
| Both, gate, 4-token prompt chunks | 59.7 | 5% | 11 tok/s | 24 s |
| Both, gate + decode overlaps voice + skip silent TTS | 61–64 | 10–15% (mostly ≤ 190 ms) | **46–51 tok/s** | 21 s (screenshot), 27 s (text, 305 tok) |

What this shows:
- Separate processes cannot work: the GPU is time-sliced blindly.
- With the gate the voice stays real time. Single decode tokens can run
  *concurrently* with voice frames almost for free (the GPU has idle gaps in
  the voice's many small kernels), so the brain still writes at about half
  its solo speed.
- Prompt processing is the blocker: it cannot overlap the voice (tried: 98%
  of frames late), a 4-token chunk costs ~21 ms and the gap between voice
  frames is ~16–20 ms, so prompts crawl at ~10 tok/s.
- Remaining late frames come from units too big to split: the vision encoder
  (130–200 ms per screenshot) and starved prompt chunks.
- `skip_silent_tts` (in `demo/speedups.py`) skips the TTS while the agent has
  been silent for ~1 s of audio (0.74× → ~0.63× real time in quiet stretches;
  every test reply still transcribed in full). Shorter windows cut off the
  ends of sentences because the audio lags the text by a second or more.

Next levers, in order: cache the constant prompt prefix (system prompt, tool
list, phone data) so each request only processes its new tokens; shrink
screenshots or drive the phone from the UI tree alone; do big prompt work
while the voice is paused for a tool (Designs A/C already pause it); a
cheaper voice LM (4-bit dropped words; mixed-precision builds untested); and,
longer term, the Neural Engine, which neither model uses.

## Not verified

- The live microphone/speaker path was not exercised by the author of this
  prototype (all runs used `--no-mic` and synthesized speech). macOS will ask
  for microphone permission for the terminal you run it in.
- No physical phone, no on-device orchestrator. Numbers are from one machine
  and these package versions (`requirements.txt`); they say nothing about a phone SoC.

## Files

- `demo/voice_engine.py`: tool-capable streaming session (hand-off, muting, batching)
- `demo/speedups.py`: compiled RVQ code generation, 8-bit speech path
- `demo/voice_loop.py`: real-time loop, audio I/O, Design A/B logic
- `demo/orchestrator.py`, `demo/brain.py`: tool execution, decider, phone agent
- `demo/phone.py`: fake phone state, UI tree and renderer
- `demo/app.py`, `demo/web/index.html`: dashboard
- `scripts/spike_voice.py`, `scripts/bench_voice.py`: offline checks and timing
