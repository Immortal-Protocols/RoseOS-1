"""Runtime patches that make mlx-vlm's VoiceChat loop fit in an 80 ms frame.

The EAR-TTS step is launch-bound: per frame it does 8 refinement iterations,
each summing 31 codebook embeddings and re-quantizing a residual, all as tiny
separate kernels. ``mx.compile`` fuses those deterministic pieces. Random
sampling stays outside compiled code, so outputs match the unpatched model up
to floating-point reassociation.
"""

from __future__ import annotations

import mlx.core as mx
from mlx_vlm.models.nemotron_voicechat import tts as tts_mod

_applied = False


@mx.compile
def _depthsum(rvq_embs: mx.array, code: mx.array) -> mx.array:
    num_quantizers, _, latent = rvq_embs.shape
    padding = mx.zeros((num_quantizers, 1, latent), dtype=rvq_embs.dtype)
    embeddings = mx.concatenate([rvq_embs, padding], axis=1)
    result = mx.zeros((*code.shape[:-1], latent))
    for idx in range(num_quantizers):
        result = result + embeddings[idx, code[..., idx]]
    return result


def _make_rvq_encode(start: int, count: int):
    @mx.compile
    def encode(rvq_embs: mx.array, residual: mx.array, code: mx.array) -> mx.array:
        pieces = [code[..., i] for i in range(rvq_embs.shape[0])]
        for idx in range(start, start + count):
            embedding = rvq_embs[idx]
            distances = mx.sum(embedding * embedding, axis=-1) - 2.0 * mx.matmul(
                residual, embedding.T
            )
            selected = mx.argmin(distances, axis=-1)
            residual = residual - embedding[selected]
            pieces[idx] = selected
        return mx.stack(pieces, axis=-1)

    return encode


_rvq_cache: dict[tuple[int, int], object] = {}


def _depthsum_embedding(self, code: mx.array) -> mx.array:
    return _depthsum(self.rvq_embs, code)


def _rvq_encode_step(self, residual, code, start, count):
    fn = _rvq_cache.get((start, count))
    if fn is None:
        fn = _rvq_cache[(start, count)] = _make_rvq_encode(start, count)
    return fn(self.rvq_embs, residual, code)


def _mog_sample(head, x, uniform, guidance_scale, top_p):
    """``MoGHead.infer`` with the Gumbel noise passed in instead of drawn."""

    for layer in head.mlp_stack:
        x = layer(x)
    if guidance_scale > 0.0:
        half = x.shape[0] // 2
        cond, uncond = x[:half], x[half:]
        x = cond + guidance_scale * (cond - uncond)
    b, t, _ = x.shape
    logits = tts_mod._top_p_logits(head.proj_logits(x), top_p)
    gumbel = -mx.log(-mx.log(uniform + 1e-8) + 1e-8)
    component = mx.argmax(
        mx.log(mx.softmax(logits.astype(mx.float32), axis=-1)) + gumbel, axis=-1
    )
    flat_x = x.reshape(-1, x.shape[-1])
    flat_component = component.reshape(-1)
    mus = head.proj_mus.weight.reshape(head.num_predictions, head.low_rank, -1)[
        flat_component
    ]
    mu = mx.matmul(mus, flat_x[..., None]).squeeze(-1)
    low_mat = head.low_mat[flat_component]
    mu = mx.matmul(low_mat, mu[..., None]).squeeze(-1).reshape(b, t, head.out_size)
    residual = head.proj_else(x)
    logs = mx.maximum(head.proj_logs(x), head.min_log_std)
    return mu * mx.exp(logs) + residual, logs


def _code_schedule(config) -> list[int]:
    import math

    rates = [i / config.num_iterations for i in range(config.num_iterations)]
    masked = [
        math.ceil(((1.0 - r**config.exponent) ** (1.0 / config.exponent)) * config.num_quantizers)
        for r in rates
    ]
    return [masked[i] - (masked[i + 1] if i + 1 < len(masked) else 0) for i in range(len(masked))]


