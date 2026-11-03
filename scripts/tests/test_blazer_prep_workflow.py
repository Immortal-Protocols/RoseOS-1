"""BLAZER-PREP: the manual blazer-prep mode of .github/workflows/os-release.yml and scripts/blazer-prep-wiring.py.

The workflow's own shell is executed with stand-ins (no host, network, token, download, vendor script or lunch): the
guard, the hosted binding check, the bounded stage invocation, the receipt allowlist, and the controller/cleanup
ownership commands against the fake builder adapter. The helper scripts/blazer-build-only.py is Flavors-owned and tested
by test_blazer_build_only.py; it is never run here."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
MODE = 'blazer-prep'
WIRING = ROOT/'scripts/blazer-prep-wiring.py'
BINDING = json.loads((ROOT/'os/build-only/blazer-prep-host.json').read_text())
RECIPE = json.loads((ROOT/'os/build-only/blazer.json').read_text())
PRODUCTION = '/home/pixelbuild/grapheneos-2026100600'
LOCK = '/home/pixelbuild/frankel-build.lock'
RECEIPTS = ('preflight.json', 'started.json', 'completed.json', 'inspection.json')

spec = importlib.util.spec_from_file_location('blazer_prep_wiring', WIRING)
wiring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wiring)


def jobs():
    body = WORKFLOW.split('\njobs:\n', 1)[1]
    return dict(re.findall(r'(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)', body))


def step(job, name):
    match = re.search(r'(?ms)^      - name: ' + re.escape(name) + r'\n(.*?)(?=^      - |\Z)', job)
    assert match, name
    return match[1]


def script(step_text):
    block = re.search(r'(?ms)^        run: \|\n(.*)', step_text)
    if block:
        lines = []
        for line in block[1].splitlines():
            if line.strip() and not line.startswith(' ' * 10):
                break  # the run block ends where its indentation does (a following step comment is not shell)
            lines.append(line[10:])
        return '\n'.join(lines) + '\n'
    return re.search(r'(?m)^        run: (.+)$', step_text)[1] + '\n'


def code(text):
    return '\n'.join(line for line in text.splitlines() if not line.strip().startswith('#'))


def bound(root, **change):
    """A bound copy of the committed binding (isolated example paths, never the production tree)."""
    record = {**BINDING, 'sourceRoot': '/srv/rose-blazer/src', 'downloadDir': '/srv/rose-blazer-dl', 'node': '/usr/bin/node',
        **change}
    path = Path(root)/'binding.json'
    path.write_text(json.dumps(record))
    return path


def wire(*argv):
    return subprocess.run([sys.executable, str(WIRING), *argv], capture_output=True, text=True, cwd=ROOT)


class Guard(unittest.TestCase):
    BASE = {'MODE': MODE, 'CANDIDATE_SHA': '', 'BUILD_NUMBER': '', 'BUILD_TIMESTAMP': '', 'APPS': '', 'PROMOTE': 'false',
            'CHANNEL': 'beta', 'RESUME_STAGE': 'none', 'RESUME_SOURCE_RUN': '', 'RESUME_UNSIGNED_SHA256': '',
            'RESUME_SIGNED_SHA256': '', 'CAMPAIGN': '', 'APP_PUBLISH': 'false', 'ROSE_RANGO_PUBLISH': 'false',
            'ROSE_FIRST_USE_ENROLL': 'false', 'ROSE_RECOVER_RUN': '', 'BOOTSTRAP_PROFILE': 'none', 'MODELS_BOOTSTRAP_RUN': '',
            'REUSE_BINDING': '', 'ALPHA_RECOVER': 'false', 'ROSE_DEVICE': 'frankel', 'BLAZER_PREP_STAGE': 'download',
            'GITHUB_SHA': 'f' * 40}

    def guard_script(self):
        return 'set -euo pipefail\n' + script(step(jobs()['guard'], 'Candidate is an exact commit on main'))

    def run_guard(self, **change):
        env = {'PATH': os.environ['PATH'], **self.BASE, **change}
        return subprocess.run(['bash', '-c', self.guard_script()], env={k: v for k, v in env.items() if v is not None},
            capture_output=True, cwd=ROOT).returncode

    def test_whole_guard_admits_exactly_the_bare_mode_with_one_preparation_stage(self):
        for stage in ('source', 'download', 'vendor', 'lunch', 'inspect', 'git-inspect', 'git-inspect-set', 'git-inspect-objects', 'git-meta', 'binding-observe', 'ssh-keygen-observe', 'output-observe'):
            with self.subTest(stage=stage):
                self.assertEqual(self.run_guard(BLAZER_PREP_STAGE=stage), 0)
        for change in ({'BLAZER_PREP_STAGE': 'none'}, {'BLAZER_PREP_STAGE': None}, {'BLAZER_PREP_STAGE': 'compile'},
                       {'BLAZER_PREP_STAGE': 'generate'}, {'BLAZER_PREP_STAGE': 'download vendor'},
                       {'CANDIDATE_SHA': 'a' * 40}, {'BUILD_NUMBER': '2026103001'}, {'BUILD_TIMESTAMP': '1793500000'},
                       {'APPS': '[["dev.pixel.voice",1]]'}, {'PROMOTE': 'true'}, {'RESUME_STAGE': 'sign'},
                       {'RESUME_SOURCE_RUN': '1'}, {'RESUME_UNSIGNED_SHA256': 'a' * 64}, {'RESUME_SIGNED_SHA256': 'a' * 64},
                       {'CAMPAIGN': 'start'}, {'APP_PUBLISH': 'true'}, {'ROSE_RANGO_PUBLISH': 'true'},
                       {'ROSE_FIRST_USE_ENROLL': 'true'}, {'ROSE_RECOVER_RUN': '41000000000'},
                       {'BOOTSTRAP_PROFILE': 'models-v0'}, {'MODELS_BOOTSTRAP_RUN': '1'}, {'REUSE_BINDING': '{}'},
                       {'ALPHA_RECOVER': 'true'}, {'ROSE_DEVICE': 'blazer'}, {'ROSE_DEVICE': 'rango'}):
            with self.subTest(change=change):
                self.assertNotEqual(self.run_guard(**change), 0)

    def test_a_preparation_stage_is_refused_in_every_other_mode(self):
        observe = dict(MODE='rango-factory-observe')
        self.assertEqual(self.run_guard(**observe, BLAZER_PREP_STAGE='none'), 0)
        self.assertEqual(self.run_guard(**observe, BLAZER_PREP_STAGE=None), 0)  # older callers never set it
        for stage in ('source', 'download', 'vendor', 'lunch', 'inspect', 'git-inspect', 'git-inspect-set', 'git-inspect-objects', 'git-meta', 'binding-observe', 'ssh-keygen-observe', 'output-observe'):
            self.assertNotEqual(self.run_guard(**observe, BLAZER_PREP_STAGE=stage), 0)
        line = '[[ "$MODE" == blazer-prep || "${BLAZER_PREP_STAGE:-none}" == none ]]'
        self.assertIn('          ' + line + '\n', jobs()['guard'])
        for mode in ('latest-beta', 'device-readiness', 'app-only', 'rango-retained', 'signing-bootstrap', 'models-sign'):
            result = subprocess.run(['bash', '-c', line], env={'MODE': mode, 'BLAZER_PREP_STAGE': 'vendor'})
            self.assertNotEqual(result.returncode, 0, mode)

    def test_mode_input_and_hosted_check_before_the_controller(self):
        self.assertIn('models-sign, rango-retained, blazer-prep, emulator-alpha-successor,', WORKFLOW)
        self.assertIn('      blazer_prep_stage:\n        description: blazer-prep only - the one preparation stage this run '
                      'executes (none for every other mode)\n        type: choice\n        options: [none, source, download, vendor, '
                      'lunch, inspect, git-inspect, git-inspect-set, git-inspect-objects, git-meta, binding-observe, ssh-keygen-observe, output-observe]\n        default: none\n', WORKFLOW)
        self.assertIn("  BLAZER_PREP_STAGE: ${{ inputs.blazer_prep_stage || 'none' }}\n", WORKFLOW)
        inputs = re.findall(r'(?m)^      (\w+):$', WORKFLOW.split('    inputs:\n', 1)[1].split('\npermissions:', 1)[0])
        self.assertIn('blazer_prep_stage', inputs)
        self.assertLessEqual(len(inputs), 25)  # the GitHub workflow_dispatch input limit
        guard = jobs()['guard']
        self.assertEqual(step(guard, 'Blazer preparation check'), "        id: blazer-prep\n        if: inputs.mode == "
            "'blazer-prep'\n        run: python3 scripts/blazer-prep-wiring.py check --stage \"$BLAZER_PREP_STAGE\" >> "
            "\"$GITHUB_OUTPUT\"\n      # Fails closed before the controller starts unless the profile is resource-bound "
            "with an approved inventory, tool\n      # identities and unexpired access, and every other option is at its "
            "non-publishing default.\n")
        self.assertIn('    outputs:\n      blazer_prep_outer_seconds: ${{ steps.blazer-prep.outputs.outer_seconds }}\n'
                      '      blazer_prep_job_minutes: ${{ steps.blazer-prep.outputs.job_minutes }}\n', guard)
        self.assertEqual(re.search(r'(?m)^    needs: (.+)$', jobs()['controller'])[1], 'guard')


class Wiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_committed_binding_is_receipt_41473133543_and_every_stage_resolves_exactly(self):
        self.assertEqual(BINDING['host'], 'immortal-os-builder')
        root = '/home/pixelbuild/rose-actions/blazer-prep-41473133543-1'
        self.assertEqual([BINDING[k] for k in ('sourceRoot', 'downloadDir', 'node')],
                         [f'{root}/source', f'{root}/downloads', '/usr/local/bin/node'])
        for phrase in ('run 41473133543 attempt 1', 'not current host proof', 'version was never run',
                       'project revisions and the pinned manifest unverified', 'source stage needs only sourceRoot'):
            self.assertIn(phrase, BINDING['basis'])
        expected = {'source': ['--source-root', f'{root}/source'], 'download': ['--download-dir', f'{root}/downloads'],
                    'vendor': ['--download-dir', f'{root}/downloads', '--source-root', f'{root}/source',
                               '--node', '/usr/local/bin/node'],
                    'lunch': ['--source-root', f'{root}/source']}
        for stage, inputs in expected.items():
            tail, _, _ = wiring.resolve(stage)
            self.assertEqual(tail, ['--stage', stage, '--host', 'immortal-os-builder', '--lock', LOCK, *inputs], stage)
        # the source stage depends on sourceRoot alone: it still resolves with the other two unbound
        alone = Path(self.tmp)/'source-only.json'
        alone.write_text(json.dumps({**BINDING, 'downloadDir': None, 'node': None}))
        self.assertEqual(wire('check', '--stage', 'source', '--binding', str(alone)).returncode, 0)
        for stage in ('download', 'vendor'):
            self.assertEqual(wire('check', '--stage', stage, '--binding', str(alone)).returncode, 4, stage)
        unbound = Path(self.tmp)/'unbound.json'
        unbound.write_text(json.dumps({**BINDING, 'sourceRoot': None, 'downloadDir': None, 'node': None}))
        for stage, missing in (('source', 'sourceRoot'), ('download', 'downloadDir'), ('vendor', 'downloadDir, sourceRoot, node'),
                               ('lunch', 'sourceRoot')):
            result = wire('check', '--stage', stage, '--binding', str(unbound))
            self.assertEqual((result.returncode, result.stdout), (4, ''), stage)
            self.assertIn('SETUP_MISSING', result.stderr)
            self.assertIn(missing, result.stderr)

    def test_bounds_are_derived_from_the_recipe_seconds(self):
        binding = bound(self.tmp)
        expected = {'source': (7200, 7500, 136), 'download': (3600, 3900, 76), 'vendor': (10800, 11100, 196), 'lunch': (1800, 2100, 46)}
        for stage, (helper_seconds, outer, minutes) in expected.items():
            self.assertEqual(RECIPE['preparation'][stage]['timeoutSeconds'], helper_seconds)
            result = wire('check', '--stage', stage, '--binding', str(binding))
            self.assertEqual((result.returncode, result.stdout), (0, f'outer_seconds={outer}\njob_minutes={minutes}\n'), stage)

    def test_exact_helper_invocation_per_stage_and_never_compile(self):
        binding = bound(self.tmp)
        seen = []
        original = os.execv
        os.execv = lambda exe, argv: seen.append(argv) or (_ for _ in ()).throw(SystemExit(0))
        try:
            for stage in ('source', 'download', 'vendor', 'lunch'):
                with self.assertRaises(SystemExit), open(os.devnull, 'w') as null:
                    stdout, sys.stdout = sys.stdout, null
                    try:
                        wiring.main(['run', '--stage', stage, '--receipt-dir', '/tmp/r', '--binding', str(binding)])
                    finally:
                        sys.stdout = stdout
        finally:
            os.execv = original
        head = [sys.executable, str(ROOT/'scripts/blazer-build-only.py'), 'run']
        common = ['--host', 'immortal-os-builder', '--lock', LOCK]
        self.assertEqual(seen, [
            head + ['--stage', 'source', *common, '--source-root', '/srv/rose-blazer/src', '--receipt-dir', '/tmp/r'],
            head + ['--stage', 'download', *common, '--download-dir', '/srv/rose-blazer-dl', '--receipt-dir', '/tmp/r'],
            head + ['--stage', 'vendor', *common, '--download-dir', '/srv/rose-blazer-dl', '--source-root', '/srv/rose-blazer/src',
                    '--node', '/usr/bin/node', '--receipt-dir', '/tmp/r'],
            head + ['--stage', 'lunch', *common, '--source-root', '/srv/rose-blazer/src', '--receipt-dir', '/tmp/r']])
        for argv in seen:
            self.assertFalse({'compile', '--jobs', '--integration-dir', '--inputs-receipt', 'plan'} & set(argv))

    def test_invalid_stage_binding_or_receipt_dir_refuses(self):
        good = bound(self.tmp)
        for argv in (('check', '--stage', 'compile'), ('check', '--stage', 'generate'), ('run', '--stage', 'compile'),
                     ('check', '--stage', 'download', '--receipt-dir', '/tmp/r'), ('run', '--stage', 'lunch'),
                     ('run', '--stage', 'lunch', '--receipt-dir', 'relative')):
            self.assertEqual(wire(*argv, '--binding', str(good)).returncode, 2, argv)
        for change in ({'sourceRoot': PRODUCTION}, {'sourceRoot': PRODUCTION + '/blazer'}, {'sourceRoot': '/home/pixelbuild'},
                       {'downloadDir': PRODUCTION + '/dl'}, {'downloadDir': '/'}, {'sourceRoot': 'srv/relative'},
                       {'sourceRoot': '/srv/a/../b'}, {'sourceRoot': '/srv/a/'}, {'node': 'node'}, {'node': 24}, {'node': '/usr/bin/nodejs'},
                       {'downloadDir': '/srv/rose-blazer'}, {'host': 'other-builder'}, {'host': 'Immortal-OS-Builder'},
                       {'lockFrom': 'os/release-pipeline/frankel.json host.sourceRoot'}, {'kind': 'other'},
                       {'schemaVersion': 2}, {'jobs': 8}):
            with self.subTest(change=change):
                self.assertEqual(wire('check', '--stage', 'vendor', '--binding', str(bound(self.tmp, **change))).returncode, 2)


class Inspect(unittest.TestCase):
    """The metadata-only inspect route: the proposed candidate root of this run (never a binding), only bound host facts,
    the helper's own bound and a 20-minute job. No lock-only inspection."""
    ROOT_TEMPLATE = '/home/pixelbuild/rose-actions/blazer-prep-{run_id}-{attempt}'

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def binding(self, **candidates):
        record = None if candidates.get('absent') else {**BINDING['inspectCandidates'], **candidates}
        path = self.tmp/'binding.json'
        path.write_text(json.dumps({**BINDING, 'inspectCandidates': record}))
        return path

    def test_committed_candidates_are_a_proposal_only_and_never_satisfy_a_stage(self):
        record = BINDING['inspectCandidates']
        self.assertEqual((record['rootTemplate'], record['verified'], record['binding']), (self.ROOT_TEMPLATE, False, False))
        unbound = self.tmp/'unbound.json'
        unbound.write_text(json.dumps({**BINDING, 'sourceRoot': None, 'downloadDir': None, 'node': None}))
        for stage in ('source', 'download', 'vendor', 'lunch'):  # candidates never satisfy a preparation stage
            self.assertEqual(wire('check', '--stage', stage, '--binding', str(unbound)).returncode, 4, stage)

    def test_inspect_passes_host_lock_and_this_runs_candidate_root_with_its_own_bounds(self):
        result = wire('check', '--stage', 'inspect')
        self.assertEqual((result.returncode, result.stdout), (0, 'outer_seconds=960\njob_minutes=20\n'))
        tail, outer, minutes = wiring.resolve('inspect', run_id='41400000000', attempt='2')
        root = '/home/pixelbuild/rose-actions/blazer-prep-41473133543-1'  # the bound facts are inspected too
        self.assertEqual(tail, ['--host', 'immortal-os-builder', '--lock', LOCK, '--source-root', f'{root}/source',
            '--download-dir', f'{root}/downloads', '--node', '/usr/local/bin/node', '--candidate-root',
            '/home/pixelbuild/rose-actions/blazer-prep-41400000000-2', '--max-entries', '200000', '--max-seconds', '900'])
        self.assertLessEqual(900, wiring.helper().INSPECT_MAX_SECONDS)
        self.assertLessEqual(outer + 30, minutes * 60 - 180)  # kill grace and checkout fit inside the 20-minute job
        self.assertFalse({'--stage', '--receipt-dir', '--candidate-node', '--candidate-node-from-path',
                          '--reference-source-root'} & set(tail))

    def test_no_lock_only_inspection_and_malformed_candidates_refuse(self):
        result = wire('check', '--stage', 'inspect', '--binding', str(self.binding(absent=True)))
        self.assertEqual(result.returncode, 4)
        self.assertIn('lock-only inspection is not offered', result.stderr)
        for change in ({'verified': True}, {'binding': True}, {'rootTemplate': '/home/pixelbuild/rose-actions/fixed'},
                       {'rootTemplate': '/home/pixelbuild/rose-actions/{run_id}-{run_id}-{attempt}'},
                       {'rootTemplate': '/home/pixelbuild/{run_id}/x-{attempt}'}, {'rootTemplate': '/srv/blazer-{run_id}-{attempt}'},
                       {'rootTemplate': '/home/pixelbuild/grapheneos-2026100600/x-{run_id}-{attempt}'},
                       {'rootTemplate': 'home/pixelbuild/x-{run_id}-{attempt}'}, {'rootTemplate': '/home/pixelbuild/x-{run_id}-{attempt}-{other}'},
                       {'extra': 1}):
            with self.subTest(change=change):
                self.assertEqual(wire('check', '--stage', 'inspect', '--binding', str(self.binding(**change))).returncode, 2)
        for run_id, attempt in (('', '1'), ('1', ''), ('0', '1'), ('1x', '1'), ('-1', '1'), ('1', '01')):
            with self.subTest(run_id=run_id, attempt=attempt):
                self.assertEqual(wire('run', '--stage', 'inspect', '--receipt-dir', str(self.tmp/'r'), '--run-id', run_id,
                    '--attempt', attempt).returncode, 2)
        self.assertEqual(wire('run', '--stage', 'inspect', '--receipt-dir', str(self.tmp/'r')).returncode, 2)
        self.assertEqual(wire('check', '--stage', 'inspect', '--run-id', '1', '--attempt', '1').returncode, 2)
        self.assertFalse((self.tmp/'r').exists())

    def test_bound_facts_are_inspected_and_still_validated(self):
        tail, _, _ = wiring.resolve('inspect', bound(self.tmp, downloadDir=None), run_id='5', attempt='1')
        self.assertEqual(tail, ['--host', 'immortal-os-builder', '--lock', LOCK, '--source-root', '/srv/rose-blazer/src',
            '--node', '/usr/bin/node', '--candidate-root', '/home/pixelbuild/rose-actions/blazer-prep-5-1',
            '--max-entries', '200000', '--max-seconds', '900'])
        for change in ({'sourceRoot': PRODUCTION}, {'node': '/usr/bin/nodejs'}, {'downloadDir': 'rel'}):
            self.assertEqual(wire('check', '--stage', 'inspect', '--binding', str(bound(self.tmp, **change))).returncode, 2, change)

    def test_real_helper_inspect_writes_one_metadata_only_v2_receipt_and_refuses_reuse(self):
        receipts = self.tmp/'run'
        argv = ('run', '--stage', 'inspect', '--receipt-dir', str(receipts), '--run-id', '41400000000', '--attempt', '1')
        result = wire(*argv)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(p.name for p in receipts.iterdir()), ['inspection.json'])
        report = json.loads((receipts/'inspection.json').read_text())
        self.assertEqual((report['schema'], report['metadataOnly'], report['processesStarted'], report['network'],
                          report['writes'], report['enablesPreparation']),
                         ('roseos.blazer-preparation-inspection.v2', True, 0, False, False, False))
        root = '/home/pixelbuild/rose-actions/blazer-prep-41473133543-1'
        self.assertEqual(report['bound'], {'sourceRoot': f'{root}/source', 'downloadDir': f'{root}/downloads',
                                           'node': '/usr/local/bin/node'})
        self.assertFalse(report['enablesPreparation'])
        candidates = report['candidates']
        self.assertEqual((candidates['proposed'], candidates['verified'], candidates['binding']), (True, False, False))
        root = '/home/pixelbuild/rose-actions/blazer-prep-41400000000-1'
        self.assertEqual(candidates['root']['path'], root)
        self.assertEqual({k: candidates[k]['path'] for k in ('sourceRoot', 'downloadDir', 'receiptsDir')},
            {'sourceRoot': root + '/source', 'downloadDir': root + '/downloads', 'receiptsDir': root + '/receipts'})
        self.assertTrue(report['reference']['isProduction'])
        self.assertIn('runner', report['node'])
        self.assertIsNone(report['node']['candidate'])
        self.assertEqual(report['limits'], {'maxEntries': 200000, 'maxSeconds': 900})
        self.assertEqual(wire(*argv).returncode, 2)
        self.assertEqual(sorted(p.name for p in receipts.iterdir()), ['inspection.json'])


