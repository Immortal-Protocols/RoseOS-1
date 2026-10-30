"""report.py and the EXP006 workflow wiring, against synthetic evidence files; nothing is run or dispatched."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import report  # noqa: E402

WORKFLOW = (HERE.parents[2]/'.github/workflows/systemui-persistent-experiment.yml').read_text()

class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='report-'))
        self.addCleanup(__import__('shutil').rmtree, self.tmp, True)
        self.evidence, self.work = self.tmp/'evidence', self.tmp/'work'
        self.evidence.mkdir()
        self.work.mkdir()

    def write(self, name, document):
        (self.evidence/name).write_text(json.dumps(document) if not isinstance(document, str) else document)

    def run_report(self, env=None):
        """report.main with its stdout captured: these synthetic stages must never reach the job's annotations."""
        summary = self.tmp/'summary.md'
        lines, notes = report.build(self.evidence, self.work, env or {})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            report.main(['--evidence', str(self.evidence), '--work', str(self.work), '--summary', str(summary)], env or {})
        self.assertEqual(out.getvalue().splitlines(), notes, 'main prints exactly the annotations build returns')
        return summary.read_text(), notes

    def test_stage_results_reasons_setup_and_cleanup_are_visible(self):
        self.write('emulator-setup.json', {'result': 'installed', 'version': 'Android emulator version 36.1.9.0'})
        self.write('results.json', {'stages': {
            'environment': {'result': 'pass', 'kvmUsableAsIs': True},
            'image': {'result': 'unavailable', 'reason': 'sdkmanager --install exited 1 with no licence answered'},
            'device': {'result': 'not-run', 'reason': 'an earlier stage did not pass'}}})
        summary, notes = self.run_report({'SYSTEMUI': 'true', 'SETUP_OUTCOME': 'success', 'RUN_OUTCOME': 'success',
            'CLEANUP_OUTCOME': 'success'})
        self.assertIn('| emulator setup | installed | Android emulator version 36.1.9.0 |', summary)
        self.assertIn('| image | unavailable | sdkmanager --install exited 1 with no licence answered |', summary)
        self.assertIn('| cleanup | success | test key, AVD and emulator home absent |', summary)
        self.assertIn('SystemUI stages requested: true', summary)
        self.assertIn('::notice title=environment%3A pass::pass', notes)
        self.assertTrue(any(n.startswith('::warning title=image%3A unavailable::') for n in notes))
        self.assertTrue(any(n.startswith('::warning title=device%3A not-run::') for n in notes))

    def test_missing_unreadable_or_leftover_state_is_never_a_pass(self):
        self.write('results.json', '{not json')
        (self.work/'avd').mkdir()
        summary, notes = self.run_report({'CLEANUP_OUTCOME': 'success'})
        self.assertIn('| emulator setup | missing | missing |', summary)
        self.assertIn('| experiment | missing | unreadable (JSONDecodeError) |', summary)
        self.assertIn("still present: ['avd']", summary)
        self.assertTrue(all(n.startswith('::warning') for n in notes if 'kvm access%3A disabled' not in n), notes)
        self.assertIn('SystemUI stages requested: false', summary, 'an absent input is the default, false')

    def test_reasons_are_bounded_and_cannot_inject_commands_or_table_cells(self):
        self.write('results.json', {'stages': {'seed': {'result': 'fail',
            'reason': 'line1\n::error::injected | cell %0A' + 'x' * 1000}}})
        summary, notes = self.run_report()
        row = next(l for l in summary.splitlines() if l.startswith('| seed'))
        self.assertLess(len(row), 400)
        self.assertNotIn('\n::error', summary)
        self.assertNotIn('injected | cell', row)
        note = next(n for n in notes if 'seed' in n)
        self.assertNotIn('\n', note)
        self.assertIn('%250A', note, 'percent signs are escaped in workflow commands')
        self.assertTrue(note.startswith('::warning title=seed%3A fail::line1 ::error::'),
            'one line: an injected command is mid-line data, which the runner does not parse')

