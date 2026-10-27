#!/usr/bin/env python3
"""Make the Rose Agent catalog from this developer's own Codex model cache."""
import argparse
import json
import os
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--cache', type=Path, default=Path.home()/'.codex/models_cache.json')
p.add_argument('--config-dir', type=Path, default=Path.home()/'.config/pixel-voice')
p.add_argument('--replace', action='store_true')
a = p.parse_args()
try:
    cache = json.loads(a.cache.expanduser().read_text())
except FileNotFoundError:
    p.error('No model cache found. Sign in and run your own Codex CLI once, then retry with its cache path.')
models = cache.get('models')
if not isinstance(models, list) or not models or any(not isinstance(m, dict) or not m.get('slug') for m in models):
    p.error('Cache does not contain a nonempty model list with model slugs.')
directory = a.config_dir.expanduser()
directory.mkdir(parents=True, exist_ok=True, mode=0o700)
target = directory/'model-catalog.json'
flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if a.replace else os.O_EXCL)
try:
    descriptor = os.open(target, flags, 0o600)
except FileExistsError:
    p.error('A catalog already exists; use --replace only to refresh your own catalog.')
with os.fdopen(descriptor, 'w') as output:
    json.dump({'models': models}, output)
    output.write('\n')
target.chmod(0o600)
print(f'Prepared private catalog with {len(models)} models; no account/cache metadata was copied.')
