"""What the cloud host must match, pinned: PR288's revision, its v1 phone wire contract and its selected models.

The cloud host does not reimplement the phone link. PR288's ``rose/remote.py`` (pairing, pinned TLS certificate,
signed requests, ``/hello``, ``/ws/audio``, ``/ws/events``, ``/ws/phone``) has no MLX dependency, so the cloud host
runs it unchanged; ``check_remote`` verifies that the module in a given PR288 checkout still has the constants and
signed string pinned here, so drift is caught instead of forked. The Android client is never touched.

Model identity: PR288 runs ``mlx-community/Qwen3.6-35B-A3B-4bit``, an MLX 4-bit conversion of
``Qwen/Qwen3.6-35B-A3B``. Two A100 40 GB cannot hold the BF16 weights (71.9 GB) with a KV cache, so the cloud
brain is Qwen's own FP8 release of the same model. That is a different quantization of the same model, stated
here, not a substitute model; outputs are not bit-identical to the Mac's.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

PR288_HEAD = 'e1b702ee3b8a6bf656b5cc59bd765787622ae543'

# v1 remote link (PR288 prototypes/voice-phone-demo/REMOTE.md and rose/remote.py at PR288_HEAD)
WIRE = {
    'PROTOCOL_VERSION': 1,
    'DEFAULT_REMOTE_PORT': 8443,
    'AUTH_PREFIX': 'rose-auth-v1',
    'CLOCK_SKEW_S': 120,
    'NONCE_TTL_S': 240,
    'INVITE_TTL_S': 600,
    'MAX_PAIR_ATTEMPTS': 5,
    'PAIR_BODY_MAX': 4096,
    'REVOKED_CLOSE': 4401,
    'RETRY_STATUS': 409,
}
SIGNED_HEADERS = ('X-Rose-Device', 'X-Rose-Time', 'X-Rose-Nonce', 'X-Rose-Signature')
REMOTE_ROUTES = ('POST /pair', 'GET /hello', '/ws/audio', '/ws/events', '/ws/phone')

# Selected models (PR288 rose/brain.py, listener.py, speaker.py at PR288_HEAD) and the cloud brain.
MAC_BRAIN = 'mlx-community/Qwen3.6-35B-A3B-4bit'
BRAIN_BASE = ('Qwen/Qwen3.6-35B-A3B', '995ad96eacd98c81ed38be0c5b274b04031597b0')  # BF16, 71.9 GB of weights
CLOUD_BRAIN = ('Qwen/Qwen3.6-35B-A3B-FP8', '95a723d08a9490559dae23d0cff1d9466213d989')  # 37.5 GB, apache-2.0
CLOUD_BRAIN_ARCH = ('Qwen3_5MoeForConditionalGeneration', 'qwen3_5_moe')
MAC_AUDIO_MODELS = {
    'vad': 'mlx-community/silero-vad',
    'turn': 'mlx-community/smart-turn-v3',
    'stream_asr': 'mlx-community/nemotron-3.5-asr-streaming-0.6b',
    'final_asr': 'mlx-community/parakeet-tdt-0.6b-v3',
    'tts': 'gradium/phonon-31m-en',
}

# What this slice provides for each part of PR288's system. Nothing here is "parity" unless it says so.
CAPABILITIES = {
    'phone_link': 'reuse: PR288 rose/remote.py unchanged (no MLX import); constants checked by check_remote',
    'brain_text': 'real adapter: vllm_brain.VllmBrain over vLLM /v1/completions with token-id prompts',
    'brain_marks': 'exact on tokens: mark/rewind/fork are token-list operations; server prefix caching is speed only',
    'brain_prefill': 'deferred: no server work until generate (same tokens, later cost); not the Mac latency profile',
    'brain_vision': 'unsupported: append_image raises Unsupported (talker look and phone-agent screenshots)',
    'brain_logits': 'unsupported: no next-token logits are kept (unused by conductor/tasks at PR288_HEAD)',
    'runtime': 'not provided yet: PR288 rose/runtime.py imports mlx (stream setup only); Linux port pending',
    'listener': 'unsupported: VAD/smart-turn/streaming ASR/final ASR are MLX models; no Linux provider yet',
    'speaker': 'unsupported: Phonon TTS runs through mlx-audio; no Linux provider yet',
}


def check_remote(pr288_dir: str | Path) -> dict:
    """Import PR288's rose.remote from ``pr288_dir`` (prototypes/voice-phone-demo) and compare it with WIRE.

    Returns {'module': path, 'mismatches': {name: (pinned, actual)}, 'canonical': bool}. Needs PR288's own
    dependencies (fastapi, cryptography) importable; it never starts a listener."""
    root = str(Path(pr288_dir).resolve())
    sys.path.insert(0, root)
    try:
        remote = importlib.import_module('rose.remote')
    finally:
        sys.path.remove(root)
    mismatches = {name: (value, getattr(remote, name, None)) for name, value in WIRE.items()
                  if getattr(remote, name, None) != value}
    expected = '\n'.join(('rose-auth-v1', 'GET', '/hello', '1700000000000', 'nonce', 'pin')).encode()
    canonical = remote.canonical_message('GET', '/hello', '1700000000000', 'nonce', 'pin') == expected
    return {'module': remote.__file__, 'mismatches': mismatches, 'canonical': canonical}