class VerdictTest(unittest.TestCase):
    FIXTURE = {n: {'result': 'pass'} for n in report.FIXTURE}

    def test_the_verdict_comes_from_required_stages_never_from_green_steps(self):
        green = {'SETUP_OUTCOME': 'success', 'RUN_OUTCOME': 'success', 'CLEANUP_OUTCOME': 'success'}
        installed = {'result': 'installed'}
        # Run 41122307845: setup unavailable, every stage not run, all steps green.
        stages = {n: {'result': 'not-run', 'reason': 'an earlier stage did not pass'} for n in report.FIXTURE}
        self.assertEqual(report.verdict({'result': 'unavailable'}, stages, True)[0], 'unavailable')
        self.assertEqual(report.verdict(installed, stages, False), ('unavailable', 'environment is not-run'))
        self.assertEqual(report.verdict(installed, dict(self.FIXTURE), False), ('pass', 'every required stage passed'))
        self.assertEqual(report.verdict(installed, {**self.FIXTURE, 'seed': {'result': 'fail'}}, False),
            ('fail', 'failed: seed'))
        missing = dict(self.FIXTURE)
        del missing['restore']
        self.assertEqual(report.verdict(installed, missing, False), ('unavailable', 'restore is missing'))
        self.assertEqual(report.verdict(None, self.FIXTURE, False)[0], 'unavailable')
        # SystemUI opt-in: requested stages must be recorded and pass; unrequested ones are not required.
        self.assertEqual(report.verdict(installed, self.FIXTURE, True)[0], 'unavailable')
        with_ui = {**self.FIXTURE, 'systemui-identity': {'result': 'pass'}, 'systemui-foreign': {'result': 'not-run'}}
        self.assertEqual(report.verdict(installed, with_ui, True), ('unavailable', 'systemui-foreign is not-run'))
        self.assertEqual(report.verdict(installed, with_ui, False)[0], 'pass')
        self.assertTrue(green)

    def test_the_verdict_leads_the_summary_and_is_annotated(self):
        tmp = Path(tempfile.mkdtemp(prefix='verdict-'))
        self.addCleanup(__import__('shutil').rmtree, tmp, True)
        (tmp/'e').mkdir()
        (tmp/'e/emulator-setup.json').write_text(json.dumps({'result': 'unavailable', 'reason': 'x'}))
        lines, notes = report.build(tmp/'e', tmp, {'RUN_OUTCOME': 'success', 'CLEANUP_OUTCOME': 'success'})
        self.assertIn('**Functional verdict: unavailable** (emulator setup is unavailable). '
            'Job and step success do not imply it.', lines)
        self.assertEqual(notes[0], '::warning title=functional verdict%3A unavailable::emulator setup is unavailable')
        self.assertTrue(any(n.startswith('::notice title=cleanup%3A success') for n in notes), 'cleanup stays separate')

