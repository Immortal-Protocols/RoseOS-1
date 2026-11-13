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
import tempfile
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
import mustang_retained as retained
import mustang_compiler_cache as compiler_cache
import test_mustang_edge_gate as edge_fixture
import test_mustang_bootstrap_collector as fixture_module


class CompilerOutputTest(unittest.TestCase):
    def test_mustang_output_reaches_job_log_and_preserves_failure_status(self):
        script = '''
import importlib.util, os, sys
spec=importlib.util.spec_from_file_location('output_adapter',sys.argv[1])
adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
engine=adapter.engine();engine.DEVICE=sys.argv[2]
code=int(sys.argv[3]);capture=sys.argv[4]=='true'
child="import sys;print('compiler stdout',flush=True);print('compiler stderr',file=sys.stderr,flush=True);sys.exit("+str(code)+")"
result=engine.supervise([sys.executable,'-c',child],os.getcwd(),{'PATH':'/usr/bin:/bin'},10,
    {'sampleSeconds':0.05,'killGraceSeconds':1,'boundBytes':1<<30,'reserveBytes':0},capture=capture)
if capture:
    assert result['stdout']=='compiler stdout\\n',result
assert result['stopped'] is None,result
sys.exit(result['returncode'])
'''
        with tempfile.TemporaryDirectory() as directory:
            for device,capture,visible in [('mustang',False,True),('mustang',True,False),('blazer',False,False)]:
                for code in (0,7):
                    with self.subTest(device=device,capture=capture,code=code):
                        run=subprocess.run([sys.executable,'-c',script,str(ROOT/'scripts/mustang-build-only.py'),
                            device,str(code),str(capture).lower()],cwd=directory,env={'PATH':'/usr/bin:/bin'},
                            capture_output=True,text=True,timeout=20)
                        self.assertEqual(run.returncode,code,run.stderr)
                        self.assertEqual(run.stdout,'compiler stdout\n' if visible else '')
                        self.assertEqual(run.stderr,'compiler stderr\n')