class GitInspect(unittest.TestCase):
    """The narrow git-only route: `inspect-git --host H` and nothing else; no broad scan; one write-once receipt."""
    BROAD = {'--stage', '--lock', '--receipt-dir', '--source-root', '--download-dir', '--node', '--candidate-root',
             '--candidate-source-root', '--candidate-download-dir', '--candidate-receipts-dir', '--candidate-node',
             '--candidate-node-from-path', '--reference-source-root', '--max-entries', '--max-seconds', '--jobs'}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_exact_narrow_call_and_bounds(self):
        tail, outer, minutes = wiring.resolve('git-inspect')
        self.assertEqual(tail, ['--host', 'immortal-os-builder'])
        self.assertFalse(self.BROAD & set(tail))
        self.assertEqual((outer, minutes), (120, 13))
        self.assertLess(wiring.helper().GIT_INSPECT_MAX_SECONDS, outer)
        self.assertLessEqual(outer + 30, minutes * 60 - 180)
        self.assertEqual(wire('check', '--stage', 'git-inspect').stdout, 'outer_seconds=120\njob_minutes=13\n')

    def test_recipe_targets_agree_with_the_bound_source_root_and_production(self):
        recipe = json.loads((ROOT/'os/build-only/blazer.json').read_text())['preparation']['gitInspection']['targets']
        self.assertEqual(recipe, {'production': f'{PRODUCTION}/vendor/adevtool/.git',
                                  'candidate': BINDING['sourceRoot'] + '/vendor/adevtool/.git'})
        moved = self.tmp/'moved.json'
        moved.write_text(json.dumps({**BINDING, 'sourceRoot': '/home/pixelbuild/rose-actions/blazer-prep-9-1/source'}))
        self.assertEqual(wire('check', '--stage', 'git-inspect', '--binding', str(moved)).returncode, 2)

    def test_real_helper_writes_exactly_one_git_receipt_and_refuses_reuse(self):
        receipts = self.tmp/'run'
        argv = ('run', '--stage', 'git-inspect', '--receipt-dir', str(receipts), '--run-id', '41600000000', '--attempt', '1')
        result = wire(*argv)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.splitlines()[0]),
                         {'helper': [str(ROOT/'scripts/blazer-build-only.py'), 'inspect-git', '--host', 'immortal-os-builder']})
        self.assertEqual(sorted(p.name for p in receipts.iterdir()), ['git-inspection.json'])
        report = json.loads((receipts/'git-inspection.json').read_text())
        self.assertEqual((report['schema'], report['metadataOnly'], report['processesStarted'], report['network'],
                          report['writes'], report['gitProcess'], report['enablesPreparation']),
                         ('roseos.blazer-git-inspection.v1', True, 0, False, False, False, False))
        self.assertEqual(set(report['targets']), {'production', 'candidate'})
        self.assertEqual(report['bounds']['maxSeconds'], 60)
        for absent in ('reference', 'candidates', 'node', 'bound', 'download', 'collisions', 'projects'):
            self.assertNotIn(absent, report)  # no broad scan section
        self.assertEqual(wire(*argv).returncode, 2)
        self.assertEqual(sorted(p.name for p in receipts.iterdir()), ['git-inspection.json'])

    def test_the_helper_declares_only_host_for_inspect_git(self):
        import argparse
        declared, original = {}, argparse.ArgumentParser.add_argument

        def record(parser, *flags, **kwargs):
            declared[flags[0]] = kwargs.get('required', False)
            return original(parser, *flags, **kwargs)
        argparse.ArgumentParser.add_argument = record
        try:
            with open(os.devnull, 'w') as null:
                stderr, sys.stderr = sys.stderr, null
                try:
                    with self.assertRaises(SystemExit):
                        wiring.helper().main_git([])
                finally:
                    sys.stderr = stderr
        finally:
            argparse.ArgumentParser.add_argument = original
        self.assertEqual({k: v for k, v in declared.items() if k != '-h'}, {'--host': True})

    def test_target_set_route_is_exactly_the_one_fixed_set(self):
        tail, outer, minutes = wiring.resolve('git-inspect-set')
        self.assertEqual(tail, ['--host', 'immortal-os-builder', '--target-set', 'adevtool-projects-gitdir'])
        self.assertFalse(self.BROAD & set(tail))
        self.assertEqual((outer, minutes), (120, 13))
        sets = json.loads((ROOT/'os/build-only/blazer.json').read_text())['preparation']['gitInspection']['targetSets']
        self.assertEqual(sets, {'adevtool-projects-gitdir': {'projectsGitdir': {'root': PRODUCTION,
                                                                                 'path': '.repo/projects/vendor/adevtool.git'}}})
        self.assertEqual(wiring.resolve('git-inspect')[0], ['--host', 'immortal-os-builder'])  # inspect-git never widens

    def test_the_helper_declares_only_host_and_target_set_for_inspect_git_set(self):
        import argparse
        declared, original = {}, argparse.ArgumentParser.add_argument

        def record(parser, *flags, **kwargs):
            declared[flags[0]] = kwargs.get('required', False)
            return original(parser, *flags, **kwargs)
        argparse.ArgumentParser.add_argument = record
        try:
            with open(os.devnull, 'w') as null:
                stderr, sys.stderr = sys.stderr, null
                try:
                    with self.assertRaises(SystemExit):
                        wiring.helper().main_git([], target_set=True)
                finally:
                    sys.stderr = stderr
        finally:
            argparse.ArgumentParser.add_argument = original
        self.assertEqual({k: v for k, v in declared.items() if k != '-h'}, {'--host': True, '--target-set': True})

    def test_real_helper_writes_exactly_one_target_set_receipt(self):
        receipts = self.tmp/'set'
        argv = ('run', '--stage', 'git-inspect-set', '--receipt-dir', str(receipts), '--run-id', '41600000000', '--attempt', '1')
        result = wire(*argv)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.splitlines()[0])['helper'][1:],
                         ['inspect-git-set', '--host', 'immortal-os-builder', '--target-set', 'adevtool-projects-gitdir'])
        self.assertEqual(sorted(p.name for p in receipts.iterdir()), ['git-target-set-inspection.json'])
        report = json.loads((receipts/'git-target-set-inspection.json').read_text())
        self.assertEqual((report['schema'], report['targetSet'], report['gitProcess'], report['writes'],
                          report['enablesPreparation'], set(report['targets'])),
                         ('roseos.blazer-git-target-set-inspection.v1', 'adevtool-projects-gitdir', False, False, False,
                          {'projectsGitdir'}))
        self.assertEqual(wire(*argv).returncode, 2)

    def touched(self, stage):
        """Host paths (/home/pixelbuild/...) the wiring's resolve touches, recorded at the os layer."""
        import builtins
        seen, saved = [], {}

        def recording(name, original):
            def call(path, *args, **kwargs):
                text = os.fsdecode(path) if isinstance(path, (str, bytes, os.PathLike)) else ''
                if text.startswith('/home/pixelbuild'):
                    seen.append((name, text))
                return original(path, *args, **kwargs)
            return call
        for name in ('stat', 'lstat', 'scandir', 'listdir', 'access', 'readlink', 'open'):
            saved[name] = getattr(os, name)
            setattr(os, name, recording(name, saved[name]))
        saved_open = builtins.open
        builtins.open = recording('builtins.open', saved_open)
        try:
            wiring.resolve(stage)
        finally:
            for name, original in saved.items():
                setattr(os, name, original)
            builtins.open = saved_open
        return seen

    def test_git_inspect_set_resolution_touches_no_host_path(self):
        self.assertEqual(self.touched('git-inspect-set'), [])
        # the recorder does see host probes: the other routes keep the probing recipe load
        self.assertTrue(self.touched('git-inspect'))

    def test_objects_route_is_exactly_the_helpers_fixed_spec(self):
        tail, outer, minutes = wiring.resolve('git-inspect-objects')
        self.assertEqual((tail, outer, minutes), (['--host', 'immortal-os-builder'], 180, 14))
        self.assertFalse(self.BROAD & set(tail))
        h = wiring.helper()
        spec = json.loads((ROOT/'os/build-only/blazer.json').read_text())['preparation']['gitObjectsInspection']
        self.assertEqual({k: v for k, v in spec.items() if k != 'basis'}, h.git_objects_spec(PRODUCTION))
        self.assertEqual(h.GIT_OBJECTS, {'maxEntries': 100000, 'maxDepth': 3, 'maxSeconds': 120, 'maxOutputBytes': 65536})
        self.assertLess(h.GIT_OBJECTS['maxSeconds'], outer)

    def test_a_drifted_objects_spec_refuses(self):
        real = wiring.helper
        for change in ({'maxEntries': 200000}, {'path': '.repo/project-objects/other.git/objects'}, {'maxSeconds': 200},
                       {'host': 'other-host'}):
            with self.subTest(change=change):
                def drifted():
                    module = real()
                    load = module.load

                    def drifted_load(*args, **kwargs):  # the recipe as loaded, then drifted: only the wiring can refuse
                        recipe = load(*args, **kwargs)
                        recipe['preparation']['gitObjectsInspection'].update(change)
                        return recipe
                    module.load = drifted_load
                    return module
                wiring.helper = drifted
                try:
                    with self.assertRaises(wiring.Invalid):
                        wiring.resolve('git-inspect-objects')
                finally:
                    wiring.helper = real

    def test_the_helper_declares_only_host_for_inspect_git_objects(self):
        import argparse
        declared, original = {}, argparse.ArgumentParser.add_argument

        def record(parser, *flags, **kwargs):
            declared[flags[0]] = kwargs.get('required', False)
            return original(parser, *flags, **kwargs)
        argparse.ArgumentParser.add_argument = record
        try:
            with open(os.devnull, 'w') as null:
                stderr, sys.stderr = sys.stderr, null
                try:
                    with self.assertRaises(SystemExit):
                        wiring.helper().main_git_objects([])
                finally:
                    sys.stderr = stderr
        finally:
            argparse.ArgumentParser.add_argument = original
        self.assertEqual({k: v for k, v in declared.items() if k != '-h'}, {'--host': True})

    def test_git_inspect_objects_resolution_touches_no_host_path(self):
        self.assertEqual(self.touched('git-inspect-objects'), [])

    def test_real_helper_writes_exactly_one_objects_receipt(self):
        receipts = self.tmp/'objects'
        argv = ('run', '--stage', 'git-inspect-objects', '--receipt-dir', str(receipts))
        result = wire(*argv)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.splitlines()[0])['helper'][1:], ['inspect-git-objects', '--host', 'immortal-os-builder'])
        self.assertEqual(sorted(p.name for p in receipts.iterdir()), ['git-objects-inspection.json'])
        report = json.loads((receipts/'git-objects-inspection.json').read_text())
        self.assertEqual(report['schema'], 'roseos.blazer-git-objects-inspection.v1')
        self.assertEqual(wire(*argv).returncode, 2)

    def test_git_meta_is_exactly_flavors_stage_with_its_recipe_bounds(self):
        tail, outer, minutes = wiring.resolve('git-meta')
        binding = json.loads((ROOT/'os/build-only/blazer-prep-host.json').read_text())
        self.assertEqual(tail, ['--stage', 'git-meta', '--host', 'immortal-os-builder', '--lock', '/home/pixelbuild/frankel-build.lock',
                                '--source-root', binding['sourceRoot']])
        h = wiring.helper()
        spec = json.loads((ROOT/'os/build-only/blazer.json').read_text())['gitMeta']
        self.assertEqual(outer, spec['timeoutSeconds'] + h.GIT_META_CLEANUP_SECONDS + wiring.GIT_META_MARGIN_SECONDS)
        self.assertEqual((outer, minutes), (6000, 111))
        self.assertEqual(spec['objectsSource'], 'public-fetch')
        self.assertEqual([p['path'] for p in spec['projects']], sorted(h.GIT_META_PATHS))
        self.assertIn(h.GIT_META_STAGE, h.STAGES)

    def test_git_meta_runs_once_with_a_fresh_receipt_dir(self):
        result = wire('run', '--stage', 'git-meta')
        self.assertEqual(result.returncode, 2)  # run needs an absolute --receipt-dir

    def test_binding_observe_runs_exactly_the_release_observer(self):
        self.assertEqual(wiring.resolve('binding-observe'), (['--host', 'immortal-os-builder'], 420, 19))
        receipts = self.tmp/'observe'
        result = wire('run', '--stage', 'binding-observe', '--receipt-dir', str(receipts))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.splitlines()[0])['helper'][1:], ['observe', '--host', 'immortal-os-builder'])
        self.assertTrue(json.loads(result.stdout.splitlines()[0])['helper'][0].endswith('scripts/blazer-binding-observe.py'))
        self.assertEqual(sorted(p.name for p in receipts.iterdir()), ['binding-observation.json'])
        report = json.loads((receipts/'binding-observation.json').read_text())
        self.assertEqual(report['schema'], 'roseos.blazer-binding-observation.v1')
        self.assertEqual(wire('run', '--stage', 'binding-observe', '--receipt-dir', str(receipts)).returncode, 2)
        self.assertIn('binding-observation.json', step(jobs()['blazer-prep'], 'Allowlisted JSON receipts only'))

    def test_ssh_keygen_observe_runs_exactly_signings_observer_with_its_bounds(self):
        # Signing's own 120 s (it starts no process), a 120 s margin; job adds the outer kill, the verdict and 10 min.
        self.assertEqual(wiring.resolve('ssh-keygen-observe'), (['--host', 'immortal-os-builder'], 240, 16))
        receipts = self.tmp/'ssh'
        result = wire('run', '--stage', 'ssh-keygen-observe', '--receipt-dir', str(receipts))
        self.assertEqual(result.returncode, 3, result.stderr)  # incomplete (no lock here): the report is still printed
        helper = json.loads(result.stdout.splitlines()[0])['helper']
        self.assertEqual((Path(helper[0]).relative_to(ROOT).as_posix(), helper[1:]),
                         ('scripts/ssh-keygen-observe.py', ['observe', '--host', 'immortal-os-builder']))
        report = json.loads((receipts/'ssh-keygen-observation.json').read_text())
        self.assertEqual((report['schema'], report['complete']), ('roseos.ssh-keygen-observation.v1', False))  # no lock here
        result = wire('verdict', '--stage', 'ssh-keygen-observe', '--receipt-dir', str(receipts))
        self.assertEqual(result.returncode, 4, result.stderr)  # Signing's bind: not bindable
        self.assertEqual(json.loads(result.stdout)['verdict'], 'NOT_BINDABLE')
        self.assertIs(json.loads((receipts/'ssh-keygen-bind.json').read_text())['bindable'], False)
        self.assertEqual(wire('verdict', '--stage', 'ssh-keygen-observe', '--receipt-dir', str(receipts)).returncode, 2)  # write-once
        allowlist = step(jobs()['blazer-prep'], 'Allowlisted JSON receipts only')
        self.assertIn('ssh-keygen-observation.json ssh-keygen-bind.json;', allowlist)

    def test_output_observe_runs_exactly_the_release_output_observer_with_its_bounds(self):
        # Its own 900 s, a 120 s margin (it starts no process); the job adds the outer kill, the verdict and 10 min.
        self.assertEqual(wiring.resolve('output-observe'), (['--host', 'immortal-os-builder'], 1020, 29))
        receipts = self.tmp/'output-observe'
        result = wire('run', '--stage', 'output-observe', '--receipt-dir', str(receipts))
        self.assertEqual(result.returncode, 0, result.stderr)  # a report was printed: the verdict decides the stage
        helper = json.loads(result.stdout.splitlines()[0])['helper']
        self.assertEqual((Path(helper[0]).relative_to(ROOT).as_posix(), helper[1:]),
                         ('scripts/blazer-output-observe.py', ['observe', '--host', 'immortal-os-builder']))
        report = json.loads((receipts/'output-observation.json').read_text())
        self.assertEqual((report['schema'], report['complete']), ('roseos.blazer-output-observation.v1', False))  # no lock here
        result = wire('verdict', '--stage', 'output-observe', '--receipt-dir', str(receipts))
        self.assertEqual((result.returncode, json.loads(result.stdout)['verdict']), (1, 'INCOMPLETE'))
        good = receipts.parent/'good'
        good.mkdir()
        (good/'output-observation.json').write_text(json.dumps({'schema': 'roseos.blazer-output-observation.v1',
                                                                  'complete': True, 'problems': []}))
        self.assertEqual(wiring.verdict('output-observe', good), 0)
        (good/'output-observation.json').write_text(json.dumps({'schema': 'roseos.blazer-binding-observation.v1',
                                                                  'complete': True, 'problems': []}))
        self.assertEqual(wiring.verdict('output-observe', good), 1)  # another observer's report never passes
        self.assertIn('binding-observation.json output-observation.json ssh-keygen', step(jobs()['blazer-prep'], 'Allowlisted JSON receipts only'))

    def test_the_verdict_not_the_observer_exit_decides_an_observation_stage(self):
        def receipt(stage, data, name=None):
            directory = Path(tempfile.mkdtemp())
            (directory/(name or wiring.RECEIPTS[stage][0])).write_bytes(data)
            return directory
        good = {'schema': 'roseos.blazer-binding-observation.v1', 'complete': True, 'problems': []}
        self.assertEqual(wiring.verdict('binding-observe', receipt('binding-observe', json.dumps(good).encode())), 0)
        for bad in ({**good, 'complete': False}, {**good, 'problems': ['lock held by another job']}, {**good, 'schema': 'x'}, [1]):
            with self.subTest(bad=bad):
                self.assertEqual(wiring.verdict('binding-observe', receipt('binding-observe', json.dumps(bad).encode())), 1)
        for data in (b'not json', b' ' * (wiring.OBSERVATION_BYTES + 1)):
            with self.assertRaises(wiring.Invalid):
                wiring.verdict('binding-observe', receipt('binding-observe', data))
        linked = receipt('binding-observe', json.dumps(good).encode(), 'real.json')
        (linked/'binding-observation.json').symlink_to(linked/'real.json')
        with self.assertRaises(wiring.Invalid):
            wiring.verdict('binding-observe', linked)
        with self.assertRaises(wiring.Invalid):
            wiring.verdict('binding-observe', Path(tempfile.mkdtemp()))  # no receipt is never a pass
        calls = []
        for code, expected in ((0, 0), (4, 4), (2, 2), (1, 2), (-9, 2)):
            directory = receipt('ssh-keygen-observe', b'{}')

            def run(argv, **kwargs):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, code)
            self.assertEqual(wiring.verdict('ssh-keygen-observe', directory, run=run), expected, code)
            self.assertTrue((directory/'ssh-keygen-bind.json').is_file())
        self.assertEqual(Path(calls[0][1]).relative_to(ROOT).as_posix(), 'scripts/ssh-keygen-observe.py')
        self.assertEqual(calls[0][2:4], ['bind', '--observation'])
        for stage in ('source', 'vendor', 'git-meta', 'inspect'):
            self.assertEqual(wiring.verdict(stage, '/nonexistent'), 0)
        self.assertEqual(wire('verdict', '--stage', 'binding-observe', '--receipt-dir', 'relative').returncode, 2)

    def test_existing_routes_are_unchanged(self):
        self.assertEqual(wiring.resolve('inspect', run_id='5', attempt='1')[1:], (960, 20))
        for stage, bounds in (('source', (7500, 136)), ('download', (3900, 76)), ('vendor', (11100, 196)), ('lunch', (2100, 46))):
            tail, outer, minutes = wiring.resolve(stage)
            self.assertEqual((outer, minutes), bounds, stage)
            self.assertEqual(tail[:2], ['--stage', stage])
        self.assertIn('git-inspection.json', step(jobs()['blazer-prep'], 'Allowlisted JSON receipts only'))
        self.assertIn('git-target-set-inspection.json', step(jobs()['blazer-prep'], 'Allowlisted JSON receipts only'))
        self.assertIn('git-objects-inspection.json', step(jobs()['blazer-prep'], 'Allowlisted JSON receipts only'))


