"""Offline checks for exclusive lock creation; no real root/guest session."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
spec = importlib.util.spec_from_file_location('mustang_prepare_lock', ROOT/'scripts/mustang-prepare-lock.py')
lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lock)


class LockPreparationTest(unittest.TestCase):
    def test_default_plan_does_not_probe_or_write_and_nonroot_execute_refuses_first(self):
        output = io.StringIO()
        with patch.object(lock, 'prepare', side_effect=AssertionError('executed')), contextlib.redirect_stdout(output):
            self.assertEqual(lock.main([]), 4)
        plan = json.loads(output.getvalue())
        self.assertFalse(plan['execute'])
        self.assertFalse(plan['overwrite'])
        with patch.object(lock.os, 'geteuid', return_value=999), \
                patch.object(lock.mustang_ota, 'module', side_effect=AssertionError('guest probe')):
            with self.assertRaisesRegex(ValueError, 'existing authorized root'):
                lock.prepare()

    def test_root_parent_contract_rejects_other_owners_writable_and_non_directory(self):
        good = dict(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0)
        lock.validate_parent(SimpleNamespace(**good))
        for change in ({'st_uid': 999}, {'st_gid': 999}, {'st_mode': stat.S_IFDIR | 0o775},
                       {'st_mode': stat.S_IFDIR | 0o757}, {'st_mode': stat.S_IFREG | 0o644}):
            with self.assertRaises(ValueError):
                lock.validate_parent(SimpleNamespace(**{**good, **change}))

    def test_exclusive_empty_file_mode_is_correct_even_with_restrictive_umask(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY)
            old = os.umask(0o077)
            try:
                result = lock.create_at(directory)
            finally:
                os.umask(old)
                os.close(directory)
            path = Path(tmp)/lock.NAME
            self.assertEqual(path.read_bytes(), b'')
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            self.assertEqual(result['sizeBytes'], 0)
            self.assertEqual(result['uid'], os.geteuid())

    def test_existing_file_link_and_fifo_are_never_replaced(self):
        for kind in ('file', 'link', 'fifo'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/lock.NAME
                if kind == 'file':
                    path.write_bytes(b'preserve original')
                elif kind == 'link':
                    path.symlink_to(Path(tmp)/'missing-target')
                else:
                    os.mkfifo(path)
                before = path.lstat()
                directory = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    with self.assertRaises(FileExistsError):
                        lock.create_at(directory)
                finally:
                    os.close(directory)
                self.assertEqual(path.lstat().st_ino, before.st_ino)
                self.assertEqual(path.lstat().st_mode, before.st_mode)
                if kind == 'file':
                    self.assertEqual(path.read_bytes(), b'preserve original')
                self.assertFalse((Path(tmp)/'missing-target').exists())

    def test_sync_failure_keeps_created_entry_and_requires_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with patch.object(lock.os, 'fsync', side_effect=OSError('uncertain durability')):
                    with self.assertRaises(OSError):
                        lock.create_at(directory)
            finally:
                os.close(directory)
            self.assertTrue((Path(tmp)/lock.NAME).is_file())

    def test_wrong_guest_refuses_before_parent_open_or_creation(self):
        def wrong_guest(*_args, **_kwargs):
            raise ValueError('wrong VM')
        with patch.object(lock.os, 'geteuid', return_value=0), \
                patch.object(lock.mustang_ota, 'module', return_value=SimpleNamespace(host_identity=wrong_guest)), \
                patch.object(lock, 'parent_fd', side_effect=AssertionError('parent opened')):
            with self.assertRaisesRegex(ValueError, 'wrong VM'):
                lock.prepare()

    def test_root_host_command_has_an_absolute_tool_and_does_not_inherit_credentials(self):
        with patch.dict(os.environ, {'PATH': '/untrusted', 'GH_TOKEN': 'secret', 'BASH_ENV': '/untrusted'}), \
                patch.object(lock.subprocess, 'run', return_value='observed') as run:
            self.assertEqual(lock.host_command(['findmnt', '--noheadings'], capture_output=True, text=True), 'observed')
        self.assertEqual(run.call_args.args[0][0], '/usr/bin/findmnt')
        self.assertEqual(set(run.call_args.kwargs['env']), {'PATH', 'LANG', 'LC_ALL'})
        with patch.object(lock.subprocess, 'run', side_effect=AssertionError('unexpected command ran')):
            with self.assertRaises(ValueError):
                lock.host_command(['/untrusted/findmnt'])

    def test_linked_parent_is_refused_without_following_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            linked = Path(tmp)/'linked'
            linked.symlink_to(tmp, target_is_directory=True)
            with self.assertRaises(lock.readers._Refused):
                lock.parent_fd(linked)


if __name__ == '__main__':
    unittest.main()
