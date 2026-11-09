"""One integrated source/copy/bundle/auth/STOP contract, using offline stand-ins.

No VM, metadata endpoint, credential, signer executable, allocation or upload is
used. Native-response fixtures below model metadata; they are not live evidence.
"""
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'scripts'),str(Path(__file__).resolve().parent)]
import mustang_bootstrap_collector as collector
import mustang_bootstrap_proof as proof
import mustang_controller as controller
import mustang_hosted_sign as hosted
import mustang_otatools_qualification as qualification
import mustang_tool_closure as closure
import mustang_ota as ota
import test_mustang_edge_gate as edge_fixture
import test_mustang_bootstrap_collector as fixture_module


class FullDeliveryTest(unittest.TestCase):
    def setUp(self):
        self.f=fixture_module.CollectorTest('test_complete_fixture_writes_hash_verified_candidate_receipt_without_admission')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.root=self.f.root;self.home=self.f.home;self.lane=self.f.lane
        self.env=dict(self.f.env,GITHUB_JOB='builder',MUSTANG_DRY_RUN='false',MUSTANG_PUBLISH='true')
        self.owner=dict(schema=controller.SCHEMA,run=self.f.run,
            owner=dict(vm=self.lane['vm'],startedByThisOperation=True))
        self.env['MUSTANG_OWNER_B64']=base64.b64encode(proof.canonical(self.owner)).decode()
        self.event={'inputs':{'publish':True}}
        # Stand-in for the two primitive guest responses. This fixture's
        # instance/UUID belong to /tmp synthetic repositories, not Mustang.
        self.probe=copy.copy(self.f.probe);self.probe.kind='native-read-only'

    def observe(self):
        result=collector.collect(self.lane,self.f.cfg,self.env,self.event,root=self.root,probe=self.probe,full_run=True)
        self.assertTrue(result['receiptWritten'],result)
        return result

    def test_pipeline_refusal_reports_safe_stage_code_before_any_allocation(self):
        pipeline=ota.module('full_delivery_reporting','mustang-pipeline.py')
        for written in (False,True):
            result=dict(receiptWritten=written,receipt=dict(success=False,stage='guest-before',
                failure='collector-data-mount-differs',failurePath='build/make/submodule-fixture',payload='MUST-NOT-PRINT'))
            output=io.StringIO()
            with patch('sys.stdout',output),self.assertRaisesRegex(ValueError,'guest-before: collector-data-mount-differs'):
                pipeline.require_bootstrap_observation(result)
            report=json.loads(output.getvalue())
            self.assertEqual(report['failureCode'],'collector-data-mount-differs')
            self.assertEqual(report['failurePath'],'build/make/submodule-fixture')
            self.assertEqual(report['receiptWritten'],written)
            self.assertNotIn('MUST-NOT-PRINT',output.getvalue())
        result['receipt'].update(stage='https://sensitive.test',failure='token=value',failurePath='https://sensitive.test?secret=token')
        with patch('sys.stdout',io.StringIO()) as output,self.assertRaisesRegex(ValueError,'unknown: collector-refused'):
            pipeline.require_bootstrap_observation(result)
        self.assertNotIn('sensitive',output.getvalue());self.assertNotIn('token',output.getvalue())

    def test_failure_report_exports_exact_existing_collector_receipt_without_resuming(self):
        result=self.observe()
        pipeline=ota.module('full_delivery_receipt_report','mustang-pipeline.py')
        # No prep directory exists yet, as in the real early failure.
        self.assertFalse((self.f.storage/'mustang-prep-999-1').exists())
        document=copy.deepcopy(result['receipt']);document.update(success=False,failure='collector-checkout-blob-differs')
        path=Path(result['receiptFile']['path']);path.write_bytes(proof.canonical(document))
        out=self.home/'failure-report'
        pipeline.collect_reports('999','1',out,lane=self.lane)
        self.assertEqual((out/collector.RESULT).read_bytes(),path.read_bytes())
        self.assertEqual(path.read_bytes(),proof.canonical(document))
        document['run']['runId']='888';path.write_bytes(proof.canonical(document))
        with self.assertRaisesRegex(ValueError,'identity differs'):
            pipeline.collect_reports('999','1',self.home/'foreign-report',lane=self.lane)
        self.assertFalse((self.home/'foreign-report'/collector.RESULT).exists())

    def copy_source(self,result):
        source=self.f.fixture.source
        target=self.f.storage/'mustang-prep-999-1/source'
        recipe=copy.deepcopy(self.f.fixture.recipe)
        # Only synthetic fixture capacity is changed; no live recipe is edited.
        recipe['capacity']['reserveBytes']=0
        engine=proof.shared_engine()
        ok,outcome=engine.construct(recipe,{'sourceRoot':target},source,root=self.root)
        self.assertTrue(ok,outcome)
        checked=proof.verify_copy(source,target,outcome['selectionRecord'],result['receipt']['sourceCandidate'])
        checked.update(run=self.f.run,builder=self.lane['vm'],nativeReceiptSha256=result['receiptFile']['sha256'],sourceRoot=str(target))
        return target,outcome,checked

    def test_owned_full_builder_observes_and_copies_real_fixture_before_make(self):
        result=self.observe();collector.require_full_result(result,self.lane,self.env,self.event)
        target,outcome,checked=self.copy_source(result)
        self.assertTrue(checked['sourceCopyVerified']);self.assertFalse(checked['otaAdmitted'])
        self.assertFalse(checked['pristine']);self.assertEqual(checked['portableInventorySha256'],result['receipt']['sourceCandidate']['portableInventorySha256'])
        self.assertEqual((target/'build/make/README').read_bytes(),(self.f.fixture.source/'build/make/README').read_bytes())
        self.assertEqual((target/'resolved-manifest.xml').read_bytes(),self.f.fixture.manifest)
        # Mutation after the shared copy's own hash is detected before Make.
        (target/'build/make/README').write_bytes(b'changed after copy')
        with self.assertRaisesRegex(proof.git.Refused,'copied-source-readback-differs'):
            proof.verify_copy(self.f.fixture.source,target,outcome['selectionRecord'],result['receipt']['sourceCandidate'])

    def test_absent_optional_cache_passes_observation_shared_copy_and_readback(self):
        shutil.rmtree(self.f.fixture.source/'vendor/adevtool/dl/unpacked')
        result=self.observe();collector.require_full_result(result,self.lane,self.env,self.event)
        target,_outcome,checked=self.copy_source(result)
        self.assertTrue(checked['sourceCopyVerified'])
        self.assertFalse((target/'vendor/adevtool/dl/unpacked').exists())
        self.assertEqual((target/'vendor/adevtool/dl/.find-ignore').read_bytes(),b'')

    def test_pinned_text_checkout_passes_collector_actual_copy_and_readback(self):
        project,files=self.f.fixture.angle_checkout(project_path='external/pigweed',
            attributes=b'* text=auto\n*.bat text eol=crlf\n*.vcxproj eol=crlf\n**/compiled/*.h eol=crlf\n')
        self.f.fixture.command(self.root,'add','--all')
        self.f.fixture.command(self.root,'commit','--quiet','-m','pinned CRLF checkout fixture')
        revision=self.f.fixture.command(self.root,'rev-parse','HEAD').decode().strip()
        self.f.run['source']=revision
        self.env.update(GITHUB_SHA=revision,GITHUB_WORKFLOW_SHA=revision)
        self.env['MUSTANG_OWNER_B64']=base64.b64encode(proof.canonical(self.owner)).decode()
        result=self.observe();collector.require_full_result(result,self.lane,self.env,self.event)
        target,_outcome,checked=self.copy_source(result)
        self.assertTrue(checked['sourceCopyVerified'])
        self.assertEqual(result['receipt']['sourceCandidate']['selectedCheckoutConvertedEntries'],3)
        self.assertEqual((target/'external/pigweed/scripts/trigger.bat').read_bytes(),
                         files['scripts/trigger.bat'].replace(b'\n',b'\r\n'))

    def test_foreign_owner_and_other_preparation_scope_stop_before_guest(self):
        for change in (dict(GITHUB_JOB='source'),dict(MUSTANG_OWNER_B64=''),dict(MUSTANG_PUBLISH='false')):
            result=collector.collect(self.lane,self.f.cfg,dict(self.env,**change),self.event,root=self.root,probe=self.probe,full_run=True)
            self.assertFalse(result['receiptWritten']);self.assertFalse(result['receipt']['success'])
        result=collector.collect(self.lane,self.f.cfg,self.env,{'inputs':{'publish':True,'vendor_only':True}},
            root=self.root,probe=self.probe,full_run=True)
        self.assertFalse(result['receiptWritten']);self.assertEqual(self.probe.calls,0)

    def test_legacy_guard_projection_is_refused_by_the_exact_runtime_binding(self):
        # Mirrors the clock guard's pre-fix output: adding null still changes
        # the captured JSON. The collector must keep rejecting that mutation.
        projected=dict(self.f.cfg,consumedBuilds=None)
        result=collector.collect(self.lane,projected,self.env,self.event,root=self.root,probe=self.probe,full_run=True)
        self.assertTrue(result['receiptWritten'])
        self.assertEqual(result['receipt']['stage'],'bindings')
        self.assertEqual(result['receipt']['failure'],'collector-captured-binding-differs')
        self.assertFalse(result['receipt']['success'])

    def test_copy_record_cannot_launder_changed_pinned_input(self):
        result=self.observe();(self.f.fixture.source/'build/make/README').write_bytes(b'changed before shared copy')
        with self.assertRaisesRegex(proof.git.Refused,'copied-selection-differs'):
            self.copy_source(result)

    def tool_fixture(self,source):
        # Canonical tiny JDK and otatools ZIPs contain inert bytes, never run.
        tree=source/'prebuilts/jdk/jdk21/linux-x86';(tree/'bin').mkdir(parents=True)
        (tree/'bin/java').write_bytes(b'NEVER EXECUTE: fixture java');(tree/'bin/java').chmod(0o755)
        blob=closure.pack_tree(tree)
        (self.root/'script').mkdir();(self.root/'script/generate-release.sh').write_bytes(b'NEVER EXECUTE: fixture script')
        package=self.home/'qualified-tools.zip'
        with zipfile.ZipFile(package,'w') as archive:archive.writestr('bin/ota_from_target_files',b'NEVER EXECUTE: fixture tool')
        expanded=len(b'NEVER EXECUTE: fixture tool')
        inspected=dict(packageSha256=hosted.sha256_file(package),packageBytes=package.stat().st_size,
            expandedBytes=expanded,inventorySha256='d'*64)
        inspected['inventory']=[dict(path='bin/'+name,type='file',mode=0o755) for name in
            ('ota_from_target_files','sign_target_files_apks','aapt2','apksigner','check_ota_package_signature','delta_generator')]
        provenance=dict(jdk=dict(entryCount=1,archiveSha256=hashlib.sha256(blob).hexdigest(),archiveSizeBytes=len(blob)))
        (self.root/qualification.EVIDENCE).write_text(json.dumps({'receipt':{'package':dict(
            path=str(package),sha256=inspected['packageSha256'],sizeBytes=inspected['packageBytes'])}}))
        return package,inspected,provenance

    def bundle_fixture(self):
        result=self.observe();source,outcome,copied=self.copy_source(result)
        package,inspected,provenance=self.tool_fixture(source)
        unsigned=self.home/'unsigned.zip';edge_fixture.fixtures.target_files(unsigned)
        bound=edge_fixture.gate.binding(self.f.revision,self.f.revision,'2026110901','1794182400','999','1','999','1')
        edge=edge_fixture.gate.inspect(unsigned,'unsigned',bound,edge_fixture.descriptor(unsigned))
        collection=dict(schema='roseos.mustang-compile-collection.v1',status='complete',builder=self.lane['vm'],
            workflow=self.lane['workflow'],runId='999',runAttempt='1',build='2026110901',collectorSource=self.f.revision,
            outputs=dict(outDir=str(source/'out-mustang-build-2026110901'),artifacts=dict(
                targetFiles=dict(state='collected',path=str(unsigned),sha256=hosted.sha256_file(unsigned),sizeBytes=unsigned.stat().st_size)),
                actualBuildIdentity=dict(device='mustang',build='2026110901',timestamp='1794182400')),
            roseEdge=edge,candidate=dict(stages={'bootstrap-source':dict(result=result['receipt'],receipt=result['receiptFile']),
                                   'source-copy':dict(result=copied)}))
        path=self.home/'collection.json';path.write_text(json.dumps(collection));out=self.home/'signing-bundle'
        with patch.object(collector.mustang_lane,'configuration',return_value=self.lane), \
                patch.object(hosted,'public_identity',return_value={'fixtureOnly':True}), \
                patch.object(qualification,'inspection_record',return_value=inspected), \
                patch.object(qualification,'inspect_package',return_value=inspected), \
                patch.object(closure,'verify_closure',return_value=provenance):
            bundle=hosted.export_bundle(path,out,root=self.root)
            hosted.require_qualified_bundle(bundle,self.root)
        return bundle,out

    def test_full_bundle_contains_measured_tools_pinned_jdk_and_source_receipts(self):
        bundle,out=self.bundle_fixture()
        self.assertTrue(bundle['fullRunSource']['copyReceipt']['sourceCopyVerified'])
        for name in ('otatools','jdk','generateRelease'):
            path=out/bundle[name]['file'];self.assertTrue(path.is_file())
            self.assertEqual(hosted.sha256_file(path),bundle[name]['sha256'])
        self.assertGreater(bundle['jdkExtractedBytes'],0);self.assertGreater(bundle['otatoolsExtractedBytes'],0)
        self.assertEqual(bundle['timestamp'],'1794182400')
        self.assertFalse(bundle['fullRunSource']['nativeReceipt']['fullBootstrapVerified'])

    def metadata(self):
        run=int(self.f.run['runId']);sha=self.f.revision
        actual=dict(id=run,run_attempt=1,head_sha=sha,head_branch='main',path=self.lane['workflow'],event='workflow_dispatch',
            status='in_progress',conclusion=None,repository=dict(id=1428996648),head_repository=dict(id=1428996648))
        jobs=[]
        for i,name in enumerate(('guard','controller','enroll','builder','signer')):
            jobs.append(dict(id=i+1,name=name,run_id=run,head_sha=sha,run_attempt=1,
                status='in_progress' if name=='signer' else 'completed',conclusion=None if name=='signer' else 'success',
                runner_group_id=5,runner_name='mustang-999',labels=[self.lane['runnerLabel']]))
        return actual,dict(total_count=len(jobs),jobs=jobs)

    def test_hosted_signer_correlates_same_run_and_rejects_failed_producer(self):
        bundle,out=self.bundle_fixture();actual,jobs=self.metadata()
        env=dict(self.env,GITHUB_JOB='signer',EXPECTED_FULL_BUNDLE_SHA256=hosted.sha256_file(out/'mustang-signing-bundle.json'))
        def api(path):return copy.deepcopy(jobs if '/attempts/1/jobs?' in path else actual)
        with patch.object(collector.mustang_lane,'configuration',return_value=self.lane):
            checked=hosted.authenticate_full_run(bundle,env,api=api,root=self.root)
            self.assertTrue(checked['actionsRunIdentityVerified']);self.assertFalse(checked['completedRunVerified'])
            self.assertFalse(checked['otaAdmitted'])
            jobs['jobs'][3]['conclusion']='failure'
            with self.assertRaisesRegex(ValueError,'producer/job identity'):
                hosted.authenticate_full_run(bundle,env,api=api,root=self.root)

    def test_changed_downloaded_bundle_stops_before_any_metadata_or_signer_access(self):
        bundle,out=self.bundle_fixture()
        env=dict(self.env,GITHUB_JOB='signer',EXPECTED_FULL_BUNDLE_SHA256='0'*64)
        with patch.object(collector.mustang_lane,'configuration',return_value=self.lane), \
                patch.object(collector,'github_reader',side_effect=AssertionError('metadata called before byte binding')):
            with self.assertRaisesRegex(ValueError,'native job hash output'):
                hosted.full_bundle_gate(bundle,out/'mustang-signing-bundle.json',env,self.root)

    def test_stop_evidence_is_numeric_owned_and_binds_cleanup_without_signing(self):
        owner=self.home/'owner.json';owner.write_text(json.dumps(self.owner))
        class Controller:
            def control_builder(inner,action,*,builder):
                self.assertEqual(action,'stop');self.assertEqual(builder,self.lane['vm']);return 'TERMINATED'
        body=dict(id=self.lane['vm']['instanceId'],name=self.lane['vm']['instance'],status='TERMINATED')
        stop=self.home/'bootstrap-stop.json'
        def describe(argv,**kwargs):
            self.assertIn(self.lane['vm']['instance'],argv)
            return SimpleNamespace(returncode=0,stdout=json.dumps(body))
        result=controller.stop_with_receipt(Controller(),self.lane,self.f.run,owner,stop,execute=describe)
        self.assertEqual(result['readback'],body)
        (self.home/'bootstrap-metadata-cleanup.json').write_text(json.dumps(dict(cleared=collector.ephemeral.METADATA_KEY,state='absent')))
        (self.home/'bootstrap-runner-cleanup.json').write_text(json.dumps(dict(deleted=[],runId='999',groupId=5)))
        life=collector.lifecycle(self.lane,dict(self.env,GITHUB_JOB='cleanup'),self.event,self.home)
        self.assertTrue(life['metadataRemovalSucceeded']);self.assertFalse(life['otaAdmitted'])
        with self.assertRaises(FileExistsError):
            controller.stop_with_receipt(Controller(),self.lane,self.f.run,owner,stop,execute=describe)
        body['id']='99999'
        with self.assertRaisesRegex(ValueError,'numeric identity'):
            controller.stop_with_receipt(Controller(),self.lane,self.f.run,owner,self.home/'wrong-stop.json',execute=describe)
        self.assertFalse((self.home/'wrong-stop.json').exists())

    def test_real_signer_adapter_loads_without_secret_access_or_execution(self):
        # Loading the reviewed entry point is safe; do not call its main.
        self.assertTrue(callable(hosted.host_sign_entry(ROOT)))

    def test_hosted_staging_builds_the_exact_verified_public_asset_set(self):
        archive_dir=self.home/'edge-archives';archive_dir.mkdir()
        build,stamp='2026110901','1794182400'
        for name in ('unsigned.zip','signed.zip'):edge_fixture.fixtures.target_files(archive_dir/name)
        bound=edge_fixture.gate.binding(self.f.revision,self.f.revision,build,stamp,'999','1','999','1')
        unsigned_proof=edge_fixture.gate.inspect(archive_dir/'unsigned.zip','unsigned',bound,
            edge_fixture.descriptor(archive_dir/'unsigned.zip'))
        release=self.home/'release';release.mkdir();public=release/'public';public.mkdir()
        package=public/f'mustang-ota_update-{build}.zip'
        with zipfile.ZipFile(package,'w') as archive:
            archive.writestr('META-INF/com/android/metadata',
                f'pre-device=mustang\npost-build-incremental={build}\npost-timestamp={stamp}\npost-build=brand/mustang/{build}:user/release-keys\nota-type=AB\n')
        (public/'releasekey.x509.pem').write_bytes(b'public certificate stand-in')
        avb=public/'avb_pkmd.bin';avb.write_bytes(b'public AVB stand-in')
        binary=self.home/'qualified/bin';binary.mkdir(parents=True)
        for name in ('check_ota_package_signature','delta_generator'):(binary/name).write_bytes(b'NEVER EXECUTE: verifier stand-in')
        cfg=json.loads((self.root/'os/build-only/mustang-runtime.json').read_text())
        cfg['candidateContinuity']='os/build-only/mustang-candidate-continuity-20261107.json'
        cfg['status']='unbound'  # Synthetic pre-clock staging fixture only.
        (self.root/'os/build-only/mustang-runtime.json').write_text(json.dumps(cfg))
        (self.root/cfg['candidateContinuity']).write_bytes((ROOT/cfg['candidateContinuity']).read_bytes())
        bundle=dict(build=build,timestamp=stamp,runId='999',runAttempt='1',sourceSha=self.f.revision,
            workflow=self.lane['workflow'],unsignedRaisedEdge=unsigned_proof)
        actual_digest=ota.digest;pins=ota.identities();calls=[]
        def digest(path):return pins['avbPublicKeySha256'] if Path(path)==avb else actual_digest(path)
        def execute(argv,**kwargs):
            calls.append(argv);self.assertEqual(argv[0],str(binary/'check_ota_package_signature'))
            self.assertNotIn('GH_TOKEN',kwargs['env']);return SimpleNamespace(returncode=0)
        signed_out=self.home/'hosted-output';signed_out.mkdir();stage=signed_out/'staged'
        live_read=ota.read
        def read_fixture(path):
            return cfg if Path(path)==ota.ROOT/'os/build-only/mustang-runtime.json' else live_read(path)
        with patch.object(ota,'fingerprint',return_value='f'*64),patch.object(ota,'digest',side_effect=digest), \
                patch.object(ota,'read',side_effect=read_fixture), \
                patch.object(ota.host_secrets,'certificate_sha256',return_value=pins['otaCertificateSha256']), \
                patch.object(ota.host_secrets,'fetch_secret',side_effect=AssertionError('staging read a secret')), \
                patch.object(ota,'observe_target',return_value={'packagedUpdaterVerified':True}):
            receipt=ota.stage_hosted(bundle,archive_dir/'unsigned.zip',archive_dir/'signed.zip',package,public,
                binary.parent,self.home/'jdk',stage,root=self.root,execute=execute)
            self.assertEqual(ota.verify_staged(stage,ota.config(),publication=False),receipt)
        self.assertEqual(len(calls),1)
        self.assertEqual({p.name for p in stage.iterdir()},
            {package.name,'mustang-stable','mustang-ota-receipt.json','SHA256SUMS'})
        self.assertEqual(package.stat().st_ino,(stage/package.name).stat().st_ino)
        self.assertFalse(receipt['currentPhone']);self.assertTrue(receipt['checks']['otaSignatures'])
        os.link(archive_dir/'signed.zip',release/'signed-target_files.zip')
        bundle['fullRunSource']={}
        delivered=hosted.write_hosted_receipt(release,signed_out,bundle)
        self.assertEqual(hosted.bind_outputs(signed_out,env=self.env),delivered)
        self.assertEqual(delivered['outputs']['ota']['file'],'staged/'+package.name)


if __name__=='__main__':unittest.main()
