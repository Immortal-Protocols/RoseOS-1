"""Offline tests for the local composer: contract checks, context selection, grammar, validation, compose and bench.

    cd workspace/models/local-composer && python3 -m unittest discover -s tests -v
"""

import copy
import json
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from composer import bench, context, grammar  # noqa: E402
from composer.backends import HeuristicBackend, LlamaServerBackend, ScriptedBackend  # noqa: E402
from composer.compose import compose  # noqa: E402
from composer.contract import DEFAULT_CATALOG, FAILURES, Refused, check_request  # noqa: E402
from composer.validate import check_fresh, validate  # noqa: E402

with open(os.path.join(ROOT, 'contract', 'examples', 'cases.json')) as f:
    CASES = json.load(f)['cases']
with open(os.path.join(ROOT, 'contract', 'examples', 'layout-aliases.json')) as f:
    EXAMPLE_LAYOUT = json.load(f)
LIMITS = DEFAULT_CATALOG['limits']


def request(i=0, **over):
    req = copy.deepcopy(CASES[i]['request'])
    req.update(over)
    return req


def snapshot():
    return copy.deepcopy(CASES[0]['request']['snapshot'])


def ctx_for(req):
    return context.select(req['snapshot'], req.get('prompt'))


def dumps(obj):
    return json.dumps(obj, separators=(',', ':'))


class FakeClock:
    def __init__(self, steps):
        self.t, self.steps = 0.0, list(steps)

    def __call__(self):
        if self.steps:
            self.t += self.steps.pop(0)
        return self.t


class ContractTest(unittest.TestCase):
    def test_examples_pass(self):
        for case in CASES:
            self.assertEqual(check_request(case['request']), LIMITS)

    def refused(self, req, category='bad_request'):
        with self.assertRaises(Refused) as cm:
            check_request(req)
        self.assertEqual(cm.exception.category, category)

    def test_bad_requests(self):
        self.refused(request(contract='rose-layout-v0'))
        self.refused(request(trigger='voice'))
        self.refused(request(requestId=''))
        self.refused(request(prompt='x' * 501))
        self.refused(request(prompt='   '))
        self.refused(request(3, prompt='typed text on a button press'))
        self.refused(request(limits={'maxSections': 9}))       # may only tighten
        self.refused(request(limits={'maxSections': True}))
        self.refused(request(limits={'colour': 1}))

    def test_tightened_limits(self):
        self.assertEqual(check_request(request(limits={'maxSections': 2}))['maxSections'], 2)

    def test_catalog_mismatch(self):
        self.refused(request(catalogVersion='cat-0'), 'catalog_mismatch')
        req = request()
        req['snapshot']['catalogVersion'] = 'cat-0'
        self.refused(req, 'catalog_mismatch')

    def test_bad_snapshots(self):
        for mutate in (lambda s: s['apps'].append(dict(s['apps'][0])),                     # duplicate id
                       lambda s: s['apps'][0].update(label='x' * 61),
                       lambda s: s['apps'][0].update(label=''),
                       lambda s: s['actions'][0].update(targetId='app.missing'),
                       lambda s: s.update(pinned=['app.missing']),
                       lambda s: s.update(apps={'id': 'x'})):
            req = request()
            mutate(req['snapshot'])
            self.refused(req)