class CompilerCacheTest(unittest.TestCase):
    def test_cache_uses_existing_runner_writable_persistent_storage_not_its_admin_parent(self):
        import mustang_lane
        import mustang_paths
        lane=mustang_lane.configuration();paths=mustang_paths.layout(lane)
        self.assertEqual(compiler_cache.location(lane),paths['storageRoot']/'compiler-cache-v1')
        self.assertNotEqual(compiler_cache.location(lane).parent,paths['home'])

    def test_cache_binding_changes_only_private_compile_commands_and_keeps_output_paths_stable(self):
        build = ota.module('cache_build_adapter', 'mustang-build-only.py')
        engine = build.engine()
        recipe = engine.load(ROOT/'os/build-only/mustang.json', probe=False)
        # The original commands function resolves compile_out_dir in its private
        # engine module. Bind that actual engine, not a process-global override.
        state = dict(path='/dedicated/cache', namespace='a'*64)
        compiler_cache.bind_engine(engine, state)
        for number in ('2611110500', '2611110600'):
            op = engine.operator('mustang', '/synthetic/source', 24, '/synthetic/lock',
                                 '/synthetic/downloads', '/synthetic/integration', 256,
                                 '/synthetic/inputs.json', '/synthetic/node', number, '1794373200')
            command = engine.commands(recipe, op)['compile']
            self.assertEqual(command['env']['OUT_DIR'], compiler_cache.OUT)
            self.assertEqual(command['env']['BUILD_NUMBER'], number)
            self.assertIn('mustang-ccache-wrapper.sh', command['env']['CC_WRAPPER'])
            self.assertEqual(command['env']['USE_CCACHE'], '1')
        self.assertEqual(build.engine().compile_out_dir(recipe, op), 'out-mustang-build-2611110600')

    def test_output_selection_preserves_compile_binding_and_refuses_foreign_paths(self):
        self.assertEqual(compiler_cache.output_name({}, '2611111500'), 'out-mustang-build-2611111500')
        candidate = dict(compilerCache=dict(enabled=True, kind='ccache', outDir=compiler_cache.OUT),
                         stages=dict(compile=dict(result=dict(outDir=compiler_cache.OUT))))
        self.assertEqual(compiler_cache.output_name(candidate, '2611111500'), compiler_cache.OUT)
        for name in ('../outside', '/outside', 'out-mustang-build-2611111500'):
            changed = copy.deepcopy(candidate); changed['compilerCache']['outDir'] = name
            with self.assertRaisesRegex(ValueError, 'binding differs'):
                compiler_cache.output_name(changed, '2611111500')
        candidate['stages']['compile']['result']['outDir'] = 'out-mustang-build-2611111500'
        with self.assertRaisesRegex(ValueError, 'binding differs'):
            compiler_cache.output_name(candidate, '2611111500')

    def test_only_exact_bounded_cache_configuration_is_enabled(self):
        self.assertFalse(compiler_cache.enabled({}))
        self.assertTrue(compiler_cache.enabled({'compilerCache':compiler_cache.SETTING}))
        with self.assertRaisesRegex(ValueError, 'configuration differs'):
            compiler_cache.enabled({'compilerCache':dict(compiler_cache.SETTING,maxBytes=0)})

    def test_cache_rejects_linked_storage_and_changed_tool_without_fetch_or_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);target=home/'other';target.mkdir();cache=home/'cache'
            cache.symlink_to(target,target_is_directory=True)
            with patch.object(compiler_cache,'location',return_value=cache), \
                    self.assertRaisesRegex(ValueError,'traverses a link'):
                compiler_cache.prepare({}, {'compilerCache':compiler_cache.SETTING})
            cache.unlink();cache.mkdir();compiler_cache.binary(cache).write_bytes(b'changed')
            with patch.object(compiler_cache,'location',return_value=cache), \
                    self.assertRaisesRegex(ValueError,'executable differs'):
                compiler_cache.prepare({}, {'compilerCache':compiler_cache.SETTING},fetch=lambda:self.fail('no fetch'))

    def test_bad_download_is_refused_before_install(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=Path(directory)/'cache'
            with patch.object(compiler_cache,'location',return_value=cache), \
                    self.assertRaisesRegex(ValueError,'download differs'):
                compiler_cache.prepare({}, {'compilerCache':compiler_cache.SETTING}, fetch=lambda:b'wrong')
            self.assertFalse(compiler_cache.binary(cache).exists())

    def test_real_shell_wrapper_scrubs_ambient_cache_settings_and_preserves_exit_status(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=Path(directory);tool=compiler_cache.binary(cache)
            tool.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                            'print(json.dumps({"args":sys.argv[1:],"env":{k:v for k,v in os.environ.items() if k.startswith("CCACHE_")}}))\n'
                            'sys.exit(7)\n');tool.chmod(0o755)
            state=dict(path=str(cache),namespace='a'*64)
            env=dict(os.environ,CCACHE_DISABLE='1',CCACHE_SLOPPINESS='time_macros',CCACHE_REMOTE_STORAGE='wrong')
            result=subprocess.run(compiler_cache.wrapper_argv(state)+['clang','-c','input.c','-o','output.o'],
                                  cwd=cache,env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,7,result.stderr)
            observed=json.loads(result.stdout)
            self.assertEqual(observed['args'],['clang',f'-fdebug-prefix-map={cache}=.','-c','input.c','-o','output.o'])
            self.assertEqual(observed['env']['CCACHE_DIR'],str(cache/'objects'))
            self.assertEqual(observed['env']['CCACHE_COMPILERCHECK'],'content')
            self.assertNotIn('CCACHE_DISABLE',observed['env'])
            self.assertNotIn('CCACHE_SLOPPINESS',observed['env'])
            self.assertNotIn('CCACHE_REMOTE_STORAGE',observed['env'])

    def test_cached_output_collection_still_requires_current_allocated_build_and_time(self):
        build = ota.module('cache_output_adapter', 'mustang-build-only.py')
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);source=base/'source';out=source/compiler_cache.OUT
            product=out/'target/product/mustang';packaging=product/'obj/PACKAGING/target_files_intermediates'
            packaging.mkdir(parents=True)
            tools=out/'soong/.intermediates/build/make/tools/otatools_package/otatools-package/linux_glibc_x86_64/gen/otatools.zip'
            tools.parent.mkdir(parents=True);tools.write_bytes(b'tools')
            for name in ('vendor_boot.img','vendor_kernel_boot.img'):(product/name).write_bytes(b'image')
            allocation=dict(build='2611110500',reservedAt='2026-11-11T05:00:00Z')
            (base/'allocation.json').write_text(json.dumps(allocation))
            props='ro.product.vendor.device=mustang\nro.vendor.build.version.incremental=2611110500\nro.vendor.build.date.utc=1794373200\n'
            with zipfile.ZipFile(packaging/'mustang-target_files.zip','w') as archive:archive.writestr('VENDOR/build.prop',props)
            self.assertEqual(build.collect_outputs(source,compiler_cache.OUT)['verdict'],'complete')
            allocation['build']='2611110600';(base/'allocation.json').write_text(json.dumps(allocation))
            self.assertEqual(build.collect_outputs(source,compiler_cache.OUT)['verdict'],'incomplete')


