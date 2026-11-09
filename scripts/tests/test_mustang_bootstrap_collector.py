"""Offline synthetic repositories, guest stand-ins and process fixtures only."""
import copy
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'scripts'),str(Path(__file__).resolve().parent)]
import mustang_bootstrap_collector as collector
import mustang_bootstrap_git as git
import mustang_bootstrap_probe as probes
import mustang_bootstrap_proof as proof
import mustang_existing_lock as locks
import test_mustang_bootstrap_proof as source_fixture
import test_mustang_edge_gate as lane_fixture


class FixtureProbe:
    kind='offline-fixture'

    def __init__(self,home,lane,cfg):
        self.calls=0
        self.value=dict(instanceId=lane['vm']['instanceId'],mount=dict(uuid=cfg['dataFilesystemUuid'],
            target=cfg['dataMount'],fstype='ext4',options='rw,relatime',fsroot='/',
            **{'maj:min':f'{os.major(home.stat().st_dev)}:{os.minor(home.stat().st_dev)}'}))

    def observe(self,home,deadline):
        self.calls+=1
        return copy.deepcopy(self.value)


class CollectorTest(unittest.TestCase):
    def setUp(self):
        # Reuse the real Git/prepared-byte fixture without inheriting its tests.
        self.fixture=source_fixture.SourceProofTest('test_complete_selected_bytes_and_prepared_payloads_match_without_bootstrap_admission')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.home=self.fixture.home;self.root=self.fixture.root
        self.lane=lane_fixture.DedicatedPathsTest().profile(self.home)
        self.storage=self.home/'rose-actions';self.storage.mkdir()
        self.lock=self.home/'mustang.lock';self.lock.touch();self.lock.chmod(0o444)
        self.cfg=dict(dataFilesystemUuid='12345678-1234-1234-1234-123456789abc',dataMount=str(self.home.parent))
        for name,value in (('mustang-lane.json',self.lane),('mustang-runtime.json',self.cfg)):
            (self.root/'os/build-only'/name).write_bytes(proof.canonical(value))
        (self.root/'.gitignore').write_text('ignored-secret\n')
        self.revision=self.fixture.init_project(self.root)
        workflow='Immortal-Protocols/RoseOS-1/'+self.lane['workflow']+'@refs/heads/main'
        self.env=dict(GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_REF='refs/heads/main',
            GITHUB_WORKFLOW_REF=workflow,GITHUB_SHA=self.revision,GITHUB_RUN_ID='999',GITHUB_RUN_ATTEMPT='1',
            GITHUB_REPOSITORY='Immortal-Protocols/RoseOS-1',GITHUB_REPOSITORY_ID='1428996648',
            GITHUB_JOB=collector.JOB,GITHUB_WORKSPACE=str(self.root),MUSTANG_BOOTSTRAP_OBSERVE_ONLY='true')
        self.env['GITHUB_WORKFLOW_SHA']=self.revision
        self.run=dict(workflow=workflow,source=self.revision,runId='999',runAttempt='1')
        self.event=dict(inputs={collector.SCOPE:True})
        self.probe=FixtureProbe(self.home,self.lane,self.cfg)
        self.vendor=self.record('vendor','111',self.fixture.vendor,'vendor-preparation-receipt.json')
        self.host=self.record('hostdeps','112',self.fixture.host,'hostdeps-receipt.json')
        self.binding=patch.object(proof.prepared,'binding',return_value=(self.vendor,self.host));self.binding.start()
        self.addCleanup(self.binding.stop)
        self.runner=patch.object(collector,'runner_identity',return_value=(999,'mustang-runner'));self.runner.start()
        self.addCleanup(self.runner.stop)
        bytecode=patch.object(collector.sys,'dont_write_bytecode',True);bytecode.start();self.addCleanup(bytecode.stop)

    def record(self,kind,number,payload,filename):
        run=dict(self.run,runId=number,source='a'*40)
        payload=dict(payload,identity=dict(builder=self.lane['vm'],diskUuid=self.cfg['dataFilesystemUuid'],run=run),success=True)
        raw=(json.dumps(payload,sort_keys=True)+'\n').encode()
        directory=self.storage/f'mustang-{kind}-{number}-1';directory.mkdir();(directory/filename).write_bytes(raw)
        return dict(receipt=payload,receiptSha256=hashlib.sha256(raw).hexdigest(),runId=number,
                    source='a'*40,stopObservation=dict(instanceId=self.lane['vm']['instanceId']))

    def collect(self,**kwargs):
        return collector.collect(self.lane,self.cfg,kwargs.pop('env',self.env),kwargs.pop('event',self.event),
                                 root=self.root,probe=kwargs.pop('probe',self.probe),**kwargs)

    def test_full_builder_failure_diagnostic_omits_exception_text_paths_and_locals(self):
        import base64
        owner=dict(schema=collector.controller.SCHEMA,run=self.run,
            owner=dict(vm=self.lane['vm'],startedByThisOperation=True))
        env=dict(self.env,GITHUB_JOB='builder',MUSTANG_DRY_RUN='false',MUSTANG_PUBLISH='true',
            MUSTANG_OWNER_B64=base64.b64encode(proof.canonical(owner)).decode())
        failure=PermissionError(13,'MUST-NOT-PRINT','https://private.test/?token=MUST-NOT-PRINT')
        output=io.StringIO()
        with patch.object(proof,'_observe_prepared_locked',side_effect=failure),contextlib.redirect_stdout(output):
            result=self.collect(env=env,event={'inputs':{'publish':True}},full_run=True)
        self.assertTrue(result['receiptWritten']);self.assertFalse(result['receipt']['success'])
        observed=json.loads(output.getvalue())
        self.assertEqual(observed['failureType'],'PermissionError');self.assertEqual(observed['failureErrno'],13)
        self.assertTrue(observed['failureFrames'])
        self.assertNotIn('MUST-NOT-PRINT',output.getvalue());self.assertNotIn('https:',output.getvalue())
        self.assertNotIn('MUST-NOT-PRINT',json.dumps(result['receipt']))

    def test_full_builder_exports_unknown_names_without_their_contents(self):
        import base64
        names=['unknown-cache-one','unknown-cache-two']
        for name in names:(self.fixture.source/name).write_bytes(b'MUST-NOT-READ-OR-PRINT')
        owner=dict(schema=collector.controller.SCHEMA,run=self.run,
            owner=dict(vm=self.lane['vm'],startedByThisOperation=True))
        env=dict(self.env,GITHUB_JOB='builder',MUSTANG_DRY_RUN='false',MUSTANG_PUBLISH='true',
            MUSTANG_OWNER_B64=base64.b64encode(proof.canonical(owner)).decode())
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            result=self.collect(env=env,event={'inputs':{'publish':True}},full_run=True)
        self.assertTrue(result['receiptWritten']);self.assertFalse(result['receipt']['success'])
        self.assertEqual(result['receipt']['failurePaths'],names)
        self.assertEqual(json.loads(output.getvalue())['failurePaths'],names)
        self.assertNotIn('MUST-NOT-READ-OR-PRINT',output.getvalue())
        self.assertNotIn('sourceCandidate',result['receipt'])

    def test_complete_fixture_writes_hash_verified_candidate_receipt_without_admission(self):
        before=self.lock.stat();result=self.collect();r=result['receipt']
        self.assertTrue(r['success'],r);self.assertTrue(result['receiptWritten'])
        self.assertTrue(r['guestChecksPassed']);self.assertTrue(r['checkoutBytesVerified'])
        self.assertEqual(r['observationKind'],'offline-fixture');self.assertEqual(self.probe.calls,2)
        for key in ('runIdentityObserved','guestIdentityObserved','dataFilesystemIdentityObserved','fullBootstrapVerified',
                    'pristine','pristineRuntimeAttestation','runtimeWired','readyForControlledStart','otaAdmitted',
                    'signingStarted','publicationStarted','authenticatedStop','stopOwnedByCollector'):
            self.assertIs(r[key],False,key)
        self.assertTrue(r['sourceCandidate']['preparedReceiptProvenanceVerified'])
        raw=Path(result['receiptFile']['path']).read_bytes()
        self.assertEqual(json.loads(raw),r);self.assertEqual(hashlib.sha256(raw).hexdigest(),result['receiptFile']['sha256'])
        self.assertEqual(len(raw),result['receiptFile']['sizeBytes'])
        after=self.lock.stat()
        self.assertEqual((before.st_ino,before.st_mode,before.st_size),(after.st_ino,after.st_mode,after.st_size))
        self.assertEqual(Path(result['receiptFile']['path']).stat().st_mode&0o777,0o600)

    def test_scope_confusion_and_unknown_inputs_refuse_before_guest_or_claim(self):
        cases=[None,{},dict(inputs=[]),dict(inputs={collector.SCOPE:False}),
               dict(inputs={collector.SCOPE:True,'typo':False}),dict(inputs={collector.SCOPE:1})]
        cases.extend(dict(inputs={collector.SCOPE:'true',flag:True}) for flag in collector.FLAGS-{collector.SCOPE})
        for event in cases:
            with self.subTest(event=event):
                r=self.collect(event=event)
                self.assertFalse(r['receiptWritten']);self.assertFalse(r['receipt']['success'])
        self.assertEqual(self.probe.calls,0)
        self.assertFalse(list(self.storage.glob('mustang-bootstrap-*')))

    def test_wrong_context_and_privileged_runner_refuse_before_guest(self):
        cases=dict(GITHUB_JOB='builder',GITHUB_REPOSITORY_ID='1',GITHUB_REPOSITORY='other/repo',
            GITHUB_EVENT_NAME='push',GITHUB_REF='refs/heads/branch',GITHUB_WORKFLOW_REF='other',
            GITHUB_RUN_ATTEMPT='2',GITHUB_RUN_ID='../1',GITHUB_SHA='0'*40,
            GITHUB_WORKFLOW_SHA='a'*40,
            GITHUB_WORKSPACE=str(self.fixture.source),MUSTANG_BOOTSTRAP_OBSERVE_ONLY='false',
            MUSTANG_PUBLISH='true',MUSTANG_DRY_RUN='true')
        for key,value in cases.items():
            with self.subTest(key=key):self.assertFalse(self.collect(env=dict(self.env,**{key:value}))['receiptWritten'])
        for identity in ((0,'mustang-runner'),(999,'other-user')):
            with patch.object(collector,'runner_identity',return_value=identity):
                self.assertFalse(self.collect()['receiptWritten'])
        with patch.object(collector.sys,'dont_write_bytecode',False):
            self.assertFalse(self.collect()['receiptWritten'])
        self.assertEqual(self.probe.calls,0)

    def test_wrong_guest_mount_and_device_refuse_without_attempt(self):
        original=copy.deepcopy(self.probe.value)
        for key,value in (('instanceId','99999'),('uuid','wrong'),('target','/'),('fstype','tmpfs'),
                          ('options','ro,relatime'),('fsroot','/subtree'),('maj:min','999:999')):
            self.probe.value=copy.deepcopy(original)
            target=self.probe.value if key=='instanceId' else self.probe.value['mount'];target[key]=value
            with self.subTest(key=key):
                r=self.collect();self.assertFalse(r['receiptWritten']);self.assertFalse(r['receipt']['success'])
        self.assertFalse(list(self.storage.glob('mustang-bootstrap-*')))

    def test_low_receipt_space_and_cross_device_path_refuse(self):
        from types import SimpleNamespace
        space=SimpleNamespace(f_bavail=1,f_frsize=4096,f_bsize=4096,f_favail=1)
        with patch.object(collector.os,'fstatvfs',return_value=space):
            self.assertEqual(self.collect()['receipt']['failure'],'collector-receipt-reserve-unavailable')
        original=proof.deps.plain_dir
        def crossed(path):
            info=original(path)
            if Path(path)==self.root:return SimpleNamespace(st_dev=info.st_dev+1,st_ino=info.st_ino)
            return info
        with patch.object(proof.deps,'plain_dir',side_effect=crossed):
            self.assertEqual(self.collect()['receipt']['failure'],'collector-path-crosses-filesystem')

    def test_missing_held_or_linked_lock_is_never_created_or_mutated(self):
        with locks.shared(self.lock,expected=self.lock):
            self.assertFalse(self.collect()['receiptWritten'])
        self.assertEqual(self.probe.calls,0)
        self.lock.unlink();self.assertFalse(self.collect()['receiptWritten']);self.assertFalse(self.lock.exists())
        outside=self.home/'other-lock';outside.touch();self.lock.symlink_to(outside)
        self.assertFalse(self.collect()['receiptWritten']);self.assertEqual(outside.read_bytes(),b'')

    def test_lock_spans_source_and_receipt_without_a_nested_self_lock(self):
        original=proof._observe_prepared_locked;write=collector.Attempt.write;calls=[]
        def locked(*args,**kwargs):
            self.assertFalse(locks.probe(self.lock,expected=self.lock));calls.append('source')
            return original(*args,**kwargs)
        def written(attempt,*args,**kwargs):
            self.assertFalse(locks.probe(self.lock,expected=self.lock));calls.append('receipt')
            return write(attempt,*args,**kwargs)
        with patch.object(proof,'_observe_prepared_locked',side_effect=locked),patch.object(collector.Attempt,'write',new=written):
            self.assertTrue(self.collect()['receipt']['success'])
        self.assertEqual(calls,['source','receipt']);self.assertTrue(locks.probe(self.lock,expected=self.lock))

    def test_existing_attempt_and_link_are_not_adopted_or_overwritten(self):
        directory=self.storage/'mustang-bootstrap-observation-999-1';directory.mkdir()
        sentinel=directory/collector.RESULT;sentinel.write_bytes(b'preserve existing receipt')
        r=self.collect();self.assertFalse(r['receiptWritten']);self.assertEqual(sentinel.read_bytes(),b'preserve existing receipt')
        sentinel.unlink();directory.rmdir()
        outside=self.home/'unrelated';outside.mkdir();directory.symlink_to(outside,target_is_directory=True)
        self.assertFalse(self.collect()['receiptWritten']);self.assertEqual(list(outside.iterdir()),[])

    def test_changed_checkout_hidden_by_index_is_recorded_as_failure(self):
        target=self.root/'os/build-only/mustang.json'
        self.fixture.command(self.root,'update-index','--assume-unchanged','os/build-only/mustang.json')
        target.write_text('{}')
        self.assertEqual(self.fixture.command(self.root,'status','--porcelain'),b'')
        result=self.collect();self.assertTrue(result['receiptWritten'])
        self.assertEqual(result['receipt']['failure'],'collector-checkout-blob-differs')
        self.assertNotIn('sourceCandidate',result['receipt'])

    def test_ignored_unknown_checkout_bytes_are_refused_before_open(self):
        unknown=self.root/'ignored-secret';unknown.write_bytes(b'not receipt content')
        original=proof.Observation.measure
        def measure(observation,path,*args,**kwargs):
            self.assertNotEqual(path,unknown);return original(observation,path,*args,**kwargs)
        with patch.object(proof.Observation,'measure',new=measure):result=self.collect()
        self.assertTrue(result['receiptWritten']);self.assertEqual(result['receipt']['failure'],'collector-checkout-untracked-path')
        self.assertNotIn('not receipt content',json.dumps(result))

    def test_changed_prepared_receipt_and_source_blob_produce_sanitized_failures(self):
        target=self.storage/'mustang-vendor-111-1/vendor-preparation-receipt.json';target.write_text('{}')
        result=self.collect();self.assertTrue(result['receiptWritten']);self.assertFalse(result['receipt']['success'])
        self.assertEqual(result['receipt']['stage'],'source');self.assertNotIn('sourceCandidate',result['receipt'])
        self.assertTrue(target.exists())

    def test_checkout_or_identity_change_during_source_is_caught_by_readback(self):
        original=proof._observe_prepared_locked
        def mutated(*args,**kwargs):
            result=original(*args,**kwargs);(self.root/'new-after-check').write_bytes(b'mutation');return result
        with patch.object(proof,'_observe_prepared_locked',side_effect=mutated):result=self.collect()
        self.assertEqual(result['receipt']['failure'],'source-namespace-changed')
        self.assertFalse(result['receipt']['success'])

    def test_changed_second_guest_observation_fails_without_claiming_identity(self):
        original=self.probe.observe
        def moved(*args):
            result=original(*args)
            if self.probe.calls==2:result['instanceId']='99999'
            return result
        with patch.object(self.probe,'observe',side_effect=moved):result=self.collect()
        self.assertFalse(result['receipt']['success']);self.assertFalse(result['receipt']['guestIdentityObserved'])
        self.assertEqual(result['receipt']['failure'],'collector-guest-identity-differs')

    def test_replaced_canonical_lock_prevents_receipt_publication_and_keeps_attempt(self):
        original=proof._observe_prepared_locked
        def replaced(*args,**kwargs):
            result=original(*args,**kwargs)
            self.lock.rename(self.home/'retained-canonical-lock');self.lock.touch();self.lock.chmod(0o444)
            return result
        with patch.object(proof,'_observe_prepared_locked',side_effect=replaced):result=self.collect()
        self.assertFalse(result['receiptWritten']);self.assertFalse(result['receipt']['success'])
        self.assertEqual(result['receipt']['failure'],'collector-canonical-lock-changed')
        self.assertTrue((self.home/'retained-canonical-lock').exists())
        self.assertEqual(list((self.storage/'mustang-bootstrap-observation-999-1').iterdir()),[])

    def test_lock_path_must_name_the_actual_held_inode_before_any_guest_probe(self):
        original=locks.shared
        @contextlib.contextmanager
        def replaced(*args,**kwargs):
            with original(*args,**kwargs) as held:
                self.lock.rename(self.home/'retained-original-lock');self.lock.touch();self.lock.chmod(0o444)
                yield held
        with patch.object(locks,'shared',new=replaced):result=self.collect()
        self.assertFalse(result['receiptWritten']);self.assertEqual(self.probe.calls,0)
        self.assertEqual(result['receipt']['failure'],'collector-canonical-lock-changed')

    def test_captured_lane_and_runtime_arguments_must_match_checkout_bytes(self):
        self.cfg['unused-caller-input']='not-in-captured-config'
        result=self.collect();self.assertTrue(result['receiptWritten'])
        self.assertEqual(result['receipt']['failure'],'collector-captured-binding-differs')
        self.assertNotIn('sourceCandidate',result['receipt'])

    def test_tracked_checkout_symlink_is_measured_without_following_target(self):
        external=self.home/'outside-checkout';external.write_bytes(b'never followed')
        (self.root/'link').symlink_to(external)
        self.fixture.command(self.root,'add','link');self.fixture.command(self.root,'commit','--quiet','-m','link fixture')
        revision=self.fixture.command(self.root,'rev-parse','HEAD').decode().strip()
        env=dict(self.env,GITHUB_SHA=revision,GITHUB_WORKFLOW_SHA=revision)
        result=self.collect(env=env);self.assertTrue(result['receipt']['success'],result)
        self.assertEqual(external.read_bytes(),b'never followed')

    def test_source_uses_inherited_expired_deadline_instead_of_starting_another_budget(self):
        with self.assertRaisesRegex(git.Refused,'source-proof-deadline'):
            proof.inspect_source(self.fixture.source,self.fixture.manifest,self.fixture.vendor,self.fixture.host,
                self.fixture.recipe,root=self.root,deadline=time.monotonic()-1)

    def test_source_link_text_reads_are_charged_against_source_byte_limit(self):
        source=self.fixture.source;link=source/'alias';link.symlink_to('build/make')
        observation=proof.Observation(source,proof.Limits(bytes=1))
        with self.assertRaisesRegex(git.Refused,'source-byte-bound'):
            observation.resolve_link(source,'selected-link','alias/README')

    def test_checkout_bytecode_side_effect_is_rejected_as_unknown_content(self):
        (self.root/'__pycache__').mkdir()
        (self.root/'__pycache__/unexpected.pyc').write_bytes(b'not authorized checkout content')
        result=self.collect();self.assertTrue(result['receiptWritten'])
        self.assertEqual(result['receipt']['failure'],'collector-checkout-untracked-path')

    def test_interrupt_preserves_claim_and_releases_lock_without_a_success_receipt(self):
        with patch.object(proof,'_observe_prepared_locked',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.collect()
        self.assertTrue(locks.probe(self.lock,expected=self.lock))
        directory=self.storage/'mustang-bootstrap-observation-999-1'
        self.assertTrue(directory.is_dir());self.assertEqual(list(directory.iterdir()),[])
        result=self.collect();self.assertFalse(result['receiptWritten']);self.assertFalse(result['receipt']['success'])

    def test_attempt_names_cannot_escape_or_select_a_rerun(self):
        for run in ({},dict(runId='../escaped',runAttempt='1'),dict(runId='999',runAttempt='2'),dict(runId=999,runAttempt='1')):
            with self.subTest(run=run),self.assertRaises(git.Refused):collector.Attempt(self.storage,run)

    def test_limits_cannot_be_raised_and_time_is_shared_with_source(self):
        for kwargs in (dict(limits=collector.Limits(seconds=7201)),dict(limits=collector.Limits(checkout_bytes=True)),
                       dict(source_limits=proof.Limits(bytes=151 << 30))):
            with self.subTest(kwargs=kwargs):self.assertFalse(self.collect(**kwargs)['receiptWritten'])
        self.assertEqual(self.probe.calls,0)
        received=[];original=proof._observe_prepared_locked
        def bounded(*args,**kwargs):
            received.append(kwargs['deadline']);self.assertLessEqual(kwargs['deadline'],start+31)
            return original(*args,**kwargs)
        start=time.monotonic()
        with patch.object(proof,'_observe_prepared_locked',side_effect=bounded):
            result=self.collect(limits=collector.Limits(seconds=30))
        self.assertTrue(result['receipt']['success'],result);self.assertEqual(len(received),1)

    def test_checkout_and_receipt_bounds_preserve_claimed_attempt(self):
        result=self.collect(limits=collector.Limits(checkout_bytes=1))
        self.assertFalse(result['receipt']['success']);self.assertTrue(result['receiptWritten'])
        other=dict(self.env,GITHUB_RUN_ID='1000')
        result=self.collect(env=other,limits=collector.Limits(receipt_bytes=1))
        self.assertFalse(result['receiptWritten']);self.assertEqual(result['receipt']['failure'],'collector-receipt-byte-bound')
        self.assertTrue((self.storage/'mustang-bootstrap-observation-1000-1').is_dir())

    def test_receipt_write_is_exclusive_and_replaced_attempt_refuses(self):
        with collector.Attempt(self.storage,self.run) as attempt:
            path=attempt.path;first=attempt.write({'sentinel':True},1000)
            with self.assertRaises(FileExistsError):attempt.write({'sentinel':False},1000)
            self.assertEqual(json.loads(Path(first['path']).read_bytes()),{'sentinel':True})
        other=dict(self.run,runId='1000')
        with collector.Attempt(self.storage,other) as attempt:
            attempt.path.rename(self.storage/'retained-attempt')
            attempt.path.mkdir()
            with self.assertRaisesRegex(git.Refused,'attempt-path-changed'):attempt.write({},1000)
        self.assertEqual(list((self.storage/'retained-attempt').iterdir()),[])

    def test_unknown_exception_payload_is_never_serialized(self):
        with patch.object(proof,'_observe_prepared_locked',side_effect=RuntimeError('TOKEN=do-not-include')):
            result=self.collect()
        self.assertEqual(result['receipt']['failure'],'collector-check-failed')
        self.assertNotIn('TOKEN',json.dumps(result));self.assertTrue(result['receiptWritten'])

    def test_candidate_cannot_turn_on_admission_through_collector(self):
        original=proof._observe_prepared_locked
        def admitted(*args,**kwargs):return dict(original(*args,**kwargs),otaAdmitted=True)
        with patch.object(proof,'_observe_prepared_locked',side_effect=admitted):result=self.collect()
        self.assertFalse(result['receipt']['success']);self.assertFalse(result['receipt']['otaAdmitted'])
        self.assertEqual(result['receipt']['failure'],'collector-source-candidate-differs')

    def test_expiry_before_receipt_publication_cannot_claim_success(self):
        original=collector.Attempt.write
        def expired(attempt,document,maximum,**kwargs):
            return original(attempt,document,maximum,deadline=time.monotonic()-1)
        with patch.object(collector.Attempt,'write',new=expired):result=self.collect()
        self.assertFalse(result['receipt']['success']);self.assertFalse(result['receiptWritten'])
        self.assertEqual(result['receipt']['failure'],'collector-deadline')

    def test_default_cli_cannot_observe_and_workflow_only_calls_cleanup_summary(self):
        script=ROOT/'scripts/mustang_bootstrap_collector.py'
        result=subprocess.run([sys.executable,str(script)],capture_output=True,text=True,check=True)
        plan=json.loads(result.stdout);self.assertFalse(plan['execute']);self.assertFalse(plan['liveObservationEnabled'])
        for flag in ('--execute','--root'):
            result=subprocess.run([sys.executable,str(script),flag],capture_output=True)
            self.assertNotEqual(result.returncode,0)
        workflow=(ROOT/'.github/workflows/mustang-ota-dedicated.yml').read_text()
        self.assertIn('mustang_bootstrap_collector.py lifecycle --execute',workflow)
        self.assertNotIn(collector.SCOPE,workflow);self.assertNotIn('  bootstrap-observe:',workflow)


class ProbeTest(unittest.TestCase):
    def test_metadata_has_no_proxy_redirect_or_unbounded_response(self):
        class Response(io.BytesIO):
            status=200
            headers={'Metadata-Flavor':'Google'}
        opener=unittest.mock.Mock()
        for raw,headers in ((b'12345',{'Metadata-Flavor':'Google'}),(b'9'*65,{'Metadata-Flavor':'Google'}),
                            (b'12345',{}),(b'invalid',{'Metadata-Flavor':'Google'})):
            response=Response(raw);response.headers=headers;opener.open.return_value=response
            with patch.object(probes.urllib.request,'build_opener',return_value=opener) as built:
                if raw==b'12345' and headers:
                    self.assertEqual(probes.metadata_id(time.monotonic()+10),'12345')
                else:
                    with self.assertRaises(git.Refused):probes.metadata_id(time.monotonic()+10)
                handlers=built.call_args.args
                self.assertEqual(handlers[0].proxies,{})
                self.assertIsInstance(handlers[1],probes.NoRedirect)
                request=opener.open.call_args.args[0]
                self.assertEqual(request.full_url,'http://169.254.169.254/computeMetadata/v1/instance/id')
        with self.assertRaisesRegex(git.Refused,'redirect-refused'):probes.NoRedirect().redirect_request(None,None,None,None,None,None)

    def process_fixture(self,payload):
        original=subprocess.Popen;children=[]
        def spawn(argv,**kwargs):
            self.assertEqual(argv[0],'/usr/bin/findmnt');self.assertIn('UUID,TARGET,FSTYPE,OPTIONS,MAJ:MIN,FSROOT',argv)
            self.assertNotIn('GH_TOKEN',kwargs['env']);self.assertTrue(kwargs['start_new_session'])
            child=original([sys.executable,'-c',payload],**kwargs);children.append(child);return child
        return spawn,children

    def test_mount_output_stream_is_bounded_and_owned_fixture_process_ends(self):
        spawn,children=self.process_fixture('import sys,time; sys.stdout.write("x"*20000); sys.stdout.flush(); time.sleep(30)')
        with patch.object(probes.subprocess,'Popen',side_effect=spawn):
            with self.assertRaisesRegex(git.Refused,'mount-output-bound'):probes.mount_record('/fixture',time.monotonic()+10)
        self.assertEqual(len(children),1);self.assertIsNotNone(children[0].poll())

    def test_mount_timeout_ends_only_owned_process(self):
        spawn,children=self.process_fixture('import time; time.sleep(30)')
        with patch.object(probes.subprocess,'Popen',side_effect=spawn):
            with self.assertRaisesRegex(git.Refused,'mount-probe-deadline'):probes.mount_record('/fixture',time.monotonic()+0.2)
        self.assertIsNotNone(children[0].poll())

    def test_mount_json_requires_one_exact_record(self):
        row=dict(uuid='fixture',target='/fixture',fstype='ext4',options='rw',fsroot='/',**{'maj:min':'1:1'})
        for rows in ([row],[],[row,row],[dict(row,children=[])],[dict(uuid='fixture')]):
            code='import sys; sys.stdout.write('+repr(json.dumps(dict(filesystems=rows)))+')'
            spawn,_=self.process_fixture(code)
            with self.subTest(rows=rows),patch.object(probes.subprocess,'Popen',side_effect=spawn):
                if rows==[row]:self.assertEqual(probes.mount_record('/fixture',time.monotonic()+10),row)
                else:
                    with self.assertRaises(git.Refused):probes.mount_record('/fixture',time.monotonic()+10)


if __name__=='__main__':unittest.main()