def _generate_codes(self, hidden_states: mx.array) -> mx.array:
    """Same algorithm as ``RVQEARTTSModel.generate_codes`` as one compiled graph."""

    config = self.config
    fn = getattr(self, "_compiled_generate", None)
    if fn is None:
        counts = [c for c in _code_schedule(config) if c]

        def run(rvq_embs, hidden, uniforms, normals):
            half = hidden.shape[0] // 2
            conditional, unconditional = hidden[:half], hidden[half:]
            code = mx.full(
                (*conditional.shape[:2], config.num_quantizers),
                config.codebook_size,
                dtype=mx.int32,
            )
            completed = 0
            for i, count in enumerate(counts):
                embedded = self.embed_code(_depthsum(rvq_embs, code))
                mog_input = mx.concatenate(
                    [embedded + conditional, embedded + unconditional], axis=0
                )
                mu, logs = _mog_sample(
                    self.mog_head, mog_input, uniforms[i], config.guidance_scale, config.top_p
                )
                residual = mu + mx.exp(logs) * normals[i] * config.noise_scale
                code = _rvq_cache_fn(completed, count)(rvq_embs, residual, code)
                completed += count
            return code

        fn = self._compiled_generate = (mx.compile(run), len(counts))
    compiled, steps = fn
    half = hidden_states.shape[0] // 2
    b, t = hidden_states.shape[0] - half, hidden_states.shape[1]
    uniforms = mx.random.uniform(shape=(steps, b, t, self.mog_head.num_predictions))
    normals = mx.random.normal((steps, b, t, self.mog_head.out_size))
    return compiled(self.rvq_embs, hidden_states, uniforms, normals)


def _rvq_cache_fn(start: int, count: int):
    fn = _rvq_cache.get((start, count))
    if fn is None:
        fn = _rvq_cache[(start, count)] = _make_rvq_encode(start, count)
    return fn


def quantize_speech_path(model, *, tts: bool = True, perception: bool = False) -> None:
    """8-bit quantize the bf16 speech-path transformers (mlx-community leaves them bf16).

    Their activations run in float32, so every bf16 matmul upcasts weights;
    the quantized kernels avoid that and roughly halve the TTS backbone.
    """

    import mlx.nn as nn

    if tts:
        nn.quantize(model.tts_model.tts_model.backbone, group_size=64, bits=8)
    if perception:
        nn.quantize(model.stt_model.perception.encoder, group_size=64, bits=8)


_silent = {"enabled": False, "ids": (), "codes": None, "run": 0, "after": 8,
           "quiet": 0, "quiet_after": 6, "rms": 0.003, "skipped": 0}


def skip_silent_tts(model, *, after_frames: int = 20, quiet_frames: int = 12) -> None:
    """Skip the TTS step while the agent is silent; emit codec silence instead.

    The TTS backbone runs every 80 ms frame (~13 ms) even when the agent has
    nothing to say. The step is replaced by exact silence codes only once the
    agent's text has been pad for ``after_frames`` frames *and* the generated
    audio has been quiet for ``quiet_frames`` frames: the audio lags the text
    by a second or more, so text alone would cut off the end of a sentence.
    Speech after a skipped stretch starts from silence codes, as it does after
    an end-of-turn. This frees GPU time for the brain.
    """

    cfg = model.config
    _silent.update(
        enabled=True,
        ids=(cfg.pad_token_id, cfg.silence_token_id),
        codes=model.tts_model.codec_silence_tokens[None, None, :],
        after=after_frames,
        quiet_after=quiet_frames,
    )
    if getattr(tts_mod.RVQEARTTSModel, "_skip_patched", False):
        return
    original = tts_mod.RVQEARTTSModel.step
    codec_cls = type(model.tts_model.audio_codec)
    original_decode = codec_cls.decode_step

    def step(self, code, subword_ids, subword_mask, cache, *, guidance=True):
        if _silent["enabled"] and subword_ids.shape == (1, 1):
            if int(subword_ids[0, 0]) in _silent["ids"]:
                _silent["run"] += 1
                if _silent["run"] > _silent["after"] and _silent["quiet"] >= _silent["quiet_after"]:
                    _silent["skipped"] += 1
                    return tts_mod.TTSStepOutput(codes=_silent["codes"], cache=cache, hidden_states=None)
            else:
                _silent["run"] = 0
        return original(self, code, subword_ids, subword_mask, cache, guidance=guidance)

    def decode_step(self, *args, **kwargs):
        samples = original_decode(self, *args, **kwargs)
        if _silent["enabled"]:
            rms = float(mx.sqrt(mx.mean(samples.astype(mx.float32) ** 2)))
            _silent["quiet"] = _silent["quiet"] + 1 if rms < _silent["rms"] else 0
        return samples

    tts_mod.RVQEARTTSModel.step = step
    codec_cls.decode_step = decode_step
    tts_mod.RVQEARTTSModel._skip_patched = True


def apply() -> None:
    global _applied
    if _applied:
        return
    tts_mod.RVQEARTTSModel.depthsum_embedding = _depthsum_embedding
    tts_mod.RVQEARTTSModel._rvq_encode_step = _rvq_encode_step
    tts_mod.RVQEARTTSModel.generate_codes = _generate_codes
    _applied = True
