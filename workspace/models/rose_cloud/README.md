# Rose on the cloud VM: compatibility slices (MODELS-ROSE-CLOUD-001, -002)

The goal is to run the same Rose system and client as PR288 on the Linux VM with two A100 40 GB GPUs. PR288 is
pinned at `e1b702ee3b8a6bf656b5cc59bd765787622ae543` and was merged into main as `39b57db` with these paths
unchanged. This directory holds only what has to change for Linux: the model runtime.

The rest is unchanged:

- the phone client;
- the v1 pairing, signature, audio, events and phone wire contract;
- PR288's scheduler, conductor, task, speaker and transport code.

None of it is forked. Cloud success is not a prerequisite for the laptop path.

## What is real, what is a fixture, what is missing

| Part of PR288 | Status here |
|---|---|
| Phone link: `rose/remote.py` (pairing, pinned TLS, signed requests, `/hello`, `/ws/*`) | **Reused unchanged** (no MLX import). `tests/test_contract.py` runs PR288's own module on Linux: a pair, a signed `/hello`, and a replay refused. `contract.check_remote` fails on any drift in the pinned constants or the signed string. |
| Scheduler (`rose/runtime.py`) | **Reused unchanged** through `nomlx.py`. Waits for the network and for synthesis run on worker threads (`pump.py`). While a job waits, it runs only strictly higher-priority jobs (`coop.py`), so the listener and speaker keep running while the talker waits for vLLM. Consumers still receive only tokens and audio frames. `tests/test_coop.py` runs this on PR288's own `Runtime` with delayed headers and a mid-stream stall. |
| Brain, text (`Brain`/`Sequence`) | **Real adapter**, `vllm_brain.py`. Token-id prompts to vLLM `/v1/completions`, streaming token ids. Marks, rewinds and forks are exact token-list operations. Cancelling (the `cancel` callable or the runtime job) shuts the socket down at once. vLLM then aborts the request, and no token arrives later. |
| Brain, latency and sampling | **Different from the Mac.** Prefill is deferred to `generate`, and every `generate` is one request carrying the whole prompt. PR288's phone-agent `_Reply` makes about 7 requests per tap. The server samples. A server stop the caller did not request raises `Unsupported`. |
| Brain, vision (`append_image`) | **Unsupported**; it raises `Unsupported`. |
| Speaker: `rose/speaker.py` `Speaker`/`Utterance` and `rose/transport.py` `BrowserTransport` (`/ws/audio`) | **Reused unchanged.** That keeps chunking, segment ids, uint32 segment + int16 24 kHz frames, played/stopped ACK accounting, stop/duck/pause and at most about 1.5 s ahead of playback. `phonon.py` supplies the model object `Speaker` calls, on the official `ptts` runtime. **FIXTURE:** the tests inject a stand-in engine (`tests/fake_ptts.py`, a tone per word), and the far end of the wire is PR288's `FakeTransport` player (browser-worklet semantics), not the Android player. No real Phonon synthesis has run here. |
| Listener (Silero VAD, smart-turn, Nemotron, Parakeet) | **Unsupported.** These are MLX models with no Linux provider yet. |
| Conductor and audio with a real phone | **Not run.** This needs the listener, a real brain and a reachable endpoint. |

## Selected models and runtimes (proposals, not runtime grants)

