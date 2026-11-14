"""Immutable signed-artifact publication, without a native builder or signer."""
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import urllib.error
import zipfile

ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'scripts'),str(Path(__file__).parent)]
import mustang_signed_delivery as signed
import mustang_hosted_sign as hosted
import mustang_ota as ota
import mustang_lane
import os_release_shape


class SignedDeliveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.home=Path(self.tmp.name)
        self.source=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
        self.run='90000000002'
        self.event=dict(inputs=dict(retained_compile_run=signed.RUN,publish='true'))
        self.env=dict(GITHUB_RUN_ID=self.run,GITHUB_RUN_ATTEMPT='1',GITHUB_SHA=self.source,
            GITHUB_WORKFLOW_SHA=self.source,GITHUB_REF='refs/heads/main',GITHUB_EVENT_NAME='workflow_dispatch',
            GITHUB_WORKFLOW_REF='Immortal-Protocols/RoseOS-1/'+signed.recovery.WORKFLOW+'@refs/heads/main',
            GITHUB_REPOSITORY='Immortal-Protocols/RoseOS-1',GITHUB_REPOSITORY_ID='1428996648',
            GITHUB_JOB='guard',MUSTANG_RETAINED_COMPILE_RUN=signed.RUN,MUSTANG_PUBLISH='true')
        common=dict(run_attempt=1,workflow_id=408460629,path=signed.recovery.WORKFLOW,head_branch='main',
            event='workflow_dispatch',repository=dict(id=1428996648),head_repository=dict(id=1428996648))
        self.producer=dict(common,id=int(signed.RUN),run_number=49,head_sha=signed.SOURCE,
            created_at='2026-11-12T03:47:34Z',status='completed',conclusion='success')
        self.current=dict(common,id=int(self.run),run_number=50,head_sha=self.source,
            created_at='2026-11-12T04:50:00Z',status='in_progress',conclusion=None)
        self.old_jobs=[]
        for name,result in (('guard','success'),('artifact-admission','success'),('signer','success'),('publisher','skipped')):
            self.old_jobs.append(dict(name=name,run_id=int(signed.RUN),run_attempt=1,head_sha=signed.SOURCE,
                status='completed',conclusion=result))
        self.old_jobs[2].update(id=130958593634,runner_group_id=6,labels=['mustang-signer-4core'],steps=[
            dict(name=name,status='completed',conclusion='success') for name in (
                'Full byte, hash, archive, and peak preflight before any signer secret',
                'Sign with the checkout entry point, never a guest-supplied executable',
                'Run python3 scripts/actions-gcp-login.py logout', 'Upload the hosted signed outputs')])
        p=signed.ARTIFACT
        self.artifacts=[dict(id=p['id'],name=p['name'],size_in_bytes=p['sizeBytes'],digest='sha256:'+p['sha256'],
            expired=False,workflow_run=dict(id=int(signed.RUN),head_sha=signed.SOURCE,repository_id=1428996648,
            head_repository_id=1428996648))]
        self.phase('guard')
        # Metadata/byte cases use a compatible synthetic source. Current main
        # can legitimately change a profile and invalidate old artifact custody;
        # that production refusal is covered by the artifact compatibility tests.
        compatible = signed.recovery.compatible
        def fixture_compatible(source, root=signed.recovery.ROOT, git=None):
            clean = lambda args: SimpleNamespace(returncode=0, stdout='')
            return compatible(source, root=root, git=git or clean)
        binding = patch.object(signed.recovery, 'compatible', fixture_compatible)
        binding.start();self.addCleanup(binding.stop)

    def phase(self,phase):
        self.env['GITHUB_JOB']=phase
        self.jobs=[dict(name='guard',status='in_progress' if phase=='guard' else 'completed',
            conclusion=None if phase=='guard' else 'success',run_id=int(self.run),run_attempt=1,head_sha=self.source)]
        if phase=='publisher':self.jobs.append(dict(name=phase,status='in_progress',conclusion=None,
            run_id=int(self.run),run_attempt=1,head_sha=self.source))
        self.jobs.extend(dict(name=name,status='completed',conclusion='skipped') for name in
            ('controller','enroll','builder','artifact-admission','signer','cleanup'))

    def api(self,path):
        base='/repos/Immortal-Protocols/RoseOS-1/actions/runs/'
        values={base+signed.RUN:self.producer,base+self.run:self.current,
            base+signed.RUN+'/attempts/1/jobs?per_page=100':dict(total_count=len(self.old_jobs),jobs=self.old_jobs),
            base+self.run+'/attempts/1/jobs?per_page=100':dict(total_count=len(self.jobs),jobs=self.jobs),
            base+signed.RUN+'/artifacts?per_page=100':dict(total_count=len(self.artifacts),artifacts=self.artifacts)}
        self.assertIn(path,values);return copy.deepcopy(values[path])

    def auth(self):return signed.authenticate(self.env,self.event,api=self.api)

    def test_exact_producer_and_distinct_publication_run(self):
        for phase in ('guard','publisher'):
            self.phase(phase);value=self.auth()
            self.assertEqual(value['signedProducerRun'],signed.RUN)
            self.assertEqual(value['publicationRun'],self.run)

    def test_wrong_source_repository_workflow_attempt_or_result_refused(self):
        for row,key,value in [(self.producer,'head_sha','f'*40),(self.producer,'run_attempt',2),
                (self.producer,'path','other.yml'),(self.producer,'conclusion','failure'),
                (self.current,'head_branch','other'),(self.current,'run_number',49)]:
            old=row[key];row[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.auth()
            row[key]=old
        self.current['head_repository']['id']=1
        with self.assertRaises(ValueError):self.auth()

    def test_signer_failure_missing_signature_step_or_wrong_route_refused(self):
        signer=self.old_jobs[2]
        for key,value in [('conclusion','failure'),('runner_group_id',5),('labels',['other'])]:
            old=signer[key];signer[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.auth()
            signer[key]=old
        signer['steps'][1]['conclusion']='skipped'
        with self.assertRaises(ValueError):self.auth()

    def test_already_published_or_current_native_signer_work_refused(self):
        self.old_jobs[3]['conclusion']='success'
        with self.assertRaises(ValueError):self.auth()
        self.old_jobs[3]['conclusion']='skipped'
        for name in ('builder','signer','cleanup'):
            row=next(j for j in self.jobs if j['name']==name);row['conclusion']='success'
            with self.subTest(name=name),self.assertRaises(ValueError):self.auth()
            row['conclusion']='skipped'

    def test_expired_foreign_ambiguous_and_changed_artifact_refused(self):
        row=self.artifacts[0]
        for key,value in [('expired',True),('size_in_bytes',1),('digest','sha256:'+'f'*64),('name','other')]:
            old=row[key];row[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.auth()
            row[key]=old
        row['workflow_run']['head_sha']='f'*40
        with self.assertRaises(ValueError):self.auth()
        row['workflow_run']['head_sha']=signed.SOURCE
        self.artifacts.append(copy.deepcopy(row))
        with self.assertRaises(ValueError):self.auth()

    def test_partial_or_dry_wrong_event_and_other_selector_refused(self):
        for key,value in [('publish','false'),('dry_run','true'),('source_only','true'),('retained_compile_run','other')]:
            event=copy.deepcopy(self.event);event['inputs'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):signed.authenticate(self.env,event,api=self.api)
        self.env['GITHUB_EVENT_NAME']='pull_request'
        with self.assertRaises(ValueError):self.auth()

    def fixture(self,changes=None,extra=None):
        data={name:b'metadata\n' for name in signed.MEMBERS}
        data['signed-target_files.zip']=b'signed target archive'
        data['staged/'+signed.OTA]=b'signed OTA package'
        outputs={key:dict(file=name,sizeBytes=len(data[name]),sha256=hashlib.sha256(data[name]).hexdigest()) for key,name in
            (('signedTargetFiles','signed-target_files.zip'),('ota','staged/'+signed.OTA))}
        stage=dict(runId=signed.RUN,runAttempt='1',recipeSource=signed.SOURCE,
            artifactProducer=signed.recovery.PROOF,ota=dict(outputs['ota'],file=signed.OTA))
        data['staged/mustang-ota-receipt.json']=json.dumps(stage).encode()
        receipt=dict(schema=hosted.RECEIPT_SCHEMA,device='mustang',signer=hosted.SIGNER,secretVersion=hosted.SECRET_VERSION,
            workflow=signed.recovery.WORKFLOW,runId=signed.RUN,runAttempt='1',sourceSha=signed.SOURCE,outputs=outputs,
            publicStage=dict(path='staged',receiptSha256=hashlib.sha256(data['staged/mustang-ota-receipt.json']).hexdigest()))
        data['mustang-hosted-sign-receipt.json']=json.dumps(receipt).encode()
        data.update(changes or {})
        path=self.home/'artifact.zip'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('staged/',b'')
            for name,value in data.items():z.writestr(name,value)
            if extra:z.writestr(*extra)
        pin=dict(signed.ARTIFACT,sizeBytes=path.stat().st_size,sha256=hosted.sha256_file(path))
        return path,pin,data,receipt

    def test_streamed_extraction_keeps_original_receipts_and_no_signed_target_copy(self):
        path,pin,data,_=self.fixture();out=self.home/'out'
        digest=signed.extract(path,out,pin=pin)
        self.assertEqual(digest,hashlib.sha256(data['mustang-hosted-sign-receipt.json']).hexdigest())
        self.assertFalse((out/'signed-target_files.zip').exists())
        self.assertEqual((out/'staged'/signed.OTA).read_bytes(),data['staged/'+signed.OTA])
        receipt=signed.bind(out,dict(EXPECTED_SIGNED_RECEIPT_SHA256=digest))
        self.assertEqual(receipt['runId'],signed.RUN)
        self.assertEqual((out/'mustang-hosted-sign-receipt.json').read_bytes(),data['mustang-hosted-sign-receipt.json'])

    def test_wrong_whole_archive_and_member_bytes_refused(self):
        path,pin,_,_=self.fixture();out=self.home/'out'
        with self.assertRaises(ValueError):signed.extract(path,out,pin=dict(pin,sha256='f'*64))
        path,pin,_,_=self.fixture(changes={'signed-target_files.zip':b'X'*len(b'signed target archive')})
        with self.assertRaises(ValueError):signed.extract(path,out,pin=pin)

    def test_path_escape_links_duplicate_and_foreign_members_refused(self):
        for i,name in enumerate(('../escape','staged/../escape','/absolute','foreign','staged/'+signed.OTA)):
            path,pin,_,_=self.fixture(extra=(name,b'x'))
            with self.subTest(name=name),self.assertRaises(ValueError):signed.extract(path,self.home/str(i),pin=pin)
        link=zipfile.ZipInfo('foreign-link');link.create_system=3;link.external_attr=(stat.S_IFLNK|0o777)<<16
        path,pin,_,_=self.fixture(extra=(link,b'/etc/passwd'))
        with self.assertRaises(ValueError):signed.extract(path,self.home/'link',pin=pin)

    def test_existing_output_and_tampered_stage_or_receipt_refused(self):
        path,pin,_,_=self.fixture();out=self.home/'out';digest=signed.extract(path,out,pin=pin)
        with self.assertRaises(FileExistsError):signed.extract(path,out,pin=pin)
        env=dict(EXPECTED_SIGNED_RECEIPT_SHA256=digest)
        (out/'staged/mustang-ota-receipt.json').write_text('{}')
        with self.assertRaises(ValueError):signed.bind(out,env)
        with self.assertRaises(ValueError):signed.bind(out,{})

    def test_streaming_download_bounds_digest_and_credentials_not_forwarded(self):
        raw=b'x'*((1<<20)+37);calls=[]
        class Response(io.BytesIO):
            status=200
            def read(self,size=-1):
                self_test.assertEqual(size,1<<20)
                return super().read(size)
        self_test=self
        def opener(request,timeout):
            calls.append(request)
            if len(calls)==1:raise urllib.error.HTTPError(request.full_url,302,'redirect',
                {'Location':'https://allowed.blob.core.windows.net/container/zip'},None)
            self.assertNotIn('Authorization',request.headers)
            return Response(raw)
        pin=dict(signed.ARTIFACT,sizeBytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        signed.download(self.home/'download','existing-test-token',open_http=opener,pin=pin)
        self.assertEqual(len(calls),2)
        self.assertEqual((self.home/'download').read_bytes(),raw)
        with self.assertRaises(ValueError):signed.download(self.home/'bad','test',open_http=lambda *a,**k:Response(raw),
            pin=dict(pin,sizeBytes=1))
        with self.assertRaises(ValueError):signed.download(self.home/'hash','test',open_http=lambda *a,**k:Response(raw),
            pin=dict(pin,sha256='f'*64))

    def test_storage_denial_and_unsafe_redirect_stop_once(self):
        for code,location in [(403,None),(302,'http://bad.blob.core.windows.net/a'),
                (302,'https://github.com.evil.test/zip'),(302,'https://user:pass@a.blob.core.windows.net/a')]:
            calls=[]
            def opener(request,timeout):
                calls.append(request)
                raise urllib.error.HTTPError(request.full_url,code,'refused',{'Location':location} if location else {},None)
            with self.subTest(code=code,location=location),self.assertRaises(ValueError):
                signed.download(self.home/'no-download','test',open_http=opener)
            self.assertEqual(len(calls),1)

    def test_workflow_explicit_status_reaches_publisher_and_skips_build_sign(self):
        jobs=os_release_shape.jobs((ROOT/'.github/workflows/mustang-ota-dedicated.yml').read_text())
        self.assertIn('!cancelled()',jobs['publisher'].split('steps:',1)[0])
        self.assertIn("inputs.retained_compile_run == '43150538877'",jobs['publisher'].split('steps:',1)[0])
        for name in ('controller','builder','signer'):
            self.assertIn("inputs.retained_compile_run != '43150538877'",jobs[name].split('steps:',1)[0])
        p=jobs['publisher']
        self.assertLess(p.index('mustang_signed_delivery.py fetch'),p.index('verify-staged'))
        self.assertLess(p.index('verify-staged'),p.index('actions/create-github-app-token'))
        self.assertIn('EXPECTED_SIGNED_RECEIPT_SHA256:',p)
        self.assertNotIn('id-token:',p)

    def test_publisher_cli_context_checks_current_metadata_without_native_admission(self):
        dedicated=ota.module('signed_publisher_context','mustang-dedicated.py')
        self.phase('publisher')
        event_path=self.home/'event.json';event_path.write_text(json.dumps(self.event))
        env=dict(self.env,GITHUB_EVENT_PATH=str(event_path))
        actual=signed.authenticate
        def authenticate(env,event):return actual(env,event,api=self.api)
        def output(argv,**kwargs):return '' if 'status' in argv else self.source+'\n'
        with patch.dict('os.environ',env,clear=True),patch.object(signed,'authenticate',side_effect=authenticate), \
                patch.object(dedicated,'guard',side_effect=AssertionError('native guard')), \
                patch.object(dedicated.subprocess,'check_output',side_effect=output):
            lane,cfg=dedicated.context(True)
            self.assertEqual(lane,mustang_lane.configuration())
            self.assertTrue(cfg['publication']['enabled'])

    def test_dedicated_verifier_keeps_signed_producer_and_uses_publication_context(self):
        dedicated=ota.module('signed_publisher_verify','mustang-dedicated.py')
        path,pin,_,_=self.fixture();out=self.home/'out';digest=signed.extract(path,out,pin=pin)
        stage=json.loads((out/'staged/mustang-ota-receipt.json').read_text())
        stage.update(builder=mustang_lane.configuration()['vm'],workflow=signed.recovery.WORKFLOW)
        with patch.dict('os.environ',dict(self.env,EXPECTED_SIGNED_RECEIPT_SHA256=digest),clear=True), \
                patch.object(dedicated.ota,'verify_staged',return_value=stage):
            self.assertEqual(dedicated.verify(out/'staged',mustang_lane.configuration(),{},True),stage)
            stage['runId']=self.run
            with self.assertRaises(ValueError):dedicated.verify(out/'staged',mustang_lane.configuration(),{},True)


if __name__=='__main__':unittest.main()
