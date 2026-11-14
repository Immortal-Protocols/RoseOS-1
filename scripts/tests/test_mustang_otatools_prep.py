"""Offline admission, measured-input and independent byte-check tests."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'scripts/tests'))
import mustang_otatools_prep as prep
import os_release_shape as shape


class OtatoolsTest(unittest.TestCase):
    def fixture_root(self,home,*,recovering=False):
        root=Path(home)/'checkout';(root/'os/build-only').mkdir(parents=True)
        (root/prep.BINDING).write_text(json.dumps({'outputRecoveryEvidence':prep.recovery.BINDING if recovering else None}))
        return root

    def env(self,job='otatools'):
        return {'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REF':'refs/heads/main',
                'GITHUB_WORKFLOW_REF':'Immortal-Protocols/RoseOS-1/.github/workflows/mustang-ota-dedicated.yml@refs/heads/main',
                'GITHUB_REPOSITORY_ID':'1428996648','GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1',
                'GITHUB_SHA':'a'*40,'GITHUB_JOB':job}

    def identity(self):
        lane=prep.mustang_lane.configuration()
        return {'run':prep.controller.invocation(lane,self.env()),'builder':lane['vm'],
                'diskUuid':json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())['dataFilesystemUuid']}

    def test_unbound_vendor_blocks_before_start_and_scope_is_exclusive(self):
        event={'inputs':{'otatools_only':True}}
        self.assertEqual(prep.binding()[0]['runId'],'42522784617')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'os/build-only').mkdir(parents=True)
            cfg=json.loads((ROOT/prep.BINDING).read_text());cfg['vendorEvidence']=None
            (root/prep.BINDING).write_text(json.dumps(cfg))
            (root/'os/build-only/mustang-runtime.json').write_bytes((ROOT/'os/build-only/mustang-runtime.json').read_bytes())
            with self.assertRaisesRegex(ValueError,'unbound'):prep.guard(self.env(),event,root=root)
        with patch.object(prep,'binding',return_value=({},{})):
            self.assertEqual(prep.guard(self.env(),event)[1]['runId'],'123')
            for flag in prep.FLAGS-{'otatools_only'} | {'unknown'}:
                with self.subTest(flag=flag),self.assertRaises(ValueError):
                    prep.guard(self.env(),{'inputs':{'otatools_only':True,flag:True}})
            for change in ({'GITHUB_RUN_ATTEMPT':'2'},{'GITHUB_REF':'refs/heads/other'},{'GITHUB_REPOSITORY_ID':'1'}):
                with self.assertRaises(ValueError):prep.guard(dict(self.env(),**change),event)

    def test_real_shell_checks_build_variables_before_only_the_fixed_make_goal(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp);(source/'build').mkdir();script=source/'proof.sh';script.write_bytes(prep.SCRIPT)
            envsetup=source/'build/envsetup.sh'
            envsetup.write_text('''lunch() { test "$1" = mustang-cur-user && export TARGET_RELEASE="${FIXTURE_RELEASE-cur}"; }
get_build_var() {
case "$1" in TARGET_PRODUCT|TARGET_DEVICE) echo mustang;; TARGET_RELEASE) echo;; TARGET_BUILD_VARIANT) echo user;; *) return 1;; esac
}
m() { printf '%s\\n' "$@" > make-arguments; }
''')
            old=source/'old-probe.sh';old.write_bytes(prep.SCRIPT.replace(b'"${TARGET_RELEASE:?lunch release missing}"',b'"$(get_build_var TARGET_RELEASE)"'))
            old_result=subprocess.run(['/bin/bash',str(old),prep.OUT,str(source/'old-identity.json')],cwd=source,
                                      env={'PATH':'/usr/bin:/bin'},capture_output=True,timeout=10)
            self.assertNotEqual(old_result.returncode,0);self.assertFalse((source/'make-arguments').exists())
            result=subprocess.run(['/bin/bash',str(script),prep.OUT,str(source/'identity.json')],cwd=source,
                                  env={'PATH':'/usr/bin:/bin'},capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            self.assertEqual((source/'make-arguments').read_text().splitlines(),['-j24','otatools-package'])
            self.assertEqual(json.loads((source/'identity.json').read_text())['TARGET_DEVICE'],'mustang')
            (source/'make-arguments').unlink()
            envsetup.write_text(envsetup.read_text().replace('echo mustang','echo frankel'))
            result=subprocess.run(['/bin/bash',str(script),prep.OUT,str(source/'wrong-identity.json')],cwd=source,
                                  env={'PATH':'/usr/bin:/bin'},capture_output=True,timeout=10)
            self.assertNotEqual(result.returncode,0);self.assertFalse((source/'make-arguments').exists())
            envsetup.write_text(envsetup.read_text().replace('echo frankel','echo mustang'))
            for release in ('trunk_staging',''):
                result=subprocess.run(['/bin/bash',str(script),prep.OUT,str(source/('release-'+(release or 'empty')+'.json'))],cwd=source,
                                      env={'PATH':'/usr/bin:/bin','FIXTURE_RELEASE':release},capture_output=True,timeout=10)
                self.assertNotEqual(result.returncode,0);self.assertFalse((source/'make-arguments').exists())

    def package(self,source,data=b'new tool package fixture'):
        path=source/prep.OUT/'soong/.intermediates/build/make/tools/otatools_package/otatools-package/host/gen/otatools.zip'
        path.parent.mkdir(parents=True);path.write_bytes(data)
        return path,prep.collect(source,source/prep.OUT,time.monotonic()+20)

    def test_module_collection_refuses_ambiguity_links_and_oversize(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp);path,observed=self.package(source)
            self.assertEqual(observed['sha256'],hashlib.sha256(path.read_bytes()).hexdigest())
            other=path.parent.parent.parent/'other/gen/otatools.zip';other.parent.mkdir(parents=True);other.write_bytes(b'other')
            with self.assertRaisesRegex(ValueError,'ambiguous'):prep.collect(source,source/prep.OUT,time.monotonic()+20)
            other.unlink();path.unlink();path.symlink_to(source/'outside')
            (source/'outside').write_bytes(b'not a module output')
            with self.assertRaises(prep.deps.readers._Refused):prep.collect(source,source/prep.OUT,time.monotonic()+20)
            path.unlink();path.write_bytes(b'over the test bound')
            with patch.object(prep,'PACKAGE_BYTES',1),self.assertRaisesRegex(ValueError,'size bound'):
                prep.collect(source,source/prep.OUT,time.monotonic()+20)

    def native(self,package):
        lane=prep.mustang_lane.configuration()
        return {'schema':'roseos.mustang-otatools-preparation-run.v1','identity':self.identity(),
                'success':True,'phase':'complete','makeExitCode':0,'product':'mustang-cur-user',
                'sourceRoot':lane['sourceRoot'],'outDir':str(Path(lane['sourceRoot'])/prep.OUT),
                'goals':['otatools-package'],'jobs':24,'allocated':False,'signingStarted':False,
                'otaAdmitted':False,'signerPinsAdmitted':False,'package':package}

    def test_independent_readback_requires_same_run_scope_and_actual_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);path,package=self.package(directory)
            relative=package['relativePath'];lane=prep.mustang_lane.configuration()
            package['path']=str(Path(lane['sourceRoot'])/prep.OUT/relative)
            native=self.native(package);raw=json.dumps(native).encode()
            (directory/prep.RECEIPT).write_bytes(raw);(directory/'otatools.zip').write_bytes(path.read_bytes())
            result=prep.verify_export(directory,self.env('otatools-verify'),directory/'verified.json')
            self.assertTrue(result['byteReadbackMatches']);self.assertFalse(result['packageExecuted'])
            self.assertFalse(result['signerPinsAdmitted']);self.assertFalse(result['otaAdmitted'])
            self.assertEqual(result['nativeReceiptSha256'],hashlib.sha256(raw).hexdigest())
            for field,value in (('identity',dict(native['identity'],run=dict(native['identity']['run'],runId='456'))),
                                ('success',False),('signingStarted',True),('goals',['target-files-package'])):
                changed=copy.deepcopy(native);changed[field]=value
                (directory/prep.RECEIPT).write_text(json.dumps(changed))
                with self.subTest(field=field),self.assertRaises(ValueError):
                    prep.verify_export(directory,self.env('otatools-verify'),directory/'never.json')
            (directory/prep.RECEIPT).write_bytes(raw);(directory/'otatools.zip').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'size differs'):
                prep.verify_export(directory,self.env('otatools-verify'),directory/'never.json')
            (directory/'otatools.zip').write_bytes(b'x'*package['sizeBytes'])
            with self.assertRaisesRegex(ValueError,'bytes differ'):
                prep.verify_export(directory,self.env('otatools-verify'),directory/'never.json')

    def test_vendor_input_rehashes_all_generated_bytes_and_source_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp);adev=source/'vendor/adevtool';previous={'phase':'complete','sourceRoot':str(source)}
            for key,relative in (('vendorTree','vendor/google_devices/mustang'),
                                 ('nodeModules','vendor/adevtool/node_modules'),('adevtoolSource','vendor/adevtool/src')):
                directory=source/relative;directory.mkdir(parents=True);(directory/'data').write_bytes(b'measured bytes')
            (source/'vendor/google_devices/mustang/mustang.mk').write_bytes(b'measured product')
            previous['sourceLoader']={}
            for name,relative in (('binRun','bin/run'),('packageJson','package.json'),('yarnLock','yarn.lock')):
                file=adev/relative;file.parent.mkdir(exist_ok=True);file.write_bytes(b'measured loader')
                previous['sourceLoader'][name]=prep.deps.fingerprint(source,file,time.monotonic()+10)
            for key,relative in (('vendorTree','vendor/google_devices/mustang'),
                                 ('nodeModules','vendor/adevtool/node_modules'),('adevtoolSource','vendor/adevtool/src')):
                previous[key]=prep.vendor.catalog(source,source/relative,time.monotonic()+10)
            previous['productMakefile']=prep.deps.fingerprint(source,source/'vendor/google_devices/mustang/mustang.mk',time.monotonic()+10)
            previous['identity']=self.identity();record={'receiptSha256':'a'*64}
            with patch.object(prep.inputs,'receipt_on_disk',return_value=previous):
                result=prep.vendor_input(source,source,record,self.identity(),time.monotonic()+10)
                self.assertTrue(result['generatedTreesRehashed']);self.assertTrue(result['sourceLoaderRehashed'])
                (adev/'lib').mkdir()
                with self.assertRaisesRegex(ValueError,'source-only runtime changed'):
                    prep.vendor_input(source,source,record,self.identity(),time.monotonic()+10)
                (adev/'lib').rmdir();(adev/'node_modules/data').write_bytes(b'dependency tampered')
                with self.assertRaisesRegex(ValueError,'bytes changed'):
                    prep.vendor_input(source,source,record,self.identity(),time.monotonic()+10)

    def test_successful_build_is_separate_bounded_and_has_no_inherited_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';source.mkdir();attempt=Path(tmp)/'attempt';attempt.mkdir()
            calls=[]
            def supervise(command,_source,env,_log,_deadline,monitor,*,outcome,**_kwargs):
                self.assertEqual(command[:1],['/bin/bash']);self.assertEqual(command[2],prep.OUT)
                self.assertEqual(env['BUILD_NUMBER'],'mustang-otatools-proof-123')
                for key in ('GH_TOKEN','GCP_SERVICE_ACCOUNT_JSON','ACTIONS_ID_TOKEN_REQUEST_TOKEN','BASH_ENV'):
                    self.assertNotIn(key,env)
                calls.append(command);self.package(source)
                (attempt/'lunch-identity.json').write_text(json.dumps({'TARGET_PRODUCT':'mustang','TARGET_RELEASE':'cur',
                    'TARGET_BUILD_VARIANT':'user','TARGET_DEVICE':'mustang'}))
                outcome.update(processStarted=True,exitCode=0);monitor()
            free=SimpleNamespace(f_bavail=1 << 40,f_frsize=1,f_favail=1000000)
            with patch.object(prep.deps,'source_check',return_value={'projectCount':1057}), \
                    patch.object(prep,'vendor_input',return_value={'generatedTreesRehashed':True}), \
                    patch.object(prep.inputs,'host_dependencies',return_value={'distributionRehashed':True}), \
                    patch.object(prep.deps,'supervise',side_effect=supervise), \
                    patch.object(prep.os,'statvfs',return_value=free), \
                    patch.dict(os.environ,{'GH_TOKEN':'private','GCP_SERVICE_ACCOUNT_JSON':'private',
                                           'ACTIONS_ID_TOKEN_REQUEST_TOKEN':'private','BASH_ENV':'private'}):
                result=prep.build(source,attempt,{}, {},self.identity(),root=self.fixture_root(tmp))
            self.assertTrue(result['success']);self.assertEqual(result['makeExitCode'],0);self.assertEqual(len(calls),1)
            self.assertFalse(result['allocated']);self.assertFalse(result['signingStarted'])
            self.assertFalse(result['signerPinsAdmitted']);self.assertFalse(result['otaAdmitted'])
            self.assertEqual(json.loads((attempt/prep.RECEIPT).read_text()),result)

    def test_export_copies_only_the_same_measured_package_and_refuses_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);source=home/'source';source.mkdir();path,package=self.package(source)
            runner=home/'runner';temporary=runner/'_work/_temp';temporary.mkdir(parents=True)
            native={'success':True,'phase':'complete','makeExitCode':0,'sourceRoot':str(source),'package':package}
            raw=json.dumps(native).encode();env=dict(self.env(),RUNNER_TEMP=str(temporary))
            free=SimpleNamespace(f_bavail=1 << 40,f_frsize=1)
            with patch.object(prep,'load_report',return_value=(native,raw)), \
                    patch.object(prep.ephemeral,'RUNNER_ROOT',runner),patch.object(prep.os,'statvfs',return_value=free):
                destination=temporary/'mustang-otatools-export'
                prep.export_package(env,destination)
                self.assertEqual((destination/'otatools.zip').read_bytes(),path.read_bytes())
                self.assertEqual((destination/prep.RECEIPT).read_bytes(),raw)
                self.assertEqual({p.name for p in destination.iterdir()},{'otatools.zip',prep.RECEIPT})
                with self.assertRaises(FileExistsError):prep.export_package(env,destination)
                path.write_bytes(b'altered source package')
                with self.assertRaisesRegex(ValueError,'changed before export'):
                    prep.export_package(env,destination)

    def test_output_collision_preserves_prior_bytes_and_no_make_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';source.mkdir();path,_=self.package(source)
            attempt=Path(tmp)/'attempt';attempt.mkdir()
            with patch.object(prep.deps,'supervise',side_effect=AssertionError('Make started')):
                with self.assertRaisesRegex(ValueError,'output exists'):
                    prep.build(source,attempt,{}, {},self.identity(),root=self.fixture_root(tmp))
            result=json.loads((attempt/prep.RECEIPT).read_text())
            self.assertFalse(result['success']);self.assertFalse(result['makeStarted'])
            self.assertEqual(path.read_bytes(),b'new tool package fixture')

    def test_new_byte_verifier_has_no_native_queue_or_signer_authority(self):
        jobs=shape.jobs((ROOT/'.github/workflows/mustang-ota-dedicated.yml').read_text())
        self.assertIn('mustang_otatools_prep.py run --execute',jobs['otatools'])
        for role in ('otatools','otatools-verify'):
            for text in ('id-token:','actions-gcp-login','sudo ','secrets.'):
                self.assertNotIn(text,jobs[role])
        for role in ('builder','signer'):self.assertIn('inputs.otatools_only == false',jobs[role])
        cleanup=jobs['cleanup'].split('steps:',1)[0]
        self.assertIn('- otatools\n',cleanup);self.assertNotIn('- otatools-verify',cleanup)
        self.assertIn('actions/download-artifact@',jobs['otatools-verify'])
        self.assertEqual(prep.ephemeral.queue_decision([{'name':'otatools','status':'in_progress'}],100),'ready')
        self.assertEqual(prep.ephemeral.queue_decision([{'name':'otatools-verify','status':'in_progress'}],100),'wait')


class OutputRecoveryTest(unittest.TestCase):
    def fixture(self,tmp):
        source=Path(tmp)/'source';source.mkdir();storage=Path(tmp)/'rose-actions';storage.mkdir()
        old=storage/'mustang-otatools-42524960701-1';old.mkdir()
        output=source/prep.OUT;output.mkdir();(output/'keep').write_bytes(b'preserve failed lunch bytes')
        (old/'lunch-identity.json').write_bytes(b'synthetic observed lunch fields')
        (old/'make.log').write_bytes(b'synthetic observed assertion marker')
        cfg,failed,observed=copy.deepcopy(prep.recovery.binding());r=failed['receipt'];o=observed['receipt']
        r.update(sourceRoot=str(source),outDir=str(output))
        raw=json.dumps(r,sort_keys=True).encode()+b'\n';(old/prep.RECEIPT).write_bytes(raw)
        failed['receiptSha256']=hashlib.sha256(raw).hexdigest();o['failedReceiptSha256']=failed['receiptSha256']
        o['lunch']['sha256']=hashlib.sha256((old/'lunch-identity.json').read_bytes()).hexdigest()
        o['privateDiagnostic']['logSha256']=hashlib.sha256((old/'make.log').read_bytes()).hexdigest()
        o['output']=prep.deps.inspect_output_entries(output)
        observed['receiptSha256']=hashlib.sha256(json.dumps(o,sort_keys=True).encode()).hexdigest()
        identity=dict(r['identity'],run=dict(r['identity']['run'],runId='789'))
        return source,storage,old,(cfg,failed,observed),identity

    def retain(self,source,storage,records,identity):
        with patch.object(prep.recovery,'binding',return_value=records):
            return prep.recovery.retain(source,storage,identity)

    def test_preservation_keeps_the_whole_directory_inode_and_never_uses_its_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,storage,old,records,identity=self.fixture(tmp)
            output=source/prep.OUT;before=output.stat();result=self.retain(source,storage,records,identity)
            target=old/'retained-output'
            self.assertFalse(output.exists());self.assertEqual(target.stat().st_ino,before.st_ino)
            self.assertEqual((target/'keep').read_bytes(),b'preserve failed lunch bytes')
            self.assertTrue(result['completed']);self.assertFalse(result['oldOutputUsedForBuild'])
            self.assertEqual(self.retain(source,storage,records,identity),result)

    def test_changed_receipt_diagnostics_metadata_identity_and_collision_refuse(self):
        for change in ('receipt','lunch','log','metadata','identity','collision','missing-output'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as tmp:
                source,storage,old,records,identity=self.fixture(tmp);output=source/prep.OUT
                if change=='receipt':(old/prep.RECEIPT).write_bytes(b'changed')
                if change=='lunch':(old/'lunch-identity.json').write_bytes(b'changed')
                if change=='log':(old/'make.log').write_bytes(b'changed')
                if change=='metadata':(output/'extra').write_bytes(b'unexpected')
                if change=='identity':identity['diskUuid']='other'
                if change=='collision':(old/'retained-output').mkdir()
                if change=='missing-output':(output/'keep').unlink();output.rmdir()
                with self.assertRaises((ValueError,prep.deps.readers._Refused)):
                    self.retain(source,storage,records,identity)
                if change!='missing-output':self.assertEqual((output/'keep').read_bytes(),b'preserve failed lunch bytes')

    def test_interrupted_or_conflicting_retention_never_resets_or_moves_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,storage,old,records,identity=self.fixture(tmp)
            with patch.object(prep.deps,'rename_noreplace',side_effect=OSError('fixture interrupted')):
                with self.assertRaises(OSError):self.retain(source,storage,records,identity)
            with self.assertRaises(prep.deps.readers._Refused):self.retain(source,storage,records,identity)
            self.assertTrue((source/prep.OUT/'keep').exists())
        with tempfile.TemporaryDirectory() as tmp:
            source,storage,old,records,identity=self.fixture(tmp);self.retain(source,storage,records,identity)
            (source/prep.OUT).mkdir()
            with self.assertRaisesRegex(ValueError,'reconciliation'):self.retain(source,storage,records,identity)

    def test_actual_binding_qualifies_only_observed_assertion_and_same_prepared_inputs(self):
        _cfg,failed,observed=prep.recovery.binding()
        self.assertEqual(failed['runId'],'42524960701');self.assertEqual(observed['runId'],'42527536373')
        record,host=prep.binding()
        self.assertEqual(record['runId'],'42522784617');self.assertEqual(host['runId'],'42512135815')

    def test_measured_inputs_are_required_before_retention_and_a_fresh_make(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);source=home/'source';source.mkdir();storage=home/'rose-actions';storage.mkdir()
            attempt=storage/'new-attempt';attempt.mkdir();old=storage/'old-attempt';old.mkdir()
            output=source/prep.OUT;output.mkdir();(output/'keep').write_bytes(b'old unqualified bytes')
            events=[];fixture=OtatoolsTest();root=fixture.fixture_root(tmp,recovering=True)
            def checked_source(*_args,**_kwargs):events.append('source');return {'projectCount':1057}
            def vendor(*_args,**_kwargs):events.append('vendor');return {'generatedTreesRehashed':True}
            def host(*_args,**_kwargs):events.append('host');return {'distributionRehashed':True}
            def retain(*_args,**_kwargs):
                self.assertEqual(events,['source','vendor','host']);events.append('retain')
                os.rename(output,old/'retained-output');return {'completed':True,'oldOutputUsedForBuild':False}
            def make(_command,_source,_env,_log,_deadline,monitor,*,outcome,**_kwargs):
                events.append('make');self.assertFalse(output.exists());fixture.package(source)
                (attempt/'lunch-identity.json').write_text(json.dumps({'TARGET_PRODUCT':'mustang','TARGET_RELEASE':'cur',
                    'TARGET_BUILD_VARIANT':'user','TARGET_DEVICE':'mustang'}))
                outcome.update(processStarted=True,exitCode=0);monitor()
            free=SimpleNamespace(f_bavail=1 << 40,f_frsize=1,f_favail=1000000)
            with patch.object(prep.deps,'source_check',side_effect=checked_source), \
                    patch.object(prep,'vendor_input',side_effect=vendor), \
                    patch.object(prep.inputs,'host_dependencies',side_effect=host), \
                    patch.object(prep.recovery,'retain',side_effect=retain), \
                    patch.object(prep.deps,'supervise',side_effect=make),patch.object(prep.os,'statvfs',return_value=free):
                result=prep.build(source,attempt,{}, {},fixture.identity(),root=root)
            self.assertTrue(result['success']);self.assertEqual(events[:5],['source','vendor','host','retain','make'])
            self.assertEqual((old/'retained-output/keep').read_bytes(),b'old unqualified bytes')

    def test_failed_input_rehash_keeps_partial_output_and_does_not_move_or_make(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);source=home/'source';source.mkdir();attempt=home/'attempt';attempt.mkdir()
            output=source/prep.OUT;output.mkdir();(output/'keep').write_bytes(b'preserved')
            fixture=OtatoolsTest();root=fixture.fixture_root(tmp,recovering=True)
            free=SimpleNamespace(f_bavail=1 << 40,f_frsize=1,f_favail=1000000)
            with patch.object(prep.deps,'source_check',return_value={}),patch.object(prep,'vendor_input',return_value={}), \
                    patch.object(prep.inputs,'host_dependencies',side_effect=ValueError('changed input')), \
                    patch.object(prep.recovery,'retain',side_effect=AssertionError('moved output')), \
                    patch.object(prep.deps,'supervise',side_effect=AssertionError('Make started')), \
                    patch.object(prep.os,'statvfs',return_value=free):
                with self.assertRaisesRegex(ValueError,'changed input'):
                    prep.build(source,attempt,{}, {},fixture.identity(),root=root)
            self.assertEqual((output/'keep').read_bytes(),b'preserved')


if __name__=='__main__':unittest.main()
