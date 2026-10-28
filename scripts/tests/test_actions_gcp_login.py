"""actions-gcp-login.py: per-job owner-only gcloud config, request token kept out of argv, logout removes it."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('gcp_login', Path(__file__).resolve().parents[1]/'actions-gcp-login.py')
login_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(login_module)

REQUEST_TOKEN = 'synthetic-actions-request-token'

class GcpLoginTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.env = {'RUNNER_TEMP': str(self.root), 'GITHUB_ENV': str(self.root/'github_env'),
            'GCP_WORKLOAD_IDENTITY_PROVIDER': 'projects/123/locations/global/workloadIdentityPools/roseos-actions/providers/github',
            'ACTIONS_ID_TOKEN_REQUEST_URL': 'https://token.actions.example/x?api-version=2.0',
            'ACTIONS_ID_TOKEN_REQUEST_TOKEN': REQUEST_TOKEN, 'PATH': os.environ['PATH']}
        self.calls = []

    def fake_run(self, argv, env, **kwargs):
        self.calls.append((argv, env['CLOUDSDK_CONFIG']))

    def test_login_writes_protected_url_sourced_credential_and_keeps_token_out_of_argv(self):
        directory = login_module.login(self.env, 'roseos-build-signer@protocols-465723.iam.gserviceaccount.com', run=self.fake_run)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        credential = directory/'credential.json'
        self.assertEqual(stat.S_IMODE(credential.stat().st_mode), 0o600)
        data = json.loads(credential.read_text())
        self.assertEqual(data['type'], 'external_account')
        self.assertIn('audience=https%3A%2F%2Fiam.googleapis.com%2Fprojects%2F123', data['credential_source']['url'])
        self.assertTrue(data['service_account_impersonation_url'].endswith(
            'roseos-build-signer@protocols-465723.iam.gserviceaccount.com:generateAccessToken'))
        (argv, config), = self.calls
        self.assertEqual(config, str(directory))
        self.assertNotIn(REQUEST_TOKEN, ' '.join(argv))
        self.assertEqual((self.root/'github_env').read_text(), f'CLOUDSDK_CONFIG={directory}\n')

    def test_logout_removes_the_config_and_bad_inputs_fail_closed(self):
        login_module.login(self.env, 'a@p.iam.gserviceaccount.com', run=self.fake_run)
        login_module.logout(self.env)
        self.assertFalse((self.root/'gcloud-job-config').exists())
        for key, value in (('GCP_WORKLOAD_IDENTITY_PROVIDER', 'roseos'), ('ACTIONS_ID_TOKEN_REQUEST_TOKEN', '')):
            with self.assertRaises(SystemExit):
                login_module.login(dict(self.env, **{key: value}), 'a@p.iam.gserviceaccount.com', run=self.fake_run)
        with self.assertRaises(SystemExit):
            login_module.login(self.env, 'not-an-account', run=self.fake_run)

if __name__ == '__main__':
    unittest.main()
