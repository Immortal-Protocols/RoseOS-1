"""Shared Android job harness for backend integration tests."""
import os
import tempfile
import unittest
from pathlib import Path
from rose.app_builder import toolchain
from rose.app_builder.service import Builder
from rose.tests.test_app_builder import SDK, JAVA_HOME, fake_backup

class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._cleanup, tmp)
        self.root = Path(tmp.name) / 'builder'

    @staticmethod
    def _cleanup(tmp):
        for here, dirs, _ in os.walk(tmp.name):
            os.chmod(here, 0o700)
        tmp.cleanup()

    def builder(self, generate, **kw):
        b = Builder(self.root, toolchain.Tools(SDK, JAVA_HOME, timeout_s=300, key_creation=True), generate,
                    backup=fake_backup, **kw)
        self.addCleanup(b.ledger.close)
        return b

    def run_job(self, b, revise=None):
        if revise is None:
            out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r1', 'description': 'Notes'})
        else:
            out = b.revise('d', revise, {'schemaVersion': 1, 'clientRequestId': f'r{len(b.projects()[0]["versions"]) + 2}',
                                         'request': 'more'})
        b.run_next()
        return out, b.job(out['jobId'])
