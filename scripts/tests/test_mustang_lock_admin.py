"""Offline boundary checks; no root execution, credentials, network or live VM."""
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import mustang_lock_admin as admin


class LockAdminTest(unittest.TestCase):
    def env(self):
        return {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_WORKFLOW_REF': 'Immortal-Protocols/RoseOS-1/.github/workflows/mustang-ota-dedicated.yml@refs/heads/main',
                'GITHUB_REPOSITORY_ID': '1428996648', 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1',
                'GITHUB_SHA': 'a'*40, 'GITHUB_JOB': 'lock-admin'}

    def test_guard_is_exclusive_fresh_main_and_rejects_before_processes(self):
        event = {'inputs': {'lock_admin_only': True}}
        with patch.object(admin.subprocess, 'run', side_effect=AssertionError('process started')):
            lane, run = admin.guard(self.env(), event)
            self.assertEqual(lane['vm']['instanceId'], '5585425566393384049')
            self.assertEqual(run['runAttempt'], '1')
            for key in ('publish', 'dry_run', 'source_only', 'hostdeps_only', 'factory_only', 'unknown'):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    admin.guard(self.env(), {'inputs': {'lock_admin_only': True, key: True}})
            for change in ({'GITHUB_RUN_ATTEMPT': '2'}, {'GITHUB_REF': 'refs/heads/other'},
                           {'GITHUB_REPOSITORY_ID': '1'}, {'GITHUB_EVENT_NAME': 'push'}):
                with self.assertRaises(ValueError):
                    admin.guard(dict(self.env(), **change), event)

    def test_default_and_unexecuted_actions_start_nothing(self):
        with patch.object(admin, 'ready', side_effect=AssertionError('ready')), \
                patch.object(admin, 'wait', side_effect=AssertionError('wait')):
            self.assertEqual(admin.main([]), 4)
            self.assertEqual(admin.main(['ready']), 4)
            self.assertEqual(admin.main(['wait']), 4)

    def test_wrong_job_or_user_refuses_before_host_identity(self):
        with patch.object(admin.deps, 'read_event', return_value={'inputs': {'lock_admin_only': True}}), \
                patch.object(admin.mustang_ota, 'module', side_effect=AssertionError('guest probe')), \
                patch.object(admin.pwd, 'getpwuid', return_value=SimpleNamespace(pw_name='other')):
            with self.assertRaises(ValueError):
                admin.guest(self.env())

    def test_hosted_guard_routes_the_scope_and_keeps_ota_runtime_closed(self):
        spec = importlib.util.spec_from_file_location('lock_admin_dedicated_test', ROOT/'scripts/mustang-dedicated.py')
        dedicated = importlib.util.module_from_spec(spec);spec.loader.exec_module(dedicated)
        with tempfile.TemporaryDirectory() as tmp:
            event = Path(tmp)/'event.json'
            event.write_text(json.dumps({'inputs': {'lock_admin_only': True, 'publish': True}}))
            env = dict(self.env(), GITHUB_JOB='guard', GITHUB_EVENT_PATH=str(event),
                       MUSTANG_LOCK_ADMIN_ONLY='true', MUSTANG_DRY_RUN='true')
            with patch.dict(os.environ, env, clear=True), patch.object(sys, 'argv', ['dedicated', 'guard']), \
                    patch.object(admin.subprocess, 'run', side_effect=AssertionError('process started')):
                with self.assertRaisesRegex(ValueError, 'exclusive'):
                    dedicated.main()
            event.write_text(json.dumps({'inputs': {'lock_admin_only': True}}))
            with patch.dict(os.environ, {**env, 'MUSTANG_DRY_RUN': 'false'}, clear=True), \
                    patch.object(sys, 'argv', ['dedicated', 'guard']), \
                    patch.object(admin.subprocess, 'run', side_effect=AssertionError('process started')):
                with self.assertRaisesRegex(ValueError, 'OTA runtime path closed'):
                    dedicated.main()

    def key_data(self):
        blob = struct.pack('>I', 11)+b'ssh-ed25519'+struct.pack('>I', 32)+bytes(range(32))
        return b'ssh-ed25519 '+base64.b64encode(blob)+b' ignored-comment\n', blob

    def test_host_pin_is_public_only_and_strips_comments(self):
        data, blob = self.key_data()
        result = admin.parse_host_key(data)
        self.assertEqual(result['publicKey'], 'ssh-ed25519 '+base64.b64encode(blob).decode())
        self.assertEqual(result['sha256Fingerprint'], 'SHA256:'+base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('='))
        self.assertNotIn('ignored-comment', json.dumps(result))
        for bad in (b'', b'ssh-rsa AAAA', b'ssh-ed25519 invalid!',
                    b'ssh-ed25519 '+base64.b64encode(blob[:-1]),
                    b'ssh-ed25519 '+base64.b64encode(blob+b'x')):
            with self.assertRaises(ValueError):
                admin.parse_host_key(bad)

    def test_host_key_reader_refuses_links_and_nonroot_owned_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'host.pub'
            path.write_bytes(self.key_data()[0])
            with self.assertRaises(ValueError):
                admin.public_host_key(path)
            linked = Path(tmp)/'linked.pub'
            linked.symlink_to(path)
            with self.assertRaises(admin.deps.readers._Refused):
                admin.public_host_key(linked)

    def test_lock_observation_never_creates_or_replaces_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'mustang.lock'
            self.assertIsNone(admin.observe_lock(path))
            self.assertFalse(path.exists())
            path.write_bytes(b'preserve')
            with self.assertRaises(ValueError):
                admin.observe_lock(path)
            self.assertEqual(path.read_bytes(), b'preserve')
            path.unlink()
            path.symlink_to(Path(tmp)/'missing')
            with self.assertRaises(OSError):
                admin.observe_lock(path)
            self.assertTrue(path.is_symlink())
            self.assertFalse((Path(tmp)/'missing').exists())

    def test_lock_metadata_requires_the_exact_root_owned_empty_regular_file(self):
        good = dict(st_mode=stat.S_IFREG | 0o644, st_uid=0, st_gid=0, st_nlink=1, st_size=0)
        admin.validate_lock_info(SimpleNamespace(**good))
        for change in ({'st_uid': 999}, {'st_gid': 999}, {'st_size': 1}, {'st_nlink': 2},
                       {'st_mode': stat.S_IFREG | 0o600}, {'st_mode': stat.S_IFREG | 0o666},
                       {'st_mode': stat.S_IFIFO | 0o644}):
            with self.assertRaises(ValueError):
                admin.validate_lock_info(SimpleNamespace(**{**good, **change}))

    def fixture(self, tmp, stack):
        directory = Path(tmp)
        lane = {'lockPath': str(directory/'canonical.lock')}
        identity = {'run': {'runId': '123', 'runAttempt': '1', 'source': 'a'*40},
                    'builder': {'instanceId': '5585425566393384049'}, 'diskUuid': 'fixture-disk'}
        stack.enter_context(patch.object(admin, 'guest', return_value=(lane, identity, directory)))
        stack.enter_context(patch.object(admin, 'public_host_key', return_value=admin.parse_host_key(self.key_data()[0])))
        stack.enter_context(patch.object(admin.subprocess, 'run', side_effect=AssertionError('process started')))
        return lane, identity, directory

    def test_ready_publishes_pin_and_expected_scope_without_root_or_credential_actions(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            lane, identity, directory = self.fixture(tmp, stack)
            result = admin.ready({})
            self.assertEqual(result['identity'], identity)
            self.assertTrue(result['externalActionRequired'])
            self.assertFalse(result['credentialsCreatedByJob'])
            self.assertFalse(result['externalActionExecutedByJob'])
            self.assertEqual(result['waitTimeoutSeconds'], 600)
            self.assertFalse(Path(lane['lockPath']).exists())
            self.assertEqual(set(p.name for p in directory.iterdir()), {admin.READY})
            with self.assertRaises(FileExistsError):
                admin.ready({})

    def test_wait_observes_only_and_cannot_attest_the_external_creator(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            lane, _identity, directory = self.fixture(tmp, stack)
            admin.ready({})
            now = {'t': 0}
            def sleep(seconds): now['t'] += seconds
            lock = {'uid': 0, 'gid': 0, 'mode': 0o644, 'sizeBytes': 0, 'available': True}
            with patch.object(admin, 'observe_lock', side_effect=[None, None, lock]) as observe:
                result = admin.wait({}, clock=lambda: now['t'], sleep=sleep)
            self.assertTrue(result['success'])
            self.assertTrue(result['finalGuestIdentityVerified'])
            self.assertFalse(result['creatorAttested'])
            self.assertFalse(result['externalActionExecutedByJob'])
            self.assertEqual(result['elapsedSeconds'], 10)
            self.assertEqual(observe.call_count, 3)
            self.assertFalse(Path(lane['lockPath']).exists())
            self.assertEqual(json.loads((directory/admin.RESULT).read_text()), result)

    def test_existing_valid_lock_needs_no_external_action_or_host_key_read(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            self.fixture(tmp, stack)
            with patch.object(admin, 'observe_lock', return_value={'uid': 0, 'mode': 0o644}), \
                    patch.object(admin, 'public_host_key', side_effect=AssertionError('unnecessary pin read')):
                result = admin.ready({})
            self.assertFalse(result['externalActionRequired'])
            self.assertIsNone(result['publicHostKey'])

    def test_failure_annotation_has_a_fixed_code_without_exception_secrets(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(admin, 'ready', side_effect=ValueError('private secret text')), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(admin.main(['ready', '--execute']), 4)
        self.assertIn('::error title=Mustang lock administration::ready: admission-refused', output.getvalue())
        self.assertNotIn('private', output.getvalue()+errors.getvalue())
        self.assertNotIn('secret', output.getvalue()+errors.getvalue())

    def test_timeout_retains_failed_observation_and_never_initializes(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            lane, _identity, directory = self.fixture(tmp, stack)
            admin.ready({})
            now = {'t': 0}
            def sleep(seconds): now['t'] += seconds
            with self.assertRaisesRegex(ValueError, 'expired'):
                admin.wait({}, clock=lambda: now['t'], sleep=sleep)
            result = json.loads((directory/admin.RESULT).read_text())
            self.assertFalse(result['success'])
            self.assertEqual(result['elapsedSeconds'], 600)
            self.assertFalse(Path(lane['lockPath']).exists())

    def test_foreign_readiness_is_refused_before_lock_polling(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            _lane, _identity, directory = self.fixture(tmp, stack)
            admin.ready({})
            path = directory/admin.READY
            data = json.loads(path.read_text());data['identity']['run']['runId'] = 'other'
            path.write_text(json.dumps(data))
            with patch.object(admin, 'observe_lock', side_effect=AssertionError('polled foreign receipt')):
                with self.assertRaisesRegex(ValueError, 'another run'):
                    admin.wait({})
            self.assertFalse((directory/admin.RESULT).exists())


if __name__ == '__main__':
    unittest.main()
