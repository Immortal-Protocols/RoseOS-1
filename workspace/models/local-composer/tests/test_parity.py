"""Cross-language parity: the committed vectors match the Python reference, and the Java core reproduces them.

    cd workspace/models/local-composer && python3 -m unittest tests.test_parity -v
"""

import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import make_vectors  # noqa: E402

VECTORS = os.path.join(ROOT, 'contract', 'vectors', 'v1.json')


class VectorsTest(unittest.TestCase):
    def test_committed_vectors_are_current(self):
        with open(VECTORS, encoding='utf-8') as f:
            self.assertEqual(f.read(), make_vectors.render(make_vectors.build()),
                             'contract/vectors/v1.json is stale: run python3 tests/make_vectors.py')

    def test_coverage(self):
        with open(VECTORS, encoding='utf-8') as f:
            data = json.load(f)
        outcomes = {v['expect']['result'].get('failure') or 'ok' for v in data['vectors']}
        outcomes |= {v['expect'].get('afterFreshness', {}).get('failure') for v in data['vectors']} - {None}
        for want in ('ok', 'bad_request', 'catalog_mismatch', 'stale_snapshot', 'empty_context', 'model_unavailable',
                     'timeout', 'cancelled', 'invalid_output', 'unknown_ref', 'disallowed_ref', 'over_limit',
                     'duplicate_ref'):
            self.assertIn(want, outcomes)

    def test_task_goal_and_ask_never_reach_the_model(self):
        with open(VECTORS, encoding='utf-8') as f:
            data = json.load(f)
        task_vectors = [v for v in data['vectors'] if 'agent task' in v['name'] and v['expect']['context']]
        self.assertGreaterEqual(len(task_vectors), 3)
        for v in task_vectors:
            text = '\n'.join(v['expect']['context']['lines'])
            self.assertIn('Add missing items to grocery list [agent_task]', text)
            self.assertNotIn('GOAL-TEXT', text)
            self.assertNotIn('Rose will', text)                          # the confirm-sheet `ask`


@unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'no JDK on PATH: Java parity not run')
class JavaParityTest(unittest.TestCase):
    def test_java_core_reproduces_every_vector(self):
        run = subprocess.run(['bash', os.path.join(ROOT, 'java', 'run-tests.sh')], capture_output=True, text=True,
                             timeout=600)
        self.assertEqual(run.returncode, 0, run.stdout[-4000:] + run.stderr[-4000:])
        self.assertRegex(run.stdout, r'passed \d+, failed 0')


if __name__ == '__main__':
    unittest.main()
