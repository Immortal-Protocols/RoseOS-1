"""Bind the existing Linux listener/Phonon providers to Rose's shared voice pipeline.

Configuration names existing, pinned local assets. Loading never provisions or
downloads models and does not start the historical cloud serving stack.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys


def prepare():
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'workspace/models/rose_cloud'))
    from rose_cloud.nomlx import install
    install()


def voices(path) -> set[str]:
    """The voices this backend's configuration gives files for (the only ones it can speak)."""
    return set(json.loads(Path(path).read_text())['speech']['voices'])


def load(path, runtime, listener, speaker, voice):
    from rose_cloud import listen, speech
    from rose_cloud.phonon import PhononProvider
    from .listener import SileroCPU
    from . import speaker as speaker_module

    config = json.loads(Path(path).read_text())
    ears = config['listener']
    listen.bind(listener, vad=SileroCPU(listen.silero_model(ears['vad'])),
                turn=listen.SmartTurn(ears['turn'], runtime=runtime),
                final=listen.Parakeet(ears['final'], runtime=runtime, device=ears.get('device', 'cuda')),
                stream=listen.NemotronStreaming(ears['stream'], device=ears.get('device', 'cuda')))
    # Only the configured voices exist here: matching and /api/voice offer no other (Nov 9 plan 1D).
    speaker.voices = {name: vid for name, vid in speaker.voices.items() if name in config['speech']['voices']}
    speech.bind(speaker_module, speaker,
                PhononProvider(config['speech']['model'], config['speech']['voices'], runtime=runtime), voice)
