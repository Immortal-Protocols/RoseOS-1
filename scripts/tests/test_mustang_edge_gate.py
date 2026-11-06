"""Offline adapter boundaries only: synthetic archives, signer/generator stand-ins, no live actions."""
import copy
import functools
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts'), str(Path(__file__).resolve().parent)]
import mustang_edge as gate
import mustang_ota as ota
import mustang_lane as lane
import mustang_controller as controller
import mustang_paths
import subprocess
import dex_fixture
import test_edge_artifact as fixtures
import os_release_shape as workflow_shape

spec = importlib.util.spec_from_file_location('mustang_sign_gate_tests', ROOT/'scripts/mustang-sign-package.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
BUILD, EPOCH, SOURCE, RECIPE = '2026110501', '1793836800', 'a'*40, 'b'*40


def descriptor(path):
    return dict(path=str(path), state='collected', **gate.observed_archive(path))


def bound():
    return gate.binding(SOURCE, RECIPE, BUILD, EPOCH, '333', '1', '222', '1')


def publication_proofs(directory, candidate=SOURCE, recipe=RECIPE, build=BUILD, epoch=EPOCH, run='333'):
    """Real shared scanner proofs for the publication stand-ins, not invented pass booleans."""
    unsigned = fixtures.target_files(directory/'unsigned.zip')
    signed = fixtures.target_files(directory/'signed.zip')
    binding = gate.binding(candidate, recipe, build, epoch, run, '1', '222', '1')
    return dict(runAttempt='1', compileRunId='222', compileRunAttempt='1',
                edgeTargetFiles={stage:gate.observed_archive(path) for stage,path in
                                 (('unsigned',unsigned),('signed',signed))},
                roseEdge={stage:gate.inspect(path,stage,binding,descriptor(path)) for stage,path in
                          (('unsigned',unsigned),('signed',signed))})


class DedicatedLaneTest(unittest.TestCase):
    def proposal(self):
        value=copy.deepcopy(lane.configuration())
        value.update(status='bound',workflow='.github/workflows/mustang-dedicated.yml',
                     runnerLabel='synthetic-mustang-runner',queue='synthetic-mustang-queue',
                     sourceRoot='/synthetic/source',storageRoot='/synthetic/actions',
                     diskResource='projects/synthetic/zones/synthetic-zone/disks/synthetic-mustang',
                     lockPath='/synthetic/mustang.lock',infraEvidence='synthetic fixture, not a real binding',
                     vm=dict(project='synthetic',zone='synthetic-zone',instance='synthetic-mustang',instanceId='12345'),
                     controller=dict(identityVariable='MUSTANG_CONTROLLER_SERVICE_ACCOUNT',
                                     providerVariable='MUSTANG_CONTROLLER_WORKLOAD_IDENTITY_PROVIDER'))
        return value

    def test_committed_dedicated_lane_is_unbound_and_renders_no_commands(self):
        self.assertEqual(lane.configuration()['status'],'unbound')
        with self.assertRaises(ValueError): lane.controller_plan(lane.configuration())

    def test_dedicated_commands_are_only_for_the_verified_instance(self):
        value=self.proposal()
        plan=lane.controller_plan(value)
        for action in ('get','start','stop'):
            self.assertIn('synthetic-mustang',plan[action])
            self.assertNotIn('immortal-os-builder',plan[action])
            self.assertNotIn('roseos-yogi-builder-20261103-a',plan[action])
        self.assertEqual(plan['ownership'],value['vm'])
        self.assertFalse(plan['runtimeAuthorized'])

    def test_different_label_does_not_allow_the_same_frankel_or_yogi_vm(self):
        capacity=json.loads((ROOT/'os/release-pipeline/lanes/capacity.json').read_text())
        for flavor in ('frankel','yogi'):
            value=self.proposal()
            value['vm'].update(capacity['hosts'][capacity['lanes'][flavor]['host']]['vm'])
            with self.subTest(flavor=flavor):
                with self.assertRaises(ValueError): lane.controller_plan(value)

    def test_retired_instance_id_runner_and_queue_cannot_be_reused(self):
        for field,value in (('instanceId','685707771105568194'),('runnerLabel','roseos-frankel-builder'),
                            ('queue','os-release-frankel'),('workflow','.github/workflows/os-release.yml'),
                            ('workflow','.github/workflows/yogi-ota.yml'),('lockPath','/')):
            profile=self.proposal()
            (profile['vm'] if field=='instanceId' else profile)[field]=value
            with self.subTest(field=field,value=value):
                with self.assertRaises(ValueError): lane.controller_plan(profile)

    def test_stop_requires_matching_dedicated_vm_and_known_start_ownership(self):
        profile=self.proposal()
        record=dict(vm=profile['vm'],startedByThisOperation=True)
        self.assertTrue(lane.owned_start(record,profile))
        self.assertFalse(lane.owned_start(dict(record,startedByThisOperation=False),profile))
        for invalid in (None,{},dict(record,startedByThisOperation='true'),
                        dict(record,vm=dict(profile['vm'],instance='immortal-os-builder')),
                        dict(record,vm=dict(profile['vm'],instanceId='999'))):
            with self.subTest(record=invalid):
                with self.assertRaises(ValueError): lane.owned_start(invalid,profile)

    def test_binding_future_capacity_cannot_restore_the_legacy_route(self):
        with patch.object(lane,'configuration',return_value=self.proposal()):
            with self.assertRaisesRegex(ValueError,'legacy route retired'): lane.reject_legacy()

    def test_actual_legacy_cli_admissions_refuse_before_controller_or_credentials(self):
        commands=[
            ['mustang-build-only.py','plan'], ['mustang-build-only.py','preflight'], ['mustang-build-only.py','run'],
            ['mustang-pipeline.py','describe'],
            ['mustang-pipeline.py','run','--run-id','1','--run-attempt','1'],
            ['mustang-pipeline.py','report','--run-id','1','--run-attempt','1','--out','/must-not-write'],
            ['mustang-delivery.py','guard','--mode','bootstrap'],
            ['mustang-delivery.py','guard','--mode','sign-package','--compile-run','1','--bootstrap-run','1'],
            ['mustang_ota.py','guard'],['mustang_ota.py','observe'],['mustang_ota.py','stage'],
            ['mustang_ota.py','publish'],
            ['mustang-sign-package.py','collect'],['mustang-sign-package.py','sign'],
            ['mustang-sign-package.py','package'],['mustang-sign-package.py','_execute'],
            ['mustang-allocate.py','reserve'],
        ]
        env=dict(PATH=os.environ['PATH'],GITHUB_REF='refs/heads/main',GITHUB_EVENT_NAME='workflow_dispatch',
                 GITHUB_WORKFLOW_REF='Immortal-Protocols/RoseOS-1/.github/workflows/os-release.yml@refs/heads/main',
                 MUSTANG_RUNTIME_ALLOW_FRANKEL='true')
        for command in commands:
            with self.subTest(command=command):
                result=subprocess.run([sys.executable,str(ROOT/'scripts'/command[0]),*command[1:]],
                                      env=env,capture_output=True,text=True,timeout=10)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('Mustang legacy route retired',result.stderr)

    def test_workflow_guard_precedes_controller_and_skipped_controller_never_stops(self):
        # Use the suite's stdlib shape reader, so this safety check also runs in
        # the dependency-free App offline job. Check job-level dependencies and
        # conditions rather than matching similarly named fields inside steps.
        jobs=workflow_shape.jobs()
        commands=jobs['guard']
        self.assertIn('scripts/mustang-pipeline.py describe',commands)
        self.assertIn('scripts/mustang-delivery.py guard',commands)
        self.assertIn('scripts/mustang_ota.py guard',commands)
        self.assertRegex(jobs['controller'], r'(?m)^    needs: guard$')
        self.assertNotRegex(jobs['controller'], r'(?m)^    if:.*always\(\)')
        self.assertRegex(jobs['cleanup'], r"(?m)^    if: always\(\) && needs\.controller\.result != 'skipped'$")


class DedicatedControllerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.record = Path(self.temp.name)/'ownership.json'
        self.lane = DedicatedLaneTest().proposal()
        self.env = dict(GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main',
                        GITHUB_WORKFLOW_REF='Immortal-Protocols/RoseOS-1/'+self.lane['workflow']+'@refs/heads/main',
                        GITHUB_SHA='a'*40, GITHUB_RUN_ID='1234', GITHUB_RUN_ATTEMPT='1')
        self.run = controller.invocation(self.lane, self.env)

    def fake(self, started=True, fail=False):
        record = self.record
        calls = []

        class Fake:
            def control_builder(self, action, *, builder, claim=None):
                calls.append((action, builder))
                if action == 'start':
                    claim(started)
                    # The durable record must exist before a request could be submitted.
                    self.record_before_request = json.loads(record.read_text())
                    if fail:
                        raise RuntimeError('synthetic uncertain start')
                return 'RUNNING' if action == 'start' else 'TERMINATED'

        fake = Fake()
        fake.calls = calls
        return fake

    def test_existing_implicit_frankel_api_is_rejected_without_calling_it(self):
        class Implicit:
            def control_builder(self, action, claim=None):
                self.fail('implicit controller must never be called')
        with self.assertRaisesRegex(ValueError, 'explicit builder API unavailable'):
            controller.start(Implicit(), self.lane, self.run, self.record)
        self.assertFalse(self.record.exists())

    def test_start_and_stop_carry_exact_dedicated_vm_and_preserve_ownership(self):
        fake = self.fake()
        result = controller.start(fake, self.lane, self.run, self.record)
        self.assertTrue(result['startedByThisOperation'])
        self.assertEqual(fake.record_before_request['run'], self.run)
        self.assertEqual(self.record.stat().st_mode & 0o777, 0o600)
        self.assertEqual(controller.stop(fake, self.lane, self.run, self.record)['observed'], 'TERMINATED')
        self.assertEqual(fake.calls, [('start',self.lane['vm']), ('stop',self.lane['vm'])])
        self.assertTrue(self.record.exists())

    def test_already_running_vm_is_not_stopped_by_this_attempt(self):
        fake = self.fake(started=False)
        controller.start(fake, self.lane, self.run, self.record)
        self.assertIn('skipped', controller.stop(fake, self.lane, self.run, self.record))
        self.assertEqual(len(fake.calls), 1)

    def test_failed_start_retains_claim_and_is_never_automatically_retried(self):
        fake = self.fake(fail=True)
        with self.assertRaisesRegex(RuntimeError, 'uncertain start'):
            controller.start(fake, self.lane, self.run, self.record)
        self.assertTrue(controller.read_ownership(self.record, self.lane, self.run))
        with self.assertRaisesRegex(ValueError, 'reconcile'):
            controller.start(fake, self.lane, self.run, self.record)
        self.assertEqual(len(fake.calls), 1)

    def test_absent_foreign_stale_or_malformed_record_never_stops(self):
        fake = self.fake()
        with self.assertRaisesRegex(ValueError, 'STOP refused'):
            controller.stop(fake, self.lane, self.run, self.record)
        valid = dict(schema=controller.SCHEMA, run=self.run,
                     owner=dict(vm=self.lane['vm'], startedByThisOperation=True))
        for value in ({}, None, [], dict(valid, run=dict(self.run, source='b'*40)),
                      dict(valid, run=dict(self.run, runId='999')),
                      dict(valid, owner=dict(vm=dict(self.lane['vm'], instanceId='999'),
                                             startedByThisOperation=True)),
                      dict(valid, owner=dict(vm=self.lane['vm'], startedByThisOperation='true'))):
            with self.subTest(value=value):
                self.record.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError, 'STOP refused'):
                    controller.stop(fake, self.lane, self.run, self.record)
        self.assertEqual(fake.calls, [])

    def test_unbound_or_other_flavor_lanes_never_reach_control(self):
        fake = self.fake()
        for invalid in (lane.configuration(), dict(self.lane, vm=dict(self.lane['vm'],
                                          instance='immortal-os-builder', project='protocols-465723',zone='us-west2-a'))):
            with self.subTest(lane=invalid):
                with self.assertRaises(ValueError): controller.start(fake, invalid, self.run, self.record)
        self.assertEqual(fake.calls, [])

    def test_record_link_or_interrupted_write_is_not_accepted_for_stop(self):
        fake = self.fake()
        self.record.write_text('{"schema":')
        with self.assertRaisesRegex(ValueError, 'STOP refused'):
            controller.stop(fake, self.lane, self.run, self.record)
        self.record.unlink()
        target = self.record.parent/'foreign.json'
        target.write_text('{}')
        self.record.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'ownership absent'):
            controller.stop(fake, self.lane, self.run, self.record)
        with self.assertRaisesRegex(ValueError, 'reconcile'):
            controller.start(fake, self.lane, self.run, self.record)
        self.assertEqual(target.read_text(), '{}')
        self.assertEqual(fake.calls, [])

    def common_api(self):
        actual=controller.common_controller()
        # Exercise the real shared implementation; swap only its process edge
        # and sleep in these tests. No gcloud process or cloud request can run.
        actual.control_builder=functools.partial(actual.control_builder,sleep=lambda seconds:None,interval=0)
        return actual

    def test_real_common_api_start_stop_keeps_target_and_durable_ownership(self):
        actual=self.common_api(); state='TERMINATED'; calls=[]
        def process(argv,**kwargs):
            nonlocal state
            calls.append(argv)
            self.assertEqual(argv[:3],['gcloud','compute','instances'])
            self.assertEqual(argv[4],self.lane['vm']['instance'])
            self.assertIn('--project='+self.lane['vm']['project'],argv)
            self.assertIn('--zone='+self.lane['vm']['zone'],argv)
            if argv[3]=='describe':
                return SimpleNamespace(returncode=0,stdout=json.dumps(dict(id=self.lane['vm']['instanceId'],
                                                    name=self.lane['vm']['instance'],status=state)))
            self.assertEqual(argv[3],'start' if state=='TERMINATED' else 'stop')
            self.assertTrue(controller.read_ownership(self.record,self.lane,self.run))
            state='RUNNING' if argv[3]=='start' else 'TERMINATED'
            return SimpleNamespace(returncode=0)
        with patch('subprocess.run',side_effect=process):
            self.assertEqual(controller.start(actual,self.lane,self.run,self.record)['observed'],'RUNNING')
            self.assertEqual(controller.stop(actual,self.lane,self.run,self.record)['observed'],'TERMINATED')
        self.assertEqual([argv[3] for argv in calls],['describe','start','describe','describe','stop','describe'])
        self.assertTrue(self.record.exists())

    def test_real_common_api_wrong_id_refuses_before_start_and_before_stop(self):
        actual=self.common_api()
        wrong=SimpleNamespace(returncode=0,stdout=json.dumps(dict(id='999',name=self.lane['vm']['instance'],
                                                               status='TERMINATED')))
        with patch('subprocess.run',return_value=wrong) as process:
            with self.assertRaisesRegex(Exception,'identity mismatch'):
                controller.start(actual,self.lane,self.run,self.record)
            self.assertFalse(self.record.exists())
            self.assertEqual([call.args[0][3] for call in process.call_args_list],['describe'])
        controller.start(self.fake(),self.lane,self.run,self.record)
        with patch('subprocess.run',return_value=wrong) as process:
            with self.assertRaisesRegex(Exception,'identity mismatch'):
                controller.stop(actual,self.lane,self.run,self.record)
            self.assertEqual([call.args[0][3] for call in process.call_args_list],['describe'])

    def test_real_common_api_failed_start_request_is_reconciled_without_resubmit(self):
        actual=self.common_api(); calls=[]
        states=iter(('TERMINATED','PROVISIONING','RUNNING'))
        def process(argv,**kwargs):
            calls.append(argv[3])
            if argv[3]=='describe':
                return SimpleNamespace(returncode=0,stdout=json.dumps(dict(id=self.lane['vm']['instanceId'],
                                                    name=self.lane['vm']['instance'],status=next(states))))
            self.assertEqual(argv[3],'start')
            self.assertTrue(controller.read_ownership(self.record,self.lane,self.run))
            return SimpleNamespace(returncode=1)
        with patch('subprocess.run',side_effect=process):
            result=controller.start(actual,self.lane,self.run,self.record)
        self.assertEqual(result['observed'],'RUNNING')
        self.assertEqual(calls,['describe','start','describe','describe'])
        self.assertTrue(controller.read_ownership(self.record,self.lane,self.run))

    def test_wrong_workflow_ref_source_event_or_attempt_is_refused(self):
        for key, value in (('GITHUB_WORKFLOW_REF','Immortal-Protocols/RoseOS-1/.github/workflows/os-release.yml@refs/heads/main'),
                           ('GITHUB_REF','refs/heads/other'), ('GITHUB_RUN_ATTEMPT','2'),
                           ('GITHUB_EVENT_NAME','push'), ('GITHUB_SHA','not-a-commit'), ('GITHUB_RUN_ID','0')):
            with self.subTest(key=key):
                with self.assertRaises(ValueError): controller.invocation(self.lane, dict(self.env, **{key:value}))

    def test_all_committed_controller_cli_actions_refuse_without_cloud_calls(self):
        env = dict(PATH=os.environ['PATH'])
        for action in ('check','plan','start','ownership','stop'):
            result = subprocess.run([sys.executable,str(ROOT/'scripts/mustang_controller.py'), action],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 4)
            self.assertIn('dedicated Mustang bindings missing', result.stderr)