| Role | PR288 on the Mac | Cloud proposal | Pin |
|---|---|---|---|
| Brain | `mlx-community/Qwen3.6-35B-A3B-4bit` (MLX) | `Qwen/Qwen3.6-35B-A3B-FP8` (Qwen's FP8 release of the same model) | `95a723d08a9490559dae23d0cff1d9466213d989` |
| Speech | `gradium/phonon-31m-en` via mlx-audio | the same model via `ptts` | `92741c5b07d085ff840e26fea8ce43110b87852f` |
| Voices | `gradium/phonon-voices` (9 PR288 voices) | the same files | `0ee17205f254b5abc43b266c8a0f215565c352f7` |

**Brain prerequisites** (`contract.BRAIN_PREREQUISITES`). None of these is verified for the VM's pinned image,
whose vLLM version is unknown:

- **Backend:** vLLM ≥ 0.19.0, as the model card recommends.
- **Architecture:** `Qwen3_5MoeForConditionalGeneration` must be registered.
- **Quantization:** fine-grained FP8 with block size 128. On A100, which has no FP8 tensor cores, this needs vLLM's
  weight-only Marlin path for both dense and MoE expert layers. General A100 FP8 (W8A16 Marlin) support does not
  prove this MoE kernel path on the pinned image.
- **API:** `/v1/completions` with a token-id `prompt` and the fields `vllm_brain.py` sends, listed in the contract.
- **Memory:** about 18.7 GB of weights per GPU at tensor parallel 2. Headroom is not measured.
- **BF16:** expected not to fit, from file size alone. This is not measured.

**Speech prerequisites:**

- **Runtime:** the `ptts` package. Neither its version nor its install is checked here. Its documented Linux wheel
  needs x86-64-v3 (AVX2/FMA/F16C).
- **Files:** the explicit local files below, checked against the pinned hashes before `ptts` sees them.
  - `model.safetensors` (sha256 `8a04cf83…`), or `model.q8.gguf` with `--quant q8`;
  - `config.json` and `tokenizer.json`;
  - the voice file, for example zoey: `voices/NbpkqMVS3CJeq2j8.safetensors` (sha256 `97c7f8c2…`).
- **Downloads:** none. Only a local model directory is passed. Voices are added with `add_voice` from the given
  files, so no catalog lookup happens.
- **Unverified:**
  - which `ptts` `quant` value selects the safetensors weights;
  - `ptts`'s chunk timing;
  - whether its audio matches mlx-audio's for the same weights and voice.

**None of these files or runtimes is present in this environment.** `phonon.check_inputs` names exactly what is
missing.

## Tests

```bash
python3 -I tests/test_vllm_brain.py
```

```bash
python3 -I tests/test_contract.py
```

```bash
python3 -I tests/test_pr288_tasks.py
```

```bash
python3 -I tests/test_coop.py
```

```bash
python3 -I tests/test_speech_wire.py
```

Most tests use only the standard library. The PR288 parts also need:

- `ROSE_PR288_DIR=<absolute path to prototypes/voice-phone-demo>`;
- PR288's dependencies: numpy, plus fastapi, cryptography and pillow for the remote check.

Without these they are skipped and print the reason. All brain and speech tests use stand-ins
(`tests/fakes.py`, `tests/fake_ptts.py`). They exercise protocol, scheduling and accounting, not a model or audio
quality.

## Runnable entry points (need real local inputs)

```bash
python3 -m rose_cloud.speech --pr288 <path to prototypes/voice-phone-demo> --model-dir <phonon-31m-en dir> --voice zoey=<NbpkqMVS3CJeq2j8.safetensors> --wav out.wav
```

This speaks through PR288's `Speaker` and `/ws/audio` to the simulated player and prints one JSON line. It exits 2
and names any missing or mismatched file.

```bash
python3 -m rose_cloud.smoke --base-url http://127.0.0.1:8000 --model rose-brain --tokenizer-dir <dir>
```

This makes one brain exchange against a running vLLM.

## Operator path (not authorized, not available today)

The VM has no guest shell for this, and nothing here assumes one. The only operator channel is the existing
bootstrap diagnostic: metadata plus one held reset, in `serving/service/bootstrap.py`, which this slice does not
own. A supported invocation would need, separately approved and owned:

1. **Staged files:** the pinned brain, Phonon and voice files on `/mnt/models-data`. That is a download, needing its
   own approval.
2. **A new once-per-id diagnostic mode** in bootstrap. It would run, in an image with vLLM ≥ 0.19 that registers
   `Qwen3_5MoeForConditionalGeneration` (offline, `--pull=never`), first a version and architecture check that
   stops early. Then one loopback vLLM start (TP 2, `--max-model-len 8192`, no `--trust-remote-code`) and
   `rose_cloud.smoke` / `rose_cloud.speech`. It would publish fixed JSON tokens to a guest attribute and remove its
   containers.
3. **Unchanged throughout:** the Muse inference hold and the original Mac operator as the only runtime operator.
