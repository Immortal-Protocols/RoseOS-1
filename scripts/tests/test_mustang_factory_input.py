"""Offline input/lifecycle checks; no actual download, login or guest action."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
sys.path.insert(0, str(ROOT/'scripts/tests'))
import mustang_factory_input as factory
import mustang_ephemeral as ephemeral
import os_release_shape as shape


class Response(io.BytesIO):
    status = 200

    def __init__(self, data, pin, **headers):
        super().__init__(data)
        self.url = pin['url']
        self.headers = {'Content-Length': str(pin['sizeBytes']), **headers}

    def geturl(self): return self.url


class FactoryInputTest(unittest.TestCase):
    def env(self):
        return {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_WORKFLOW_REF': 'Immortal-Protocols/RoseOS-1/.github/workflows/mustang-ota-dedicated.yml@refs/heads/main',
                'GITHUB_REPOSITORY_ID': '1428996648', 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1',
                'GITHUB_SHA': 'a'*40, 'GITHUB_JOB': 'factory'}

    def pin(self, data=b'public factory fixture'):
        return {'file': 'factory.zip', 'url': 'https://dl.google.com/dl/android/aosp/factory.zip',
                'sha256': hashlib.sha256(data).hexdigest(), 'sizeBytes': len(data)}

    def test_main_only_exclusive_scope(self):
        event = {'inputs': {'factory_only': True}}
        lane, run, pin = factory.guard(self.env(), event)
        self.assertEqual(lane['vm']['instanceId'], '5585425566393384049')
        self.assertEqual(pin['sizeBytes'], 11366092596)
        for key in factory.FLAGS-{'factory_only'} | {'unknown'}:
            with self.subTest(key=key), self.assertRaises(ValueError):
                factory.guard(self.env(), {'inputs': {'factory_only': True, key: True}})
        for change in ({'GITHUB_RUN_ATTEMPT': '2'}, {'GITHUB_REF': 'refs/heads/other'},
                       {'GITHUB_REPOSITORY_ID': '1'}, {'GITHUB_EVENT_NAME': 'pull_request'}):
            with self.assertRaises(ValueError): factory.guard(dict(self.env(), **change), event)

    def test_plan_does_not_connect_or_spawn(self):
        with patch.object(sys, 'argv', ['mustang_factory_input.py']), \
                patch.object(factory.urllib.request, 'build_opener', side_effect=AssertionError('network')), \
                patch.object(subprocess, 'run', side_effect=AssertionError('process')):
            self.assertEqual(factory.main(), 4)

    def test_dedicated_guard_rejects_mixed_scope_before_process_or_network(self):
        spec = importlib.util.spec_from_file_location('factory_guard_test', ROOT/'scripts/mustang-dedicated.py')
        dedicated = importlib.util.module_from_spec(spec);spec.loader.exec_module(dedicated)
        with tempfile.TemporaryDirectory() as tmp:
            event = Path(tmp)/'event.json'
            event.write_text(json.dumps({'inputs': {'factory_only': True, 'publish': True}}))
            env = dict(self.env(), MUSTANG_FACTORY_ONLY='true', MUSTANG_DRY_RUN='true', GITHUB_EVENT_PATH=str(event))
            with patch.dict(os.environ, env, clear=True), patch.object(sys, 'argv', ['dedicated', 'guard']), \
                    patch.object(subprocess, 'run', side_effect=AssertionError('process')):
                with self.assertRaisesRegex(ValueError, 'exclusive'):
                    dedicated.main()

    def test_exact_stream_hash_readback_and_no_overwrite(self):
        data = b'public factory fixture';pin = self.pin(data)
        with tempfile.TemporaryDirectory() as tmp, patch.object(factory, 'space', return_value=1 << 40):
            p = Path(tmp)
            observed = factory.download(p, pin, opener=lambda *_a, **_k: Response(data, pin))
            self.assertTrue(observed['verified'])
            self.assertEqual(observed['sha256'], pin['sha256'])
            self.assertEqual((p/pin['file']).read_bytes(), data)
            self.assertFalse((p/(pin['file']+'.part')).exists())
            self.assertEqual((p/pin['file']).stat().st_nlink, 1)
            with self.assertRaises(ValueError): factory.download(p, pin)
            self.assertEqual((p/pin['file']).read_bytes(), data)

    def test_short_long_and_wrong_hash_preserve_partial(self):
        pin = self.pin()
        for data in (b'short', b'x'*100, b'x'*pin['sizeBytes']):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(factory, 'space', return_value=1 << 40):
                p = Path(tmp);observation = {}
                with self.assertRaises(ValueError):
                    factory.download(p, pin, opener=lambda *_a, **_k: Response(data, pin), observation=observation)
                self.assertFalse((p/pin['file']).exists())
                self.assertTrue((p/(pin['file']+'.part')).exists())
                self.assertFalse(observation['verified'])
                self.assertIn('elapsedSeconds', observation)

    def test_http_identity_rejected_before_file_creation(self):
        pin = self.pin()
        for change in ({'status': 403}, {'url': 'https://other.invalid/factory.zip'},
                       {'headers': {'Content-Length': '21', 'Content-Encoding': 'gzip'}},
                       {'headers': {'Content-Length': 'poison'}}, {'headers': {'Content-Length': '999'}}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(factory, 'space', return_value=1 << 40):
                response = Response(b'public factory fixture', pin)
                for k, v in change.items(): setattr(response, k, v)
                with self.assertRaises(ValueError):
                    factory.download(Path(tmp), pin, opener=lambda *_a, **_k: response)
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_collision_link_reserve_and_timeout_stop_before_publication(self):
        pin = self.pin()
        with tempfile.TemporaryDirectory() as tmp, patch.object(factory, 'space', return_value=1 << 40):
            p = Path(tmp);part = p/(pin['file']+'.part');part.symlink_to('/does-not-exist')
            with self.assertRaises(ValueError): factory.download(p, pin)
            self.assertTrue(part.is_symlink());part.unlink()
            with self.assertRaises(ValueError):
                factory.download(p, pin, clock=iter([0, 3601, 3602]).__next__,
                                 opener=lambda *_a, **_k: Response(b'public factory fixture', pin))
            self.assertFalse((p/pin['file']).exists())
        with tempfile.TemporaryDirectory() as tmp, patch.object(factory.os, 'statvfs') as space:
            space.return_value.f_bavail = 1;space.return_value.f_frsize = 1;space.return_value.f_favail = 5000
            with self.assertRaises(ValueError): factory.space(Path(tmp), 100)

    def test_redirect_cannot_leave_official_https_host(self):
        handler = factory.OfficialRedirect()
        for url in ('http://dl.google.com/input', 'https://other.invalid/input',
                    'https://user@dl.google.com/input', 'https://dl.google.com:444/input'):
            with self.assertRaises(ValueError): handler.redirect_request(None, None, 302, '', {}, url)

    def test_failed_attempt_keeps_receipt_and_partial(self):
        lane, run, pin = factory.guard(self.env(), {'inputs': {'factory_only': True}})
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp);identity = dict(run=run, builder=lane['vm'], diskUuid='fixture')
            paths = {'storageRoot': storage}
            def failed_download(directory, _pin, **kwargs):
                (directory/'fixture.part').write_bytes(b'partial')
                kwargs['observation']['downloadedBytes'] = 7
                raise ValueError('factory input total timeout')
            from contextlib import nullcontext
            with patch.object(factory, 'guest', return_value=(lane, identity, paths, pin)), \
                    patch.object(factory.deps, 'lock', return_value=nullcontext()), \
                    patch.object(factory, 'download', side_effect=failed_download):
                with self.assertRaises(ValueError): factory.execute(self.env())
            receipt = json.loads((storage/'mustang-factory-123-1'/factory.RECEIPT).read_text())
            self.assertFalse(receipt['success'])
            self.assertFalse(receipt['authenticatedStop'])
            self.assertEqual(receipt['download']['downloadedBytes'], 7)
            self.assertEqual((storage/factory.DIRECTORY/'fixture.part').read_bytes(), b'partial')

    def test_workflow_is_separate_guest_job_and_owned_cleanup(self):
        jobs = shape.jobs((ROOT/'.github/workflows/mustang-ota-dedicated.yml').read_text())
        body = jobs['factory']
        self.assertIn('if: inputs.factory_only', body)
        self.assertIn('mustang_factory_input.py run --execute', body)
        self.assertIn('mustang_factory_input.py report', body)
        for forbidden in ('id-token:', 'sudo ', 'actions-gcp-login', 'generate-all', 'secret', 'm -j'):
            self.assertNotIn(forbidden, body)
        self.assertIn('- factory', jobs['cleanup'].split('steps:', 1)[0])
        self.assertEqual(ephemeral.queue_decision([{'name': 'factory', 'status': 'in_progress'}], 100), 'ready')
        for role in ('builder', 'signer'):
            self.assertIn('inputs.factory_only == false', jobs[role])


if __name__ == '__main__': unittest.main()