class Job(unittest.TestCase):
    def setUp(self):
        self.job = jobs()[MODE]
        self.tmp = Path(tempfile.mkdtemp())

    def test_isolated_read_only_bounded_job(self):
        head = code(self.job.split('    steps:\n', 1)[0])
        self.assertEqual(head, "    needs: [guard, controller]\n    if: inputs.mode == 'blazer-prep'\n"
            '    runs-on: [self-hosted, roseos-frankel-builder]\n'
            "    timeout-minutes: ${{ fromJSON(needs.guard.outputs.blazer_prep_job_minutes || '1') }}\n"
            '    permissions:\n      contents: read\n    env:\n      OUTER_SECONDS: ${{ needs.guard.outputs.blazer_prep_outer_seconds }}')
        body = code(self.job)
        for absent in ('id-token', 'secrets.', 'actions-gcp-login', 'GCP_', 'PIXEL_', 'sign', 'publish', 'compile', 'make',
                       'download-artifact', 'release-pipeline.py', 'device-readiness', PRODUCTION, 'grapheneos'):
            self.assertNotIn(absent, body)
        self.assertEqual(sorted(set(re.findall(r'python3 (\S+)', body))), ['scripts/blazer-prep-wiring.py'])
        self.assertIn('          persist-credentials: false\n', self.job)
        self.assertEqual([n for n, b in jobs().items() if 'blazer-prep-wiring.py run' in b or 'blazer-build-only.py' in code(b)], [MODE])
        self.assertNotIn('blazer', jobs()['device-readiness'])

    def stage(self, outer, check_output):
        bin_dir = self.tmp/'bin'
        bin_dir.mkdir(exist_ok=True)
        log = self.tmp/'calls.log'
        (bin_dir/'python3').write_text('#!/bin/sh\necho "python3 $*" >> "$LOG"\n'
            'case "$*" in *" check "*) printf "%s\\n" "$CHECK_OUTPUT";; esac\n')
        (bin_dir/'timeout').write_text('#!/bin/sh\necho "timeout $*" >> "$LOG"\n')
        for name in ('python3', 'timeout'):
            (bin_dir/name).chmod(0o755)
        env = {'PATH': f'{bin_dir}:/usr/bin:/bin', 'LOG': str(log), 'OUTER_SECONDS': outer, 'CHECK_OUTPUT': check_output,
            'BLAZER_PREP_STAGE': 'vendor', 'TASK_RUN': '/runner/tmp/blazer-prep-1-1/run', 'GITHUB_RUN_ID': '41400000000',
            'GITHUB_RUN_ATTEMPT': '1'}
        result = subprocess.run(['bash', '-c', script(step(self.job, 'Preparation stage (bounded, under the shared lock)'))],
            env=env, capture_output=True)
        calls = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return result.returncode, calls

    def test_stage_runs_once_under_the_outer_timeout_matching_the_rechecked_bound(self):
        code_, calls = self.stage('11100', 'outer_seconds=11100\njob_minutes=196')
        self.assertEqual(code_, 0)
        self.assertEqual(calls, ['python3 scripts/blazer-prep-wiring.py check --stage vendor',
            'timeout --kill-after=30s 11100s python3 scripts/blazer-prep-wiring.py run --stage vendor --receipt-dir '
            '/runner/tmp/blazer-prep-1-1/run --run-id 41400000000 --attempt 1',
            'timeout --kill-after=5s 60s python3 scripts/blazer-prep-wiring.py verdict --stage vendor --receipt-dir '
            '/runner/tmp/blazer-prep-1-1/run'])
        for outer, check in (('1080', 'outer_seconds=11100\njob_minutes=196'), ('', 'outer_seconds=11100'),
                             ('11100s', 'outer_seconds=11100'), ('0', 'outer_seconds=0')):
            code_, calls = self.stage(outer, check)
            self.assertNotEqual(code_, 0, outer)
            self.assertFalse([c for c in calls if c.startswith('timeout')], outer)

    def test_only_the_three_regular_receipts_leave_the_host_and_the_task_is_removed(self):
        task = self.tmp/'task'
        run = task/'run'
        run.mkdir(parents=True)
        for name in (*RECEIPTS[:2], 'inspection.json'):
            (run/name).write_text('{}')
        (run/'completed.json').symlink_to(run/'preflight.json')
        (run/'extra.json').write_text('{}')
        env = {'PATH': os.environ['PATH'], 'TASK_ROOT': str(task), 'TASK_RUN': str(run), 'TASK_RECEIPTS': str(task/'receipts')}
        subprocess.run(['bash', '-c', script(step(self.job, 'Allowlisted JSON receipts only'))], env=env, check=True)
        self.assertEqual(sorted(p.name for p in (task/'receipts').iterdir()), ['inspection.json', 'preflight.json', 'started.json'])
        artifact = step(self.job, 'Receipts artifact')
        self.assertIn('          name: blazer-prep-${{ inputs.blazer_prep_stage }}-${{ github.run_id }}-${{ github.run_attempt }}\n'
                      '          path: ${{ env.TASK_RECEIPTS }}\n          retention-days: 7\n', artifact)
        subprocess.run(['bash', '-c', script(step(self.job, "Remove this attempt's task directory"))], env=env, check=True)
        self.assertFalse(task.exists())
        self.assertEqual(subprocess.run(['bash', '-c', script(step(self.job, 'Allowlisted JSON receipts only'))],
            env={'PATH': os.environ['PATH']}).returncode, 0)  # no task directory: nothing to collect


