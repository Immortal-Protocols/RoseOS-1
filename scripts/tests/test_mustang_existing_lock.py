"""Actual read-only flock behavior and private compiler/allocator binding tests."""
import copy
import fcntl
import importlib.util
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import mustang_existing_lock as locks
import mustang_lane


def module(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def profile(home):
    lane=copy.deepcopy(mustang_lane.configuration())
    lane.update(sourceRoot=str(home/'source'),storageRoot=str(home/'rose-actions'),lockPath=str(home/'mustang.lock'),
                infraEvidence='synthetic fixture, not runtime evidence',runnerLabel='synthetic-mustang-runner',
                vm={'project':'synthetic','zone':'synthetic-zone','instance':'synthetic-mustang','instanceId':'12345'},
                diskResource='projects/synthetic/zones/synthetic-zone/disks/synthetic-mustang')
    return lane


class ExistingLockTest(unittest.TestCase):
    def test_real_read_only_exclusion_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'lock';path.touch();path.chmod(0o444);before=path.stat()
            with locks.open_stream(path,expected=path) as stream:
                self.assertFalse(stream.writable());self.assertFalse(os.get_inheritable(stream.fileno()))
            self.assertTrue(locks.probe(path,expected=path))
            with locks.shared(path,expected=path):
                self.assertFalse(locks.probe(path,expected=path))
                with self.assertRaisesRegex(locks.LockUnavailable,'held'):
                    with locks.shared(path,expected=path):pass
            self.assertTrue(locks.probe(path,expected=path))
            self.assertEqual((path.stat().st_ino,path.stat().st_mode,path.stat().st_size),
                             (before.st_ino,before.st_mode,before.st_size))

    def test_absent_wrong_linked_nonempty_and_special_locks_never_create_or_adopt(self):
        for kind in ('absent','wrong','symlink','parent-link','hardlink','nonempty','fifo'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);path=root/'lock';expected=path
                if kind not in ('absent','fifo','symlink','parent-link'):path.touch()
                if kind=='wrong':expected=root/'other'
                if kind=='symlink':(root/'target').touch();path.symlink_to('target')
                if kind=='parent-link':
                    (root/'real').mkdir();(root/'real/lock').touch();(root/'alias').symlink_to('real');path=root/'alias/lock';expected=path
                if kind=='hardlink':os.link(path,root/'alias')
                if kind=='nonempty':path.write_bytes(b'preserve existing bytes')
                if kind=='fifo':os.mkfifo(path)
                self.assertFalse(locks.probe(path,expected=expected))
                with self.assertRaises(locks.LockUnavailable):locks.open_stream(path,expected=expected)
                if kind=='absent':self.assertFalse(path.exists())
                if kind=='nonempty':self.assertEqual(path.read_bytes(),b'preserve existing bytes')

    def test_private_mustang_engine_uses_existing_lock_without_changing_shared_default(self):
        adapter=module('mustang_read_only_engine_fixture','mustang-build-only.py')
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);lane=profile(home);path=home/'mustang.lock'
            with patch.object(adapter.mustang_lane,'configuration',return_value=lane):engine=adapter.engine()
            self.assertFalse(engine.lock_free(str(path)));self.assertFalse(path.exists())
            path.touch();path.chmod(0o444)
            with engine.open_shared_lock(str(path)) as stream:
                self.assertFalse(stream.writable());fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
                self.assertFalse(engine.lock_free(str(path)))
            self.assertTrue(engine.lock_free(str(path)))
            recipe=engine.load(ROOT/'os/build-only/mustang.json',probe=False)
            op={'sourceRoot':None,'downloadDir':home/'download','lock':path,'integrationDir':None,
                'inputsReceipt':None,'node':None,'host':'synthetic','buildNumber':None,'buildDatetime':None,'jobs':None}
            space=lambda _path:SimpleNamespace(f_bavail=10**15,f_frsize=1,f_blocks=2*10**15)
            with locks.shared(path,expected=path):
                blocked=engine.preflight(recipe,'download',op,statvfs=space)
                self.assertIn('the builder lock is held by another job',blocked['problems'])
            ready=engine.preflight(recipe,'download',op,statvfs=space)
            self.assertNotIn('the builder lock is held by another job',ready['problems'])
            with self.assertRaises(locks.LockUnavailable):engine.open_shared_lock(str(home/'other'))
            shared=module('unchanged_blazer_lock_fixture','blazer-build-only.py')
            other=home/'legacy.lock';self.assertTrue(shared.lock_free(str(other)));self.assertTrue(other.is_file())
            self.assertEqual(shared.DEVICE,'blazer')

    def test_dedicated_allocator_default_uses_read_only_lock_and_keeps_override(self):
        allocator=module('mustang_read_only_allocator_fixture','mustang-allocate.py')
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);lane=profile(home);(home/'rose-actions').mkdir();path=home/'mustang.lock';path.touch();path.chmod(0o444)
            inode=path.stat().st_ino
            with patch.object(allocator,'observe',return_value=(set(),{})), \
                    patch.object(allocator.allocator,'shared_lock',side_effect=AssertionError('legacy write lock selected')):
                record=allocator.reserve(str(home),'123','1','a'*40,clock=lambda:1794096000,lane=lane)
            self.assertEqual(record['device'],'mustang');self.assertEqual(path.stat().st_ino,inode)
            with patch.object(allocator,'observe',side_effect=AssertionError('allocation passed a busy lock')):
                with locks.shared(path,expected=path):
                    with self.assertRaises(allocator.Refused):
                        allocator.reserve(str(home),'124','1','a'*40,lane=lane)


if __name__=='__main__':unittest.main()
