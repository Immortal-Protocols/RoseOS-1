"""Exact run46 artifact recovery; offline metadata and byte-failure fixtures only."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'scripts'),str(Path(__file__).parent)]
import mustang_artifact_delivery as recovery
import mustang_hosted_sign as hosted
import mustang_retained
import mustang_clock
import mustang_lane
import os_release_shape


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.home=Path(self.tmp.name)
        self.source=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
        self.run='90000000001';self.event=dict(inputs=dict(retained_compile_run=recovery.RUN,publish='true'))
        self.event_path=self.home/'event.json';self.event_path.write_text(json.dumps(self.event))
        self.env=dict(GITHUB_RUN_ID=self.run,GITHUB_RUN_ATTEMPT='1',GITHUB_SHA=self.source,
            GITHUB_WORKFLOW_SHA=self.source,GITHUB_REF='refs/heads/main',GITHUB_EVENT_NAME='workflow_dispatch',
            GITHUB_WORKFLOW_REF='Immortal-Protocols/RoseOS-1/'+recovery.WORKFLOW+'@refs/heads/main',
            GITHUB_REPOSITORY='Immortal-Protocols/RoseOS-1',GITHUB_REPOSITORY_ID='1428996648',
            GITHUB_JOB='guard',GCP_SIGNER_SERVICE_ACCOUNT=hosted.SIGNER,GITHUB_EVENT_PATH=str(self.event_path),MUSTANG_RETAINED_COMPILE_RUN=recovery.RUN)
        common=dict(run_attempt=1,workflow_id=408460629,path=recovery.WORKFLOW,head_branch='main',
                    event='workflow_dispatch',repository=dict(id=1428996648),head_repository=dict(id=1428996648))
        self.producer=dict(common,id=int(recovery.RUN),run_number=46,head_sha=recovery.SOURCE,
            created_at='2026-11-11T18:03:16Z',status='completed',conclusion='failure')
        self.current=dict(common,id=int(self.run),run_number=47,head_sha=self.source,
            created_at='2026-11-11T21:30:00Z',status='in_progress',conclusion=None)
        self.old_jobs=[]
        for i,(name,result) in enumerate((('guard','success'),('controller','success'),('enroll','success'),
                ('builder','failure'),('cleanup','success'),('signer','skipped'),('publisher','skipped'))):
            self.old_jobs.append(dict(id=i+1,name=name,status='completed',conclusion=result,
                run_id=int(recovery.RUN),head_sha=recovery.SOURCE,run_attempt=1))
        self.builder=self.old_jobs[3];self.builder.update(id=130709837750,runner_group_id=5,runner_name='mustang-'+recovery.RUN,
            steps=[dict(name=name,conclusion='success') for name in (
                'Prepare, allocate, integrate and compile once on Mustang disk',
                'Require unsigned raised-edge proof before the hosted signing bundle',
                'Bundle the same-run unsigned target-files, otatools, and JDK bytes',
                'Metadata gate only; no payload byte verification')]+[
                dict(name='Upload the bundled inputs only after the metadata gate',conclusion='failure')])
        self.artifacts=[dict(id=p['id'],name=p['name'],size_in_bytes=p['sizeBytes'],digest='sha256:'+p['sha256'],expired=False,
            workflow_run=dict(id=int(recovery.RUN),head_sha=recovery.SOURCE,repository_id=1428996648,head_repository_id=1428996648))
            for p in recovery.ARTIFACTS.values()]
        self.calls=[];self.phase('guard')

    def phase(self,name):
        self.env['GITHUB_JOB']=name
        self.jobs=[]
        for i in ('guard','artifact-admission','signer'):
            if i=='artifact-admission' and name=='guard' or i=='signer' and name!='signer':continue
            active=i==name
            self.jobs.append(dict(id=100+len(self.jobs),name=i,run_id=int(self.run),head_sha=self.source,
                run_attempt=1,status='in_progress' if active else 'completed',conclusion=None if active else 'success',
                runner_group_id=6,labels=['mustang-signer-4core']))
        for i in ('controller','builder'):
            self.jobs.append(dict(id=200+len(self.jobs),name=i,status='completed',conclusion='skipped'))

    def api(self,path):
        self.calls.append(path);base='/repos/Immortal-Protocols/RoseOS-1/actions/runs/'
        found={base+recovery.RUN:self.producer,base+self.run:self.current,
            base+recovery.RUN+'/attempts/1/jobs?per_page=100':dict(total_count=len(self.old_jobs),jobs=self.old_jobs),
            base+self.run+'/attempts/1/jobs?per_page=100':dict(total_count=len(self.jobs),jobs=self.jobs),
            base+recovery.RUN+'/artifacts?per_page=100':dict(total_count=len(self.artifacts),artifacts=self.artifacts)}
        self.assertIn(path,found);return copy.deepcopy(found[path])

    def authenticate(self):return recovery.authenticate(self.env,self.event,api=self.api)

    def test_guard_admission_and_signer_authenticate_exact_producer_without_native_work(self):
        for phase in ('guard','artifact-admission','signer'):
            self.phase(phase);result=self.authenticate()
            self.assertTrue(result['artifactProducerVerified']);self.assertFalse(result['signerAdmitted'])
            self.assertFalse(result['payloadBytesVerified'])
        self.assertTrue(all(path.startswith('/repos/Immortal-Protocols/RoseOS-1/actions/runs/') for path in self.calls))

    def test_active_uncleaned_or_previously_signed_original_is_refused(self):
        for job,key,value in [(self.producer,'status','in_progress'),(self.producer,'head_sha','a'*40),
                              (self.old_jobs[4],'conclusion','failure'),(self.old_jobs[5],'conclusion','success')]:
            old=job[key];job[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):self.authenticate()
            job[key]=old
        self.builder['steps'][0]['conclusion']='failure'
        with self.assertRaisesRegex(ValueError,'compile/collection'):self.authenticate()

    def test_missing_corrupt_foreign_expired_and_duplicate_artifact_metadata_are_refused(self):
        for key,value in [('size_in_bytes',1),('digest','sha256:'+'0'*64),('expired',True),('id',1),('name','other')]:
            row=self.artifacts[1];old=row[key];row[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.authenticate()
            row[key]=old
        self.artifacts[1]['workflow_run']['head_sha']='b'*40
        with self.assertRaisesRegex(ValueError,'identity differs'):self.authenticate()
        self.artifacts[1]['workflow_run']['head_sha']=recovery.SOURCE
        removed=self.artifacts.pop()
        with self.assertRaisesRegex(ValueError,'missing or ambiguous'):self.authenticate()
        self.artifacts.extend([removed,copy.deepcopy(removed)])
        with self.assertRaisesRegex(ValueError,'missing or ambiguous'):self.authenticate()

    def test_recovery_cannot_start_native_work_or_select_a_partial_run(self):
        self.jobs[-1].update(status='in_progress',conclusion=None)
        with self.assertRaisesRegex(ValueError,'cannot start a native'):self.authenticate()
        for name in ('publish','dry_run','source_only','retained_compile_run'):
            event=copy.deepcopy(self.event);event['inputs'][name]='false' if name=='publish' else 'true'
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'complete signing/publication'):
                recovery.scope(self.env,event)
        env=dict(self.env,GITHUB_RUN_ATTEMPT='2')
        with self.assertRaisesRegex(ValueError,'context differs'):recovery.scope(env,self.event)

    def test_original_build_allocation_source_and_hashes_survive_distinct_delivery_binding(self):
        value=recovery.rebound(self.run,self.source);old=recovery.original()
        self.assertEqual(value['clockAllocation'],old['clockAllocation']);self.assertEqual(value['unsigned'],old['unsigned'])
        self.assertEqual(value['fullRunSource'],old['fullRunSource']);self.assertEqual(value['build'],'2611111800')
        self.assertEqual(mustang_retained.compile_context(value),(recovery.RUN,'1',recovery.SOURCE))
        cfg=json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
        mustang_clock.require_bundle(value,mustang_lane.configuration(),cfg)
        recovery.require_bundle(value);hosted.require_full_bundle_source(value)
        for key in ('unsigned','clockAllocation','fullRunSource'):
            changed=copy.deepcopy(value);changed[key]={}
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'pinned producer'):recovery.require_bundle(changed)
        value['artifactProducer']['source']='0'*40
        with self.assertRaisesRegex(ValueError,'proof differs'):mustang_retained.compile_context(value)

    def test_signer_manifest_matches_independent_admission_and_wrong_bytes_never_reach_secrets(self):
        self.phase('artifact-admission');out=self.home/'admission'
        value=recovery.prepare(self.env,self.event,out,api=self.api)
        digest=hosted.sha256_file(out/'mustang-signing-bundle.json')
        self.phase('signer');self.env['EXPECTED_FULL_BUNDLE_SHA256']=digest
        other=self.home/'signer';recovery.prepare(self.env,self.event,other,api=self.api)
        self.assertEqual(digest,hosted.sha256_file(other/'mustang-signing-bundle.json'))
        hosted.authenticate_full_run(value,self.env,api=self.api)
        bad=self.home/'bad';bad.write_bytes(b'wrong bytes')
        config=hosted.load_capacity();config['activeRunner']=dict(label='offline-fixture',ssdBytes=10**18)
        with self.assertRaisesRegex(ValueError,'hash differs'):
            hosted.evaluate(value,ROOT,10**18,files={k:bad for k in ('unsigned','otatools','jdk','generateRelease')},
                secret_access=lambda:self.fail('must not access any secret'),capacity_config=config,env=self.env)
        self.env['EXPECTED_FULL_BUNDLE_SHA256']='0'*64
        with self.assertRaisesRegex(ValueError,'bundle hashes differ'):
            recovery.prepare(self.env,self.event,self.home/'wrong',api=self.api)

    def test_changed_delivery_tools_or_original_record_are_refused(self):
        def git(args):return SimpleNamespace(returncode=0,stdout='scripts/host-sign.py\n' if args[0]=='diff' else '')
        with self.assertRaisesRegex(ValueError,'inputs changed'):recovery.compatible(self.source,git=git)
        path=self.home/recovery.RECORD;path.parent.mkdir(parents=True);value=recovery.original();value['build']='2611111900'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'document differs'):recovery.original(self.home)

    def test_queued_original_cleanup_requires_all_recovery_steps_before_signer_admission(self):
        self.old_jobs[4].update(status='queued',conclusion=None)
        recovery.authenticate(self.env,self.event,api=self.api,cleanup_ready=False)
        with self.assertRaisesRegex(ValueError,'queued cleanup must complete'):self.authenticate()
        self.phase('artifact-admission')
        with self.assertRaisesRegex(ValueError,'queued cleanup must complete'):self.authenticate()
        names=('Stop only the authenticated original Mustang owner',
               'Clear original JIT metadata after owned STOP','Remove only the original offline group5 runner')
        self.jobs[1]['steps']=[dict(name=n,status='completed',conclusion='success') for n in names]
        self.authenticate()
        self.phase('signer');self.jobs[1]['steps']=[dict(name=n,status='completed',conclusion='success') for n in names]
        self.authenticate()
        self.jobs[1]['steps'][-1]['conclusion']='failure'
        with self.assertRaisesRegex(ValueError,'queued cleanup must complete'):self.authenticate()
        self.old_jobs[4].update(status='in_progress',conclusion=None)
        with self.assertRaisesRegex(ValueError,'original job outcome'):self.authenticate()

    def test_recovered_controller_owner_matches_original_bytes_and_only_stops_original_boot(self):
        self.phase('artifact-admission');owner=self.home/'owner.json'
        record=recovery.recover_owner(self.env,self.event,owner,api=self.api)
        native=recovery.original()['fullRunSource']['nativeReceipt']
        self.assertEqual(hashlib.sha256(owner.read_bytes()).hexdigest(),native['controllerOwnershipSha256'])
        vm=mustang_lane.configuration()['vm'];calls=[]
        class Adapter:
            def control_builder(inner,action,*,builder):
                calls.append((action,builder));return 'TERMINATED'
        changed=False
        def execute(argv,**kwargs):
            body=dict(id=vm['instanceId'],name=vm['instance'],status='TERMINATED')
            if 'lastStartTimestamp' in argv[-1]:
                body['lastStartTimestamp']='2026-11-11T19:00:00Z' if changed else '2026-11-11T18:03:51.948Z'
            return SimpleNamespace(returncode=0,stdout=json.dumps(body))
        result=recovery.stop_original(owner,self.home/'stop.json',execute=execute,adapter=Adapter())
        self.assertEqual(result['readback']['status'],'TERMINATED')
        self.assertEqual(calls,[('stop',vm)])
        changed=True
        with self.assertRaisesRegex(ValueError,'start changed'):
            recovery.stop_original(owner,self.home/'foreign-stop.json',execute=execute,adapter=Adapter())
        self.assertEqual(len(calls),1)
        owner.write_text('{}')
        with self.assertRaisesRegex(ValueError,'ownership bytes differ'):
            recovery.stop_original(owner,self.home/'bad-stop.json',execute=execute,adapter=Adapter())
        self.assertEqual(len(calls),1)

    def test_workflow_uses_existing_signer_and_publisher_with_all_native_jobs_skipped(self):
        jobs=os_release_shape.jobs((ROOT/recovery.WORKFLOW).read_text());signer=jobs['signer']
        self.assertIn("inputs.retained_compile_run != '43074045726'",jobs['controller'])
        self.assertIn("inputs.retained_compile_run != '43074045726'",jobs['builder'])
        self.assertIn("needs.controller.result == 'success'",jobs['enroll'])
        self.assertIn("needs.controller.result != 'skipped'",jobs['cleanup'])
        admission=jobs['artifact-admission']
        self.assertNotIn('GCP_SIGNER_SERVICE_ACCOUNT',admission)
        self.assertIn('login "$MUSTANG_CONTROLLER_SERVICE_ACCOUNT"',admission)
        self.assertIn('sweep --execute --run-id 43074045726',admission)
        self.assertIn('mustang_artifact_delivery.py prepare',signer)
        self.assertIn('run-id:',signer);self.assertIn('github-token: ${{ github.token }}',signer)
        self.assertIn('roseos-mustang-signer',signer)
        self.assertLess(signer.index('mustang_hosted_sign.py preflight'),signer.index('actions-gcp-login.py login'))
        self.assertIn("needs.signer.result == 'success'",jobs['publisher'])
        self.assertNotIn('continue-on-error: true',signer)


if __name__=='__main__':unittest.main()
