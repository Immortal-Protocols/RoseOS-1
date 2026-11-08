"""Offline synthetic public-tool archive safety checks."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import mustang_yarn as yarn


class Response(io.BytesIO):
    status=200
    def __init__(self,data):
        super().__init__(data);self.headers={'Content-Length':str(len(data))}
    def geturl(self):return yarn.URL


class YarnTest(unittest.TestCase):
    def bundle(self, entries=None):
        output=io.BytesIO()
        with tarfile.open(fileobj=output,mode='w:gz') as archive:
            for name,data,kind in entries or [('package/bin/yarn.js',b'fixture script',tarfile.REGTYPE),
                                             ('package/lib/cli.js',b'fixture CLI',tarfile.REGTYPE)]:
                entry=tarfile.TarInfo(name);entry.type=kind;entry.mode=0o755;entry.size=len(data)
                if kind==tarfile.SYMTYPE:entry.linkname='../../outside';entry.size=0
                archive.addfile(entry,io.BytesIO(data) if entry.isfile() else None)
        return output.getvalue()

    def fixture(self,tmp,data):
        root=Path(tmp)/'checkout';(root/'os/build-only').mkdir(parents=True)
        record={'version':'1.22.22','url':yarn.URL,'sizeBytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        (root/yarn.PIN).write_text(json.dumps(record));attempt=Path(tmp)/'attempt';attempt.mkdir()
        return root,attempt

    def test_exact_bundle_is_verified_and_extracted_without_system_changes(self):
        data=self.bundle()
        with tempfile.TemporaryDirectory() as tmp:
            root,attempt=self.fixture(tmp,data)
            result=yarn.install(attempt,time.monotonic()+10,root=root,opener=lambda *_a,**_k:Response(data))
            self.assertEqual(result['archiveSha256'],hashlib.sha256(data).hexdigest())
            self.assertEqual(len(result['catalog']),2);self.assertFalse(result['systemPackagesChanged'])
            self.assertFalse(result['credentialsUsed']);self.assertEqual(Path(result['path']).read_bytes(),b'fixture script')
            with self.assertRaises(FileExistsError):yarn.install(attempt,time.monotonic()+10,root=root)

    def test_wrong_bytes_are_retained_but_never_extracted(self):
        data=self.bundle()
        with tempfile.TemporaryDirectory() as tmp:
            root,attempt=self.fixture(tmp,data);wrong=b'x'*len(data)
            with self.assertRaisesRegex(ValueError,'SHA-256 differs'):
                yarn.install(attempt,time.monotonic()+10,root=root,opener=lambda *_a,**_k:Response(wrong))
            self.assertEqual((attempt/'yarn-tools/yarn.tgz').read_bytes(),wrong)
            self.assertFalse((attempt/'yarn-tools/package').exists())

    def test_tar_links_traversal_special_and_duplicate_members_are_refused(self):
        cases=[[('package/link',b'',tarfile.SYMTYPE)], [('package/../outside',b'bad',tarfile.REGTYPE)],
               [('/package/absolute',b'bad',tarfile.REGTYPE)], [('package/pipe',b'',tarfile.FIFOTYPE)],
               [('package/same',b'a',tarfile.REGTYPE),('package/same',b'b',tarfile.REGTYPE)]]
        for entries in cases:
            with self.subTest(entries=entries),tempfile.TemporaryDirectory() as tmp:
                data=self.bundle(entries);root,attempt=self.fixture(tmp,data)
                with self.assertRaises(ValueError):
                    yarn.install(attempt,time.monotonic()+10,root=root,opener=lambda *_a,**_k:Response(data))
                self.assertFalse((Path(tmp)/'outside').exists())

    def test_deadline_http_and_unpacked_limits_fail_closed(self):
        data=self.bundle()
        with tempfile.TemporaryDirectory() as tmp:
            root,attempt=self.fixture(tmp,data)
            with self.assertRaisesRegex(ValueError,'timeout'):
                yarn.install(attempt,time.monotonic()-1,root=root,opener=lambda *_a,**_k:(_ for _ in ()).throw(AssertionError('network')))
            self.assertEqual(list(attempt.iterdir()),[])
            response=Response(data);response.status=403
            with self.assertRaisesRegex(ValueError,'HTTP'):
                yarn.install(attempt,time.monotonic()+10,root=root,opener=lambda *_a,**_k:response)
        with tempfile.TemporaryDirectory() as tmp,patch.object(yarn,'UNPACKED_BYTES',1):
            root,attempt=self.fixture(tmp,data)
            with self.assertRaisesRegex(ValueError,'unpacked byte bound'):
                yarn.install(attempt,time.monotonic()+10,root=root,opener=lambda *_a,**_k:Response(data))

    def test_redirect_and_alternate_source_pins_are_refused(self):
        with self.assertRaises(ValueError):yarn.NoRedirect().redirect_request(None,None,302,'',{},'https://elsewhere.invalid')
        data=self.bundle()
        with tempfile.TemporaryDirectory() as tmp:
            root,_attempt=self.fixture(tmp,data);path=root/yarn.PIN;record=json.loads(path.read_text())
            record['url']='https://elsewhere.invalid/tool';path.write_text(json.dumps(record))
            with self.assertRaises(ValueError):yarn.pin(root)


if __name__=='__main__':unittest.main()
