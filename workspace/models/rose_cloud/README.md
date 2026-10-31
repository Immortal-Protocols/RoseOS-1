# Rose on the cloud VM: compatibility slice (MODELS-ROSE-CLOUD-001)

The goal is to run the same Rose system and client as PR288 on the Linux VM with two A100 40 GB GPUs. PR288 is
pinned at `e1b702ee3b8a6bf656b5cc59bd765787622ae543`. This directory holds only the part that has to change for
Linux: the model runtime.

The rest is unchanged. That covers:

- the phone client;
- the v1 pairing, signature, audio, events and phone wire contract;
- the conductor and task code.

None of it is forked. Cloud success is not a prerequisite for the laptop path.

## What is real, what is a fixture, what is missing

| Part of PR288 | Status here |
|---|---|
| Phone link: `rose/remote.py` (pairing, pinned TLS, signed requests, `/hello`, `/ws/*`) | **Reused unchanged.** It has no MLX import. `tests/test_contract.py` runs PR288's own module on Linux: a pair, a signed `/hello` and a replay refusal, with a software P-256 key standing in for the phone's Keystore key. `contract.check_remote` fails on any drift in the pinned constants or the signed string. |
| Brain, text (`Brain`/`Sequence`: `encode`, `system_prefix`, `mark`/`rewind`/`fork`, `prefill`, `generate`) | **Real adapter**, `rose_cloud/vllm_brain.py`. It sends token-id prompts to vLLM `/v1/completions` with `stream` and `return_token_ids`; older servers fall back to `logprobs` with `return_tokens_as_token_ids`. Marks, rewinds and forks are exact, because they are operations on the token list. The stop token is yielded and kept in the token list. Closing the generator aborts the request, and only yielded tokens are kept. |
| Brain, latency profile | **Different.** Prefill is deferred to `generate`, and vLLM's prefix caching is the only reuse. Every `generate` is one request carrying the whole prompt. PR288's phone-agent `_Reply` makes about 7 requests per tap. |
| Brain, sampling | The server samples with the same parameters, so outputs are not bit-identical to the Mac. If the server stops on a token the caller did not request (vLLM merges the model's default stop ids into every request), the adapter raises `Unsupported` rather than approximating. |
| Brain, vision (`append_image`: talker look, phone-agent screenshots) | **Unsupported.** The adapter raises `Unsupported`. Token-id completions cannot place image embeddings in the prompt. |
| Scheduler (`rose/runtime.py`) and task manager (`rose/tasks.py`) | **PR288's own code, unchanged**, loaded through `rose_cloud/nomlx.py`. That module stands in for `mlx.core`'s stream and memory setup; any other MLX use raises. `tests/test_pr288_tasks.py` runs a deep-think task end to end through the adapter. |
| Listener (Silero VAD, smart-turn, Nemotron streaming ASR, Parakeet final ASR) | **Unsupported.** These are MLX models with no Linux provider yet. |
| Speaker (Phonon 31M through mlx-audio) | **Unsupported.** It is MLX-only, with no Linux provider yet. |
| Conductor, audio, interruption, turn ordering on a real phone | **Not run.** These need the listener and speaker. |

All brain tests run against `tests/fakes.py`, which provides a stand-in tokenizer and a stand-in vLLM streaming
server. They exercise the adapter's protocol handling, not a model. No real model response has been produced
through this code yet; that needs the runtime step below.

## Selected models

| | Model | Revision |
|---|---|---|
| PR288 on the Mac | `mlx-community/Qwen3.6-35B-A3B-4bit` (MLX 4-bit) | — |
| Its base model | `Qwen/Qwen3.6-35B-A3B` (BF16, 71.9 GB of weights) | `995ad96eacd98c81ed38be0c5b274b04031597b0` |
| Cloud | `Qwen/Qwen3.6-35B-A3B-FP8` (Qwen's own FP8 release of the same model, 37.5 GB, Apache-2.0, not gated) | `95a723d08a9490559dae23d0cff1d9466213d989` |

Notes on the choice:

- **Why not BF16:** the BF16 weights do not fit two 40 GB GPUs with a KV cache.
- **Not a substitute:** the cloud model is the same model at a different quantization. It uses Qwen's chat template
  and special tokens, which the conductor's prompt segments require.
- **No fallbacks:** the Muse Glimmer service and the existing `serving/` tree are not used. The tokenizer must be
  the model's own; `VllmBrain` refuses a tokenizer without Qwen's special tokens.
- **Unverified:** support for this architecture (`Qwen3_5MoeForConditionalGeneration`, `qwen3_5_moe`) and for FP8
  MoE on A100 in the VM's pinned vLLM image. That image's vLLM version is unknown.

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

The tests use only the standard library. `test_contract.py`'s PR288 check and `test_pr288_tasks.py` also need:

- `ROSE_PR288_DIR=prototypes/voice-phone-demo` (absolute path). PR288 merged into main as `39b57db` with these
  paths identical to the pin;
- PR288's dependencies: fastapi, cryptography, pillow and numpy.

Without these, those tests are skipped and the skip reason is printed.

## Runtime plan (not authorized here)

One bounded bring-up would turn the adapter tests into a real model response. Every step is for the original Mac
operator only, after a separate approval.

1. **Check the image.** In the VM's pinned image, with `--pull=never` and `--network none`, read
   `vllm.__version__`. Stop if `Qwen3_5MoeForConditionalGeneration` is not in vLLM's supported architectures.
2. **Fetch the weights.** Download `Qwen/Qwen3.6-35B-A3B-FP8` at `95a723d0…` (37.5 GB) into
   `/mnt/models-data/hf`. The Muse Glimmer inference hold stays in place.
3. **Start the server once,** loopback only:

   ```bash
   vllm serve Qwen/Qwen3.6-35B-A3B-FP8 --revision 95a723d08a9490559dae23d0cff1d9466213d989 --served-model-name rose-brain --host 127.0.0.1 --port 8000 --tensor-parallel-size 2 --max-model-len 8192
   ```

   Do not add `--trust-remote-code`.
4. **Run the smoke test:**

   ```bash
   python3 -m rose_cloud.smoke --base-url http://127.0.0.1:8000 --model rose-brain --tokenizer-dir <snapshot dir>
   ```

   It prints one JSON line.
5. **Stop** the server.

**Expected evidence:**

- a reply that ends with `<|im_end|>`;
- `rewindExact: true`;
- first-token and total milliseconds.

The phone path stays out of this plan: it needs Linux ASR/TTS providers and a reachable endpoint. Both are later,
separately approved work.