class DiskSpaceTest(unittest.TestCase):
    def test_full_run_budget_includes_source_preparation_compile_and_runner_reserve(self):
        import mustang_route
        recipe=json.loads((ROOT/'os/build-only/mustang.json').read_text())
        self.assertTrue(recipe['sprint']['reserveChecks'])
        required=mustang_route.full_run_disk_budget(recipe)
        self.assertEqual(required,844 << 30)
        import mustang_lane
        lane=mustang_lane.configuration();cfg=json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
        total=cfg.get('dataFilesystemTotalInodes',mustang_lane.CONFIRMED_TOTAL_INODES)
        obs=dict(instanceId=lane['vm']['instanceId'],filesystemUuid=cfg['dataFilesystemUuid'],
                 mount=cfg['dataMount'],availableBytes=required-1,availableInodes=10**6,totalInodes=total,euid=999)
        with self.assertRaisesRegex(ValueError,'source, metadata, lunch, compile and reserve'):
            mustang_route.evaluate_guest(obs,lane,cfg)
        obs['availableBytes']=required
        self.assertFalse(mustang_route.evaluate_guest(obs,lane,cfg)['admitted'])
        cfg['dataFilesystemTotalInodes']=2*total
        with self.assertRaisesRegex(ValueError,'inode count differs'):
            mustang_route.evaluate_guest(obs,lane,cfg)
        obs['totalInodes']=cfg['dataFilesystemTotalInodes']
        self.assertFalse(mustang_route.evaluate_guest(obs,lane,cfg)['admitted'])

    def test_stage_preflight_and_supervisor_keep_reporting_space_before_filesystem_exhaustion(self):
        engine=ota.module('disk_space_adapter','mustang-build-only.py').engine()
        recipe=engine.load(ROOT/'os/build-only/mustang.json',probe=False)
        bound=recipe['capacity']['compileAllowanceBytes'];reserve=recipe['capacity']['reserveBytes']
        facts={};problems=[]
        engine.space('compile',recipe,ROOT,lambda p:SimpleNamespace(f_bavail=bound+reserve-1,f_frsize=1),facts,problems)
        self.assertEqual(len(problems),1);self.assertIn('below the reserve',problems[0])
        self.assertEqual(facts['capacity']['reserveBytes'],reserve)
        with tempfile.TemporaryDirectory() as directory:
            outcome=engine.supervise([sys.executable,'-c','import time;time.sleep(10)'],directory,{'PATH':'/usr/bin:/bin'},5,
                {'sampleSeconds':0.01,'killGraceSeconds':1,'boundBytes':100,'reserveBytes':20,
                 'checkReserve':engine.reserve_checks(recipe)},statvfs=lambda p:SimpleNamespace(f_bavail=19,f_frsize=1))
            self.assertIn('fell below the reserve',outcome['stopped'])
            self.assertIsNotNone(outcome['returncode'])

    def test_filesystem_growth_refuses_wrong_identity_device_or_size_before_resize(self):
        script=(ROOT/'scripts/mustang-grow-data-filesystem.sh').read_text()
        fake='''#!/usr/bin/python3
import os,sys,json
from pathlib import Path
name=Path(sys.argv[0]).name;args=sys.argv[1:];case=os.environ['GROW_CASE']
with open(os.environ['GROW_LOG'],'a') as out:out.write(json.dumps([name,*args])+'\\n')
if name=='id':print('1000' if case=='nonroot' else '0')
elif name=='curl':
 if '-X' not in args:print('wrong' if case=='instance' else '5585425566393384049')
elif name=='findmnt':
 field=args[args.index('--output')+1]
 print({'TARGET':'/mnt/disks/mustang-data','FSTYPE':'ext4','UUID':'wrong' if case=='uuid' else '4183bff8-6be4-46bb-a641-2193d19813ee','SOURCE':'/dev/sdb'}[field])
elif name=='readlink':print('/dev/sdc' if case=='device' and 'by-id' in args[-1] else '/dev/sdb')
elif name=='lsblk':print('part' if case=='partition' else 'disk')
elif name=='blockdev':print('999' if case=='size' else '3221225472000')
elif name=='timeout':os.execv(os.environ['FAKE_BIN']+'/resize2fs',[os.environ['FAKE_BIN']+'/resize2fs',*args[2:]])
elif name=='python3':print('{"fixture":"growth-readback"}')
elif name=='mktemp':
 import tempfile
 fd,path=tempfile.mkstemp(dir=Path(os.environ['GROW_LOG']).parent);os.close(fd);print(path)
'''
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);fake_bin=base/'bin';fake_bin.mkdir()
            for name in ('id','curl','findmnt','readlink','lsblk','blockdev','timeout','resize2fs','systemctl','python3','mktemp'):
                path=fake_bin/name;path.write_text(fake);path.chmod(0o755)
            test_script=base/'grow.sh'
            test_script.write_text(script.replace('export PATH=/usr/sbin:/usr/bin:/sbin:/bin',
                                                   f'export PATH={fake_bin}:/usr/bin:/bin',1))
            for case in ('nonroot','instance','uuid','device','partition','size','success'):
                log=base/(case+'.jsonl')
                run=subprocess.run(['/bin/bash',str(test_script)],env=dict(os.environ,GROW_CASE=case,GROW_LOG=str(log),FAKE_BIN=str(fake_bin),MUSTANG_GROW_OPERATION='offline-fixture'),
                                   capture_output=True,text=True,timeout=10)
                calls=[json.loads(line) for line in log.read_text().splitlines()]
                resizes=[call for call in calls if call[0]=='resize2fs']
                self.assertEqual(run.returncode,0 if case=='success' else 4,run.stderr)
                self.assertEqual(resizes,[['resize2fs','/dev/disk/by-id/google-mustang-data']] if case=='success' else [])
                self.assertFalse(any(call[0] in ('mkfs','parted','chown','chmod') for call in calls))


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

    def bundle_fixture(self, cached=False):
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
        if cached:
            collection['candidate']['compilerCache'] = dict(enabled=True, kind='ccache', outDir=compiler_cache.OUT)
            collection['outputs']['outDir'] = str(source/compiler_cache.OUT)
            cfg_path = self.root/'os/build-only/mustang-runtime.json'
            cfg = json.loads(cfg_path.read_text())
            cfg['compilerCache'] = compiler_cache.SETTING
            cfg_path.write_text(json.dumps(cfg))
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

    def test_cached_output_path_preserves_full_signing_bundle_and_current_build_binding(self):
        bundle,out=self.bundle_fixture(cached=True)
        self.assertEqual(bundle['build'],'2026110901')
        self.assertEqual(bundle['timestamp'],'1794182400')
        self.assertTrue(bundle['fullRunSource']['copyReceipt']['sourceCopyVerified'])
        self.assertTrue((out/bundle['otatools']['file']).is_file())

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


