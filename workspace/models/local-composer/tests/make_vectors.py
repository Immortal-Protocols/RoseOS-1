"""Generate contract/vectors/v1.json from the Python reference. Java must reproduce every expected field.

    cd workspace/models/local-composer && python3 tests/make_vectors.py          # rewrite
    cd workspace/models/local-composer && python3 tests/make_vectors.py --check  # fail if stale

Snapshots are stored once under `snapshots` and referenced as `{"$ref": name}` from each request. Each vector: a
request, an optional catalog/budget, the scripted model output (text, or an error category the
inference raises), and what must come out: the selected context (aliases and the exact lines the model sees),
the per-request grammar (SHA-256 of the GBNF text, and the output-token cap), the result (status + failure, or the
validated layout with real IDs), and, when `currentSnapshotVersion` is given, the result after the freshness check.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from composer import context as context_mod, grammar as grammar_mod  # noqa: E402
from composer.backends import Generation, HeuristicBackend  # noqa: E402
from composer.compose import compose  # noqa: E402
from composer.contract import DEFAULT_CATALOG, Refused, check_request  # noqa: E402
from composer.validate import check_fresh  # noqa: E402

OUT = os.path.join(ROOT, 'contract', 'vectors', 'v1.json')
EXAMPLES = os.path.join(ROOT, 'contract', 'examples')


class Scripted:
    info = {'id': 'scripted-vector', 'revision': 'n/a', 'quantization': 'n/a', 'runtime': 'none (no model)'}

    def __init__(self, output):
        self.output = output

    def generate(self, system, user, grammar, max_tokens, deadline_s):
        if isinstance(self.output, dict):
            if self.output['error'] == 'crash':
                raise RuntimeError('runtime crashed')
            raise Refused(self.output['error'])
        return Generation(self.output)


def load(name):
    with open(os.path.join(EXAMPLES, name)) as f:
        return json.load(f)


def dumps(obj):
    return json.dumps(obj, separators=(',', ':'), ensure_ascii=False)


def req(snap, trigger='direct_text', prompt='something for cooking dinner', rid='r-vec', **extra):
    r = {'contract': 'rose-layout-v1', 'requestId': rid, 'trigger': trigger, 'prompt': prompt,
         'catalogVersion': 'cat-1', 'snapshot': snap}
    r.update(extra)
    return r


def with_tasks(snap):
    """The demo snapshot plus two Rose tasks (App Core PR782 §4.3 shape: ask + goal stay out of the model)."""
    s = copy.deepcopy(snap)
    s['actions'] += [
        {'id': 'act.grocery', 'label': 'Add missing items to grocery list', 'kind': 'agent_task',
         'targetId': 'cnt.1', 'requiresConfirmation': True,
         'ask': 'Rose will compare the recipe with your Notes list and add what is missing.',
         'goal': 'GOAL-TEXT-MUST-NOT-REACH-THE-MODEL compare recipe and notes'},
        {'id': 'act.draft', 'label': 'Draft a reply to Jamie', 'kind': 'agent_task', 'targetId': 'ntf.2',
         'requiresConfirmation': True, 'ask': 'Rose will draft a reply in Mail.',
         'goal': 'GOAL-TEXT-MUST-NOT-REACH-THE-MODEL draft reply'}]
    return s


def mutsnap(snap, fn):
    s = copy.deepcopy(snap)
    fn(s)
    return s


def heuristic_text(r, budget=1600):
    ctx = context_mod.select(r['snapshot'], r.get('prompt'), budget_chars=budget)
    from composer import prompt as prompt_mod
    system, user = prompt_mod.render(ctx, DEFAULT_CATALOG, r.get('prompt'), r['snapshot'].get('timeOfDay'))
    return HeuristicBackend().generate(system, user, '', 0, 1).text


def cases():
    snap = load('snapshot-demo.json')
    example = load('layout-aliases.json')
    tasks = with_tasks(snap)
    L = lambda **kw: dumps(dict(copy.deepcopy(example), **kw))  # noqa: E731

    def mut(fn):
        x = copy.deepcopy(example)
        fn(x)
        return dumps(x)

    unicode_snap = copy.deepcopy(snap)
    unicode_snap['content'].append({'id': 'cnt.u', 'kind': 'document', 'label': 'Crème brûlée 🍮 for two',
                                    'appId': 'app.recipes'})
    unicode_snap['apps'].append({'id': 'app.café', 'label': 'Café ☕', 'category': 'food', 'recentRank': 11})
    empty = {'snapshotVersion': 's-empty', 'catalogVersion': 'cat-1', 'apps': [], 'notifications': [],
             'content': [], 'actions': []}
    morning = dict(copy.deepcopy(snap), timeOfDay='morning', pinned=[])
    bad_task = copy.deepcopy(tasks)
    bad_task['actions'][-1]['requiresConfirmation'] = False
    first_build = {  # OS Product's first-build shape: apps and actions only, registry id formats (#787)
        'snapshotVersion': 's-fb-1', 'catalogVersion': 'cat-1', 'createdAtMs': 1794600000000, 'timeOfDay': 'evening',
        'apps': [{'id': 'app:org.example.clock/.Main', 'label': 'Clock', 'category': 'tools', 'recentRank': 2},
                 {'id': 'app:org.example.notes/.Notes', 'label': 'Notes', 'category': 'cooking', 'recentRank': 1},
                 {'id': 'app:org.example.music/.Player', 'label': 'Music', 'category': 'music', 'recentRank': 3}],
        'notifications': [], 'content': [],
        'actions': [{'id': 'intent:timer?minutes=12', 'label': 'Start 12-min timer', 'kind': 'deeplink',
                     'targetId': 'app:org.example.clock/.Main', 'requiresConfirmation': False},
                    {'id': 'task:ws-grocery', 'label': 'Add missing items to grocery list', 'kind': 'agent_task',
                     'targetId': 'app:org.example.notes/.Notes', 'requiresConfirmation': True,
                     'ask': 'Rose will compare the recipe with your Notes list and add what is missing.',
                     'goal': 'GOAL-TEXT-MUST-NOT-REACH-THE-MODEL grocery'},
                    {'id': 'sc:org.example.music/slow-supper', 'label': 'Play Slow Supper', 'kind': 'shortcut',
                     'targetId': 'app:org.example.music/.Player', 'requiresConfirmation': False}],
        'pinned': []}
    bad_kind = copy.deepcopy(snap)
    bad_kind['actions'][0]['kind'] = 'shell'

    v = [
        ('valid example layout', req(snap), L(), {}),
        ('valid button best guess', req(snap, 'button', None), heuristic_text(req(snap, 'button', None)), {}),
        ('valid morning button, no pins', req(morning, 'button', None),
         heuristic_text(req(morning, 'button', None)), {}),
        ('valid conversational', req(snap, 'conversational', 'get ready for work'),
         heuristic_text(req(snap, 'conversational', 'get ready for work')), {}),
        ('stale snapshot at render', req(snap), L(), {'currentSnapshotVersion': 's-demo-2'}),
        ('fresh snapshot at render', req(snap), L(), {'currentSnapshotVersion': 's-demo-1'}),
        ('unknown alias', req(snap), mut(lambda x: x['sections'][0]['items'].append('a99')), {}),
        ('raw real id instead of alias', req(snap), mut(lambda x: x['sections'][0]['items'].append('app.timer')), {}),
        ('action targets another item', req(snap), mut(lambda x: x['sections'][1].update(actions=['x3'])), {}),
        ('actions on a grid', req(snap), mut(lambda x: x['sections'][0].update(actions=['x2'])), {}),
        ('app in a list', req(snap), mut(lambda x: x['sections'][2].update(component='list', items=['a3', 'a4'])),
         {}),
        ('duplicate item', req(snap), mut(lambda x: x['sections'][2]['items'].append('a1')), {}),
        ('too many sections', req(snap), mut(lambda x: x['sections'].extend(
            [{'component': 'button', 'title': 'Go', 'items': ['x3']},
             {'component': 'button', 'title': 'Play', 'items': ['x4']},
             {'component': 'list', 'title': 'More', 'items': ['c3']},
             {'component': 'list', 'title': 'Also', 'items': ['c4']}])), {}),
        ('layout title too long', req(snap), L(title='x' * 41), {}),
        ('section title too long', req(snap), mut(lambda x: x['sections'][0].update(title='y' * 31)), {}),
        ('grid over max items', req(snap), mut(lambda x: x['sections'][0].update(
            items=['a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7', 'a8', 'a9'])), {}),
        ('grid under min items', req(snap), mut(lambda x: x['sections'][0].update(items=['a1'])), {}),
        ('tightened maxSections', req(snap, limits={'maxSections': 2}), L(), {}),
        ('tightened maxTotalItems', req(snap, limits={'maxTotalItems': 5}), L(), {}),
        ('unknown component', req(snap), mut(lambda x: x['sections'][0].update(component='webview')), {}),
        ('extra key', req(snap), mut(lambda x: x.update(intent='open')), {}),
        ('duplicate json key', req(snap), '{"title":"A","title":"B","sections":[]}', {}),
        ('nan constant', req(snap), '{"title":"A","sections":NaN}', {}),
        ('trailing text', req(snap), L() + ' ok', {}),
        ('not json', req(snap), 'Here is your layout!', {}),
        ('control char in title', req(snap), L(title='a\nb'), {}),
        ('empty sections', req(snap), '{"title":"A","sections":[]}', {}),
        ('bad trigger', req(snap, 'voice'), L(), {}),
        ('prompt on button', req(snap, 'button', 'typed'), L(), {}),
        ('prompt too long', req(snap, prompt='p' * 501), L(), {}),
        ('limits looser than catalog', req(snap, limits={'maxSections': 9}), L(), {}),
        ('catalog mismatch', req(snap, catalogVersion='cat-0'), L(), {}),
        ('unknown action kind', req(bad_kind), L(), {}),
        ('empty context', req(empty), L(), {}),
        ('limits not an object', req(snap, limits=[1]), L(), {}),
        ('unknown time of day', req(dict(copy.deepcopy(snap), timeOfDay=['evening'])), L(), {}),
        ('pinned not a list', req(dict(copy.deepcopy(snap), pinned='app.timer')), L(), {}),
        ('non-string category', req(mutsnap(snap, lambda x: x['apps'][0].update(category=7))), L(), {}),
        ('non-string target', req(mutsnap(snap, lambda x: x['actions'][0].update(targetId=['app.timer']))), L(), {}),
        ('recent rank out of range', req(mutsnap(snap, lambda x: x['apps'][0].update(recentRank=10 ** 7))), L(), {}),
        ('recent rank boolean', req(mutsnap(snap, lambda x: x['apps'][0].update(recentRank=True))), L(), {}),
        ('id containing arrow text', req(mutsnap(snap, lambda x: (x['apps'][0].update(id='app -> timer'),
                                                                   x['actions'][0].update(targetId='app -> timer'),
                                                                   x.update(pinned=['app -> timer'])))),
         L(), {}),
        ('regenerate with avoid hint', req(snap, avoid=['app.recipes', 'cnt.1', 'gone:id']),
         heuristic_text(req(snap, avoid=['app.recipes', 'cnt.1', 'gone:id'])), {}),
        ('avoid not a list', req(snap, avoid='app.recipes'), L(), {}),
        ('avoid with a non-string', req(snap, avoid=['app.recipes', 3]), L(), {}),
        ('avoid too long', req(snap, avoid=['x'] * 65), L(), {}),
        ('pinned id is not selectable', req(snap), mut(lambda x: x['sections'][0]['items'].append('app.timer')), {}),
        ('button row of three actions', req(snap), dumps({'title': 'Now', 'sections': [
            {'component': 'button', 'title': 'Quick actions', 'items': ['x2', 'x3', 'x4']}]}), {}),
        ('button row of five is over limit', req(tasks), dumps({'title': 'Now', 'sections': [
            {'component': 'button', 'title': 'Quick actions', 'items': ['x1', 'x2', 'x3', 'x4', 'x5']}]}), {}),
        ('six sections fit', req(snap), dumps({'title': 'Dinner', 'sections': [
            {'component': 'grid', 'title': 'Kitchen', 'items': ['a1', 'a2']},
            {'component': 'card', 'title': 'Timer', 'items': ['n1'], 'actions': ['x2']},
            {'component': 'carousel', 'title': 'Recipes', 'items': ['c2', 'c3']},
            {'component': 'list', 'title': 'Messages', 'items': ['n2', 'n3']},
            {'component': 'button', 'title': 'Play', 'items': ['x4']},
            {'component': 'list', 'title': 'Listen', 'items': ['c1']}]}), {}),
        ('seven sections are over limit', req(snap), dumps({'title': 'Dinner', 'sections': [
            {'component': 'list', 'title': 'L', 'items': [i]} for i in ('n1', 'n2', 'n3', 'c1', 'c2', 'c3', 'c4')]}),
         {}),
        ('first build: apps and actions only', req(first_build, 'button', None),
         heuristic_text(req(first_build, 'button', None)), {}),
        ('first build: task row', req(first_build, prompt='cooking timer'), dumps({'title': 'Cooking', 'sections': [
            {'component': 'grid', 'title': 'Kitchen', 'items': ['a1', 'a2']},
            {'component': 'button', 'title': 'Quick actions', 'items': ['x1', 'x2']}]}), {}),
        ('label too long', req(mutsnap(snap, lambda x: x['apps'][0].update(label='z' * 61))), L(), {}),
        ('label 60 astral chars fits', req(mutsnap(snap, lambda x: x['apps'][1].update(label='🍝' * 60))), L(), {}),
        ('inference cancelled', req(snap), {'error': 'cancelled'}, {}),
        ('inference timeout', req(snap), {'error': 'timeout'}, {}),
        ('runtime crash is model_unavailable', req(snap), {'error': 'crash'}, {}),
        ('agent task as a button', req(tasks), dumps({'title': 'Dinner', 'sections': [
            {'component': 'button', 'title': 'Groceries', 'items': ['x5']}]}), {}),
        ('agent task as a card action on its target', req(tasks), dumps({'title': 'Dinner', 'sections': [
            {'component': 'card', 'title': 'Recipe', 'items': ['c2'], 'actions': ['x5']}]}), {}),
        ('agent task in a grid is disallowed', req(tasks), dumps({'title': 'Dinner', 'sections': [
            {'component': 'grid', 'title': 'Do', 'items': ['a1', 'x5']}]}), {}),
        ('agent task without confirmation', req(bad_task), L(), {}),
        ('small budget, example refers outside context', req(snap), L(), {'budgetChars': 120}),
        ('small budget, alias outside context', req(snap), mut(lambda x: x['sections'][2]['items'].append('c4')),
         {'budgetChars': 120}),
        ('unicode labels and prompt', req(unicode_snap, prompt='crème brûlée at the café'),
         heuristic_text(req(unicode_snap, prompt='crème brûlée at the café')), {}),
        ('unicode budget', req(unicode_snap, prompt='crème brûlée'), dumps({'title': 'Dessert ☕', 'sections': [
            {'component': 'list', 'title': 'Recettes 🍮', 'items': ['c1']}]}), {'budgetChars': 90}),
    ]
    return v


def expected(request, output, opts):
    budget = opts.get('budgetChars', 1600)
    out = {'context': None, 'grammar': None}
    try:
        limits = check_request(copy.deepcopy(request), DEFAULT_CATALOG)
        ctx = context_mod.select(request['snapshot'], request.get('prompt'), budget_chars=budget,
                                 avoid=request.get('avoid'))
        out['context'] = {'aliases': [[a, i] for a, i in ctx.aliases.items()], 'lines': ctx.lines,
                          'chars': ctx.chars}
        tree = grammar_mod.build(ctx, DEFAULT_CATALOG, limits)
        if tree is not None:
            g = grammar_mod.to_gbnf(tree)
            out['grammar'] = {'sha256': hashlib.sha256(g.encode('utf-8')).hexdigest(), 'length': len(g),
                              'maxChars': grammar_mod.max_chars(tree)}
    except Refused:
        pass
    res = compose(copy.deepcopy(request), Scripted(output), budget_chars=budget)
    keep = {'status': res['status']}
    if res['status'] == 'ok':
        keep['layout'] = res['layout']
        keep['maxTokens'] = res['context']['maxTokens']
    else:
        keep['failure'] = res['failure']
    out['result'] = keep
    if 'currentSnapshotVersion' in opts:
        fresh = check_fresh(res, opts['currentSnapshotVersion'])
        out['afterFreshness'] = {'status': fresh['status'], 'failure': fresh.get('failure')}
    return out


def build():
    vectors, snapshots = [], {}
    for name, request, output, opts in cases():
        exp = expected(request, output, opts)
        key = dumps(request['snapshot'])
        ref = next((k for k, s in snapshots.items() if dumps(s) == key), None)
        if ref is None:
            ref = f's{len(snapshots) + 1}'
            snapshots[ref] = request['snapshot']
        request = dict(request, snapshot={'$ref': ref})
        vectors.append({'name': name, 'request': request, 'output': output, 'options': opts, 'expect': exp})
    return {'contract': 'rose-layout-v1', 'catalog': DEFAULT_CATALOG, 'snapshots': snapshots,
            'note': 'Generated by tests/make_vectors.py from the Python reference; do not edit by hand. '
                    'Synthetic, public-safe data. Outputs are scripted, not model output.',
            'vectors': vectors}


def render(data) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False, sort_keys=False) + '\n'


def main(argv):
    text = render(build())
    if '--check' in argv:
        with open(OUT, encoding='utf-8') as f:
            if f.read() != text:
                print('contract/vectors/v1.json is stale: run python3 tests/make_vectors.py', file=sys.stderr)
                return 1
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
