# Historical candidate metadata (2026-11-13)

Retained with acquisition-manifest.json from the previous source-only session. These statements describe
that session at that time, not the authorized Mac runtime implementation. See ../README.md for current state.

## Candidate matrix

Metadata from the official Hugging Face model API on 2026-11-13 (revision = commit SHA at that time). Byte counts
are the published files; none were downloaded. **Size is not a performance result**: no candidate has been run.

| candidate | licence / access | pinned revision | runtime file (bytes) | KV cache f16 per token | at 1024 / 2048 ctx |
|---|---|---|---|---|---|
| Qwen3-0.6B | Apache-2.0, ungated | `Qwen/Qwen3-0.6B-GGUF@23749fef` | Q8_0 GGUF 639,446,688 | 112 KiB (28 L × 8 KV × 128) | 112 / 224 MiB |
| Qwen3-1.7B | Apache-2.0, ungated | `Qwen/Qwen3-1.7B-GGUF@90862c4b` | Q8_0 GGUF 1,834,426,016 | 112 KiB (28 L × 8 KV × 128) | 112 / 224 MiB |
| SmolLM2-1.7B-Instruct | Apache-2.0, ungated | `HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF@2d4a76a3` | Q4_K_M GGUF 1,055,609,536 | 192 KiB (24 L × 32 KV × 64) | 192 / 384 MiB |
| Gemma 3 1B IT | Gemma Terms of Use (not OSI); Google repos gated | `ggml-org/gemma-3-1b-it-GGUF@f9c28bcd` (ungated mirror) | Q4_K_M GGUF 806,058,240 | not verified (config gated) | not verified |
| Gemma 3 1B IT (LiteRT) | Gemma terms; gated (auto) | `litert-community/Gemma3-1B-IT@a9bd8564` | q4 ekv4096 .litertlm 584,417,280 | runtime-managed | n/a |
| Llama 3.2 1B Instruct | Llama 3.2 Community Licence; gated (manual) | `meta-llama/Llama-3.2-1B-Instruct@92131767` | safetensors 2,471,645,608 (no GGUF checked) | not verified (config gated) | not verified |

Base-model revisions: `Qwen/Qwen3-0.6B@c1899de2`, `Qwen/Qwen3-1.7B@70d244cc`,
`HuggingFaceTB/SmolLM2-1.7B-Instruct@31b70e2e`, `google/gemma-3-1b-it@dcc83ea8`. The official Qwen GGUF repos
publish Q8_0 only; a 4-bit Qwen file would be a local requantization (or a third-party file) and needs its own pin.
Qwen3's thinking mode must be off (`enable_thinking=false`); the grammar forces JSON from the first output token.

Budgets for one request: about 150 tokens of instructions + up to about 550 tokens of context + at most about 290
output tokens, so a 1024-token context is enough. With Qwen3-0.6B Q8_0 that is about 610 MiB of weights plus 112
MiB of KV cache before runtime overhead; measured peak memory replaces this estimate.

**Proposed first pair to measure**, once a route exists: Qwen3-0.6B (smallest, Apache-2.0, ungated, GGUF + GBNF)
against Gemma 3 1B (strong small instruction model, if its licence is acceptable to Nathan) or Qwen3-1.7B. Both on
llama.cpp CPU with the same grammar and cases first, so the runtime is held constant. No fine-tuning is proposed
until at least two candidates have device numbers and validity/usefulness on the same cases.