class KvmReportTest(unittest.TestCase):
    """EXP006-KVM-001: the KVM grant and its restoration are reported, never inferred."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='kvm-report-'))
        self.addCleanup(__import__('shutil').rmtree, self.tmp, True)
        (self.tmp/'e').mkdir()

    def rows(self, document=None, enabled=True):
        if document is not None:
            (self.tmp/'e/kvm-access.json').write_text(json.dumps(document))
        lines, notes = report.build(self.tmp/'e', self.tmp, {'KVM_ACL': 'true' if enabled else 'false'})
        return [l for l in lines if l.startswith('| kvm')], notes

    def test_disabled_granted_refused_and_unobserved_restore(self):
        self.assertEqual(self.rows(enabled=False)[0], ['| kvm access | disabled | kvm_acl input false: no ACL change |'])
        rows, notes = self.rows({'grant': {'result': 'granted', 'uid': 1001}, 'restore': {'result': 'restored', 'reason': 'cleanup'}})
        self.assertEqual(rows, ['| kvm access | granted | granted |', '| kvm restore | restored | cleanup |'])
        self.assertTrue(any(n.startswith('::notice title=kvm restore%3A restored') for n in notes))
        rows, notes = self.rows({'grant': {'result': 'granted'}})
        self.assertEqual(rows[1], '| kvm restore | not observed | no restore record |', 'a missing restore is never restored')
        self.assertTrue(any(n.startswith('::warning title=kvm restore%3A not observed') for n in notes))
        rows, notes = self.rows({'grant': {'result': 'refused', 'reason': 'the mask rights r-- would not let a rw entry take effect'},
            'restore': {'result': 'restore-failed', 'reason': '/dev/kvm was replaced; it is not touched'}})
        self.assertEqual(rows[0], '| kvm access | refused | the mask rights r-- would not let a rw entry take effect |')
        self.assertTrue(any(n.startswith('::warning title=kvm restore%3A restore-failed') for n in notes))
        (self.tmp/'e/kvm-access.json').unlink()
        rows, _ = self.rows()
        self.assertEqual(rows[0], '| kvm access | missing | missing |')

    def test_a_granted_kvm_never_makes_the_verdict_pass_by_itself(self):
        (self.tmp/'e/emulator-setup.json').write_text(json.dumps({'result': 'installed'}))
        (self.tmp/'e/results.json').write_text(json.dumps({'stages': {
            n: {'result': 'not-run'} for n in report.FIXTURE}}))
        lines, notes = report.build(self.tmp/'e', self.tmp, {'KVM_ACL': 'true', 'RUN_OUTCOME': 'success'})
        self.assertTrue(notes[0].startswith('::warning title=functional verdict%3A unavailable'))

class WorkflowTest(unittest.TestCase):
    def test_the_systemui_input_is_manual_boolean_default_false_and_forwarded_only_when_true(self):
        self.assertIn("      systemui:\n        description: 'Also run the opt-in SystemUI stages on the emulator image (EXP006)'"
            "\n        type: boolean\n        default: false\n", WORKFLOW)
        run = WORKFLOW[WORKFLOW.index('      - name: Run the experiment'):WORKFLOW.index('      - name: Evidence')]
        self.assertIn('SYSTEMUI: ${{ inputs.systemui }}', run)
        self.assertIn('if [ "$SYSTEMUI" = "true" ]; then flag=(--systemui); fi', run)
        self.assertNotIn('${{ inputs.systemui }}\n', run.split('run: |')[1], 'the input reaches the shell only via env')
        for value, expected in (('true', ['--systemui']), ('false', []), ('', [])):
            script = 'SYSTEMUI=%s; flag=(); if [ "$SYSTEMUI" = "true" ]; then flag=(--systemui); fi; ' \
                'printf "%%s\\n" "${flag[@]}"' % value
            out = subprocess.run(['bash', '-c', script], capture_output=True, text=True).stdout.split()
            self.assertEqual(out, expected, value)

    def test_the_summary_runs_last_always_and_keeps_existing_bounds(self):
        steps = WORKFLOW[WORKFLOW.index('    steps:'):]
        self.assertLess(steps.index('name: Remove the test key and AVD'), steps.index('name: Stage and cleanup summary'))
        summary = steps[steps.index('name: Stage and cleanup summary'):]
        for needle in ('if: always()', 'CLEANUP_OUTCOME: ${{ steps.cleanup.outcome }}', 'SETUP_OUTCOME: ${{ steps.setup.outcome }}',
                'RUN_OUTCOME: ${{ steps.run.outcome }}', '--summary "$GITHUB_STEP_SUMMARY"'):
            self.assertIn(needle, summary)
        for needle in ('permissions:\n  contents: read', 'timeout-minutes: 30', 'runs-on: ubuntu-24.04',
                'cancel-in-progress: false', 'test_report.py -v'):
            self.assertIn(needle, WORKFLOW)
        code = (HERE/'report.py').read_text().split('"""', 2)[2]
        for banned in ('subprocess', 'logcat', 'read_bytes', 'secrets', 'GITHUB_TOKEN'):
            self.assertNotIn(banned, code, banned)

if __name__ == '__main__':
    unittest.main()
