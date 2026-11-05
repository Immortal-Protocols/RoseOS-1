#!/usr/bin/env python3
import base64
import importlib.util
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
spec = importlib.util.spec_from_file_location('allocation_recovery', ROOT/'scripts/frankel-allocation-recovery.py')
r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)


class AllocationRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def metadata(self):
        return (dict(id=r.RECEIPT_RUN, head_sha=r.RECEIPT_SOURCE,
            path='.github/workflows/frankel-draft-recovery.yml', event='workflow_dispatch', head_branch='main',
            run_attempt=1, status='completed', conclusion='success'),
            dict(id=r.PIN[0], name=r.PIN[1], size_in_bytes=r.PIN[2], digest='sha256:'+r.PIN[3],
                 expired=False, workflow_run={'id':r.RECEIPT_RUN}))

    def test_exact_receipt_run_and_artifact(self):
        run, artifact = self.metadata(); r.check_receipt_source(run, artifact)
        for key, value in [('id',1), ('head_sha','f'*40), ('event','push'), ('head_branch','other'),
                           ('path','other'), ('run_attempt',2), ('status','in_progress'), ('conclusion','failure')]:
            with self.subTest(key=key), self.assertRaises(r.Refused):
                r.check_receipt_source(dict(run, **{key:value}), artifact)
        for key, value in [('id',1), ('name','other'), ('size_in_bytes',1), ('digest','sha256:'+'f'*64),
                           ('expired',True), ('workflow_run',{'id':1})]:
            with self.subTest(key=key), self.assertRaises(r.Refused):
                r.check_receipt_source(run, dict(artifact, **{key:value}))

    def test_admission_is_fresh_manual_main(self):
        env=dict(GITHUB_REPOSITORY=r.REPO, GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main',
                 GITHUB_RUN_ATTEMPT='1', GITHUB_WORKFLOW_REF=r.REPO+'/'+r.WORKFLOW+'@refs/heads/main', GITHUB_SHA='a'*40)
        r.admission(env)
        for key in env:
            with self.subTest(key=key), self.assertRaises(r.Refused): r.admission(dict(env, **{key:'wrong'}))

    def documents(self):
        record=dict(candidateSha='c'*40,buildTimestamp=123)
        candidate=dict(sha='c'*40,build=r.recovery.BUILD,timestamp='123',channel='beta',device='frankel')
        docs={'allocation/allocation.json':json.dumps(record).encode(),
              'ingest-receipt.json':json.dumps(dict(producerRun=r.recovery.RUN,sourceSha=r.recovery.SOURCE,candidate=candidate)).encode(),
              'publish-receipt.json':json.dumps(dict(producerRun=r.recovery.RUN,recoveryRun=str(r.RECEIPT_RUN),
                   candidate=candidate,draft=True,promoted=False,releaseId=435513431)).encode()}
        return record,candidate,docs

    def zip(self, docs):
        path=self.home/'receipt.zip'
        with zipfile.ZipFile(path,'w') as out:
            for name,data in docs.items(): out.writestr(name,data)
        return path

    def test_validated_allocation_bytes_are_preserved(self):
        record,candidate,docs=self.documents()
        with patch.object(r.recovery,'allocation',return_value=record) as validate:
            data,found=r.extract(self.zip(docs),self.home)
        self.assertEqual(data,docs['allocation/allocation.json']); self.assertEqual(found,candidate)
        validate.assert_called_once_with(self.home)

    def test_receipt_identity_mismatch_refuses(self):
        for change in ({'draft':False},{'promoted':True},{'releaseId':1},{'producerRun':1},{'candidate':{}},{'recoveryRun':'1'}):
            record,candidate,docs=self.documents()
            published=json.loads(docs['publish-receipt.json']); published.update(change)
            docs['publish-receipt.json']=json.dumps(published).encode()
            home=self.home/str(len(list(self.home.iterdir())));home.mkdir()
            with patch.object(r.recovery,'allocation',return_value=record), self.assertRaises(r.Refused):
                r.extract(self.zip(docs),home)

    def test_archive_extra_missing_symlink_and_size_refuse(self):
        record,candidate,docs=self.documents()
        cases=[dict(docs, **{'../outside':b'x'}), {'allocation/allocation.json':b'{}'},
               dict(docs, **{'ingest-receipt.json':b'x'*65537})]
        for index,case in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(r.Refused): r.extract(self.zip(case),self.home)
        path=self.home/'receipt.zip'
        with zipfile.ZipFile(path,'w') as out:
            link=zipfile.ZipInfo('allocation/allocation.json');link.external_attr=(stat.S_IFLNK|0o777)<<16
            out.writestr(link,'/outside')
        with self.assertRaises(r.Refused):r.extract(path,self.home)

    def branch_api(self, data, existing=False, corrupt=False):
        client=Mock();calls=[]
        def request(method,url,label,**kw):
            path=url.split('/repos/'+r.REPO,1)[1];calls.append((method,path,kw))
            if method=='GET' and path=='/git/ref/heads/'+r.BRANCH:
                if any(c[0]=='POST' and c[1]=='/git/refs' for c in calls):return 200,None,{'object':{'sha':'b'*40}}
                return (200 if existing else 404),None,{}
            if method=='GET' and path.startswith('/contents/'):
                if '?ref='+'b'*40 in path:
                    return 200,None,dict(encoding='base64',size=len(data),content=base64.b64encode(b'wrong' if corrupt else data).decode())
                return 404,None,{}
            if method=='GET':return 200,None,{'tree':{'sha':'a'*40}}
            return 201,None,{'sha':'b'*40}
        client._request.side_effect=request
        return client,calls

    def test_only_new_branch_and_single_exact_path_written(self):
        data=b'{"safe":"allocation"}\n';client,calls=self.branch_api(data)
        result=r.write_review_branch(client,'a'*40,data)
        writes=[c for c in calls if c[0]!='GET']
        self.assertEqual([c[1] for c in writes],['/git/trees','/git/commits','/git/refs'])
        self.assertEqual(writes[0][2]['body']['tree'],[dict(path=r.PATH,mode='100644',type='blob',content=data.decode())])
        self.assertEqual(writes[1][2]['body']['parents'],['a'*40])
        self.assertEqual(writes[2][2]['body'],{'ref':'refs/heads/'+r.BRANCH,'sha':'b'*40})
        self.assertEqual(result['commit'],'b'*40)
        self.assertFalse(any(c[0] in ('PATCH','DELETE') for c in calls))

    def test_existing_branch_prevents_all_writes(self):
        client,calls=self.branch_api(b'{}',existing=True)
        with self.assertRaises(r.Refused):r.write_review_branch(client,'a'*40,b'{}')
        self.assertFalse(any(c[0]!='GET' for c in calls))

    def test_existing_file_prevents_all_writes(self):
        client=Mock();client._request.side_effect=[(404,None,{}),(200,None,{})]
        with self.assertRaises(r.Refused):r.write_review_branch(client,'a'*40,b'{}')
        self.assertEqual(client._request.call_count,2)

    def test_denied_read_stops_without_retry_or_write(self):
        client=Mock();client._request.side_effect=r.recovery.github.PublishError('HTTP403')
        with self.assertRaises(r.recovery.github.PublishError):r.recover(client,self.home/'new','a'*40)
        client._request.assert_called_once()

    def test_failed_readback_never_retries_ref_creation(self):
        client,calls=self.branch_api(b'{}',corrupt=True)
        with self.assertRaises(r.Refused):r.write_review_branch(client,'a'*40,b'{}')
        self.assertEqual(sum(c[0]=='POST' and c[1]=='/git/refs' for c in calls),1)

    def test_invalid_receipt_prevents_branch_write(self):
        run,artifact=self.metadata();client=Mock();client._request.side_effect=[(200,None,run),(200,None,artifact)]
        with patch.object(r.recovery,'download'), patch.object(r,'extract',side_effect=r.Refused('bad receipt')), \
                patch.object(r,'write_review_branch') as write, self.assertRaises(r.Refused):
            r.recover(client,self.home/'new','a'*40)
        write.assert_not_called()

    def test_workflow_permissions_and_no_runtime_side_effects(self):
        text=(ROOT/r.WORKFLOW).read_text()
        for expected in ('workflow_dispatch:','contents: write','actions: read','timeout-minutes: 15',
                         'runs-on: ubuntu-24.04','persist-credentials: false'):
            self.assertIn(expected,text)
        for forbidden in ('pull-requests:','id-token:','secrets.','self-hosted','workflow_run:', 'schedule:',
                          'start-builder','--promote','create-github-app-token'):
            self.assertNotIn(forbidden,text)


if __name__=='__main__':unittest.main()