class ContextTest(unittest.TestCase):
    def test_aliases_map_back_to_snapshot_ids(self):
        snap = snapshot()
        ids = {e['id'] for k in ('apps', 'notifications', 'content', 'actions') for e in snap[k]}
        ctx = context.select(snap, 'cooking dinner')
        self.assertTrue(set(ctx.aliases.values()) <= ids)
        self.assertEqual(len(set(ctx.aliases.values())), len(ctx.aliases))
        for alias, kind in ctx.kinds.items():
            self.assertEqual(alias[0], {'apps': 'a', 'notifications': 'n', 'content': 'c', 'actions': 'x'}[kind])

    def test_no_raw_ids_reach_the_model(self):
        snap = snapshot()
        text = '\n'.join(context.select(snap, 'cooking').lines)
        for k in ('apps', 'notifications', 'content', 'actions'):
            for e in snap[k]:
                self.assertNotIn(e['id'], text)

    def test_pinned_first_and_prompt_ranks(self):
        ctx = context.select(snapshot(), 'recipes for cooking')
        self.assertEqual(ctx.aliases['a1'], 'app.timer')                    # pinned
        self.assertEqual({ctx.aliases['a2'], ctx.aliases['a3']}, {'app.recipes', 'app.groceries'})

    def test_button_uses_time_of_day_hints(self):
        ctx = context.select(snapshot(), None)                               # evening: cooking/media/music
        top = [ctx.aliases[a] for a in list(ctx.aliases)[:4]]
        self.assertEqual(top[0], 'app.timer')
        self.assertTrue(set(top[1:]) <= {'app.recipes', 'app.groceries', 'app.music', 'app.video',
                                          'app.podcasts'})

    def test_budget_is_respected_and_deterministic(self):
        for budget in (40, 120, 300, 1600):
            ctx = context.select(snapshot(), 'cooking', budget_chars=budget)
            self.assertLessEqual(ctx.chars, budget)
            self.assertEqual(ctx.chars, sum(len(x) + 1 for x in ctx.lines))
            self.assertEqual(ctx.lines, context.select(snapshot(), 'cooking', budget_chars=budget).lines)

    def test_per_app_cap(self):
        snap = snapshot()
        snap['content'] += [{'id': f'cnt.r{i}', 'kind': 'document', 'label': f'Recipe {i}', 'appId': 'app.recipes'}
                            for i in range(6)]
        ctx = context.select(snap, 'recipe')
        from_recipes = [i for i in ctx.aliases.values() if i == 'app.recipes' or i.startswith('cnt.r')
                        or i in ('cnt.1', 'cnt.2')]
        self.assertLessEqual(len(from_recipes), 3)

    def test_action_targets_resolve_or_drop(self):
        ctx = context.select(snapshot(), 'cooking')
        for act, target in ctx.targets.items():
            self.assertEqual(ctx.kinds[act], 'actions')
            self.assertIn(target, ctx.aliases)
        tiny = context.select(snapshot(), 'start timer', budget_chars=60)
        for line in tiny.lines:
            if line.startswith('x') and ' -> ' in line:
                self.assertIn(line.rsplit(' -> ', 1)[1], tiny.aliases)

    def test_empty_context(self):
        snap = {k: [] for k in ('apps', 'notifications', 'content', 'actions')}
        with self.assertRaises(Refused) as cm:
            context.select(snap, 'anything')
        self.assertEqual(cm.exception.category, 'empty_context')


class GrammarTest(unittest.TestCase):
    def setUp(self):
        self.ctx = ctx_for(request())
        self.tree = grammar.build(self.ctx, DEFAULT_CATALOG, LIMITS)
        self.rx = grammar.to_regex(self.tree)

    def accepts(self, layout):
        return self.rx.fullmatch(dumps(layout)) is not None

    def test_example_layout_is_accepted_and_valid(self):
        self.assertTrue(self.accepts(EXAMPLE_LAYOUT))
        out = validate(dumps(EXAMPLE_LAYOUT), self.ctx, DEFAULT_CATALOG, LIMITS, 's-demo-1')
        self.assertEqual(out['sections'][1]['actions'], [self.ctx.aliases['x2']])

    def test_rejects_what_the_contract_forbids(self):
        bad = copy.deepcopy(EXAMPLE_LAYOUT)
        bad['sections'][0]['items'].append('a99')                            # alias not given
        self.assertFalse(self.accepts(bad))
        bad = copy.deepcopy(EXAMPLE_LAYOUT)
        bad['sections'][0]['component'] = 'webview'                          # not in catalog
        self.assertFalse(self.accepts(bad))
        bad = copy.deepcopy(EXAMPLE_LAYOUT)
        bad['sections'] *= 2                                                 # 6 sections > 4
        self.assertFalse(self.accepts(bad))
        bad = copy.deepcopy(EXAMPLE_LAYOUT)
        bad['sections'][2]['items'] = ['a1', 'a2']                           # apps fit a carousel ...
        self.assertTrue(self.accepts(bad))
        bad['sections'][2]['component'] = 'list'                             # ... but not a list
        self.assertFalse(self.accepts(bad))
        bad = copy.deepcopy(EXAMPLE_LAYOUT)
        bad['title'] = 'x' * 41
        self.assertFalse(self.accepts(bad))
        bad = copy.deepcopy(EXAMPLE_LAYOUT)
        bad['sections'][1]['actions'] = ['x2', 'x3', 'x4']                   # card: at most 2 actions
        self.assertFalse(self.accepts(bad))

    def test_tightened_limits_shrink_grammar(self):
        tight = grammar.to_regex(grammar.build(self.ctx, DEFAULT_CATALOG, dict(LIMITS, maxSections=2)))
        self.assertIsNone(tight.fullmatch(dumps(EXAMPLE_LAYOUT)))
        two = dict(EXAMPLE_LAYOUT, sections=EXAMPLE_LAYOUT['sections'][:2])
        self.assertIsNotNone(tight.fullmatch(dumps(two)))

    def test_gbnf_shape(self):
        g = grammar.to_gbnf(self.tree)
        self.assertTrue(g.startswith('root ::= '))
        self.assertEqual(g.count('('), g.count(')'))
        for alias in self.ctx.aliases:
            self.assertIn(f'\\"{alias}\\"', g)
        self.assertNotIn('app.', g)                                          # real IDs never in the grammar
        self.assertGreater(grammar.max_chars(self.tree), len(dumps(EXAMPLE_LAYOUT)))

    def test_component_needing_absent_kinds_is_dropped(self):
        snap = snapshot()
        for k in ('notifications', 'content', 'actions'):
            snap[k] = []
        snap['actions'] = []
        ctx = context.select(snap, 'cooking')
        g = grammar.to_gbnf(grammar.build(ctx, DEFAULT_CATALOG, LIMITS))
        self.assertIn('grid', g)
        for name in ('list', 'card', 'button'):
            self.assertNotIn(f'component\\":\\"{name}', g)
        one_app = dict(snap, apps=snap['apps'][:1], pinned=['app.timer'])
        self.assertIsNone(grammar.build(context.select(one_app, 'x'), DEFAULT_CATALOG, LIMITS))


