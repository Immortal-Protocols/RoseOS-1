"""Explicit dedicated Mustang paths; no host probes, fallback, login or execution."""
from pathlib import Path
import json
import re

import mustang_lane


def layout(lane):
    mustang_lane.validate(lane)
    source, storage, lock = (Path(lane[key]) for key in ('sourceRoot', 'storageRoot', 'lockPath'))
    home = source.parent
    if home == Path('/') or storage != home/'rose-actions' or lock.parent != home or lock in (source, storage) \
            or source == storage or source in storage.parents or storage in source.parents:
        raise ValueError('dedicated Mustang source, rose-actions and lock must have the admitted sibling layout')
    return {'home':home, 'sourceRoot':source, 'storageRoot':storage, 'lockPath':lock}


def attempt(lane, kind, run_id, run_attempt):
    paths = layout(lane)
    if kind not in ('prep', 'collect', 'sign', 'package') or not all(
            re.fullmatch(r'[1-9][0-9]{0,19}', str(value)) for value in (run_id, run_attempt)):
        raise ValueError('dedicated Mustang attempt kind/run/attempt invalid')
    return paths['storageRoot']/f'mustang-{kind}-{run_id}-{run_attempt}'


def compiler_recipe(engine, lane, root):
    """Keep the validated canonical recipe; replace only its retired host path pointers."""
    paths = layout(lane)
    if json.loads((Path(root)/'os/build-only/mustang-lane.json').read_text()) != lane:
        raise ValueError('compiler path pointers must name the exact committed dedicated lane')
    recipe = engine.load(Path(root)/'os/build-only/mustang.json', root=Path(root), probe=False)
    recipe['preparation']['productionSourceRootFrom'] = 'os/build-only/mustang-lane.json sourceRoot'
    recipe['preparation']['lockFrom'] = 'os/build-only/mustang-lane.json lockPath'
    recipe['preparation']['candidateLayout']['ancestorBoundary'] = str(paths['home'])
    return recipe
