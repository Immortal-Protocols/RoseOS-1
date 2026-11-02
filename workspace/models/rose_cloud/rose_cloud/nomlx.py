"""Load PR288's own Rose modules on a host without MLX, unchanged.

PR288's ``rose/runtime.py`` (the cooperative priority scheduler) and ``rose/brain.py`` (prompt formats, Sampling,
tool-call parsing) import ``mlx.core`` at module level, but outside the MLX model code the scheduler only uses it to
pick a GPU stream and wire memory when its thread starts. ``install()`` registers a stand-in ``mlx.core`` whose
stream and memory calls do nothing and whose every other attribute raises ``MlxUnavailable``. So PR288's scheduler and
task manager run as written with ``VllmBrain`` as the brain (tests/test_pr288_tasks.py), and anything that still
reaches for an MLX model (PR288's own Brain, listener, mlx-audio loader) fails loudly instead of being imitated.
PR288's Speaker calls ``mx.eval`` on each audio frame; with a Linux provider those frames are NumPy arrays that are
already computed, so ``eval`` accepts NumPy arrays (and lists of them) and refuses anything else. PR288's listener
wraps audio with ``mx.array`` before the final recognizer and reads weights with ``astype(mx.float32)``; here
``array`` gives a NumPy float32 array and ``float32`` is the NumPy dtype name.
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


def _eval_host_arrays(*arrays) -> None:
    """``mx.eval`` for what a Linux provider hands PR288's speaker: NumPy arrays, already computed."""
    import numpy as np

    for a in arrays:
        if not isinstance(a, (np.ndarray, list, tuple)):
            raise MlxUnavailable(f'mlx.core.eval of {type(a).__name__}: MLX is not available on this host')


def _host_array(values, dtype=None):
    """``mx.array`` for audio handed to a Linux recognizer (PR288's listener wraps samples with it): a NumPy array."""
    import numpy as np

    return np.asarray(values, dtype=np.float32 if dtype is None else dtype)


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
    core.eval = _eval_host_arrays
    core.array = _host_array
    core.float32 = 'float32'  # what ndarray.astype accepts
    pkg.core = core
    pkg.__path__ = []
    sys.modules['mlx'], sys.modules['mlx.core'] = pkg, core