class ValidateTest(unittest.TestCase):
    def setUp(self):
        self.ctx = ctx_for(request())

    def check(self, text):
        return validate(text, self.ctx, DEFAULT_CATALOG, LIMITS, 's-demo-1')

    def fails(self, layout_or_text, category):
        text = layout_or_text if isinstance(layout_or_text, str) else dumps(layout_or_text)
        with self.assertRaises(Refused) as cm:
            self.check(text)
        self.assertEqual(cm.exception.category, category, text[:120])

    def mutated(self, fn):
        layout = copy.deepcopy(EXAMPLE_LAYOUT)
        fn(layout)
        return layout

    def test_maps_aliases_to_real_ids(self):
        out = self.check(dumps(EXAMPLE_LAYOUT))
        self.assertEqual(out['snapshotVersion'], 's-demo-1')
        self.assertEqual(out['sections'][0]['items'], [self.ctx.aliases[a] for a in ('a1', 'a2', 'x1')])

    def test_malformed(self):
        for text in ('', 'not json', '[]', '{"title":"x"}', dumps(EXAMPLE_LAYOUT) + ' trailing',
                     '{"title":"x","title":"y","sections":[]}', '{"title":"x","sections":NaN}',
                     '{"title":"x","sections":[],"extra":1}', '{"title":"x","sections":[]}', 'x' * 9000):
            self.fails(text, 'invalid_output')
        self.fails(self.mutated(lambda l: l.update(title='')), 'invalid_output')
        self.fails(self.mutated(lambda l: l.update(title='a\nb')), 'invalid_output')
        self.fails(self.mutated(lambda l: l['sections'][0].update(component='webview')), 'invalid_output')
        self.fails(self.mutated(lambda l: l['sections'][0].update(items='a1')), 'invalid_output')
        self.fails(self.mutated(lambda l: l['sections'][0].update(items=['a1'])), 'invalid_output')  # grid min 2
        self.fails(self.mutated(lambda l: l['sections'][0].update(intent='x')), 'invalid_output')

    def test_refs(self):
        self.fails(self.mutated(lambda l: l['sections'][0]['items'].append('a99')), 'unknown_ref')
        self.fails(self.mutated(lambda l: l['sections'][0]['items'].append('app.timer')), 'unknown_ref')
        self.fails(self.mutated(lambda l: l['sections'][2]['items'].append('a1')), 'duplicate_ref')
        self.fails(self.mutated(lambda l: l['sections'][2].update(component='list', items=['a3', 'a4'])),
                   'disallowed_ref')
        self.fails(self.mutated(lambda l: l['sections'][1].update(actions=['x3'])), 'disallowed_ref')  # other target
        self.fails(self.mutated(lambda l: l['sections'][0].update(actions=['x2'])), 'disallowed_ref')  # grid
        self.fails(self.mutated(lambda l: l['sections'][1].update(actions=['a3'])), 'disallowed_ref')

    def test_limits(self):
        self.fails(self.mutated(lambda l: l.update(title='x' * 41)), 'over_limit')
        self.fails(self.mutated(lambda l: l['sections'][0].update(title='x' * 31)), 'over_limit')
        self.fails(self.mutated(lambda l: l['sections'][0].update(items=[f'a{i}' for i in range(1, 10)])),
                   'over_limit')
        five = self.mutated(lambda l: l['sections'].extend(
            [{'component': 'button', 'title': 'Go', 'items': ['x3']},
             {'component': 'button', 'title': 'Play', 'items': ['x4']}]))
        self.fails(five, 'over_limit')
        tight = dict(LIMITS, maxTotalItems=5)
        with self.assertRaises(Refused) as cm:
            validate(dumps(EXAMPLE_LAYOUT), self.ctx, DEFAULT_CATALOG, tight, 's-demo-1')
        self.assertEqual(cm.exception.category, 'over_limit')

    def test_fuzz_never_crashes_and_never_leaks_unknown_ids(self):
        """Random mutations of valid layouts: every outcome is a typed failure or a layout of snapshot IDs only."""
        rng = random.Random(1234)
        aliases = list(self.ctx.aliases) + ['a99', 'zz', '', 'app.timer']
        comps = list(DEFAULT_CATALOG['components']) + ['webview']
        allowed = set(self.ctx.aliases.values())
        seen = set()
        for _ in range(3000):
            layout = copy.deepcopy(EXAMPLE_LAYOUT)
            for _ in range(rng.randint(1, 4)):
                s = rng.choice(layout['sections']) if layout['sections'] else None
                op = rng.randrange(8)
                if op == 0 and s:
                    s['items'].append(rng.choice(aliases))
                elif op == 1 and s and s['items']:
                    s['items'].pop(rng.randrange(len(s['items'])))
                elif op == 2 and s:
                    s['component'] = rng.choice(comps)
                elif op == 3 and s:
                    s['actions'] = rng.sample(aliases, rng.randint(0, 3))
                elif op == 4:
                    layout['sections'].append(copy.deepcopy(rng.choice(EXAMPLE_LAYOUT['sections'])))
                elif op == 5 and layout['sections']:
                    layout['sections'].pop()
                elif op == 6 and s:
                    s['title'] = rng.choice(['', 'ok', 'x' * 31, '"quoted"', 'tab\there'])
                elif op == 7:
                    layout[rng.choice(['title', 'extra'])] = rng.choice(['T', 3, None])
            try:
                out = self.check(dumps(layout))
                ids = {i for s in out['sections'] for i in s['items'] + s['actions']}
                self.assertTrue(ids <= allowed)
                self.assertEqual(len(ids), sum(len(s['items']) + len(s['actions']) for s in out['sections']))
                seen.add('ok')
            except Refused as r:
                self.assertIn(r.category, FAILURES)
                seen.add(r.category)
        self.assertGreaterEqual(len(seen), 6, seen)

    def test_grammar_and_validator_agree_on_the_fuzzed_valid_set(self):
        """Every layout the validator accepts that is also free of cross-references is accepted by the grammar."""
        rx = grammar.to_regex(grammar.build(self.ctx, DEFAULT_CATALOG, LIMITS))
        rng = random.Random(99)
        kinds = {k: [a for a, v in self.ctx.kinds.items() if v == k] for k in set(self.ctx.kinds.values())}
        checked = 0
        for _ in range(500):
            sections = []
            for _ in range(rng.randint(1, 4)):
                name = rng.choice(list(DEFAULT_CATALOG['components']))
                rule = DEFAULT_CATALOG['components'][name]
                pool = [a for k in rule['items'] for a in kinds.get(k, [])]
                n = rng.randint(rule['min'], rule['max'])
                if len(pool) < n:
                    continue
                sections.append({'component': name, 'title': 'T', 'items': rng.sample(pool, n)})
            if not sections:
                continue
            text = dumps({'title': 'Page', 'sections': sections})
            try:
                self.check(text)
            except Refused:
                continue
            self.assertIsNotNone(rx.fullmatch(text), text)
            checked += 1
        self.assertGreater(checked, 20)