class Ownership(unittest.TestCase):
    """The controller and cleanup commands as written in the workflow, run against the fake builder adapter."""
    def commands(self):
        controller, cleanup = jobs()['controller'], jobs()['cleanup']
        start = re.search(r"(?m)^      - if: inputs.mode == 'blazer-prep' \|\| inputs.mode == 'rango-factory-package' \|\| inputs.mode == 'rango-factory-inspect' \|\| inputs.mode == 'launcher3-compile' \|\| inputs.mode == 'blazer-compile' \|\| inputs.mode == 'blazer-integrate'\n        run: (.+)$", controller)[1]
        record = re.search(r"(?m)^        if: always\(\) && \(inputs.mode == 'blazer-prep' \|\| inputs.mode == 'rango-factory-package' \|\| inputs.mode == 'rango-factory-inspect' \|\| inputs.mode == 'launcher3-compile' \|\| inputs.mode == 'blazer-compile' \|\| inputs.mode == 'blazer-integrate'\)\n        run: (.+)$", controller)[1]
        stop = re.search(r"(?m)^      - if: inputs.mode == 'blazer-prep' \|\| inputs.mode == 'rango-factory-package' \|\| inputs.mode == 'rango-factory-inspect' \|\| inputs.mode == 'launcher3-compile' \|\| inputs.mode == 'blazer-compile' \|\| inputs.mode == 'blazer-integrate'\n        env:\n          STARTED: \$\{\{ needs.controller"
                         r".outputs.started \}\}\n        run: (.+)$", cleanup)[1]
        return start, record, stop

    def test_workflow_routes_only_blazer_prep_through_ownership(self):
        controller, cleanup = jobs()['controller'], jobs()['cleanup']
        self.assertIn("    outputs:\n      started: ${{ steps.ownership.outputs.started }}\n", controller)
        self.assertIn("      - if: inputs.mode != 'blazer-prep' && inputs.mode != 'rango-factory-package' && inputs.mode != 'rango-factory-inspect' && inputs.mode != 'launcher3-compile' && inputs.mode != 'blazer-compile' && inputs.mode != 'blazer-integrate'\n        run: python3 scripts/release-pipeline.py start-builder\n", controller)
        self.assertIn("      - if: inputs.mode != 'blazer-prep' && inputs.mode != 'rango-factory-package' && inputs.mode != 'rango-factory-inspect' && inputs.mode != 'launcher3-compile' && inputs.mode != 'blazer-compile' && inputs.mode != 'blazer-integrate'\n        run: python3 scripts/release-pipeline.py stop-builder\n", cleanup)
        self.assertEqual(len(re.findall(r'release-pipeline.py (?:start|stop)-builder', controller + cleanup)), 4)
        self.assertIn('rango-factory-observe, blazer-prep, rango-factory-package, rango-factory-inspect, launcher3-compile, blazer-compile, blazer-integrate, app-builder', re.search(r'(?m)^    needs: (.+)$', cleanup)[1])
        self.assertIn("    if: always() && needs.controller.result != 'skipped'\n", cleanup)
        start, record, stop = self.commands()
        self.assertEqual(start, 'python3 scripts/release-pipeline.py start-builder --ownership-record "$RUNNER_TEMP/builder-ownership.json"')
        self.assertEqual(stop, 'python3 scripts/release-pipeline.py stop-builder --only-if-started "$STARTED"')
        for job in (j for n, j in jobs().items() if n not in ('controller', 'cleanup')):
            self.assertNotRegex(job, r'(start|stop)-builder|ownership-record|only-if-started')

    def simulate(self, observed, record_step=True):
        tmp = Path(tempfile.mkdtemp())
        fake = tmp/'fake'
        fake.mkdir()
        if observed:
            (fake/'builder-observed').write_text(observed)
        start, record, stop = self.commands()
        suffix = f' --adapter fake --fake-state {fake}'
        env = {'PATH': os.environ['PATH'], 'RUNNER_TEMP': str(tmp), 'GITHUB_OUTPUT': str(tmp/'out')}
        run = lambda cmd, **extra: subprocess.run(['bash', '-c', cmd + suffix], env={**env, **extra}, cwd=ROOT, capture_output=True)
        self.assertEqual(run(start).returncode, 0)
        started = ''
        if record_step:
            self.assertEqual(run(record).returncode, 0)
            started = dict(line.split('=', 1) for line in (tmp/'out').read_text().split())['started']
        result = run(stop, STARTED=started)
        events = (fake/'events.log').read_text().splitlines()
        return started, result.returncode, events

    def test_stops_only_a_builder_this_run_started(self):
        self.assertEqual(self.simulate('TERMINATED'), ('true', 0, ['builder start', 'builder stop']))
        self.assertEqual(self.simulate('RUNNING'), ('false', 0, ['builder start']))
        self.assertEqual(self.simulate('STAGING'), ('false', 0, ['builder start']))
        self.assertEqual(self.simulate('TERMINATED', record_step=False), ('', 1, ['builder start']))  # unknown: no stop


if __name__ == '__main__':
    unittest.main()
