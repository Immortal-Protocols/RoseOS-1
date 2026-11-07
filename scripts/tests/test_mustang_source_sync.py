"""Source sync uses exact argv, the pinned checkout, and a bounded stop."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts')]
import mustang_source_sync as sync


class SourceSyncRecipeTest(unittest.TestCase):
    def approval(self):
        record = sync.load()
        return {'schema': 'roseos.mustang-source-sync-approval.v1', 'scope': 'source-sync-only',
                'recipeSha256': sync.recipe_sha256(),
                **{flag: record[flag] for flag in sync.FLAGS}}

    def test_committed_recipe_is_not_admitted_and_pins_the_literal_revision(self):
        record = sync.load()
        self.assertIsNone(record['approval'])
        self.assertFalse(record['executed'])
        self.assertFalse(record['compileAuthorized'])
        self.assertFalse(record['enrollmentClaimed'])
        self.assertFalse(record['oidcTested'])
        self.assertEqual(record['argv'], sync.canonical_argv())
        self.assertEqual(record['argv'], sync.canonical_argv({'ROSE_REVISION': ''}))
        self.assertEqual(record['argv'], sync.canonical_argv({'ROSE_REVISION': 'deadbeef'}))
        flat = [part for command in record['argv'] for part in command]
        self.assertIn(sync.REVISION, flat)
        self.assertNotIn('deadbeef', ''.join(flat))
        self.assertNotIn('$ROSE_REVISION', ''.join(flat))
        init = [command for command in record['argv'] if 'repo' in command and 'init' in command][0]
        self.assertEqual(init[init.index('-b')+1], sync.REVISION)
        self.assertEqual(sync.main(), 4)
        with self.assertRaisesRegex(ValueError, 'fields differ'):
            sync.admit(None)

    def test_repo_commands_run_as_mustang_runner_in_the_source_directory(self):
        for command in sync.canonical_argv():
            if 'repo' not in command:
                continue
            self.assertEqual(command[:8], ['/usr/bin/env', '--chdir', '/tmp', '--', 'sudo', '-H', '-u', 'mustang-runner'])
            self.assertEqual(command[11], sync.SOURCE)
            self.assertNotIn('root', command)

    def test_parent_hash_admits_source_sync_only(self):
        got = sync.admit(self.approval())
        self.assertEqual(got['admitted'], 'source-sync-only')
        self.assertFalse(got['executed'])
        self.assertEqual(got['argv'], sync.canonical_argv())
        stamped = self.approval()
        stamped['approvedAt'] = '2026-11-07T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'fields differ'):
            sync.admit(stamped)
        wrong = self.approval()
        wrong['recipeSha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'hash differs'):
            sync.admit(wrong)
        broader = self.approval()
        broader['compileAuthorized'] = True
        with self.assertRaisesRegex(ValueError, 'more than source sync'):
            sync.admit(broader)

    def test_argv_equality_rejects_a_changed_revision_or_an_extra_command(self):
        record = json.loads(json.dumps(sync.load()))
        init = [command for command in record['argv'] if 'init' in command][0]
        init[init.index('-b')+1] = 'deadbeef'
        with self.assertRaisesRegex(ValueError, 'argv differs'):
            sync.validate(record)
        extra = json.loads(json.dumps(sync.load()))
        extra['argv'].append(['repo', 'sync', '-c'])
        with self.assertRaisesRegex(ValueError, 'argv differs'):
            sync.validate(extra)

    def test_host_head_and_manifest_fail_closed(self):
        sync.require_before_sync(
            '5585425566393384049\n', '4183bff8-6be4-46bb-a641-2193d19813ee',
            'mustang-runner 750\n', sync.REVISION+'\n',
            sync.MANIFEST_SHA+'  os/pinned-manifest.xml\n')
        with self.assertRaisesRegex(ValueError, 'instance differs'):
            sync.require_host('0\n', '4183bff8-6be4-46bb-a641-2193d19813ee')
        with self.assertRaisesRegex(ValueError, 'disk uuid differs'):
            sync.require_host('5585425566393384049', 'other')
        with self.assertRaisesRegex(ValueError, 'git HEAD'):
            sync.require_before_sync('5585425566393384049', '4183bff8-6be4-46bb-a641-2193d19813ee',
                                     'mustang-runner 750', 'deadbeef', sync.MANIFEST_SHA+'  os/pinned-manifest.xml')
        with self.assertRaisesRegex(ValueError, 'manifest hash'):
            sync.require_before_sync('5585425566393384049', '4183bff8-6be4-46bb-a641-2193d19813ee',
                                     'mustang-runner 750', sync.REVISION, '0'*64+'  os/pinned-manifest.xml')

    def test_resolved_manifest_requires_commit_revisions(self):
        count = sync.project_count()
        good = '<manifest>'+''.join(f'<project name="p{i}" revision="{"a"*40}"/>' for i in range(count))+'</manifest>'
        evidence = sync.require_resolved(good.encode())
        self.assertEqual(evidence['projectCount'], count)
        self.assertFalse(evidence['executed'])
        self.assertFalse(evidence['compileAuthorized'])
        bad = good.replace('a'*40, 'main', 1)
        with self.assertRaisesRegex(ValueError, 'not a commit'):
            sync.require_resolved(bad.encode())

    def test_failure_and_timeout_request_vm_stop(self):
        stops = []
        sync.watch_sync(0, 10, stops.append)
        self.assertEqual(stops, [])
        with self.assertRaisesRegex(ValueError, 'VM stop'):
            sync.watch_sync(1, 10, stops.append)
        self.assertEqual(stops[-1]['reason'], 'source-sync-failed')
        self.assertEqual(stops[-1]['stopArgv'][:4], sync.STOP_ARGV)
        with self.assertRaisesRegex(ValueError, 'bounded timeout'):
            sync.watch_sync(None, sync.SYNC_TIMEOUT_SECONDS, stops.append)
        self.assertEqual(stops[-1]['reason'], 'source-sync-timeout')
        with self.assertRaisesRegex(ValueError, 'crash stop'):
            sync.watch_sync(None, 0, stops.append, timeout=14400)
        with self.assertRaisesRegex(ValueError, 'VM stop'):
            sync.on_command('git rev-parse', 1, stops.append)
        self.assertEqual(stops[-1]['command'], 'git rev-parse')
        self.assertLess(sync.SYNC_TIMEOUT_SECONDS, 14400)


if __name__ == '__main__':
    unittest.main()