class ComposeTest(unittest.TestCase):
    def test_heuristic_end_to_end_all_cases(self):
        for case in CASES:
            res = compose(copy.deepcopy(case['request']), HeuristicBackend())
            self.assertEqual(res['status'], 'ok', res)
            self.assertEqual(res['layout']['snapshotVersion'], 's-demo-1')
            for key in ('contextMs', 'generationMs', 'validationMs', 'composeMs'):
                self.assertIn(key, res['timing'])

    def test_backend_sees_aliases_and_grammar_only(self):
        backend = ScriptedBackend([dumps(EXAMPLE_LAYOUT)])
        res = compose(request(), backend)
        self.assertEqual(res['status'], 'ok')
        call = backend.calls[0]
        self.assertIn('Request: something for cooking dinner', call['user'])
        self.assertNotIn('app.timer', call['user'] + call['grammar'])
        self.assertTrue(call['grammar'].startswith('root ::= '))
        self.assertLessEqual(call['max_tokens'], 512)

    def test_typed_failures(self):
        cases = [('model_unavailable', [RuntimeError('boom')]), ('timeout', [Refused('timeout')]),
                 ('cancelled', [Refused('cancelled')]), ('invalid_output', ['{"title":']),
                 ('unknown_ref', [dumps({'title': 'X', 'sections': [{'component': 'button', 'title': 'B',
                                                                     'items': ['x9']}]})])]
        for category, script in cases:
            res = compose(request(), ScriptedBackend(script))
            self.assertEqual((res['status'], res['failure']), ('failed', category))
            self.assertNotIn('layout', res)
            self.assertNotIn('boom', json.dumps(res))
        res = compose(request(trigger='voice'), ScriptedBackend([]))
        self.assertEqual(res['failure'], 'bad_request')
        self.assertEqual(compose('not a dict', ScriptedBackend([]))['requestId'], None)

    def test_deadline_counts_the_whole_compose(self):
        # context 1 s, generation 3.5 s: 4.5 s > 4 s deadline even though the backend returned output
        clock = FakeClock([0, 1.0, 3.5, 0, 0])
        res = compose(request(), ScriptedBackend([dumps(EXAMPLE_LAYOUT)]), clock=clock)
        self.assertEqual(res['failure'], 'timeout')

    def test_stale_snapshot(self):
        res = compose(request(), ScriptedBackend([dumps(EXAMPLE_LAYOUT)]))
        self.assertIs(check_fresh(res, 's-demo-1'), res)
        self.assertEqual(check_fresh(res, 's-demo-2')['failure'], 'stale_snapshot')

    def test_llama_server_adapter_is_loopback_only(self):
        info = {'id': 'm', 'revision': 'r', 'quantization': 'q', 'runtime': 'llama.cpp'}
        for url in ('http://127.0.0.1:8080', 'http://localhost:8080', 'http://[::1]:8080'):
            LlamaServerBackend(url, info)
        for url in ('https://api.example.com', 'http://10.0.0.2:8080', 'http://rose-server:8080'):
            with self.assertRaises(ValueError):
                LlamaServerBackend(url, info)

    def test_llama_server_unreachable_is_model_unavailable(self):
        info = {'id': 'm', 'revision': 'r', 'quantization': 'q', 'runtime': 'llama.cpp'}
        res = compose(request(), LlamaServerBackend('http://127.0.0.1:9', info))   # discard port: nothing listens
        self.assertEqual(res['failure'], 'model_unavailable')