class DedicatedPathsTest(unittest.TestCase):
    def profile(self, home=Path('/synthetic')):
        value=DedicatedLaneTest().proposal()
        value.update(sourceRoot=str(home/'source'), storageRoot=str(home/'rose-actions'),
                     lockPath=str(home/'mustang.lock'), workflow='.github/workflows/mustang-ota-dedicated.yml',queue='mustang-ota')
        return value

    def test_paths_are_explicit_and_other_or_unbound_hosts_refuse(self):
        profile=self.profile()
        self.assertEqual(mustang_paths.attempt(profile,'prep','123','1'),
                         Path('/synthetic/rose-actions/mustang-prep-123-1'))
        for invalid in (lane.configuration(), dict(profile,storageRoot='/elsewhere/actions'),
                        dict(profile,lockPath=profile['sourceRoot'])):
            with self.assertRaises(ValueError): mustang_paths.layout(invalid)
        for kind,run,attempt in (('../prep','123','1'),('prep','../1','1'),('prep','1','0')):
            with self.assertRaises(ValueError): mustang_paths.attempt(profile,kind,run,attempt)

    def test_compiler_keeps_pins_and_omissions_and_changes_only_three_path_fields(self):
        spec=importlib.util.spec_from_file_location('mustang_path_engine',ROOT/'scripts/mustang-build-only.py')
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        canonical=module.engine().load(ROOT/'os/build-only/mustang.json',probe=False)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            record=root/'os/build-only/mustang-lane.json'; record.parent.mkdir(parents=True)
            profile=self.profile(root/'host'); record.write_text(json.dumps(profile))
            fake=SimpleNamespace(load=lambda path,**kwargs:copy.deepcopy(canonical))
            recipe=mustang_paths.compiler_recipe(fake,profile,root)
            self.assertEqual(recipe['preparation']['productionSourceRootFrom'],
                             'os/build-only/mustang-lane.json sourceRoot')
            self.assertEqual(recipe['preparation']['lockFrom'],'os/build-only/mustang-lane.json lockPath')
            self.assertEqual(recipe['preparation']['candidateLayout']['ancestorBoundary'],str(root/'host'))
            for field in ('productionSourceRootFrom','lockFrom'):
                recipe['preparation'][field]=canonical['preparation'][field]
            recipe['preparation']['candidateLayout']=canonical['preparation']['candidateLayout']
            self.assertEqual(recipe,canonical)
            record.write_text(json.dumps(lane.configuration()))
            with self.assertRaisesRegex(ValueError,'exact committed'):
                mustang_paths.compiler_recipe(fake,profile,root)

    def test_allocator_uses_only_the_dedicated_lock_and_storage(self):
        spec=importlib.util.spec_from_file_location('mustang_path_allocator',ROOT/'scripts/mustang-allocate.py')
        allocate=importlib.util.module_from_spec(spec); spec.loader.exec_module(allocate)
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary); profile=self.profile(home); (home/'rose-actions').mkdir()
            locks=[]
            def take_lock(path):
                locks.append(path)
                return allocate.allocator.shared_lock(path)
            with patch.object(allocate,'observe',return_value=(set(),{})):
                value=allocate.reserve(str(home),'123','1',SOURCE,clock=lambda:int(EPOCH),lock=take_lock,lane=profile,consumed=[BUILD])
                with self.assertRaisesRegex(allocate.Refused,'allocation home differs'):
                    allocate.reserve('/different-home','124','1',SOURCE,lock=take_lock,lane=profile)
            self.assertEqual(locks,[profile['lockPath']])
            self.assertNotEqual(value['build'],BUILD)
            self.assertEqual(value['observed']['carriedForwardBuilds'],[BUILD])
            self.assertTrue(Path(value['workDir']).is_relative_to(home/'rose-actions'))
            self.assertTrue((home/'rose-actions/mustang-allocations'/f'{value["build"]}.json').is_file())

    def test_dedicated_workflow_orders_proofs_and_ownership_before_credentials(self):
        workflow=(ROOT/'.github/workflows/mustang-ota-dedicated.yml').read_text()
        jobs=workflow_shape.jobs(workflow)
        self.assertEqual(set(jobs),{'guard','controller','builder','publisher','cleanup'})
        self.assertIn('    runs-on: ubuntu-24.04',jobs['guard'])
        self.assertIn('python3 scripts/mustang-dedicated.py guard',jobs['guard'])
        active='\n'.join(line for line in workflow.splitlines() if not line.lstrip().startswith('#'))
        for forbidden in ('immortal-os-builder','roseos-frankel-builder','builder-reclaim','schedule:', 'workflow_run:'):
            self.assertNotIn(forbidden,active)
        self.assertIn('  group: mustang-ota',workflow)
        self.assertIn('needs: guard',jobs['controller'])
        self.assertNotIn('always()',jobs['controller'].split('steps:',1)[0])
        self.assertIn("needs.controller.result != 'skipped'",jobs['cleanup'])
        self.assertLess(jobs['builder'].index('mustang-dedicated.py collect'),jobs['builder'].index('actions-gcp-login.py login'))
        self.assertLess(jobs['builder'].index('mustang-dedicated.py baseline'),jobs['builder'].index('mustang-dedicated.py produce'))
        self.assertLess(jobs['publisher'].index('verify-staged'),jobs['publisher'].index('actions/create-github-app-token'))
        self.assertLess(jobs['cleanup'].index('cleanup-prepare'),jobs['cleanup'].index('actions-gcp-login.py login'))
        env=dict(PATH=os.environ['PATH'])
        for action in ('guard','host-check','fetch-inputs','baseline','produce','collect','sign','_execute','stage',
                       'verify-staged','publish','report','ownership-output','cleanup-prepare','require-owned'):
            result=subprocess.run([sys.executable,str(ROOT/'scripts/mustang-dedicated.py'),action],env=env,
                                  capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,4)
            self.assertIn('dedicated Mustang bindings missing',result.stderr)

class DedicatedRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.host=self.root/'host'
        self.lane=DedicatedPathsTest().profile(self.host)
        (self.host/'source').mkdir(parents=True); (self.host/'rose-actions').mkdir()
        spec=importlib.util.spec_from_file_location('mustang_dedicated_test',ROOT/'scripts/mustang-dedicated.py')
        self.driver=importlib.util.module_from_spec(spec); spec.loader.exec_module(self.driver)
        self.env=dict(GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_REF='refs/heads/main',GITHUB_SHA=SOURCE,
            GITHUB_REPOSITORY_ID='1428996648',
            GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1',
            GITHUB_WORKFLOW_REF='Immortal-Protocols/RoseOS-1/'+self.lane['workflow']+'@refs/heads/main')
        for name in ('os/pinned-manifest.xml','os/build-only/mustang.json','os/latest-beta/allocation.json','os/ota/mustang.json'):
            target=self.root/name; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes((ROOT/name).read_bytes())
        baseline=self.driver.load(self.root/'os/ota/mustang.json')['baseline']['signReceipt']
        target=self.root/baseline; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes((ROOT/baseline).read_bytes())
        archive=self.driver.load(target)['outputs']['targetFiles']
        source=dict(builder=self.lane['vm'],sourceRoot=self.lane['sourceRoot'],pristine=True,
            manifestSha256=gate.digest(self.root/'os/pinned-manifest.xml'),
            adevtoolRevision=self.driver.load(ROOT/'os/build-only/mustang.json')['pins']['vendor/adevtool'])
        source_ref=self.record('source',source)
        registration=self.record('runner',dict(builder=self.lane['vm'],workflow=self.lane['workflow'],
            repositoryId='1428996648',groupId='99',readyOnEveryStart=True,publicRepositoryAdmitted=True))
        signing=self.record('route',dict(builder=self.lane['vm'],workflow=self.lane['workflow'],
            identityVariable='GCP_SIGNER_SERVICE_ACCOUNT',providerVariable='MUSTANG_SIGNER_WORKLOAD_IDENTITY_PROVIDER',
            establishedMustangIdentityPreserved=True,approved=True))
        state=self.record('state',dict(failedRun='41884368820',unknown=False,consumedBuilds=['2026110402'],
            builder=self.lane['vm'],dataFilesystemUuid='fixture-uuid'))
        allocation=self.record('allocation',dict(sourceSha=SOURCE))
        apps=self.record('apps',dict(schema='roseos.app-inputs-record.v1',producer=dict(device='frankel',sourceSha=SOURCE,
                allocation=dict(path=allocation,sha256=gate.digest(self.root/allocation)))))
        self.cfg=dict(schema='roseos.mustang-dedicated-runtime.v1',status='bound',jobs=24,
            tools=dict(aapt=sys.executable,apksigner=sys.executable,node=sys.executable),dataFilesystemUuid='fixture-uuid',
            automaticStopHours=12,networkEvidence=self.record('network',dict(builder=self.lane['vm'],
                githubEgressVerified=True,approvedRoute='synthetic approved route')),
            sourceEvidence=source_ref,allocationState=state,appInputsRecord=apps,
            baselineTargetFiles=dict(archive,path=str(self.host/'baseline.zip')),
            runner=dict(groupName='fixture-mustang',groupId='99',lifetime='persistent',registrationEvidence=registration),
            signingRouteEvidence=signing,publication=dict(enabled=False,authorityRecord=None))

    def record(self,name,value):
        path=Path('os/fixtures')/(name+'.json'); (self.root/path).parent.mkdir(parents=True,exist_ok=True)
        (self.root/path).write_text(json.dumps(value)); return str(path)

    def admitted(self,cfg=None,publish=False,diff=None):
        return self.driver.guard(self.lane,cfg or self.cfg,self.env,publish,root=self.root,diff=diff or (lambda *args:''))

    def test_actual_evidence_guard_allows_private_and_refuses_stale_or_unbound(self):
        self.assertEqual(self.admitted()['consumedBuilds'],['2026110402'])
        for change in (dict(status='unbound'),dict(jobs=None),dict(sourceEvidence=None),dict(signingRouteEvidence=None),
                       dict(runner=dict(self.cfg['runner'],lifetime=None)),dict(dataFilesystemUuid=None),
                       dict(baselineTargetFiles=dict(self.cfg['baselineTargetFiles'],path='/retired/baseline.zip'))):
            with self.subTest(change=change):
                with self.assertRaises(ValueError): self.admitted(dict(self.cfg,**change))
        with self.assertRaisesRegex(ValueError,'omits current app source'):
            self.admitted(diff=lambda *args:'changed-app-source')
        with self.assertRaises(ValueError): self.admitted(publish=True)
        state=self.driver.load(self.root/self.cfg['allocationState']); state['unknown']=True
        (self.root/self.cfg['allocationState']).write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError,'allocation state remains unresolved'): self.admitted()

    def test_factory_baseline_requires_exclusive_explicit_bound_evidence_before_host_start(self):
        ref=self.record('factory-baseline',dict(schema='roseos.mustang-factory-baseline-input.v1',status='unbound'))
        with self.assertRaisesRegex(ValueError,'exactly one baseline'):
            self.admitted(dict(self.cfg,baselineFactoryEvidence=ref))
        with self.assertRaisesRegex(ValueError,'recovery is unbound'):
            self.admitted(dict(self.cfg,baselineFactoryEvidence=ref,baselineTargetFiles=None))

    def test_guest_identity_mount_and_storage_checks_are_read_only_and_fail_closed(self):
        executed=[]
        def process(argv,**kwargs):
            executed.append(argv); return SimpleNamespace(returncode=0,stdout='fixture-uuid\n')
        with patch.object(self.driver.shutil,'which',return_value=sys.executable):
            self.driver.host_check(self.lane,self.cfg,observe_id=lambda:self.lane['vm']['instanceId'],execute=process)
        self.assertEqual(executed[0][:2],['findmnt','--noheadings'])
        executed.clear()
        with self.assertRaisesRegex(ValueError,'not the admitted dedicated VM'):
            self.driver.host_check(self.lane,self.cfg,observe_id=lambda:'999',execute=process)
        self.assertEqual(executed,[])
        with patch.object(self.driver.os,'statvfs',return_value=SimpleNamespace(f_bavail=0,f_files=100,f_favail=10)):
            with self.assertRaisesRegex(ValueError,'no available bytes/inodes'):
                self.driver.host_check(self.lane,self.cfg,observe_id=lambda:self.lane['vm']['instanceId'],execute=process)

    def test_changed_checkout_refuses_before_any_host_or_signing_action(self):
        with patch.object(lane,'configuration',return_value=self.lane),patch.object(self.driver,'guard',return_value=self.cfg), \
                patch.object(self.driver,'load',return_value=self.cfg),patch.dict(os.environ,self.env,clear=True), \
                patch.object(self.driver.subprocess,'check_output',side_effect=[SOURCE+'\n','?? changed-source\n']):
            with self.assertRaisesRegex(ValueError,'captured clean source'): self.driver.context()

    def test_native_producer_does_not_inherit_job_or_signing_credentials(self):
        capabilities=('GH_TOKEN','GITHUB_TOKEN','ACTIONS_ID_TOKEN_REQUEST_TOKEN','ACTIONS_ID_TOKEN_REQUEST_URL',
                      'CLOUDSDK_CONFIG','GOOGLE_APPLICATION_CREDENTIALS','PIXEL_OTA_TOKEN','GITHUB_ENV','GITHUB_OUTPUT')
        def produce(*args):
            self.assertFalse(set(capabilities)&set(os.environ))
            self.assertEqual(os.environ['GITHUB_SHA'],SOURCE)
        pipeline=SimpleNamespace(run=produce,collect_reports=lambda *args,**kwargs:None)
        with patch.object(self.driver,'context',return_value=(self.lane,self.cfg)), \
                patch.object(self.driver,'host_check'),patch.object(ota,'module',return_value=pipeline), \
                patch.dict(os.environ,dict(self.env,**{key:'synthetic-capability' for key in capabilities}),clear=True), \
                patch.object(sys,'argv',['mustang-dedicated.py','produce','--dir',str(self.root/'inputs')]):
            self.assertEqual(self.driver.main(),0)

    def test_cleanup_uses_exact_owner_without_runtime_or_signing_input_guard(self):
        run=controller.invocation(self.lane,self.env)
        owner=dict(schema=controller.SCHEMA,run=run,owner=dict(vm=self.lane['vm'],startedByThisOperation=False))
        encoded=__import__('base64').b64encode(json.dumps(owner).encode()).decode()
        record=self.root/'cleanup.json'
        with patch.object(lane,'configuration',return_value=self.lane), \
                patch.object(self.driver,'context',side_effect=AssertionError('runtime guard called')), \
                patch.object(self.driver,'host_check',side_effect=AssertionError('host accessed')), \
                patch.dict(os.environ,dict(self.env,MUSTANG_OWNER_B64=encoded),clear=True), \
                patch.object(sys,'argv',['mustang-dedicated.py','cleanup-prepare','--ownership-record',str(record)]):
            self.assertEqual(self.driver.main(),0)
        self.assertFalse(controller.read_ownership(record,self.lane,run))
        self.assertFalse((self.host/'mustang.lock').exists())
        with patch.object(lane,'configuration',return_value=self.lane), \
                patch.object(self.driver,'context',side_effect=AssertionError('runtime guard called')), \
                patch.dict(os.environ,self.env,clear=True), \
                patch.object(sys,'argv',['mustang-dedicated.py','require-owned','--ownership-record',str(record)]):
            with self.assertRaisesRegex(ValueError,'already running or owned elsewhere'): self.driver.main()

    def test_immutable_artifact_download_binds_metadata_and_zip_digest_without_retry(self):
        archive=self.root/'fixture.zip'
        contents={'signed-apps/app.apk':b'fixture bytes','app-inputs-export.json':b'{}','SHA256SUMS':b'fixture sums'}
        with zipfile.ZipFile(archive,'w') as zipped:
            for name,value in contents.items(): zipped.writestr(name,value)
        artifact=dict(id=9,name='fixture-artifact',digest='sha256:'+gate.digest(archive))
        observed_artifact=copy.deepcopy(artifact)
        rec=dict(export=dict(run='222',artifact=artifact),files=[dict(path='signed-apps/app.apk',sizeBytes=13)])
        path=self.root/'record.json'; path.write_text(json.dumps(rec)); calls=[]
        def process(argv,**kwargs):
            calls.append(argv)
            if argv[-1].endswith('/zip'):
                kwargs['stdout'].write(archive.read_bytes()); return SimpleNamespace(returncode=0)
            return SimpleNamespace(returncode=0,stdout=json.dumps(dict(observed_artifact,expired=False,workflow_run=dict(id=222))))
        out=self.driver.fetch_inputs(path,self.root/'download',execute=process)
        self.assertEqual((out/'signed-apps/app.apk').read_bytes(),b'fixture bytes')
        self.assertEqual(len(calls),2)
        rec['export']['artifact']['digest']='sha256:'+'0'*64; path.write_text(json.dumps(rec))
        with self.assertRaisesRegex(ValueError,'metadata differs'):
            self.driver.fetch_inputs(path,self.root/'foreign',execute=process)
        self.assertFalse((self.root/'foreign').exists())
        rec['export']['artifact']['digest']=observed_artifact['digest']; path.write_text(json.dumps(rec))
        def corrupt(argv,**kwargs):
            if argv[-1].endswith('/zip'):
                kwargs['stdout'].write(b'changed download'); return SimpleNamespace(returncode=0)
            return process(argv,**kwargs)
        with self.assertRaisesRegex(ValueError,'download/digest failed'):
            self.driver.fetch_inputs(path,self.root/'corrupt',execute=corrupt)
        self.assertEqual((self.root/'corrupt/download.zip').read_bytes(),b'changed download')

    def test_internal_signing_executor_rechecks_scope_and_proof_before_key_access(self):
        directory=self.host/'rose-actions/mustang-sign-123-1'; (directory/'release').mkdir(parents=True)
        config=directory/'sign-config.json'; unsigned=directory/'release/unsigned-target_files.zip'
        unsigned.write_bytes(b'synthetic unsigned bytes')
        source=self.host/'rose-actions/mustang-prep-123-1/source'
        sign=dict(project=adapter.PROJECT,keysSecret=adapter.SECRETS['keys'],passphraseSecret=adapter.SECRETS['passphrase'],
                  secretVersion='1',passphraseSecretVersion='1',otatools='tools.zip')
        outputs=dict(otatools=dict(path=str(source/f'out-mustang-build-{BUILD}/tools.zip')),
                     targetFiles=dict(sha256=gate.digest(unsigned)))
        calls=[]
        fake=SimpleNamespace(PROJECT=adapter.PROJECT,SECRETS=adapter.SECRETS,digest=adapter.digest,
            compile_binding=lambda *args,**kwargs:(calls.append('proof') or ({},source,BUILD,EPOCH,outputs)),
            execute_sign=lambda *args:calls.append('keys'))
        env=dict(self.env,ROSE_RELEASE_DIR=str(directory/'release'),BUILD_NUMBER=BUILD,BUILD_DATETIME=EPOCH)
        with patch.object(self.driver,'context',return_value=(self.lane,self.cfg)),patch.object(self.driver,'host_check'), \
                patch.object(ota,'module',return_value=fake),patch.dict(os.environ,env,clear=True), \
                patch.object(sys,'argv',['mustang-dedicated.py','_execute','--config',str(config)]), \
                patch.object(Path,'cwd',return_value=source):
            for change in (dict(keysSecret='FOREIGN_CONTAINER'),dict(secretVersion='2'),dict(project='foreign-project')):
                config.write_text(json.dumps(dict(target=dict(device='mustang'),host=dict(sign=dict(sign,**change)))))
                with self.assertRaisesRegex(ValueError,'version-1 scope'): self.driver.main()
            self.assertEqual(calls,[])
            config.write_text(json.dumps(dict(target=dict(device='mustang'),host=dict(sign=sign))))
            self.assertEqual(self.driver.main(),0)
            self.assertEqual(calls,['proof','keys'])
            unsigned.write_bytes(b'changed copy')
            with self.assertRaisesRegex(ValueError,'unsigned proof'): self.driver.main()
            self.assertEqual(calls,['proof','keys','proof'])

    def test_dedicated_producer_uses_pristine_own_base_and_never_seeds_from_frankel(self):
        pipeline=ota.module('dedicated_producer_fixture','mustang-pipeline.py')
        allocate=ota.module('dedicated_allocator_fixture','mustang-allocate.py')
        stages=[]; commands=[]
        recipe=self.driver.load(ROOT/'os/build-only/mustang.json')
        class Engine:
            SOURCE_PROVENANCE={'projectRevisionsVerified':False,'pinnedManifestVerified':False}
            symlink_in=staticmethod(lambda path:False)
            def operator(inner,host,source,jobs,lock,*args):
                self.assertEqual(lock,self.lane['lockPath'])
                self.assertEqual(host,self.lane['vm']['instance'])
                return dict(source=source,jobs=jobs,lock=lock)
            def run(inner,recipe,stage,op,receipts):
                stages.append(stage); Path(receipts).mkdir(parents=True)
                Path(op['source']).mkdir(parents=True,exist_ok=True)
                value=dict(ok=True,returncode=0,stopped=None,
                           selectionRecord=dict(path='synthetic-selection',sha256='f'*64))
                (Path(receipts)/'completed.json').write_text(json.dumps(value))
                return 0
        engine=Engine()
        def assemble(apps,policy,build,source,tools,out):
            Path(out).mkdir(); (Path(out)/'background-policy.json').write_text('{}')
            return dict(apps=[['dev.pixel.voice',1]])
        def admit(bundle,route,out,tools):
            Path(out).mkdir(); (Path(out)/'local-inputs.json').write_text('{}')
        def dependency(name,file):
            if file=='mustang-build-only.py': return SimpleNamespace(engine=lambda:engine)
            if file=='app-inputs-verify.py': return SimpleNamespace(verify=lambda *args:dict(toolsRechecked=True))
            if file=='mustang-allocate.py': return allocate
            if file=='mustang-candidate-bundle.py': return SimpleNamespace(assemble=assemble)
            raise AssertionError('unexpected dependency/other-device seed: '+file)
        def call(engine,argv,*args):
            commands.append(argv); return dict(returncode=0,stopped=None),''
        cfg=dict(self.cfg,consumedBuilds=[BUILD])
        apps=self.driver.load(self.root/cfg['appInputsRecord']); apps['apps']=[dict(package='dev.pixel.voice',versionCode=1)]
        (self.root/cfg['appInputsRecord']).write_text(json.dumps(apps))
        inputs=self.root/'app-inputs'; inputs.mkdir()
        with patch.object(pipeline,'ROOT',self.root),patch.object(pipeline,'head',return_value=SOURCE), \
                patch.object(pipeline,'module',side_effect=dependency),patch.object(pipeline,'call',side_effect=call), \
                patch.object(pipeline,'omissions',return_value=['unrelated sprint omissions']), \
                patch.object(pipeline,'print',create=True), \
                patch.object(pipeline.mustang_paths,'compiler_recipe',return_value=recipe), \
                patch.object(pipeline.integration,'admit',side_effect=admit), \
                patch.object(pipeline.integration,'render',return_value=({},['synthetic-integrate'])), \
                patch.object(allocate,'observe',return_value=(set(),{})):
            self.assertEqual(pipeline.run('123','1',self.lane,cfg,inputs),0)
        result=self.driver.load(self.host/'rose-actions/mustang-prep-123-1/candidate-receipt.json')
        self.assertEqual(stages,['source','git-meta','download','vendor','lunch','compile'])
        self.assertEqual(result['builder'],self.lane['vm'])
        self.assertEqual(result['workflow'],self.lane['workflow'])
        self.assertFalse(result['stages']['seed']['priorReconcileSeed'])
        self.assertEqual(result['allocation']['observed']['carriedForwardBuilds'],[BUILD])
        self.assertTrue(result['unsignedCompileComplete'])
        self.assertFalse(result['signed'])
        self.assertIn('no bootstrap',result['nextStage'])
        self.assertNotIn('immortal-os-builder',str(commands))


class ProofTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root/'target-files.zip'

    def proof(self, **changes):
        fixtures.target_files(self.path, **changes)
        return gate.inspect(self.path, 'unsigned', bound(), descriptor(self.path))

    def test_pass_records_exact_archive_checker_spec_and_apk_hashes(self):
        proof = self.proof()
        self.assertEqual(proof['status'], 'passed')
        self.assertEqual(proof['targetFiles'], gate.observed_archive(self.path))
        self.assertEqual(proof['checker']['checkerSha256'], gate.digest(ROOT/gate.CHECKER_PATH))
        self.assertEqual(set(proof['evidence']['apkSha256']), {fixtures.L3_APK, fixtures.SYSUI_APK})
        gate.require(proof, 'unsigned', bound(), descriptor(self.path))

    def test_pre548_switches_and_malformed_dex_fail_with_evidence(self):
        cases = [
            dict(launcher3=dex_fixture.apk([dex_fixture.dex(fixtures.LAUNCHER3)])),
            dict(props='persist.wm.debug.pixel_agent_tray=1\n'),
            dict(props=fixtures.PROPS+'persist.wm.debug.rose_edge=false\n'),
            dict(launcher3=dex_fixture.apk([b'not dex'])),
            dict(launcher3=False),
        ]
        for changes in cases:
            with self.subTest(changes=tuple(changes)):
                proof = self.proof(**changes)
                self.assertEqual(proof['status'], 'failed')
                self.assertTrue(proof['failures'])
                with self.assertRaises(ValueError):
                    gate.require(proof, 'unsigned', bound(), descriptor(self.path))

    def test_r8_methods_can_disappear_and_shell_literals_still_pass(self):
        launcher = dex_fixture.apk([dex_fixture.dex([(fixtures.GESTURE, []), (fixtures.SWIPE, [])],
                                                   strings=[fixtures.DIAG])])
        shell = dex_fixture.apk([dex_fixture.dex([('Lrenamed/a;', []), ('Lrenamed/b;', [])],
                                               strings=[fixtures.DESCRIPTOR, fixtures.MIC_TAG])])
        proof = self.proof(launcher3=launcher, systemui=shell)
        gate.require(proof, 'unsigned', bound(), descriptor(self.path))
        self.assertEqual([c['by'] for c in proof['evidence']['classes']], ['class','class','literal','literal'])

    def test_missing_stale_or_boolean_only_proof_is_refused(self):
        original = self.proof()
        changes = [None, {'status':'passed'}, dict(original, checker={}),
                   dict(original, binding=dict(bound(),runId='999')),
                   dict(original, evidence={}), dict(original, stage='signed'),
                   dict(original, targetFiles=dict(original['targetFiles'],sha256='0'*64))]
        for proof in changes:
            with self.subTest(proof=proof):
                with self.assertRaises(ValueError):
                    gate.require(proof, 'unsigned', bound(), descriptor(self.path))
        changed = copy.deepcopy(original)
        changed['evidence']['literals'] = []
        with self.assertRaises(ValueError):
            gate.require(changed,'unsigned',bound(),descriptor(self.path))

    def test_archive_change_and_unreadable_archive_are_not_passes(self):
        proof = self.proof()
        self.path.write_bytes(b'changed archive')
        with self.assertRaises(ValueError):
            gate.require(proof,'unsigned',bound(),descriptor(self.path))
        failed = gate.inspect(self.path,'unsigned',bound(),descriptor(self.path))
        self.assertEqual((failed['status'],failed['error']),('failed','BadZipFile'))

    def test_shared_configuration_without_independent_marker_is_not_admitted(self):
        real = gate.json.loads
        def without_marker(text):
            value = real(text)
            value['roseEdge'].pop('literals')
            return value
        with patch.object(gate.json,'loads',side_effect=without_marker):
            proof = self.proof()
        self.assertEqual((proof['status'],proof['error']),('failed','ValueError'))

    def test_forged_pass_rows_do_not_substitute_for_full_evidence(self):
        original=self.proof()
        for section,field,value in (('props','files',[]),('classes','where',fixtures.SYSUI_APK+'!classes.dex'),
                                   ('classes','by','literal'),('literals','pass',False)):
            proof=copy.deepcopy(original)
            proof['evidence'][section][0][field]=value
            with self.subTest(section=section,field=field):
                with self.assertRaises(ValueError): gate.require(proof,'unsigned',bound(),descriptor(self.path))


class AdapterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.actions = self.root/'rose-actions'; self.actions.mkdir()
        self.source = self.actions/'mustang-prep-222-1/source'; self.source.mkdir(parents=True)
        (self.source/'script').mkdir()
        (self.source/'script/generate-release.sh').write_text('synthetic source fixture, never executed')
        self.out = self.source/f'out-mustang-build-{BUILD}'; self.out.mkdir()
        self.tf = fixtures.target_files(self.out/'mustang-target_files.zip')
        self.tools = self.out/'otatools.zip'; self.tools.write_bytes(b'synthetic otatools')
        self.artifacts = dict(targetFiles=descriptor(self.tf), otatools=descriptor(self.tools))
        for role in ('vendorBootImage','vendorKernelBootImage'):
            path = self.out/(role+'.img'); path.write_bytes(b'synthetic image')
            self.artifacts[role] = descriptor(path)
        self.allocation = dict(build=BUILD, device='mustang', source=SOURCE, runId='222', runAttempt='1',
                               reservedAt='2026-11-05T00:00:00Z')
        (self.source.parent/'allocation.json').write_text(json.dumps(self.allocation))
        self.outputs = dict(verdict='complete', actualBuildIdentity=dict(device='mustang',build=BUILD,timestamp=EPOCH),
                            artifacts=self.artifacts)
        compile_result = dict(device='mustang',stage='compile',returncode=0,stopped=None,ok=True,
                              buildIdentity=dict(buildNumber=BUILD,buildDatetime=EPOCH),outputs=self.outputs)
        self.candidate = dict(schema='roseos.mustang-candidate-run.v1',device='mustang',source=SOURCE,
                              runId='222',runAttempt='1',allocation=self.allocation,root=str(self.source.parent),
                              unsignedCompileComplete=True,omittedChecks=['other sprint omissions remain'],
                              stages=dict(integrate=dict(source=SOURCE,result=dict(returncode=0,stopped=None)),
                                          compile=dict(result=compile_result)))
        downloaded = self.root/'downloaded'; downloaded.mkdir()
        self.receipt = downloaded/'candidate-receipt.json'
        self.bootstrap = downloaded/'synthetic-public-bootstrap.json'
        self.bootstrap.write_text('{"fixture":"no secret or key material"}')
        self.receipt.write_text(json.dumps(self.candidate))
        (downloaded/'compile-completed.json').write_text(json.dumps(compile_result))
        self.calls = []
        self.signed_changes = {}
        self.hp = adapter.module('mustang_test_real_factory','host-factory-package.py')
        local_lock = self.hp.shared_lock
        self.hp.shared_lock = lambda unused:local_lock(self.root/'builder.lock')
        self.hp.unzip_otatools = self.unpack
        self.hp.run_script = self.factory
        self.sb = SimpleNamespace(load_profiles=lambda:dict(profiles={adapter.PROFILE:dict(
            status='resource-bound',missing=[],project=adapter.PROJECT,secrets=adapter.SECRETS)}))
        for p in (patch.object(adapter,'BASE',self.actions),patch.object(adapter.mustang_collection,'BASE',self.actions),
                  patch.object(adapter,'LOCK',str(self.root/'builder.lock')),patch.object(adapter,'source_sha',return_value=RECIPE),
                  patch.object(adapter,'module',side_effect=self.modules),
                  patch.object(adapter,'print',lambda *a,**kw:None,create=True),
                  patch.object(adapter,'bootstrap_binding',return_value=(
                      dict(publicIdentity={'synthetic':'public identity'}),dict(keys='1',passphrase='1'))),
                  patch.dict(os.environ,dict(GITHUB_RUN_ID='333',GITHUB_RUN_ATTEMPT='1',
                      EXPECTED_COMPILE_RUN='222',EXPECTED_COMPILE_SOURCE=SOURCE,EXPECTED_COMPILE_RECOVERY='false',
                      GCP_SIGNER_SERVICE_ACCOUNT=adapter.SIGNER),clear=True)):
            p.start(); self.addCleanup(p.stop)

    def modules(self, name, filename):
        if filename=='host-factory-package.py': return self.hp
        if filename=='mustang-build-only.py':
            return SimpleNamespace(collect_outputs=lambda *a:copy.deepcopy(self.outputs),
                otatools_outputs=lambda *a:[self.tools],engine=lambda:SimpleNamespace(supervise=self.synthetic_sign))
        if filename=='signing-bootstrap.py': return self.sb
        if filename=='host-sign.py':
            return SimpleNamespace(derive_factory_only_script=lambda *a:'synthetic factory stand-in')
        raise AssertionError('unexpected adapter dependency: '+filename)

    def unpack(self, archive, directory):
        binary = directory/'bin'; binary.mkdir(exist_ok=True)
        (binary/'fastboot').write_bytes(b'synthetic fastboot')
        (binary/'delta_generator').write_bytes(b'synthetic delta generator')

    def factory(self, script, work, log):
        self.calls.append('factory')
        with zipfile.ZipFile(work/f'mustang-install-{BUILD}.zip','w') as archive:
            archive.writestr('flash-all.sh','synthetic')
            archive.writestr('script.txt','synthetic')
        return 'ok',0

    def synthetic_sign(self, argv, cwd, env, *bounds):
        self.calls.append('signer stand-in')
        release = Path(env['ROSE_RELEASE_DIR']); (release/'public').mkdir()
        fixtures.target_files(release/'signed-target_files.zip',**self.signed_changes)
        (release/'public'/f'mustang-ota_update-{BUILD}.zip').write_bytes(b'synthetic OTA, not installable')
        (release/'public'/'avb_pkmd.bin').write_bytes(b'synthetic public AVB')
        return dict(returncode=0,stopped=None)

    def collect(self):
        return adapter.collect(str(self.receipt),'333','1')

    def sign(self):
        return adapter.sign(str(self.receipt),str(self.bootstrap),'333','1')

    def test_unsigned_failure_keeps_failed_receipt_and_never_reaches_signer(self):
        fixtures.target_files(self.tf,launcher3=dex_fixture.apk([dex_fixture.dex(fixtures.LAUNCHER3)]))
        self.artifacts['targetFiles'] = descriptor(self.tf)
        # Keep the admitted compile receipt internally consistent with these synthetic bytes.
        self.candidate['stages']['compile']['result']['outputs'] = self.outputs
        self.receipt.write_text(json.dumps(self.candidate))
        (self.receipt.parent/'compile-completed.json').write_text(json.dumps(self.candidate['stages']['compile']['result']))
        with self.assertRaises(ValueError): self.collect()
        failed = adapter.read(adapter.run_root('collect','333','1')/'compile-collection.json')
        self.assertEqual((failed['status'],failed['roseEdge']['status']),('failed','failed'))
        self.assertTrue(self.tf.is_file())
        with self.assertRaises(ValueError): self.sign()
        self.assertEqual(self.calls,[])

    def test_signed_failure_keeps_outputs_and_blocks_factory_and_public_stage(self):
        self.collect()
        self.signed_changes = dict(launcher3=dex_fixture.apk([dex_fixture.dex(fixtures.LAUNCHER3)]))
        with self.assertRaises(ValueError): self.sign()
        signed = adapter.read(adapter.run_root('sign','333','1')/'mustang-sign-receipt.json')
        self.assertEqual((signed['status'],signed['roseEdge']['signed']['status']),('failed','failed'))
        self.assertTrue(Path(signed['outputs']['targetFiles']['path']).is_file())
        with self.assertRaises(ValueError): adapter.package('333','1','333','1')
        with patch.object(ota,'module',return_value=adapter):
            with self.assertRaises(ValueError): ota.stage('333','1','unused',self.root/'public-stage')
        self.assertEqual(self.calls,['signer stand-in'])

    def test_changed_unsigned_bytes_or_collection_proof_stop_before_signer(self):
        self.collect()
        self.tf.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'compiled output identity changed'): self.sign()
        self.assertEqual(self.calls,[])

    def test_missing_unsigned_proof_stops_before_signer(self):
        self.collect()
        path=adapter.run_root('collect','333','1')/'compile-collection.json'
        collection=adapter.read(path); collection.pop('roseEdge'); path.write_text(json.dumps(collection))
        with self.assertRaises(ValueError): self.sign()
        self.assertEqual(self.calls,[])

    def test_stale_unsigned_proof_stops_before_signer(self):
        self.collect()
        path=adapter.run_root('collect','333','1')/'compile-collection.json'
        collection=adapter.read(path)
        collection['roseEdge']['binding']['runId']='999'
        path.write_text(json.dumps(collection))
        with self.assertRaises(ValueError): self.sign()
        self.assertEqual(self.calls,[])

    def test_private_factory_requires_both_proofs_and_retains_omissions(self):
        self.collect(); signed=self.sign()
        result=adapter.package('333','1','333','1')
        self.assertEqual(result['roseEdge'],signed['roseEdge'])
        self.assertEqual(result['omittedChecks'],['other sprint omissions remain'])
        self.assertFalse(signed['signingResultVerified'])
        self.assertEqual(self.calls,['signer stand-in','factory'])

    def test_explicit_dedicated_collection_sign_and_factory_never_use_legacy_paths_or_lock(self):
        profile=DedicatedPathsTest().profile(self.root)
        self.candidate.update(builder=dict(profile['vm']),workflow=profile['workflow'])
        self.receipt.write_text(json.dumps(self.candidate))
        locks=[]; previous=self.hp.shared_lock
        def take_lock(path):
            locks.append(str(path))
            return previous(path)
        self.hp.shared_lock=take_lock
        with patch.object(adapter,'BASE',self.root/'retired'), \
                patch.object(adapter.mustang_collection,'BASE',self.root/'retired'), \
                patch.object(adapter,'LOCK',str(self.root/'retired.lock')):
            adapter.collect(str(self.receipt),'333','1',lane=profile)
            signed=adapter.sign(str(self.receipt),str(self.bootstrap),'333','1',lane=profile)
            result=adapter.package('333','1','333','1',lane=profile)
        self.assertEqual(locks,[profile['lockPath']]*3)
        self.assertEqual(result['roseEdge'],signed['roseEdge'])
        self.assertEqual(self.calls,['signer stand-in','factory'])
        self.assertFalse((self.root/'retired').exists())
        self.assertFalse((self.root/'retired.lock').exists())

    def test_dedicated_collection_refuses_old_or_foreign_builder_receipts(self):
        profile=DedicatedPathsTest().profile(self.root)
        with self.assertRaisesRegex(ValueError,'exact dedicated Mustang VM/workflow'):
            adapter.collect(str(self.receipt),'333','1',lane=profile)
        self.candidate.update(builder=dict(profile['vm'],instanceId='999'),workflow=profile['workflow'])
        self.receipt.write_text(json.dumps(self.candidate))
        with self.assertRaisesRegex(ValueError,'exact dedicated Mustang VM/workflow'):
            adapter.collect(str(self.receipt),'333','1',lane=profile)
        self.assertEqual(self.calls,[])

    def test_unbound_dedicated_collection_fails_before_admission_or_output(self):
        with self.assertRaisesRegex(ValueError,'dedicated Mustang bindings missing'):
            adapter.collect(str(self.receipt),'333','1',lane=lane.configuration())
        self.assertEqual(self.calls,[])
        self.assertFalse((self.actions/'mustang-collect-333-1').exists())

    def test_private_factory_cannot_reuse_pre_gate_success_receipt(self):
        self.collect(); self.sign()
        path=adapter.run_root('sign','333','1')/'mustang-sign-receipt.json'
        signed=adapter.read(path); signed.pop('roseEdge'); path.write_text(json.dumps(signed))
        with self.assertRaises(ValueError): adapter.package('333','1','333','1')
        self.assertEqual(self.calls,['signer stand-in'])

    def test_changed_signed_archive_blocks_private_factory_and_ota(self):
        self.collect(); signed=self.sign()
        path=Path(signed['outputs']['targetFiles']['path'])
        path.write_bytes(b'changed signed output')
        with self.assertRaises(ValueError): adapter.package('333','1','333','1')
        with self.assertRaises(ValueError): self.stage()
        self.assertEqual(self.calls,['signer stand-in'])
        self.assertTrue(path.is_file())

    def test_checker_interruption_preserves_failed_collection_and_input(self):
        with patch.object(gate.edge_artifact,'check',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.collect()
        failed=adapter.read(adapter.run_root('collect','333','1')/'compile-collection.json')
        self.assertEqual(failed['status'],'failed')
        self.assertIn('KeyboardInterrupt',failed['error'])
        self.assertTrue(self.tf.is_file())
        self.assertEqual(self.calls,[])

    def test_replaced_collection_proof_blocks_ota_even_with_valid_signed_proof(self):
        self.collect(); self.sign()
        path=adapter.run_root('collect','333','1')/'compile-collection.json'
        row=adapter.read(path); row['roseEdge']['binding']['runId']='999'
        path.write_text(json.dumps(row))
        with self.assertRaisesRegex(ValueError,'collection proof differs'): self.stage()

    def stage(self,lane=None,publication=True):
        context={'lane':lane} if lane is not None else {}
        signed=adapter.read(adapter.run_root('sign','333','1',**context)/'mustang-sign-receipt.json')
        public=Path(signed['publicAvbPath']).parent
        (public/'releasekey.x509.pem').write_bytes(b'synthetic public certificate')
        checker=self.source/'build/make/tools/releasetools/check_ota_package_signature.py'
        checker.parent.mkdir(parents=True); checker.write_text('synthetic verifier stand-in')
        pin='c'*64
        pins=dict(otaCertificateSha256=pin,avbPublicKeySha256=gate.digest(public/'avb_pkmd.bin'))
        baseline=self.root/'baseline.json'
        baseline.write_text(json.dumps(dict(packagedUpdaterVerified=True,build=ota.config()['baseline']['installedBuild'],
                                            trustedOtaCertificateSha256=[pin])))
        def module(name,filename):
            if filename=='mustang-sign-package.py': return adapter
            if filename=='host-factory-package.py': return self.hp
            raise AssertionError(filename)
        cfg=ota.config()
        if not publication: cfg=dict(cfg,publicationEnabled=False)
        with patch.object(ota,'config',return_value=cfg),patch.object(ota,'module',side_effect=module),patch.object(ota,'identities',return_value=pins), \
                patch.object(ota.host_secrets,'certificate_sha256',return_value=pin), \
                patch.object(ota,'observe_target',return_value={'packagedUpdaterVerified':True}), \
                patch.object(ota,'ota_metadata',return_value={'pre-device':'mustang'}), \
                patch.object(ota,'fingerprint',return_value='d'*64), \
                patch.object(ota.subprocess,'run',return_value=SimpleNamespace(returncode=0)):
            result=ota.stage('333','1',str(baseline),self.root/'public-stage',**context,
                             **({'tools':{},'publication':False} if not publication else {}))
            ota.verify_staged(self.root/'public-stage',cfg,publication=publication)
            if not publication:
                with self.assertRaisesRegex(ValueError,'public OTA activation remains pending'):
                    ota.publish(SimpleNamespace(),self.root/'public-stage',cfg,self.root/'must-not-publish')
        return result

    def test_dedicated_private_ota_staging_preserves_both_proofs_without_public_activation(self):
        profile=DedicatedPathsTest().profile(self.root)
        self.candidate.update(builder=dict(profile['vm']),workflow=profile['workflow'])
        self.receipt.write_text(json.dumps(self.candidate))
        adapter.collect(str(self.receipt),'333','1',lane=profile)
        signed=adapter.sign(str(self.receipt),str(self.bootstrap),'333','1',lane=profile)
        with patch.object(adapter,'BASE',self.root/'retired'),patch.object(adapter,'LOCK',str(self.root/'retired.lock')):
            result=self.stage(lane=profile,publication=False)
        self.assertEqual(result['builder'],profile['vm'])
        self.assertEqual(result['roseEdge'],signed['roseEdge'])
        self.assertFalse((self.root/'must-not-publish').exists())

    def test_public_stage_preserves_and_requires_exact_two_proofs(self):
        self.collect(); signed=self.sign()
        result=self.stage()
        self.assertEqual(result['roseEdge'],signed['roseEdge'])
        self.assertTrue(result['checks']['roseEdge'])
        self.assertEqual(set(p.name for p in (self.root/'public-stage').iterdir()),
                         {f'mustang-ota_update-{BUILD}.zip','mustang-stable','mustang-ota-receipt.json','SHA256SUMS'})

    def test_missing_or_stale_signed_proof_stops_stage_before_outputs(self):
        self.collect(); self.sign()
        path=adapter.run_root('sign','333','1')/'mustang-sign-receipt.json'
        original=adapter.read(path)
        for alter in ('missing','stale','archive'):
            signed=copy.deepcopy(original)
            if alter=='missing': signed['roseEdge'].pop('signed')
            elif alter=='stale': signed['roseEdge']['signed']['checker']['specSha256']='0'*64
            else: signed['edgeTargetFiles']['signed']['sha256']='0'*64
            path.write_text(json.dumps(signed))
            with patch.object(ota,'module',return_value=adapter):
                with self.assertRaises(ValueError): ota.stage('333','1','unused',self.root/'public-stage')
            self.assertFalse((self.root/'public-stage').exists())


class NumberingCompatibilityTest(unittest.TestCase):
    """Source/protocol evidence only. These tests never reserve a build number."""
    fixtures = ROOT/'os/tests/fixtures/mustang-numbering'
    synthetic_ids = ('9999123200', '9999999999')

    def test_mustang_board_include_chain_and_symbolic_avb_default(self):
        import hashlib
        import shutil
        import xml.etree.ElementTree as ET
        directory=self.fixtures/'avb'
        audit=json.loads((directory/'sources.json').read_text())
        self.assertFalse(audit['realImageEvidence'])
        pins={p.attrib['name']:p.attrib['revision']
              for p in ET.parse(ROOT/'os/pinned-manifest.xml').iter('project')}
        adevtool=json.loads((ROOT/'os/build-only/mustang.json').read_text())['pins']['vendor/adevtool']
        sources={}
        for row in audit['sources']:
            data=(directory/row['file']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(),row['sha256'])
            self.assertEqual(hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest(),row['gitBlob'])
            self.assertEqual(row['revision'],adevtool if row['repository']=='GrapheneOS/adevtool' else pins['platform_build'])
            prefix='vendor/adevtool/' if row['repository']=='GrapheneOS/adevtool' else 'build/make/'
            sources[prefix+row['path']]=data.decode()
        queue=['vendor/adevtool/vendor-skels/google_devices/mustang/BoardConfig.mk'];seen=set()
        while queue:
            path=queue.pop()
            if path in seen: continue
            seen.add(path)
            for line in sources[path].splitlines():
                if not line.startswith('include '): continue
                included=line.removeprefix('include ').strip()
                # Flag declarations are outside this board-include/default audit.
                if included=='build/make/target/product/build_variables.mk': continue
                self.assertIn(included,sources)
                queue.append(included)
        self.assertEqual(seen,set(sources))
        rule=[line for text in sources.values() for line in text.splitlines()
              if line.startswith('BOARD_AVB_ROLLBACK_INDEX :=')]
        self.assertEqual(rule,['BOARD_AVB_ROLLBACK_INDEX := $(PLATFORM_SECURITY_PATCH_TIMESTAMP)'])
        make=shutil.which('make')
        if make is None: self.skipTest('Make unavailable; symbolic rollback-default probe unrun')
        with tempfile.TemporaryDirectory() as tmp:
            makefile=Path(tmp)/'probe.mk'
            makefile.write_text(rule[0]+"\nprobe:\n\t@printf '%s\\n' '$(BOARD_AVB_ROLLBACK_INDEX)'\n")
            for build,stamp in [(self.synthetic_ids[0],'4102444800'),(self.synthetic_ids[1],'4102444800'),
                                (self.synthetic_ids[1],'4102444801')]:
                result=subprocess.run([make,'--no-print-directory','-s','-f',str(makefile),'probe',
                    'BUILD_NUMBER='+build,'BUILD_DATETIME=1','PLATFORM_SECURITY_PATCH_TIMESTAMP='+stamp],
                    capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout,stamp+'\n')

    def test_frozen_upstream_excerpts_match_the_manifest_pins(self):
        import hashlib
        import xml.etree.ElementTree as ET
        audit=json.loads((self.fixtures/'sources.json').read_text())
        projects={p.attrib['name']:p.attrib['revision']
                  for p in ET.parse(ROOT/'os/pinned-manifest.xml').iter('project')}
        self.assertFalse(audit['runtimeProof'])
        for source in audit['sources']:
            with self.subTest(source=source['key']):
                self.assertEqual(source['revision'],projects[source['name']])
                self.assertRegex(source['gitBlob'],r'^[0-9a-f]{40}$')
                self.assertRegex(source['sourceSha256'],r'^[0-9a-f]{64}$')
                for excerpt in source['excerpts']:
                    data=(self.fixtures/excerpt['file']).read_bytes()
                    self.assertEqual(hashlib.sha256(data).hexdigest(),excerpt['sha256'])
                    self.assertEqual(len(data.splitlines()),excerpt['lastLine']-excerpt['firstLine']+1)
        failed=audit['failedRunSource']
        self.assertFalse(failed['allocationKnown'])
        self.assertFalse(failed['runtimeExecutionAttested'])
        self.assertTrue(failed['reservationMayPrecedeAttemptReceipt'])
        for excerpt in failed['excerpts']:
            data=(self.fixtures/excerpt['file']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(),excerpt['sha256'])
            self.assertEqual(len(data.splitlines()),excerpt['lastLine']-excerpt['firstLine']+1)

    def test_captured_legacy_generator_ceiling_without_any_clock_bound(self):
        import ast
        import datetime
        import hashlib
        import inspect
        alloc=adapter.module('mustang_numbering_legacy_test','mustang-allocate.py')
        audit=json.loads((self.fixtures/'sources.json').read_text())['failedRunSource']['generator']
        frozen=(self.fixtures/audit['file']).read_text()
        self.assertEqual(hashlib.sha256(frozen.encode()).hexdigest(),audit['sha256'])
        # The reviewed old function is still the function called by today's date allocator.
        self.assertEqual(ast.dump(ast.parse(frozen)),ast.dump(ast.parse(inspect.getsource(alloc.allocator.next_build))))
        self.assertEqual(alloc.DAILY_LIMIT,audit['dailyLimit'])
        class Refused(ValueError): pass
        scope={'Refused':Refused}
        exec(compile(frozen,str(self.fixtures/audit['file']),'exec'),scope)
        maximum_day=datetime.datetime(datetime.MAXYEAR,12,31,tzinfo=datetime.timezone.utc)
        consumed=set()
        for _ in range(99):
            consumed.add(scope['next_build'](consumed,maximum_day,99))
        self.assertEqual(max(consumed),audit['maximum'])
        self.assertEqual(10**10-1-int(audit['maximum']),876800)
        with self.assertRaisesRegex(Refused,'sequence_exhausted'):
            scope['next_build'](consumed,maximum_day,99)
        # A lone ID substitution is unsafe: today's allocator still refuses the new namespace.
        with self.assertRaisesRegex(alloc.Refused,'clock_behind'):
            alloc.allocator.next_build(set(self.synthetic_ids),datetime.datetime(2026,1,1),99)

    def test_actual_releasetools_conversion_and_channel_script(self):
        import ast
        source=(self.fixtures/'otaTools-313-354.excerpt').read_text()
        self.assertEqual([n.name for n in ast.parse(source).body],['BuildLegacyOtaMetadata'])
        scope={'ota_metadata_pb2':SimpleNamespace(OtaMetadata=SimpleNamespace(AB=1,BLOCK=2))}
        exec(compile(source,'pinned-ota_utils.py','exec'),scope)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for build in self.synthetic_ids:
                post=SimpleNamespace(build=[f'google/mustang/mustang:17/A/{build}:user/release-keys'],
                    build_incremental=build,sdk_level='37',security_patch_level='2026-10-05',timestamp=4102444801)
                pre=SimpleNamespace(build=[],device=['mustang'])
                proto=SimpleNamespace(type=1,wipe=False,downgrade=False,required_cache=0,
                    postcondition=post,precondition=pre,spl_downgrade=False,property_files={})
                metadata=scope['BuildLegacyOtaMetadata'](proto)
                self.assertEqual(metadata['post-build-incremental'],build)
                archive=root/(build+'.zip')
                with zipfile.ZipFile(archive,'w') as z:
                    z.writestr('META-INF/com/android/metadata',''.join(f'{k}={v}\n' for k,v in metadata.items()))
                result=subprocess.run([sys.executable,str(self.fixtures/'generateMetadata-1-18.excerpt'),str(archive)],
                                      capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                for channel in ('stable','beta','alpha','testing'):
                    self.assertEqual((root/('mustang-'+channel)).read_text(),f'{build} 4102444801 mustang {channel}\n')
                self.assertEqual(ota.ota_metadata(archive,build,'4102444801',
                    dict(build='2026110302',timestamp='1793742435')),metadata)
                # Identity magnitude does not bypass full-update timestamp ordering.
                with self.assertRaisesRegex(ValueError,'newer'):
                    ota.ota_metadata(archive,build,'4102444801',dict(build='2026110302',timestamp='4102444801'))

    def test_pinned_native_recovery_functions(self):
        import shutil
        compiler=shutil.which('g++')
        if compiler is None: self.skipTest('C++ compiler unavailable; native boundary probe unrun')
        with tempfile.TemporaryDirectory() as directory:
            binary=Path(directory)/'recovery-probe'
            compile_result=subprocess.run([compiler,'-std=c++17','-Wall','-Wextra','-Werror',
                str(self.fixtures/'RecoveryProbe.cpp'),'-o',str(binary)],capture_output=True,text=True,timeout=30)
            self.assertEqual(compile_result.returncode,0,compile_result.stderr)
            for build in self.synthetic_ids:
                result=subprocess.run([str(binary),build],capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('8 recovery assertions passed',result.stdout)

    def test_pinned_updater_protocol_block_and_real_release_selector(self):
        import shutil
        java=shutil.which('java')
        if java is None: self.skipTest('Java unavailable; Java boundary probes unrun')
        modules=subprocess.run([java,'--list-modules'],capture_output=True,text=True,timeout=10)
        self.assertEqual(modules.returncode,0,modules.stderr)
        if 'jdk.compiler@' not in modules.stdout:
            self.skipTest('JDK compiler module unavailable; Java boundary probes unrun')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            template=(self.fixtures/'UpdaterProbe.java.in').read_text()
            marker='// PINNED_METADATA_ADMISSION'
            self.assertEqual(template.count(marker),1)
            probe=root/'UpdaterProbe.java'
            probe.write_text(template.replace(marker,(self.fixtures/'updaterService-301-317.excerpt').read_text()))
            result=subprocess.run([java,'-m','jdk.compiler/com.sun.tools.javac.Main','-d',str(root),str(probe),
                str(ROOT/'os/overlays/packages/apps/Updater/src/app/seamlessupdate/client/RoseReleases.java'),
                str(ROOT/'os/tests/MustangReleasesTest.java')],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            for main,args,expected in [('UpdaterProbe',self.synthetic_ids,'14 updater protocol assertions passed'),
                    ('app.seamlessupdate.client.MustangReleasesTest',(),'checks passed')]:
                result=subprocess.run([java,'-ea','-cp',str(root),main,*args],capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn(expected,result.stdout)


class CounterRecoveryContractTest(unittest.TestCase):
    """Synthetic authority/CAS and replica fixtures; no real numbering epoch exists."""
    def setUp(self):
        import mustang_numbering
        self.contract=mustang_numbering
        self.policy=json.loads((ROOT/'os/build-only/mustang-numbering.json').read_text())
        self.policy.update(status='bound',epoch='1'*64,authority='https://counter.synthetic.invalid/mustang',
                           minimumTimestamp='1000')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.events=[]
        self.anchor=self.contract.expected_anchor(self.policy,self.events)

    def run_context(self,run='101'):
        return dict(repositoryId='1428996648',workflow=self.contract.WORKFLOW,runId=run,attempt=1,source='a'*40)

    def proposal(self,run='101',epoch='1001'):
        return self.contract.propose(self.policy,self.anchor,self.events,self.run_context(run),epoch)

    def commit_fixture(self,proposal):
        """Model an external authority's atomic CAS, not a production backend."""
        import release_receipts
        if proposal['expected']!=self.anchor: return False
        entry=proposal['reservation']
        release_receipts.write_once(self.root/(str(entry['counter'])+'.json'),entry)
        self.events.append(copy.deepcopy(entry))
        self.anchor=self.contract.expected_anchor(self.policy,self.events)
        return True

    def test_committed_policy_and_cli_are_inert(self):
        value=json.loads((ROOT/'os/build-only/mustang-numbering.json').read_text())
        self.assertEqual(value['status'],'unbound')
        for key in ('epoch','authority','minimumTimestamp'): self.assertIsNone(value[key])
        with self.assertRaisesRegex(ValueError,'unbound'): self.contract.policy(value)
        result=subprocess.run([sys.executable,str(ROOT/'scripts/mustang_numbering.py')],
                              capture_output=True,text=True,timeout=10)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('no allocator',result.stderr)

    def test_monotonic_counter_and_timestamp_are_distinct(self):
        for sequence in range(1,5):
            proposal=self.proposal(str(100+sequence),str(1000+sequence))
            self.assertEqual(proposal['reservation']['build'],str(self.contract.LEGACY_MAX+sequence))
            self.assertEqual(proposal['reservation']['buildTimestamp'],str(1000+sequence))
            with self.assertRaisesRegex(ValueError,'not committed'):
                self.contract.confirm(self.policy,proposal,self.anchor,self.events)
            self.assertTrue(self.commit_fixture(proposal))
            confirmed=self.contract.confirm(self.policy,proposal,self.anchor,self.events)
            self.assertEqual(confirmed,proposal['reservation'])
        # Real time cannot be manufactured from the synthetic ID or silently moved backwards.
        for epoch in ('1004','1003','0','-1','01005','9223372036854775808'):
            with self.subTest(epoch=epoch),self.assertRaises(ValueError): self.proposal('105',epoch)

    def test_counter_boundaries_cannot_wrap_or_use_a_date_sequence(self):
        self.assertEqual(self.contract.build_for_counter(1),'9999123200')
        self.assertEqual(self.contract.build_for_counter(self.contract.CAPACITY),'9999999999')
        for bad in (0,-1,True,'1',self.contract.CAPACITY+1):
            with self.subTest(counter=bad),self.assertRaises(ValueError): self.contract.build_for_counter(bad)

    def test_concurrent_proposals_require_one_successful_compare_and_swap(self):
        import concurrent.futures
        import threading
        proposals=[self.proposal('101'),self.proposal('102')]
        barrier=threading.Barrier(2);cas_lock=threading.Lock()
        def commit(proposal):
            barrier.wait(timeout=5)
            with cas_lock: return self.commit_fixture(proposal)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(commit,proposals))
        self.assertEqual(sorted(outcomes),[False,True])
        self.assertEqual(len(self.events),1)
        for proposal,won in zip(proposals,outcomes):
            if won: self.assertEqual(self.contract.confirm(self.policy,proposal,self.anchor,self.events),self.events[0])
            else:
                with self.assertRaises(ValueError): self.contract.confirm(self.policy,proposal,self.anchor,self.events)

    def test_atomic_receipt_primitive_does_not_overwrite_a_concurrent_winner(self):
        import concurrent.futures
        import release_receipts
        path=self.root/'same-slot.json'
        def write(value):
            try: release_receipts.write_once(path,value);return True
            except release_receipts.ReceiptExists: return False
        rows=[self.proposal('101')['reservation'],self.proposal('102')['reservation']]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(write,rows)),[False,True])
        self.assertIn(json.loads(path.read_text()),rows)

    def test_interruption_before_commit_consumes_nothing_and_after_commit_recovers_exact_receipt(self):
        first=self.proposal()
        self.assertEqual(self.events,[])
        self.assertEqual(self.contract.recovery_suffix(self.policy,self.anchor,self.events,[]),[])
        self.commit_fixture(first)
        # Caller died before storing or seeing the receipt. Readback, not a new proposal, resolves it.
        recovered=self.contract.recovery_suffix(self.policy,self.anchor,self.events,[])
        self.assertEqual(recovered,[first['reservation']])
        self.assertEqual(self.contract.confirm(self.policy,first,self.anchor,self.events),recovered[0])
        self.assertEqual(self.contract.confirm(self.policy,first,self.anchor,self.events),recovered[0])
        with self.assertRaisesRegex(ValueError,'already consumes'): self.proposal()
        for attempt in (0,2,True,'1'):
            run=self.run_context('102');run['attempt']=attempt
            with self.subTest(attempt=attempt),self.assertRaises(ValueError):
                self.contract.propose(self.policy,self.anchor,self.events,run,'1002')

    def test_replica_restore_and_replay_are_idempotent_without_a_new_counter(self):
        import release_receipts
        for n in range(1,4): self.commit_fixture(self.proposal(str(100+n),str(1000+n)))
        replica=[copy.deepcopy(self.events[0])]
        missing=self.contract.recovery_suffix(self.policy,self.anchor,self.events,replica)
        self.assertEqual(missing,self.events[1:])
        for event in missing:
            release_receipts.write_once(self.root/('replica-'+str(event['counter'])+'.json'),event)
            replica.append(json.loads((self.root/('replica-'+str(event['counter'])+'.json')).read_text()))
        self.assertEqual(self.contract.recovery_suffix(self.policy,self.anchor,self.events,replica),[])
        self.assertEqual(self.anchor['counter'],3)

    def test_uncertain_commit_is_confirmed_after_another_writer_advances_the_head(self):
        first=self.proposal();self.commit_fixture(first)
        # The first caller lost its response. Another admitted writer commits
        # before it can read back; the current head is no longer its own event.
        second=self.proposal('102','1002');self.commit_fixture(second)
        before=copy.deepcopy(self.events)
        confirmed=self.contract.confirm(self.policy,first,self.anchor,self.events)
        self.assertEqual(confirmed,first['reservation'])
        self.assertNotEqual(self.anchor['head'],self.contract.digest(confirmed))
        self.assertEqual(self.events,before)
        self.assertEqual(self.anchor['counter'],2)
        self.assertEqual(self.contract.recovery_suffix(self.policy,self.anchor,self.events,[]),before)
        with self.assertRaisesRegex(ValueError,'already consumes'): self.proposal()

    def test_restoring_a_different_policy_under_the_same_epoch_cannot_reset_the_floor(self):
        self.commit_fixture(self.proposal())
        changed=dict(self.policy,minimumTimestamp='1')
        self.assertEqual(changed['epoch'],self.policy['epoch'])
        self.assertNotEqual(self.contract.policy(changed),self.contract.policy(self.policy))
        with self.assertRaises(ValueError):
            self.contract.propose(changed,self.anchor,self.events,self.run_context('102'),'1002')
        with self.assertRaises(ValueError):
            self.contract.recovery_suffix(changed,self.anchor,self.events,[])

    def test_replica_write_uncertainty_rechecks_existing_bytes(self):
        import release_receipts
        proposal=self.proposal();self.commit_fixture(proposal)
        replica=self.root/'replica.json'
        original=os.fsync;calls=0
        def fail_directory_sync(fd):
            nonlocal calls
            calls+=1
            if calls==2: raise OSError('synthetic failure after immutable receipt link')
            return original(fd)
        with patch.object(release_receipts.os,'fsync',side_effect=fail_directory_sync):
            with self.assertRaises(OSError): release_receipts.write_once(replica,self.events[0])
        # The return was uncertain; neither retry allocation nor assert that the write did not happen.
        observed=json.loads(replica.read_text())
        self.assertEqual(self.contract.recovery_suffix(self.policy,self.anchor,self.events,[observed]),[])
        self.assertEqual(self.contract.confirm(self.policy,proposal,self.anchor,self.events),observed)

    def test_stale_backup_missing_receipt_or_missing_anchor_cannot_authorize_reuse(self):
        old_anchor=copy.deepcopy(self.anchor)
        self.commit_fixture(self.proposal())
        for anchor,events in ((self.anchor,[]),(old_anchor,self.events),(None,self.events)):
            with self.subTest(anchor=anchor),self.assertRaises(ValueError):
                self.contract.propose(self.policy,anchor,events,self.run_context('102'),'1002')
        with self.assertRaises(ValueError): self.contract.recovery_suffix(self.policy,self.anchor,[],[])

    def test_checksums_cannot_establish_external_authority_freshness(self):
        old_anchor=copy.deepcopy(self.anchor)
        self.commit_fixture(self.proposal())
        # A jointly replayed old head and journal are internally consistent. Only
        # the mandatory fresh external head distinguishes them; no hash proves freshness.
        self.assertEqual(self.contract.snapshot(self.policy,old_anchor,[]),old_anchor)
        with self.assertRaises(ValueError): self.contract.snapshot(self.policy,self.anchor,[])

    def test_epoch_or_authority_loss_requires_external_resolution_not_initialization(self):
        self.commit_fixture(self.proposal())
        for key,value in [('epoch',None),('epoch','2'*64),('authority',None),
                          ('authority','https://other.synthetic.invalid/counter')]:
            policy=dict(self.policy,**{key:value})
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                self.contract.propose(policy,self.anchor,self.events,self.run_context('102'),'1002')

    def test_tampered_forward_or_forked_replica_and_forged_confirmation_refuse(self):
        proposal=self.proposal();self.commit_fixture(proposal)
        for key,value in [('build','9999999999'),('previous','0'*64),('counter',True),
                          ('buildTimestamp','1000'),('epoch','2'*64)]:
            rows=copy.deepcopy(self.events);rows[0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                self.contract.recovery_suffix(self.policy,self.anchor,self.events,rows)
        forged=copy.deepcopy(proposal);forged['reservation']['run']['source']='b'*40
        with self.assertRaises(ValueError): self.contract.confirm(self.policy,forged,self.anchor,self.events)
        self.commit_fixture(self.proposal('102','1002'))
        first_anchor=self.contract.expected_anchor(self.policy,self.events[:1])
        with self.assertRaises(ValueError): self.contract.recovery_suffix(self.policy,first_anchor,self.events[:1],self.events)

    def test_legacy_unknown_is_preserved_and_quarantine_proof_cannot_be_relaxed(self):
        for key,value in [('legacyAllocationKnown',True),('legacyCeiling','2026110499'),
                          ('failedSource','b'*40),('legacyProofSha256','0'*64)]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                self.contract.policy(dict(self.policy,**{key:value}))
        self.assertFalse(self.policy['legacyAllocationKnown'])


if __name__=='__main__':
    unittest.main()