class RetainedDeliveryTest(unittest.TestCase):
    """Real local journal, output ZIP/hash/edge collection and two-run admission.

    Original API/archive responses are offline stand-ins; the production artifact
    bytes have not been read by this test or represented as independently proved.
    """
    def setUp(self):
        import test_mustang_clock as clocks
        self.f=clocks.ClockAllocationTest('test_actual_local_reservation_keeps_lost_legacy_unknown_and_binds_compiled_time')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.root,self.home,self.lane=self.f.root,self.f.home,self.f.lane
        self.f.source=retained.SOURCE
        first=self.f.environment('42622314509',35,'2026-11-09T13:04:51Z')
        self.f.reserve(first,now='2026-11-09T14:09:50Z')
        old=self.f.environment(retained.RUN,retained.RUN_NUMBER,retained.CREATED_AT)
        self.allocation=self.f.reserve(old,now='2026-11-13T19:48:46Z')
        op=self.f.compiler_operator(self.allocation)
        self.source=Path(op['sourceRoot']);self.build=self.allocation['build'];self.stamp=op['buildDatetime']
        (self.source.parent/'allocation.json').write_bytes(proof.canonical(self.allocation))
        Path(self.allocation['integrationDir']).mkdir()
        out=self.source/f'out-mustang-build-{self.build}';product=out/'target/product/mustang'
        package=product/'obj/PACKAGING/target_files_intermediates/mustang-target_files.zip'
        package.parent.mkdir(parents=True)
        edge_fixture.fixtures.target_files(package,extra={'VENDOR/build.prop':
            f'ro.product.vendor.device=mustang\nro.vendor.build.version.incremental={self.build}\nro.vendor.build.date.utc={self.stamp}\n'})
        for name in ('vendor_boot.img','vendor_kernel_boot.img'):(product/name).write_bytes(b'fixture image')
        tools=out/'soong/.intermediates/build/make/tools/otatools_package/otatools-package/linux_glibc_x86_64/gen/otatools.zip'
        tools.parent.mkdir(parents=True);tools.write_bytes(b'fixture tools, never executed')
        adapter=ota.module('retained_output_fixture','mustang-build-only.py')
        self.outputs=adapter.collect_outputs(self.source,out.name);self.assertEqual(self.outputs['verdict'],'complete')
        self.cfg=dict(self.f.cfg,dataMount=str(self.home.parent),appInputsRecord='os/app-inputs/frankel-2026110901.json')
        for name in ('os/pinned-manifest.xml',self.cfg['appInputsRecord']):
            target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/name).read_bytes())
        (self.root/'os/build-only/mustang-runtime.json').write_text(json.dumps(self.cfg))
        (self.root/'os/build-only/mustang-lane.json').write_text(json.dumps(self.lane))
        record=json.loads((self.root/self.cfg['appInputsRecord']).read_text())
        run=dict(runId=retained.RUN,runAttempt='1',source=retained.SOURCE,
            workflow='Immortal-Protocols/RoseOS-1/'+self.lane['workflow']+'@refs/heads/main')
        native=dict(schema=collector.SCHEMA,run=run,workflowJob='builder',observationKind='native-read-only',
            success=True,guestIdentityObserved=True,dataFilesystemIdentityObserved=True,checkoutBytesVerified=True,
            workflowContextValidated=True,fullBootstrapVerified=False,pristine=False,otaAdmitted=False,signingStarted=False,
            controllerOwnershipSha256='a'*64,sourceCandidate=dict(manifestSha256=hosted.sha256_file(self.root/'os/pinned-manifest.xml'),
                portableInventorySha256='b'*64))
        copied=dict(schema='roseos.mustang-full-run-source-copy.v1',run=run,builder=self.lane['vm'],sourceCopyVerified=True,
            portableInventorySha256='b'*64,nativeReceiptSha256=hashlib.sha256(proof.canonical(native)).hexdigest())
        completed=dict(device='mustang',stage='compile',returncode=0,stopped=None,ok=True,
            buildIdentity=dict(buildNumber=self.build,buildDatetime=self.stamp),outputs=self.outputs)
        self.candidate=dict(schema='roseos.mustang-candidate-run.v1',device='mustang',source=retained.SOURCE,
            runId=retained.RUN,runAttempt='1',builder=self.lane['vm'],workflow=self.lane['workflow'],
            root=str(self.source.parent),allocation=self.allocation,unsignedCompileComplete=True,omittedChecks=[],
            appInputAdmission=dict(verdict='ADMITTED',producer=record['producer']['tag'],export=record['export'],apps=record['apps'],toolsRechecked=True),
            stages={'integrate':dict(source=retained.SOURCE,result=dict(returncode=0,stopped=None)),
                'compile':dict(result=completed),'bootstrap-source':dict(result=native,receipt=dict(sha256=copied['nativeReceiptSha256'])),
                'source-copy':dict(result=copied)})
        self.docs=dict(zip(retained.FILES,(self.candidate,completed,self.allocation)))
        self.original_artifact=dict(retained.ARTIFACT)
        self.delivery='90000000001';self.sha='c'*40
        self.current=dict(runId=self.delivery,runAttempt='1',source=self.sha,workflow=run['workflow'])
        owner=dict(schema=controller.SCHEMA,run=self.current,owner=dict(vm=self.lane['vm'],startedByThisOperation=True))
        self.env=dict(GITHUB_RUN_ID=self.delivery,GITHUB_RUN_ATTEMPT='1',GITHUB_SHA=self.sha,
            GITHUB_WORKSPACE=str(self.root),
            GITHUB_WORKFLOW_SHA=self.sha,GITHUB_WORKFLOW_REF=run['workflow'],GITHUB_REF='refs/heads/main',
            GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_REPOSITORY='Immortal-Protocols/RoseOS-1',GITHUB_REPOSITORY_ID='1428996648',
            GITHUB_JOB='builder',MUSTANG_DRY_RUN='false',MUSTANG_PUBLISH='true',MUSTANG_RETAINED_COMPILE_RUN=retained.RUN,
            MUSTANG_OWNER_B64=base64.b64encode(proof.canonical(owner)).decode())
        self.event={'inputs':dict(publish=True,retained_compile_run=retained.RUN)}
        self.facts=dict(instanceId=self.lane['vm']['instanceId'],filesystemUuid=self.cfg['dataFilesystemUuid'],
            mount=self.cfg['dataMount'],availableBytes=10**12,availableInodes=10**6,totalInodes=10**7,euid=999)
        self.actual=dict(id=int(retained.RUN),run_attempt=1,run_number=retained.RUN_NUMBER,head_sha=retained.SOURCE,head_branch='main',
            path=retained.WORKFLOW,workflow_id=408460629,event='workflow_dispatch',created_at=retained.CREATED_AT,
            status='completed',conclusion='failure',repository=dict(id=1428996648),head_repository=dict(id=1428996648))
        self.jobs=[]
        for i,(name,conclusion) in enumerate((('guard','success'),('controller','success'),('enroll','success'),
                ('builder','failure'),('cleanup','success'),('signer','skipped'),('publisher','skipped'))):
            self.jobs.append(dict(id=132098482375 if name=='builder' else i+1,name=name,run_id=int(retained.RUN),
                head_sha=retained.SOURCE,status='completed',conclusion=conclusion,runner_group_id=5,
                runner_name='mustang-'+retained.RUN,labels=[self.lane['runnerLabel']],steps=[
                    dict(name='Prepare, allocate, integrate and compile once on Mustang disk',conclusion='success'),
                    dict(name='Require unsigned raised-edge proof before the hosted signing bundle',conclusion='failure')]))
        self.calls=[];self.repack()
        self.live_compatible=retained.compatible
        def compatible(source,root=ROOT,git=None):
            return self.live_compatible(source,root,git=lambda args:SimpleNamespace(returncode=0,stdout='scripts/mustang_retained.py\n'))
        for patcher in (patch.dict(os.environ,self.env,clear=True),patch.object(retained,'compatible',side_effect=compatible),
                patch.object(collector,'runner_identity',return_value=(999,'mustang-runner')),
                patch.object(sys,'dont_write_bytecode',True),patch.object(collector.mustang_lane,'configuration',return_value=self.lane)):
            patcher.start();self.addCleanup(patcher.stop)
        self.auth=retained.authenticate
        def authenticate(env,root=ROOT):return self.auth(env,self.root,api=self.api,read=self.read)
        patcher=patch.object(retained,'authenticate',side_effect=authenticate);patcher.start();self.addCleanup(patcher.stop)

    def repack(self):
        data=io.BytesIO();self.raw={name:proof.canonical(value) for name,value in self.docs.items()}
        with zipfile.ZipFile(data,'w') as zipped:
            for name,raw in self.raw.items():zipped.writestr(name,raw)
        self.blob=data.getvalue()
        retained.ARTIFACT=dict(retained.ARTIFACT,sizeBytes=len(self.blob),sha256=hashlib.sha256(self.blob).hexdigest())
        self.addCleanup(setattr,retained,'ARTIFACT',self.original_artifact)
        original=self.source.parent/'delivery-inputs';original.mkdir(exist_ok=True)
        for name in retained.FILES:(original/name).write_bytes(self.raw[name])

    def api(self,path):
        self.calls.append(path)
        self.assertIn(path,('/repos/Immortal-Protocols/RoseOS-1/actions/runs/'+retained.RUN,
            '/repos/Immortal-Protocols/RoseOS-1/actions/runs/'+retained.RUN+'/attempts/1/jobs?per_page=100'))
        return copy.deepcopy(dict(total_count=len(self.jobs),jobs=self.jobs) if '/jobs?' in path else self.actual)

    def read(self,suffix,limit):
        self.calls.append(suffix)
        if suffix=='/zip':return self.blob
        self.assertEqual(suffix,'')
        return json.dumps(dict(id=retained.ARTIFACT['id'],name=retained.ARTIFACT['name'],expired=False,
            digest='sha256:'+retained.ARTIFACT['sha256'],size_in_bytes=len(self.blob),workflow_run=dict(id=int(retained.RUN),head_sha=retained.SOURCE))).encode()

    def preserved(self):
        paths=list(self.f.journal.rglob('*.json'))+list((self.source.parent/'delivery-inputs').iterdir())
        return {str(p):p.read_bytes() for p in paths}

    def collect(self):
        with patch.object(ota.module('retained_sign_loader','mustang-sign-package.py').mustang_collection,'ROOT',self.root):
            original=ota.module
            def module(name,file):
                value=original(name,file)
                if file=='mustang-sign-package.py':value.source_sha=lambda:self.sha
                return value
            with patch.object(ota,'module',side_effect=module):
                return retained.collect(self.lane,self.cfg,self.env,self.event,self.facts,self.root)

    def test_authenticate_real_archive_and_collect_rehash_without_make_allocation_or_mutation(self):
        before=self.preserved()
        with patch.object(retained.mustang_clock,'reserve',side_effect=AssertionError('allocated again')):
            report=self.collect()
        self.assertEqual(report['status'],'complete');self.assertEqual(report['compileRunId'],retained.RUN)
        self.assertEqual(report['runId'],self.delivery);self.assertEqual(report['originalWorkflowConclusion'],'failure')
        self.assertFalse(report['makeRerun']);self.assertEqual(before,self.preserved())
        self.assertEqual(report['roseEdge']['binding']['candidateSource'],retained.SOURCE)
        self.assertEqual(report['roseEdge']['binding']['recipeSource'],self.sha)
        self.assertEqual(retained.compile_context(dict(retainedProducer=report['retainedProducer'],
            runId=self.delivery,runAttempt='1',sourceSha=self.sha)),(retained.RUN,'1',retained.SOURCE))
        with self.assertRaisesRegex(ValueError,'attempt exists'):self.collect()

    def test_wrong_original_outcome_steps_archive_clock_apps_and_cleanup_are_refused(self):
        for change in (dict(conclusion='success'),dict(head_sha='d'*40),dict(run_attempt=2),dict(run_number=52)):
            with self.subTest(change=change),patch.dict(self.actual,change):
                with self.assertRaises(ValueError):retained.authenticate(self.env,self.root)
        self.jobs[4]['conclusion']='failure'
        with self.assertRaises(ValueError):retained.authenticate(self.env,self.root)
        self.jobs[4]['conclusion']='success';self.jobs[3]['steps'][0]['conclusion']='failure'
        with self.assertRaises(ValueError):retained.authenticate(self.env,self.root)
        self.jobs[3]['steps'][0]['conclusion']='success'
        bad=self.blob;self.blob=bad[:-1]+bytes([bad[-1]^1])
        with self.assertRaisesRegex(ValueError,'archive digest'):retained.authenticate(self.env,self.root)
        self.blob=bad;self.docs['candidate-receipt.json']['appInputAdmission']['apps']=[];self.repack()
        with self.assertRaisesRegex(ValueError,'signed-app'):retained.authenticate(self.env,self.root)

    def test_guest_journal_and_retained_bytes_fail_closed_without_repair(self):
        before=self.preserved()
        with self.assertRaises(ValueError):
            retained.guest_receipt(self.lane,self.cfg,self.env,self.event,dict(self.facts,instanceId='wrong'))
        head=self.f.journal/'head.json';raw=head.read_bytes();head.write_text('{}')
        with self.assertRaisesRegex(ValueError,'journal'):self.collect()
        self.assertEqual(head.read_text(),'{}');head.write_bytes(raw)
        target=Path(self.outputs['artifacts']['targetFiles']['path']);target.write_bytes(b'changed retained output')
        with self.assertRaisesRegex(ValueError,'identity incomplete|changed during collection'):self.collect()
        self.assertEqual(before,self.preserved())

    def test_compatibility_and_scope_do_not_allow_os_drift_make_or_preparation_modes(self):
        for changed in ('os/pinned-manifest.xml','workspace/rose-agent/app/build.gradle','os/build-only/mustang-clock-numbering.json'):
            with self.subTest(path=changed),self.assertRaises(ValueError):
                self.live_compatible(self.sha,self.root,git=lambda args:SimpleNamespace(returncode=0,
                    stdout=changed+'\n' if args[0]=='diff' else ''))
        with self.assertRaises(ValueError):retained.selected(dict(MUSTANG_RETAINED_COMPILE_RUN='any-other-run'))
        with self.assertRaises(collector.git.Refused):collector.full_invocation(self.lane,self.env,self.event)
        bad=dict(inputs=dict(self.event['inputs'],dry_run=True))
        with self.assertRaises(collector.git.Refused):collector.full_invocation(self.lane,self.env,bad,retained=True)

    def test_denied_read_stops_once_before_native_collection(self):
        calls=[]
        def denied(*args):calls.append(args);raise ValueError('fixture HTTP403; no retry')
        with self.assertRaisesRegex(ValueError,'HTTP403'):
            self.auth(self.env,self.root,api=denied,read=self.read)
        self.assertEqual(len(calls),1)
        self.assertFalse((self.home/'rose-actions'/('mustang-collect-'+self.delivery+'-1')).exists())

    def test_newer_journal_reservation_is_preserved_and_blocks_retained_delivery(self):
        later=self.f.environment('90000000000',52,'2026-11-13T22:00:00Z')
        self.f.reserve(later,now='2026-11-13T22:20:00Z');before=self.preserved()
        with self.assertRaisesRegex(ValueError,'journal'):self.collect()
        self.assertEqual(before,self.preserved())
        self.assertFalse((self.home/'rose-actions'/('mustang-retained-'+self.delivery+'-1')).exists())

    def test_archive_denial_has_no_retry_or_alternate_read(self):
        calls=[]
        def read(suffix,limit):
            calls.append(suffix)
            if suffix=='/zip':raise ValueError('fixture archive HTTP403; no retry')
            return self.read(suffix,limit)
        with self.assertRaisesRegex(ValueError,'archive HTTP403'):
            self.auth(self.env,self.root,api=self.api,read=read)
        self.assertEqual(calls,['','/zip'])

    def bundle(self):
        report=self.collect()
        _package,inspected,provenance=FullDeliveryTest.tool_fixture(self,self.source)
        path=self.home/'rose-actions'/('mustang-collect-'+self.delivery+'-1')/'compile-collection.json'
        with patch.object(hosted,'public_identity',return_value={'fixtureOnly':True}), \
                patch.object(qualification,'inspection_record',return_value=inspected), \
                patch.object(qualification,'inspect_package',return_value=inspected), \
                patch.object(closure,'verify_closure',return_value=provenance):
            bundle=hosted.export_bundle(path,self.home/'new-delivery-bundle',root=self.root)
        self.assertEqual(bundle['retainedProducer'],report['retainedProducer'])
        return bundle

    def test_hosted_bundle_authenticates_two_runs_and_rejects_provenance_substitution(self):
        before=self.preserved();bundle=self.bundle()
        current=dict(id=int(self.delivery),run_attempt=1,head_sha=self.sha,head_branch='main',path=retained.WORKFLOW,
            event='workflow_dispatch',workflow_id=408460629,run_number=52,created_at='2026-11-13T22:00:00Z',
            status='in_progress',conclusion=None,repository=dict(id=1428996648),head_repository=dict(id=1428996648))
        jobs=[]
        for i,name in enumerate(('guard','controller','enroll','builder','signer')):
            jobs.append(dict(id=i+1,name=name,run_id=int(self.delivery),head_sha=self.sha,run_attempt=1,
                status='in_progress' if name=='signer' else 'completed',conclusion=None if name=='signer' else 'success',
                runner_group_id=5,runner_name='mustang-'+self.delivery,labels=[self.lane['runnerLabel']]))
        def api(path):
            self.assertIn('/'+self.delivery,path)
            return copy.deepcopy(dict(total_count=len(jobs),jobs=jobs) if '/jobs?' in path else current)
        env=dict(self.env,GITHUB_JOB='signer',EXPECTED_FULL_BUNDLE_SHA256='a'*64)
        checked=hosted.authenticate_full_run(bundle,env,api=api,root=self.root)
        self.assertTrue(checked['actionsRunIdentityVerified']);self.assertFalse(checked['otaAdmitted'])
        self.assertTrue(checked['retainedProducerVerified']);self.assertEqual(checked['retainedProducerRunId'],retained.RUN)
        self.assertEqual(before,self.preserved())
        self.assertEqual(bundle['fullRunSource']['nativeReceipt']['run']['runId'],retained.RUN)
        self.assertEqual(bundle['retainedGuest']['run']['runId'],self.delivery)
        self.assertEqual(bundle['clockAllocation'],self.allocation)
        for broken in (dict(retainedProducer=dict(bundle['retainedProducer'],source=self.sha)),
                       dict(retainedGuest=dict(bundle['retainedGuest'],controllerOwnershipSha256='0'*64)),
                       dict(sourceSha=retained.SOURCE),dict(runId=retained.RUN)):
            with self.subTest(change=broken),self.assertRaises(ValueError):
                hosted.authenticate_full_run(dict(bundle,**broken),env,api=api,root=self.root)
        self.actual['conclusion']='cancelled'
        with self.assertRaises(ValueError):hosted.authenticate_full_run(bundle,env,api=api,root=self.root)

    def test_retained_clock_and_edge_bindings_survive_publication_receipt_shape(self):
        bundle=self.bundle()
        # The public stage carries delivery identity plus the old producer proof,
        # never a newly issued allocation for the new run.
        receipt=dict(clockAllocation=bundle['clockAllocation'],build=self.build,timestamp=self.stamp,
            runId=self.delivery,runAttempt='1',sourceSha=self.sha,retainedProducer=bundle['retainedProducer'])
        retained.mustang_clock.require_bundle(receipt,self.lane,self.cfg,self.root)
        with self.assertRaises(ValueError):
            retained.mustang_clock.require_bundle(dict(receipt,retainedProducer=None),self.lane,self.cfg,self.root)
        hosted.require_full_bundle_source(bundle,self.root)
        wrong=copy.deepcopy(bundle);wrong['unsignedRaisedEdge']['binding']['recipeSource']=retained.SOURCE
        with self.assertRaises(ValueError):hosted.require_full_bundle_source(wrong,self.root)

    def test_retained_hosted_staging_keeps_producer_and_delivery_separate(self):
        bundle=self.bundle();unsigned=self.home/'new-delivery-bundle'/bundle['unsigned']['file']
        signed=self.home/'signed-target_files.zip';shutil.copyfile(unsigned,signed)
        public=self.home/'public';public.mkdir();ota_path=public/f'mustang-ota_update-{self.build}.zip'
        with zipfile.ZipFile(ota_path,'w') as zipped:
            zipped.writestr('META-INF/com/android/metadata',
                f'pre-device=mustang\npost-build-incremental={self.build}\npost-timestamp={self.stamp}\npost-build=brand/mustang/{self.build}:user/release-keys\nota-type=AB\n')
        (public/'releasekey.x509.pem').write_bytes(b'public certificate fixture')
        avb=public/'avb_pkmd.bin';avb.write_bytes(b'public AVB fixture')
        binary=self.home/'extracted/bin';binary.mkdir(parents=True)
        for name in ('check_ota_package_signature','delta_generator'):(binary/name).write_bytes(b'NEVER EXECUTE: verifier fixture')
        java=self.home/'jdk';java.mkdir()
        self.cfg.update(candidateContinuity='os/build-only/mustang-candidate-continuity-20261107.json',status='bound')
        (self.root/'os/build-only/mustang-runtime.json').write_text(json.dumps(self.cfg))
        (self.root/self.cfg['candidateContinuity']).write_bytes((ROOT/self.cfg['candidateContinuity']).read_bytes())
        pins=ota.identities();actual_digest=ota.digest;commands=[]
        def digest(path):return pins['avbPublicKeySha256'] if Path(path)==avb else actual_digest(path)
        def verify(argv,**kwargs):
            commands.append(argv);self.assertEqual(argv[0],str(binary/'check_ota_package_signature'))
            self.assertNotIn('GH_TOKEN',kwargs['env']);return SimpleNamespace(returncode=0)
        stage=self.home/'staged'
        live_read=ota.read
        def read(path):
            return self.cfg if Path(path)==ROOT/'os/build-only/mustang-runtime.json' else live_read(path)
        with patch.object(ota,'read',side_effect=read),patch.object(ota,'identities',return_value=pins), \
                patch.object(ota,'fingerprint',return_value='f'*64),patch.object(ota,'digest',side_effect=digest), \
                patch.object(ota.host_secrets,'certificate_sha256',return_value=pins['otaCertificateSha256']), \
                patch.object(ota,'observe_target',return_value={'packagedUpdaterVerified':True}):
            receipt=ota.stage_hosted(bundle,unsigned,signed,ota_path,public,binary.parent,java,stage,
                root=self.root,execute=verify)
            self.assertEqual(ota.verify_staged(stage,ota.config(),publication=False),receipt)
        self.assertTrue(commands);self.assertEqual(receipt['candidateSource'],retained.SOURCE)
        self.assertEqual(receipt['recipeSource'],self.sha);self.assertEqual(receipt['runId'],self.delivery)
        self.assertEqual(receipt['compileRunId'],retained.RUN);self.assertEqual(receipt['clockAllocation'],self.allocation)
        self.assertEqual(receipt['roseEdge']['signed']['binding']['candidateSource'],retained.SOURCE)


if __name__=='__main__':unittest.main()