class BenchTest(unittest.TestCase):
    def test_records_and_summary(self):
        backend = HeuristicBackend()
        records = bench.run(CASES, backend, bench.host_env(backend), repeats=3)
        self.assertEqual(len(records), 3 * len(CASES))
        self.assertEqual([r['phase'] for r in records[:3]], ['first', 'warm', 'warm'])
        for r in records:
            self.assertEqual(r['timingScope'], 'composer-only')
            self.assertIsNone(r['requestToRenderMs'])
            self.assertIsNone(r['peakRssMb'])
            self.assertIsNone(r['energyMj'])
            for k in bench.ENV_KEYS:
                self.assertIn(k, r['env'])
        s = bench.summarize(records)
        self.assertEqual(s['ok'], len(records))
        self.assertIn('not request-to-render', s['timingScope'])
        self.assertIsNotNone(s['warm.composeMs']['p50'])
        self.assertGreater(s['meanRecall'], 0)
        self.assertLess(s['meanPrecision'], 0.6)   # the no-model baseline shows everything: not a useful layout

    def test_env_must_be_complete(self):
        with self.assertRaises(ValueError):
            bench.run(CASES, HeuristicBackend(), {'device': 'x'})

    def test_cli(self):
        import contextlib
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, 'b.jsonl')
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = bench.main(['--cases', os.path.join(ROOT, 'contract', 'examples', 'cases.json'),
                                 '--repeats', '2', '--out', out])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(buf.getvalue())['runs'], 2 * len(CASES))
            with open(out) as f:
                self.assertEqual(len(f.readlines()), 2 * len(CASES))


if __name__ == '__main__':
    unittest.main()
