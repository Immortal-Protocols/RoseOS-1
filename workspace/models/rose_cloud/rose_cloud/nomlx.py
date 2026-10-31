"""Load PR288's own Rose modules on a host without MLX, unchanged.

PR288's ``rose/runtime.py`` (the cooperative priority scheduler) and ``rose/brain.py`` (prompt formats, Sampling,
tool-call parsing) import ``mlx.core`` at module level, but outside the MLX model code the scheduler only uses it to
pick a GPU stream and wire memory when its thread starts. ``install()`` registers a stand-in ``mlx.core`` whose
stream and memory calls do nothing and whose every other attribute raises ``MlxUnavailable``. So PR288's scheduler and
task manager run as written with ``VllmBrain`` as the brain (tests/test_pr288_tasks.py), and anything that still
reaches for an MLX model (PR288's own Brain, listener, speaker) fails loudly instead of being imitated.
"""

from __future__ import annotations

import importlib.util
import sys
import types


class MlxUnavailable(RuntimeError):
    pass


def _module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__dict__['__rose_cloud_stub__'] = True

    def missing(attr: str):
        if attr.startswith('__'):
            raise AttributeError(attr)
        raise MlxUnavailable(f'{name}.{attr}: MLX is not available on this host')

    mod.__getattr__ = missing
    return mod


def install() -> None:
    """Idempotent. Refuses to shadow a real MLX installation."""
    current = sys.modules.get('mlx.core')
    if current is not None:
        if getattr(current, '__rose_cloud_stub__', False):
            return
        raise RuntimeError('a real mlx.core is already imported; nothing to stand in for')
    if importlib.util.find_spec('mlx') is not None:
        raise RuntimeError('MLX is installed here; use PR288 as is')
    pkg, core = _module('mlx'), _module('mlx.core')
    core.gpu, core.cpu = 'gpu', 'cpu'
    core.new_stream = lambda device: None
    core.set_default_stream = lambda stream: None
    core.set_wired_limit = lambda limit: None
    core.device_info = lambda: {}
    pkg.core = core
    pkg.__path__ = []
    sys.modules['mlx'], sys.modules['mlx.core'] = pkg, core
