"""Offline adapter boundaries only: synthetic archives, signer/generator stand-ins, no live actions."""
import copy
import functools
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import tarfile
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
import test_app_inputs_export as app_fixture
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

    def test_committed_dedicated_lane_is_identity_bound_and_not_runtime_authorized(self):
        bound_lane=lane.configuration()
        self.assertEqual(bound_lane['status'],'bound')
        self.assertEqual(bound_lane['vm']['instanceId'],lane.CONFIRMED_INSTANCE_ID)
        plan=lane.controller_plan(bound_lane)
        self.assertEqual(plan['ownership'],bound_lane['vm'])
        self.assertEqual(plan['automaticStop'],{'seconds':14400,'action':'STOP','hours':4})
        self.assertFalse(plan['runtimeAuthorized'])
        self.assertNotIn('immortal-os-builder',' '.join(plan['start']))
        self.assertNotIn('roseos-yogi-builder-20261105-b',' '.join(plan['start']))
        yogi=json.loads((ROOT/'os/ota/yogi.json').read_text())['builder']
        for profile in (
                dict(bound_lane,diskResource='projects/protocols-465723/zones/us-west2-a/disks/immortal-os-builder'),
                dict(bound_lane,vm=dict(bound_lane['vm'],instance='immortal-os-builder',instanceId=lane.FRANKEL_INSTANCE_ID)),
                dict(bound_lane,vm=dict(bound_lane['vm'],instance=yogi['instance'],instanceId=yogi['instanceId'],zone=yogi['zone'])),
                dict(bound_lane,vm=dict(bound_lane['vm'],instanceId=yogi['retired']['instanceId'])),
                dict(bound_lane,disk=dict(bound_lane['disk'],id=yogi['restoredDiskId'])),
                dict(bound_lane,runnerLabel='roseos-frankel-builder'),
                dict(bound_lane,queue='os-release-frankel'),
                dict(bound_lane,workflow='.github/workflows/os-release.yml'),
                dict(bound_lane,workflow='.github/workflows/yogi-ota.yml'),
                dict(bound_lane,sourceRoot='/home/pixelbuild/source'),
                dict(bound_lane,sourceRoot='/srv/rose-yogi/src'),
                dict(bound_lane,infraEvidence='synthetic fixture, not a real binding')):
            with self.subTest(profile=profile.get('workflow'),instance=profile['vm']['instance']):
                with self.assertRaises(ValueError): lane.controller_plan(profile)

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
        unbound = dict(lane.configuration(), status='unbound')
        for invalid in (unbound, dict(self.lane, vm=dict(self.lane['vm'],
                                          instance='immortal-os-builder', project='protocols-465723',zone='us-west2-a'))):
            with self.subTest(instance=invalid['vm']['instance']):
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
        for action in ('check','plan'):
            result = subprocess.run([sys.executable,str(ROOT/'scripts/mustang_controller.py'), action],
                                    env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            body=json.loads(result.stdout)
            self.assertFalse(body['runtimeAuthorized'])
            self.assertNotIn('gcloud', result.stderr)
        start = subprocess.run([sys.executable,str(ROOT/'scripts/mustang_controller.py'), 'start'],
                               env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(start.returncode, 4)
        self.assertIn('requires its exact main workflow and a fresh attempt', start.stderr)
        self.assertNotIn('gcloud', start.stderr)
        for action in ('ownership','stop'):
            result = subprocess.run([sys.executable,str(ROOT/'scripts/mustang_controller.py'), action],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 4)
            self.assertNotIn('gcloud', result.stderr)


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
        committed=mustang_paths.layout(lane.configuration())
        self.assertEqual(committed['home'], Path('/mnt/disks/mustang-data/roseos'))
        self.assertFalse(str(committed['storageRoot']).startswith('/home/pixelbuild'))
        for invalid in (dict(lane.configuration(), status='unbound'), dict(profile,storageRoot='/elsewhere/actions'),
                        dict(profile,lockPath=profile['sourceRoot']),
                        dict(profile,sourceRoot='/home/pixelbuild/source',storageRoot='/home/pixelbuild/rose-actions',
                             lockPath='/home/pixelbuild/mustang.lock')):
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
        self.assertEqual(set(jobs),{'guard','controller','enroll','queue-watch','builder','signer','source','hostdeps','lock-admin','factory','vendor','otatools','otatools-verify','publisher','cleanup'})
        self.assertNotIn('id-token', jobs['source'])
        self.assertNotIn('actions-gcp-login', jobs['source'])
        self.assertIn('mustang_source_sync.py run --execute', jobs['source'])
        self.assertIn('- source', jobs['cleanup'].split('steps:',1)[0])
        self.assertLess(jobs['cleanup'].index('mustang_controller.py stop'), jobs['cleanup'].index('sweep --execute'))
        self.assertNotIn('pull_request', workflow)
        self.assertNotIn('workflow_call', workflow)
        self.assertNotIn('pull_request_target', workflow)
        self.assertIn('    runs-on: ubuntu-24.04',jobs['guard'])
        self.assertIn('python3 scripts/mustang-dedicated.py guard',jobs['guard'])
        guard_checkout=jobs['guard'].split('python3 scripts/mustang-dedicated.py guard',1)[0]
        self.assertIn('fetch-depth: 0', guard_checkout)
        active='\n'.join(line for line in workflow.splitlines() if not line.lstrip().startswith('#'))
        for forbidden in ('immortal-os-builder','roseos-frankel-builder','builder-reclaim','schedule:', 'workflow_run:'):
            self.assertNotIn(forbidden,active)
        self.assertIn('  group: mustang-ota',workflow)
        self.assertIn('timeout-minutes: 200',jobs['builder'])
        self.assertNotIn('timeout-minutes: 600',jobs['builder'])
        self.assertIn('needs: guard',jobs['controller'])
        self.assertNotIn('always()',jobs['controller'].split('steps:',1)[0])
        self.assertIn("needs.controller.result != 'skipped'",jobs['cleanup'])
        self.assertNotIn('id-token', jobs['builder'])
        self.assertNotIn('actions-gcp-login', jobs['builder'])
        self.assertNotIn('mustang-dedicated.py sign', jobs['builder'])
        self.assert_signer_targets_the_approved_runner(jobs['signer'])
        self.assertLess(jobs['signer'].index('mustang_hosted_sign.py preflight'), jobs['signer'].index('actions-gcp-login.py login'))
        self.assertLess(jobs['builder'].index('mustang-dedicated.py collect'), jobs['builder'].index('mustang_hosted_sign.py export-bundle'))
        self.assertLess(jobs['builder'].index('mustang-dedicated.py baseline'),jobs['builder'].index('mustang-dedicated.py produce'))
        self.assertNotIn('prepare-artifact-cli',jobs['builder'])
        self.assertLess(jobs['publisher'].index('verify-staged'),jobs['publisher'].index('actions/create-github-app-token'))
        self.assertLess(jobs['cleanup'].index('cleanup-prepare'),jobs['cleanup'].index('actions-gcp-login.py login'))
        env=dict(PATH=os.environ['PATH'])
        preflight=subprocess.run([sys.executable,str(ROOT/'scripts/mustang-dedicated.py'),'preflight'],env=env,
                                 capture_output=True,text=True,timeout=20)
        self.assertEqual(preflight.returncode,0,preflight.stderr)
        readiness=json.loads(preflight.stdout)
        self.assertFalse(readiness['buildAuthorized'])
        self.assertFalse(readiness['publicationAuthorized'])
        self.assertEqual(readiness['stopSafeguard']['seconds'],14400)
        self.assertNotIn('signer-capacity-unadmitted',{item['code'] for item in readiness['blockers']})
        for action in ('guard','host-check','fetch-inputs','baseline','produce','collect','sign','_execute','stage',
                       'verify-staged','publish','report','ownership-output','cleanup-prepare','require-owned'):
            result=subprocess.run([sys.executable,str(ROOT/'scripts/mustang-dedicated.py'),action],env=env,
                                  capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,4,action)
            self.assertNotIn('gcloud',result.stderr)
        signer=jobs['signer']
        for label,rewritten in (
                ('raw ubuntu', signer.replace(
                    '    runs-on:\n      group: roseos-mustang-signer\n      labels: mustang-signer-4core\n',
                    '    runs-on: ubuntu-24.04\n')),
                ('builder group', signer.replace(
                    '      group: roseos-mustang-signer\n      labels: mustang-signer-4core\n',
                    '      group: ${{ needs.guard.outputs.runner_group }}\n      labels: ${{ needs.guard.outputs.runner_label }}\n')),
                ('other group', signer.replace('roseos-mustang-signer','roseos-other-signer'))):
            with self.assertRaisesRegex(AssertionError,'signer is misrouted'):
                self.assert_signer_targets_the_approved_runner(rewritten)

    def assert_signer_targets_the_approved_runner(self, signer):
        expected=('    runs-on:\n'
                  '      group: roseos-mustang-signer\n'
                  '      labels: mustang-signer-4core\n')
        if expected not in signer or 'runs-on: ubuntu-24.04' in signer \
                or 'needs.guard.outputs.runner_group' in signer or 'roseos-mustang-builder' in signer \
                or signer.count('group: roseos-mustang-signer') != 1 \
                or signer.count('labels: mustang-signer-4core') != 1:
            raise AssertionError('signer is misrouted')

class PrivateInputImportTest(app_fixture.Fixture):
    """Canonical exporter fixtures, real tar imports and shared admission; no host/network."""
    def setUp(self):
        super().setUp()
        self.receipt,self.artifact=self.export()
        spec=importlib.util.spec_from_file_location('mustang_private_input_test',ROOT/'scripts/mustang-dedicated.py')
        self.driver=importlib.util.module_from_spec(spec); spec.loader.exec_module(self.driver)
        self.lane=DedicatedPathsTest().profile(self.tmp/'mustang')
        self.cfg=dict(dataFilesystemUuid='fixture-data-uuid')
        self.archive=Path(self.lane['storageRoot']).parent/'inputs'/self.receipt['producer']['tag']/'export.tar'
        self.archive.parent.mkdir(parents=True)
        reconcile=self.repo/'os/build-only/mustang-reconcile.json'; reconcile.parent.mkdir(parents=True,exist_ok=True)
        reconcile.write_bytes((ROOT/'os/build-only/mustang-reconcile.json').read_bytes())
        self.rec={key:self.receipt[key] for key in ('producer','signerSha256','sdk','apps','files')}
        self.rec.update(schema=self.driver.PRIVATE_RECORD,export=dict(
            receiptSha256=app_fixture.sha((self.artifact/'app-inputs-export.json').read_bytes()),
            privateArchive=dict(path=str(self.archive),provenance=dict(path='os/app-inputs/private-provenance.json'))))
        self.proof=dict(schema=self.driver.PRIVATE_PROVENANCE,producer=self.rec['producer'],
            receiptSha256=self.rec['export']['receiptSha256'],
            source=dict(exporter='scripts/host-app-inputs-export.py',exporterSource=SOURCE,
                        originalAttemptChainVerified=True,evidenceRef='synthetic exporter verification'),
            transfer=dict(approved=True,completed=True,approvalRef='synthetic transfer approval',
                evidenceRef='synthetic receiver readback',destination=dict(builder=self.lane['vm'],
                    dataFilesystemUuid=self.cfg['dataFilesystemUuid'],path=str(self.archive))))
        self.path=self.repo/'os/app-inputs/private.json'
        self.members=[(str(p.relative_to(self.artifact)),p.read_bytes())
                      for p in sorted(self.artifact.rglob('*')) if p.is_file()]
        self.pack()

    def bind(self):
        row=self.rec['export']['privateArchive']
        row.update(sha256=gate.digest(self.archive),sizeBytes=self.archive.stat().st_size)
        self.proof['archive']={key:row[key] for key in ('sha256','sizeBytes')}
        self.proof['transfer']['received']=dict(self.proof['archive'])
        proof_path=self.repo/row['provenance']['path']; proof_path.parent.mkdir(parents=True,exist_ok=True)
        proof_path.write_text(json.dumps(self.proof)); row['provenance']['sha256']=gate.digest(proof_path)
        self.path.write_text(json.dumps(self.rec))

    def pack(self,members=None):
        with tarfile.open(self.archive,'w') as stream:
            for name,data in self.members if members is None else members:
                if isinstance(name,tarfile.TarInfo): info=name
                else: info=tarfile.TarInfo(name); info.size=len(data)
                stream.addfile(info,io.BytesIO(data))
        self.bind()

    def imported(self,name='import'):
        with patch.object(self.driver.subprocess,'run',side_effect=AssertionError('private import used network/tools')):
            return self.driver.fetch_inputs(self.path,self.tmp/name,lane=self.lane,cfg=self.cfg,root=self.repo)

    def admit(self,directory,identity=None):
        return app_fixture.verify.verify(self.path,directory,dict(aapt='fixture-aapt',apksigner='fixture-apksigner'),
                                         root=self.repo,identity=identity or self.identity)

    def test_private_tar_imports_exact_payload_without_download_and_preserves_archive(self):
        before=self.archive.read_bytes(); directory=self.imported()
        self.assertEqual(self.archive.read_bytes(),before)
        self.assertEqual({str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file()},
                         {name for name,_ in self.members})
        for name,data in self.members: self.assertEqual((directory/name).read_bytes(),data)
        with self.assertRaises(FileExistsError): self.imported()

    def test_common_tar_dot_prefix_and_directories_are_supported_without_extracting_metadata(self):
        root=tarfile.TarInfo('.'); root.type=tarfile.DIRTYPE; root.mode=0o777
        apps=tarfile.TarInfo('./signed-apps'); apps.type=tarfile.DIRTYPE; apps.mode=0o777
        self.pack([(root,b''),(apps,b'')]+[('./'+n,d) for n,d in self.members])
        directory=self.imported()
        self.assertEqual((directory/'background-policy.json').read_bytes(),(self.artifact/'background-policy.json').read_bytes())
        self.assertNotEqual(directory.stat().st_mode&0o777,0o777)

    def test_mixed_actions_metadata_and_missing_or_wrong_transfer_provenance_refuse_before_copy(self):
        original=copy.deepcopy(self.rec); proof=copy.deepcopy(self.proof)
        cases=[lambda:self.rec['export'].update(artifact={'id':9}),
               lambda:self.proof['transfer'].update(approved=False),
               lambda:self.proof['transfer'].update(completed=False),
               lambda:self.proof['transfer'].update(approvalRef=''),
               lambda:self.proof['transfer']['destination'].update(builder=dict(self.lane['vm'],instanceId='999')),
               lambda:self.proof['transfer']['destination'].update(dataFilesystemUuid='other-disk'),
               lambda:self.proof['source'].update(originalAttemptChainVerified=False),
               lambda:self.proof['source'].update(exporterSource='guessed'),
               lambda:self.proof.update(receiptSha256='0'*64)]
        for i,change in enumerate(cases):
            self.rec=copy.deepcopy(original); self.proof=copy.deepcopy(proof); change(); self.bind()
            with self.subTest(case=i),self.assertRaises(ValueError): self.imported(str(i))
            self.assertFalse((self.tmp/str(i)).exists())

    def test_changed_provenance_or_archive_hash_refuses_and_retains_bad_copy(self):
        proof=self.repo/self.rec['export']['privateArchive']['provenance']['path']
        proof.write_text(proof.read_text()+' ')
        with self.assertRaisesRegex(ValueError,'provenance bytes differ'): self.imported('proof-drift')
        self.bind(); original=self.archive.read_bytes()
        self.archive.write_bytes(b'x'+original[1:])
        with self.assertRaisesRegex(ValueError,'copy/hash differs'): self.imported('archive-drift')
        self.assertEqual((self.tmp/'archive-drift/input.tar').read_bytes(),self.archive.read_bytes())
        self.assertFalse((self.tmp/'archive-drift/files').exists())

    def test_wrong_size_path_links_and_special_source_files_refuse(self):
        original=copy.deepcopy(self.rec)
        self.rec['export']['privateArchive']['sizeBytes']+=1
        self.path.write_text(json.dumps(self.rec))
        with self.assertRaises(ValueError): self.imported('size')
        self.rec=original; self.rec['export']['privateArchive']['path']='/retired/Frankel/export.tar'
        self.path.write_text(json.dumps(self.rec))
        with self.assertRaisesRegex(ValueError,'path/hash/size'): self.imported('path')
        self.rec['export']['privateArchive']['path']=str(self.archive); self.bind()
        target=self.archive.with_name('original.tar'); self.archive.rename(target); self.archive.symlink_to(target)
        with self.assertRaisesRegex(ValueError,'link'): self.imported('link')
        self.archive.unlink(); os.mkfifo(self.archive)
        with self.assertRaisesRegex(ValueError,'regular file'): self.imported('fifo')

    def test_archive_on_another_filesystem_refuses(self):
        with patch.object(self.driver.os,'fstat',return_value=SimpleNamespace(st_mode=0o100600,
                st_size=self.archive.stat().st_size,st_dev=-1)):
            with self.assertRaisesRegex(ValueError,'Mustang data storage'): self.imported()

    def test_traversal_absolute_unexpected_duplicate_links_and_devices_refuse(self):
        cases=[('../escape',b'x'),('/escape',b'x'),('extra',b'x'),self.members[0],
               ('./'+self.members[0][0],self.members[0][1])]
        for kind in (tarfile.SYMTYPE,tarfile.LNKTYPE,tarfile.FIFOTYPE,tarfile.CHRTYPE):
            info=tarfile.TarInfo(self.members[0][0]); info.type=kind; info.linkname='outside'
            cases.append((info,b''))
        for i,extra in enumerate(cases):
            with self.subTest(case=i):
                self.pack(([extra]+self.members[1:]) if isinstance(extra[0],tarfile.TarInfo) else self.members+[extra])
                with self.assertRaises(ValueError): self.imported(str(i))
                self.assertTrue((self.tmp/str(i)/'input.tar').is_file())
        self.assertFalse((self.tmp/'escape').exists())

    def test_sparse_missing_oversized_and_malformed_tar_refuse(self):
        self.pack(self.members[:-1])
        with self.assertRaisesRegex(ValueError,'inventory differs'): self.imported('missing')
        name='background-policy.json'; data=(self.artifact/name).read_bytes()
        self.pack([(name,data+b'x')]+[(n,d) for n,d in self.members if n!=name])
        with self.assertRaisesRegex(ValueError,'member size differs'): self.imported('size')
        self.archive.write_bytes(b'not a tar'); self.bind()
        with self.assertRaises(tarfile.TarError): self.imported('malformed')
        self.pack()
        with patch.object(tarfile.TarInfo,'issparse',return_value=True):
            with self.assertRaisesRegex(ValueError,'sparse'): self.imported('sparse')

    def test_payload_record_must_have_exact_canonical_inventory(self):
        for i,files in enumerate((self.rec['files'][:-1],self.rec['files']+[self.rec['files'][0]])):
            original=self.rec['files']; self.rec['files']=files; self.path.write_text(json.dumps(self.rec))
            with self.assertRaisesRegex(ValueError,'canonical ten payload'): self.imported(str(i))
            self.rec['files']=original

    def test_private_producer_cannot_relabel_another_committed_candidate(self):
        self.rec['producer']=dict(self.rec['producer'],candidateSha='f'*40)
        self.proof['producer']=self.rec['producer']; self.bind()
        with self.assertRaisesRegex(ValueError,'committed allocation'): self.imported()

    def test_failed_extraction_retains_archive_without_modifying_source_and_retry_is_explicit(self):
        before=self.archive.read_bytes()
        with patch.object(self.driver.shutil,'copyfileobj',side_effect=OSError('synthetic interruption')):
            with self.assertRaises(OSError): self.imported('interrupted')
        self.assertEqual((self.tmp/'interrupted/input.tar').read_bytes(),before)
        self.assertEqual(self.archive.read_bytes(),before)
        with self.assertRaises(FileExistsError): self.imported('interrupted')
        self.imported('new-attempt')

    def test_shared_verifier_admits_private_bytes_with_signature_policy_and_receipt_checks(self):
        directory=self.imported()
        self.assertEqual(self.admit(directory)['verdict'],'ADMITTED')
        with self.assertRaises(app_fixture.verify.Refused):
            self.admit(directory,identity=lambda apk,*args:dict(self.identity(apk,*args),certificates=['0'*64]))
        receipt=directory/'signed-apps/dev.pixel.voice.receipt.json'; receipt.write_text('{}')
        with self.assertRaises(app_fixture.verify.Refused): self.admit(directory)
        # Make hashes/receipt/sums self-consistent around a bad policy. Only the
        # actual shared policy admission can reject this, not archive hashing.
        policy=self.artifact/'background-policy.json'; changed=json.loads(policy.read_text())
        changed['voice']['apkSha256']='0'*64; policy.write_text(json.dumps(changed))
        for row in self.receipt['files']:
            file=self.artifact/row['path']; row.update(sha256=gate.digest(file),sizeBytes=file.stat().st_size)
        receipt_path=self.artifact/'app-inputs-export.json'; receipt_path.write_text(json.dumps(self.receipt))
        self.rec['files']=copy.deepcopy(self.receipt['files'])
        self.rec['export']['receiptSha256']=gate.digest(receipt_path)
        self.proof['receiptSha256']=self.rec['export']['receiptSha256']
        sums={row['path']:row['sha256'] for row in self.rec['files']}
        sums['app-inputs-export.json']=gate.digest(receipt_path)
        (self.artifact/'SHA256SUMS').write_text(''.join(f'{digest}  {name}\n' for name,digest in sorted(sums.items())))
        self.members=[(str(p.relative_to(self.artifact)),p.read_bytes())
                      for p in sorted(self.artifact.rglob('*')) if p.is_file()]
        self.pack(); directory=self.imported('bad-policy')
        with self.assertRaisesRegex(app_fixture.verify.Refused,'policy'): self.admit(directory)

    def test_private_fetch_step_must_finish_shared_admission_before_emitting_inputs(self):
        out=self.tmp/'fetch-main'
        shared=SimpleNamespace(verify=lambda path,directory,tools:app_fixture.verify.verify(
            path,directory,tools,root=self.repo,identity=self.identity))
        cfg=dict(self.cfg,appInputsRecord=str(self.path.relative_to(self.repo)),tools=dict(aapt='aapt',apksigner='apksigner'))
        with patch.object(self.driver,'ROOT',self.repo),patch.object(self.driver,'context',return_value=(self.lane,cfg)), \
                patch.object(self.driver,'host_check'),patch.object(self.driver.ota,'module',return_value=shared), \
                patch.object(self.driver,'emit') as emitted, \
                patch.dict(os.environ,dict(GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1')), \
                patch.object(sys,'argv',['mustang-dedicated.py','fetch-inputs','--out',str(out)]):
            self.assertEqual(self.driver.main(),0)
            emitted.assert_called_once_with(inputs_dir=str(out/'files'))
        emitted.reset_mock()
        with patch.object(self.driver,'ROOT',self.repo),patch.object(self.driver,'context',return_value=(self.lane,cfg)), \
                patch.object(self.driver,'host_check'),patch.object(self.driver.ota,'module',return_value=shared), \
                patch.object(self.driver,'emit') as emitted, \
                patch.object(app_fixture.verify,'verify',side_effect=app_fixture.verify.Refused('synthetic signature refusal')), \
                patch.dict(os.environ,dict(GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1')), \
                patch.object(sys,'argv',['mustang-dedicated.py','fetch-inputs','--out',str(self.tmp/'refused-main')]):
            with self.assertRaises(app_fixture.verify.Refused): self.driver.main()
            emitted.assert_not_called()


class DedicatedRuntimeTest(unittest.TestCase):
    def test_frozen_apps_are_exactly_the_requested_export_and_one_attempt(self):
        driver = self.driver
        cfg = json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
        record = json.loads((ROOT/cfg['appInputsRecord']).read_text())
        choice = cfg['frozenAppInputs']
        allowed = lambda c=cfg,e={'GITHUB_RUN_NUMBER':str(choice['workflowRunNumber'])},r=record: \
            driver.frozen_app_inputs_allowed(r,c,e,ROOT)
        self.assertTrue(allowed())
        self.assertFalse(allowed(e={'GITHUB_RUN_NUMBER':str(choice['workflowRunNumber']+1)}))
        self.assertFalse(allowed(e={}))
        wrong = dict(cfg,frozenAppInputs=dict(choice,recordSha256='0'*64))
        self.assertFalse(allowed(c=wrong))
        wrong_record = dict(record,export=dict(record['export'],run='1'))
        self.assertFalse(allowed(r=wrong_record))

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
            automaticStopHours=4,networkEvidence=self.record('network',dict(builder=self.lane['vm'],
                githubEgressVerified=True,approvedRoute='synthetic approved route')),
            sourceEvidence=source_ref,allocationState=state,appInputsRecord=apps,
            baselineTargetFiles=dict(archive,path=str(self.host/'baseline.zip')),
            runner=dict(groupName='fixture-mustang',groupId='99',lifetime='persistent',registrationEvidence=registration),
            signingRouteEvidence=signing,publication=dict(enabled=False,authorityRecord=None))
        for key,name in (('vendorEvidence','mustang-vendor-42522784617.json'),
                         ('hostDependenciesEvidence','mustang-hostdeps-42512135815.json')):
            value=json.loads((ROOT/'os/build-only'/name).read_text())
            value['receipt']['identity']['builder']=self.lane['vm']
            value['receipt']['identity']['diskUuid']='fixture-uuid'
            value['receipt']['sourceRoot']=self.lane['sourceRoot']
            if key=='hostDependenciesEvidence':
                value['receipt']['outDir']=str(self.host/'source/out_adevtool_deps')
            value['stopObservation']['instanceId']=self.lane['vm']['instanceId']
            path=Path('os/build-only')/name
            (self.root/path).write_text(json.dumps(value));self.cfg[key]=str(path)

    def record(self,name,value):
        path=Path('os/fixtures')/(name+'.json'); (self.root/path).parent.mkdir(parents=True,exist_ok=True)
        (self.root/path).write_text(json.dumps(value)); return str(path)

    def admitted(self,cfg=None,publish=False,diff=None):
        # These fixtures isolate native input admission. Real signer capacity
        # and its pre-secret failure boundary have their own integrated tests.
        import mustang_hosted_sign
        with patch.object(mustang_hosted_sign,'require_delivery_capacity',return_value={}):
            return self.driver.guard(self.lane,cfg or self.cfg,self.env,publish,root=self.root,diff=diff or (lambda *args:''))

    def test_native_host_check_uses_prepared_tools_without_cli_installation(self):
        def execute(*args,**kwargs):return SimpleNamespace(returncode=0,stdout='fixture-uuid')
        with patch.object(self.driver.route,'evaluate_guest',return_value={}), \
                patch.object(self.driver.shutil,'which',return_value=None) as which:
            self.driver.host_check(self.lane,self.cfg,observe_id=lambda:self.lane['vm']['instanceId'],execute=execute)
        which.assert_not_called()

    def test_artifact_transport_strips_credentials_from_the_one_storage_redirect(self):
        import urllib.error
        requests=[]
        def open_http(request,**kwargs):
            requests.append(request)
            if len(requests)==1:
                raise urllib.error.HTTPError(request.full_url,302,'Found',
                    {'Location':'https://fixture.blob.core.windows.net/artifact?sig=private-fixture'},None)
            response=io.BytesIO(b'archive');response.status=200;return response
        read=self.driver.app_artifact_reader(9,'fixture-token',open_http)
        self.assertEqual(read('/zip',7),b'archive')
        self.assertEqual(requests[0].get_header('Authorization'),'Bearer fixture-token')
        self.assertEqual(requests[1].header_items(),[])
        self.assertEqual(len(requests),2)
        with self.assertRaisesRegex(ValueError,'scope'):read('/other',7)
        self.assertEqual(len(requests),2)

    def test_artifact_transport_denial_is_single_attempt_and_sanitized(self):
        import urllib.error
        for stage in ('metadata','archive-api','archive-storage'):
            calls=[]
            def open_http(request,**kwargs):
                calls.append(request)
                if stage=='archive-storage' and len(calls)==1:
                    raise urllib.error.HTTPError(request.full_url,302,'Found',
                        {'Location':'https://fixture.blob.core.windows.net/artifact?sig=secret-signature'},None)
                raise urllib.error.HTTPError(request.full_url,403,'sensitive-url-token',{},None)
            read=self.driver.app_artifact_reader(9,'secret-token',open_http)
            with self.subTest(stage=stage),self.assertRaisesRegex(ValueError,stage+' HTTP 403; no retry') as raised:
                read('' if stage=='metadata' else '/zip',7)
            self.assertNotIn('secret',str(raised.exception))
            self.assertNotIn('sensitive',str(raised.exception))
            self.assertEqual(len(calls),2 if stage=='archive-storage' else 1)

    def test_artifact_transport_rejects_redirect_target_and_oversized_response(self):
        import urllib.error
        for target in ('http://fixture.blob.core.windows.net/file','https://attacker.test/file',
                       'https://name:password@fixture.blob.core.windows.net/file'):
            calls=[]
            def open_http(request,**kwargs):
                calls.append(request)
                raise urllib.error.HTTPError(request.full_url,302,'Found',{'Location':target},None)
            with self.subTest(target=target),self.assertRaisesRegex(ValueError,'redirect differs'):
                self.driver.app_artifact_reader(9,'fixture-token',open_http)('/zip',7)
            self.assertEqual(len(calls),1)
        def oversized(*args,**kwargs):
            response=io.BytesIO(b'12345678');response.status=200;return response
        with self.assertRaisesRegex(ValueError,'byte bound'):
            self.driver.app_artifact_reader(9,'fixture-token',oversized)('',7)

    def test_clock_guard_preserves_exact_captured_runtime_without_legacy_fields(self):
        import mustang_clock
        cfg=dict(self.cfg,clockNumbering='os/fixtures/clock.json')
        with patch.object(mustang_clock,'policy',return_value={}) as clock_policy:
            admitted=self.admitted(cfg)
        clock_policy.assert_called_once_with(self.lane,cfg,self.root)
        self.assertEqual(admitted,cfg)
        self.assertNotIn('consumedBuilds',admitted)
        # The real collector rejects this legacy projection; do not weaken it
        # to accept extra fields in order to hide a guard/caller mismatch.
        self.assertNotEqual(dict(admitted,consumedBuilds=None),cfg)

    def test_actual_evidence_guard_allows_private_and_refuses_stale_or_unbound(self):
        self.assertEqual(self.admitted()['consumedBuilds'],['2026110402'])
        for change in (dict(status='unbound'),dict(jobs=None),dict(sourceEvidence=None),dict(signingRouteEvidence=None),
                       dict(automaticStopHours=12),dict(runner=dict(self.cfg['runner'],lifetime=None)),dict(dataFilesystemUuid=None),
                       dict(baselineTargetFiles=dict(self.cfg['baselineTargetFiles'],path='/retired/baseline.zip'))):
            with self.subTest(change=change):
                with self.assertRaises(ValueError): self.admitted(dict(self.cfg,**change))
        with self.assertRaisesRegex(ValueError,'omits current app source'):
            self.admitted(diff=lambda *args:'changed-app-source')
        with self.assertRaises(ValueError): self.admitted(publish=True)
        state=self.driver.load(self.root/self.cfg['allocationState']); state['unknown']=True
        (self.root/self.cfg['allocationState']).write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError,'allocation state remains unresolved'): self.admitted()

    def test_candidate_continuity_satisfies_guard_without_the_old_target_files(self):
        name='os/build-only/mustang-candidate-continuity-20261107.json'
        dest=self.root/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT/name).read_bytes())
        cfg=dict(self.cfg, baselineTargetFiles=None, candidateContinuity=name)
        self.assertIsNone(self.admitted(cfg)['baselineTargetFiles'])
        broken=json.loads(dest.read_text()); broken['currentPhone']=True
        dest.write_text(json.dumps(broken))
        with self.assertRaisesRegex(ValueError,'not a current phone'): self.admitted(cfg)

    def test_source_sync_receipt_is_not_bootstrap_admission(self):
        ref=self.record('sync', {'schema':'roseos.mustang-source-sync-evidence.v1', 'pristine':False})
        with self.assertRaisesRegex(ValueError, 'not pristine bootstrap'):
            self.admitted(dict(self.cfg, sourceEvidence=ref))

    def test_prepared_vendor_binding_is_required_before_start(self):
        with self.assertRaisesRegex(ValueError,'vendor and host-dependency evidence'):
            self.admitted(dict(self.cfg,vendorEvidence=None))
        path=self.root/self.cfg['vendorEvidence'];record=self.driver.load(path)
        record['receipt']['identity']['builder']['instanceId']='999'
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError,'another source, builder, disk'):
            self.admitted()

    def test_copy_time_frankel_snapshot_is_not_a_source_pin(self):
        path=self.root/self.cfg['sourceEvidence']
        source=self.driver.load(path)
        source['selectionSha256']='34cdc3a98e8cfc614a88c5a40d002c51e87404aaec8a4f2962cb46e824c4f6e4'
        path.write_text(json.dumps(source))
        with self.assertRaisesRegex(ValueError,'not a dedicated source pin'): self.admitted()
        source.pop('selectionSha256')
        source['base']='/home/pixelbuild/grapheneos-2026100600'
        path.write_text(json.dumps(source))
        with self.assertRaisesRegex(ValueError,'not a dedicated source pin'): self.admitted()

    def test_private_schema_requires_private_provenance_during_hosted_guard(self):
        path=self.root/self.cfg['appInputsRecord']; record=self.driver.load(path)
        record['schema']=self.driver.PRIVATE_RECORD; path.write_text(json.dumps(record))
        with patch.object(self.driver,'private_binding',side_effect=ValueError('synthetic transfer not approved')) as check:
            with self.assertRaisesRegex(ValueError,'transfer not approved'): self.admitted()
            check.assert_called_once_with(record,self.lane,self.cfg,self.root)

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
        with patch.object(self.driver.os,'statvfs',return_value=SimpleNamespace(f_bavail=10,f_frsize=4096,f_files=100,f_favail=0)):
            with self.assertRaisesRegex(ValueError,'no available bytes/inodes'):
                self.driver.host_check(self.lane,self.cfg,observe_id=lambda:self.lane['vm']['instanceId'],execute=process)

    def test_real_disk_preflight_rejects_wrong_identity_low_space_and_root(self):
        import mustang_route as readiness
        peak=readiness.compile_peak()
        lane_binding=lane.configuration(); runtime=json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
        good=dict(instanceId=lane.CONFIRMED_INSTANCE_ID,filesystemUuid=lane.CONFIRMED_UUID,
                  mount=lane.CONFIRMED_MOUNT,availableBytes=lane.CONFIRMED_FREE_BYTES,
                  availableInodes=1000,totalInodes=runtime.get('dataFilesystemTotalInodes',lane.CONFIRMED_TOTAL_INODES),euid=1000)
        observed=readiness.evaluate_guest(good,lane_binding,runtime)
        self.assertFalse(observed['admitted'])
        self.assertGreaterEqual(observed['availableBytes'],peak)
        for bad,message in (
                (dict(good,instanceId=lane.FRANKEL_INSTANCE_ID),'not the admitted dedicated VM'),
                (dict(good,instanceId='2720796557371812468'),'not the admitted dedicated VM'),
                (dict(good,filesystemUuid='other-uuid'),'not the admitted mounted disk'),
                (dict(good,mount='/home/pixelbuild'),'data mount differs'),
                (dict(good,mount='/srv/rose-yogi'),'data mount differs'),
                (dict(good,availableBytes=peak-1),'recorded compile peak'),
                (dict(good,availableInodes=0),'no available bytes/inodes'),
                (dict(good,totalInodes=1),'inode count differs'),
                (dict(good,euid=0),'nonroot')):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError,message):
                    readiness.evaluate_guest(bad,lane_binding,runtime)
        report=readiness.report()
        codes={item['code'] for item in report['blockers']}
        self.assertTrue({'runtime-unbound','signer-capacity-unadmitted'}.isdisjoint(codes))
        self.assertTrue({'runner-unregistered','allocation-unresolved','baseline-not-on-disk','signing-route-unadmitted'}.isdisjoint(codes))
        self.assertNotIn('app-inputs-unadmitted', codes)
        # The accepted export is reported stale exactly when this checkout's app sources moved past its producer: an
        # app-source change merges before the Frankel build and re-export that refresh it, so main may carry either.
        drift = readiness.bound_app_freshness(json.loads((ROOT/runtime['appInputsRecord']).read_text()), ROOT)
        self.assertEqual('app-inputs-stale' in codes, bool(drift))
        self.assertEqual(runtime['appInputsRecord'], readiness.ACCEPTED_APP_EXPORT['path'])
        self.assertEqual(runtime['status'], 'bound')
        self.assertEqual(runtime['signingRouteEvidence'],'os/build-only/mustang-dedicated-signing-route.json')
        self.assertEqual(runtime['sourceEvidence'],'os/build-only/mustang-full-run-source.json')
        self.assertNotIn('source-unprovisioned', codes)
        self.assertNotIn('source-sync-unverified', codes)
        self.assertTrue(report['sourceSync']['bound'])
        self.assertFalse(report['sourceSync']['buildAuthorized'])
        self.assertFalse(report['sourceSync']['pristine'])
        self.assertEqual(report['sourceSync']['projectCount'], 1057)
        self.assertFalse(report['sourcePin']['pinned'])
        self.assertFalse(report['sourcePin']['syncAuthorized'])
        record=json.loads((ROOT/runtime['appInputsRecord']).read_text())
        export=readiness.ACCEPTED_APP_EXPORT
        self.assertTrue(readiness.accepted_app_export(record, runtime['appInputsRecord']))
        self.assertEqual(record['apps'], [
            {'package':'dev.pixel.backgroundcontrol','versionCode':41,'apkSha256':'7746c5b6724e5fd6aa732a3e2bf6d303c16d7d2704c88b136fd4222a8675d5d6'},
            {'package':'dev.pixel.voice','versionCode':370,'apkSha256':'6cf2c3baacb01032e156ed5bcb79ec44e586a459a57eb7e5aa2a0dad86758ed5'},
            {'package':'dev.pixel.calculator.agent','versionCode':6,'apkSha256':'19309b3ea65e31fe531f46e7c5bd677b2ebf1fa8a15798e5c1581840972e0b2d'}])
        self.assertIn(str(export['archiveBytes']), runtime['basis'])
        self.assertIn('not an OTA extraction', runtime['basis'])
        head=subprocess.check_output(['git','-C',str(ROOT),'-c','protocol.allow=never','rev-parse','HEAD'],text=True).strip()
        self.assertNotIn('app-inputs-history-unavailable', codes)
        # HEAD may be past the producer's app sources (see above), so only consistency is pinned at HEAD; the fixed
        # commit below has none of the producer's app sources changed.
        self.assertEqual(readiness.bound_app_freshness(record), readiness.app_source_drift(record, head))
        self.assertEqual(readiness.app_source_drift(record, export['recordMerge']), [])
        self.assertEqual(readiness.app_source_drift(record, head, diff=lambda *args:'workspace/rose-agent/changed\n'),
                         ['dev.pixel.voice','dev.pixel.calculator.agent','dev.pixel.backgroundcontrol'])
        missing='0000000000000000000000000000000000000000'
        with self.assertRaisesRegex(ValueError,'app source history is unavailable'):
            readiness.git_name_diff(ROOT, record['producer']['sourceSha'], missing, ['workspace/rose-agent'])
        with self.assertRaisesRegex(ValueError,'app source history is unavailable'):
            readiness.app_source_drift(record, missing)
        original=readiness.git_local
        def shallow(root,args):
            if args and args[0]=='cat-file':
                return SimpleNamespace(returncode=128,stdout='',stderr='fatal: bad object')
            return original(root,args)
        with patch.object(readiness,'git_local',side_effect=shallow):
            self.assertIsNone(readiness.bound_app_freshness(record))
            with self.assertRaisesRegex(ValueError,'app source history is unavailable'):
                readiness.app_source_drift(record, head)
        checks=(ROOT/'.github/workflows/mustang-ota-checks.yml').read_text().split('steps:',1)[1]
        self.assertLess(checks.index('fetch-depth: 0'), checks.index('scripts/tests/test_mustang_edge_gate.py'))
        self.assertIn('persist-credentials: false\n', checks)
        self.assertFalse(report['buildAuthorized']); self.assertFalse(report['publicationAuthorized'])
        self.assertFalse(report['admitted']['appArchive']); self.assertFalse(report['admitted']['baselineTargetFiles'])
        self.assertEqual(report['minimumFreeBytes'],121686892544)
        self.assertEqual(report['minimumFreeBytes'],peak)
        self.assertEqual(report['minimumFreeBytesSource'],
                         'os/mustang-ota/41761758623/producer/compile-completed.json peakSampledBytes')
        self.assertEqual(report['workflowTrust']['problems'],[])
        self.assertIsNone(report['workflowTrust']['lifetime'])
        self.assertFalse(report['workflowTrust']['ephemeralFeasibleNow'])
        self.assertFalse(report['signingGroupImpact']['modified'])
        self.assertEqual(readiness.enrollment()['github']['groupId'],'5')
        self.assertIsNone(readiness.enrollment()['github']['lifetime'])
        self.assertIsNone(readiness.enrollment()['github']['runnerId'])
        self.assertEqual(readiness.enrollment()['github']['runnerCount'],0)
        self.assertNotIn('baseline-not-on-disk',codes)
        continuity=report['otaContinuity']['currentContinuity']
        self.assertEqual(continuity['record'],'os/build-only/mustang-candidate-continuity-20261107.json')
        self.assertFalse(continuity['currentPhone']);self.assertFalse(continuity['admitsRuntime'])
        self.assertFalse(report['numberStrategy']['selectsBuildNumber'])
        self.assertIsNone(report['numberStrategy']['allocatedBuild'])
        self.assertEqual(report['numberStrategy']['selectedMode'], 'UTC-hour00-local')
        self.assertEqual(report['numberStrategy']['legacySuffixExcluded'],'01..99')
        self.assertFalse(report['numberStrategy']['legacyAllocationKnown'])
        self.assertFalse(report['numberStrategy']['reserved'])
        self.assertIsNone(report['numberStrategy']['authority'])
        self.assertFalse(report['numberStrategy']['issued'])
        self.assertFalse(report['numberStrategy']['backfill'])
        self.assertNotIn('reserves', json.loads((ROOT/'os/build-only/mustang-allocation-state-20261107.json').read_text())['basis'])
        requirement = json.loads((ROOT/'os/build-only/mustang-source-requirement-20261107.json').read_text())
        self.assertNotIn('unverified', requirement['required'])
        self.assertTrue(requirement['upstream']['factoryBody']['boxHashVerified'])
        self.assertFalse(requirement['upstream']['factoryBody']['vendorGenerated'])
        self.assertFalse(requirement['upstream']['factoryBody']['synced'])
        box = json.loads((ROOT/'os/build-only/mustang-factory-box-20261107.json').read_text())
        self.assertTrue(box['installerInventory']['simg2img']['completed'])
        self.assertEqual(box['installerInventory']['simg2img']['sizeBytes'], 4191461376)
        self.assertFalse(box['installerInventory']['lpunpackApplied'])
        self.assertNotIn('not in use', box['installerInventory']['readers'])
        self.assertIn('not the next BUILD_NUMBER', report['numberStrategy']['legacyCeilingMeaning'])
        self.assertFalse(report['sourceRequirement']['pinned'])
        self.assertEqual(report['sourceRequirement']['roseRevision'], '2ac24c5475de121ebe4e9e6a5c1a404ef44aa9c8')
        self.assertFalse(report['sourceRequirement']['syncAuthorized'])
        self.assertIsNone(report['sourceRequirement']['rejected']['resolvedManifest'])
        self.assertFalse(report['sourcePin']['pinned'])
        self.assertFalse(report['sourcePin']['blockedOnMissingUpstreamDevice'])
        self.assertEqual(report['sourcePin']['deviceSupport'], 'adevtool-generated')
        self.assertFalse(report['sourcePin']['mustangProjectInManifest'])
        self.assertFalse(report['sourcePin']['vendorFactoryDownloaded'])
        self.assertFalse(report['sourcePin']['vendorFactoryBodyHashed'])
        self.assertTrue(report['sourcePin']['factoryBodyHashMatchesIndex'])
        self.assertFalse(report['sourcePin']['factoryOnDedicatedDisk'])
        self.assertFalse(report['sourcePin']['factoryExtracted'])
        self.assertEqual(report['sourcePin']['factoryBox']['sizeBytes'], 11366092596)
        self.assertTrue(report['otaContinuity']['installerAlternative']['historical'])
        self.assertFalse(report['otaContinuity']['installerAlternative']['apkExtracted'])
        self.assertEqual(report['otaContinuity']['installerAlternative']['inventory']['safeEntries'], 34)
        self.assertFalse(report['otaContinuity']['installerAlternative']['isTargetFiles'])
        current = report['otaContinuity']['currentContinuity']
        self.assertEqual(current['updaterSha256'], '32d5f335098c14839641784552fbed513932921c26f4b83647967747198c2cd9')
        self.assertEqual(current['certificateDerSha256'], '93cc757e7f4e4e5c4ef7726fbeec9acc77b1f97d471c1cd0d62753cf94e471a8')
        self.assertFalse(current['apkSignerRemeasured'])
        self.assertFalse(current['currentPhone'])
        self.assertEqual(current['runtimeStatus'], 'bound')
        self.assertFalse(report['otaContinuity']['phoneUpdatePassed'])
        self.assertEqual(report['otaContinuity']['updater']['otaCertificateSha256'],
                         '93cc757e7f4e4e5c4ef7726fbeec9acc77b1f97d471c1cd0d62753cf94e471a8')
        self.assertEqual(report['sourcePin']['projectsWith40HexRevision'], report['sourcePin']['projectCount'])
        self.assertGreater(report['sourcePin']['projectCount'], 1000)
        self.assertFalse(report['otaContinuity']['fullOtaUsesPreviousTargetFiles'])
        self.assertFalse(report['otaContinuity']['oldSignedTargetFilesNeededForInstalledTrust'])
        self.assertTrue(report['otaContinuity']['installedTrustObservationRequired'])
        self.assertEqual(report['otaContinuity']['installedTrustPath'], 'candidate-installer-continuity')
        self.assertFalse(report['otaContinuity']['phoneAcceptanceIncluded'])
        self.assertEqual(report['otaContinuity']['installedTimestamp'], '1793742435')
        self.assertNotIn('secretAccessor', json.dumps(report['controllerWif']))
        self.assertNotIn('zoneOperations', json.dumps(report['controllerWif']['controller']['permissions']))
        self.assertFalse(report['controllerWif']['signerRequiredForDryRun'])
        self.assertFalse(report['controllerWif']['neededBeforeBootstrap'])
        self.assertFalse(report['controllerWif']['oidcTested'])
        self.assertEqual(report['controllerWif']['controller']['providerChecks']['repositoryId'], '1428996648')
        self.assertEqual(report['controllerWif']['controller']['providerChecks']['workflowRef'],
                         'Immortal-Protocols/RoseOS-1/.github/workflows/mustang-ota-dedicated.yml@refs/heads/main')
        self.assertEqual(report['controllerWif']['controller']['statusPoll'], 'compute.instances.get')
        import datetime
        import importlib.util
        spec = importlib.util.spec_from_file_location('release_allocate_ceiling', ROOT/'scripts/release-allocate.py')
        allocator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(allocator)
        with self.assertRaisesRegex(allocator.Refused, 'clock_behind'):
            allocator.next_build({'9999123200'}, datetime.datetime(2026, 11, 7, tzinfo=datetime.timezone.utc), 99)
        plan=readiness.provision_plan()
        rendered=json.dumps(plan)
        self.assertEqual(plan['noblePackages']['absent'],['libncurses5','yarn'])
        self.assertNotIn('libncurses5',plan['noblePackages']['main'])
        self.assertFalse(plan['executedByThisRepository']); self.assertFalse(plan['selectsBuildNumber'])
        self.assertNotIn('immortal-os-builder',rendered); self.assertNotIn('BUILD_NUMBER',rendered)
        self.assertNotIn('2026110502',rendered); self.assertNotIn('ghp_',rendered)
        self.assertEqual(readiness.runner_architecture()['lifetime'],None)
        self.assertEqual(readiness.runner_architecture()['builderJob'],'self-hosted')
        with self.assertRaisesRegex(ValueError,'no new build number'):
            pipeline_module=ota.module('unresolved_allocation','mustang-pipeline.py')
            pipeline_module.run('123','1',self.lane,dict(self.cfg),self.root/'inputs')
        self.assertFalse((self.host/'rose-actions/mustang-prep-123-1').exists())

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
        def read(suffix,limit):
            calls.append(suffix)
            if suffix=='/zip': return archive.read_bytes()
            return json.dumps(dict(observed_artifact,expired=False,workflow_run=dict(id=222),size_in_bytes=archive.stat().st_size)).encode()
        out=self.driver.fetch_inputs(path,self.root/'download',read=read)
        self.assertEqual((out/'signed-apps/app.apk').read_bytes(),b'fixture bytes')
        self.assertEqual(calls,['','/zip'])
        rec['export']['artifact']['digest']='sha256:'+'0'*64; path.write_text(json.dumps(rec))
        with self.assertRaisesRegex(ValueError,'metadata differs'):
            self.driver.fetch_inputs(path,self.root/'foreign',read=read)
        self.assertFalse((self.root/'foreign').exists())
        rec['export']['artifact']['digest']=observed_artifact['digest']; path.write_text(json.dumps(rec))
        def corrupt(suffix,limit):
            if suffix=='/zip':return b'changed download'
            return read(suffix,limit)
        with self.assertRaisesRegex(ValueError,'download/digest failed'):
            self.driver.fetch_inputs(path,self.root/'corrupt',read=corrupt)
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
            config.write_text(json.dumps(dict(target=dict(device='mustang'),host=dict(sign=sign))))
            with self.assertRaisesRegex(ValueError,'guest signing is closed'): self.driver.main()
            self.assertEqual(calls,[])

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
        # The dedicated lock is prepared separately; production cannot create or write it.
        Path(self.lane['lockPath']).touch();Path(self.lane['lockPath']).chmod(0o444)
        import mustang_bootstrap_collector as bootstrap
        import mustang_bootstrap_proof as source_proof
        bootstrap_result=dict(receiptWritten=True,receiptFile=dict(sha256='e'*64),
            receipt=dict(success=True,sourceCandidate={},run=dict(runId='123',runAttempt='1',source=SOURCE)))
        with patch.object(pipeline,'ROOT',self.root),patch.object(pipeline,'head',return_value=SOURCE), \
                patch.object(pipeline,'module',side_effect=dependency),patch.object(pipeline,'call',side_effect=call), \
                patch.object(pipeline,'omissions',return_value=['unrelated sprint omissions']), \
                patch.object(pipeline,'print',create=True), \
                patch.object(pipeline.mustang_vendor_consumer,'consume',return_value={'generationStarted':False}) as consume, \
                patch.object(pipeline.mustang_paths,'compiler_recipe',return_value=recipe), \
                patch.object(pipeline.integration,'admit',side_effect=admit), \
                patch.object(pipeline.integration,'render',return_value=({},['synthetic-integrate'])), \
                patch.object(allocate,'observe',return_value=(set(),{})), \
                patch.object(bootstrap.proof.deps,'read_event',return_value={'inputs':{'publish':False}}), \
                patch.object(bootstrap,'collect',return_value=bootstrap_result) as observed, \
                patch.object(bootstrap,'require_full_result'), \
                patch.object(source_proof,'verify_copy',return_value={'sourceCopyVerified':True}) as copied:
            self.assertEqual(pipeline.run('123','1',self.lane,cfg,inputs),0)
        result=self.driver.load(self.host/'rose-actions/mustang-prep-123-1/candidate-receipt.json')
        self.assertEqual(stages,['source','git-meta','lunch','compile'])
        consume.assert_called_once()
        observed.assert_called_once();copied.assert_called_once()
        self.assertTrue(result['stages']['source-copy']['result']['sourceCopyVerified'])
        self.assertFalse(result['stages']['vendor-inputs']['result']['generationStarted'])
        self.assertNotIn('vendor-sprint-patch',result['stages'])
        self.assertNotIn('adevtool-no-input-hash.patch',str(commands))
        exported=self.root/'reports';pipeline.collect_reports('123','1',exported,lane=self.lane)
        self.assertTrue((exported/'vendor-inputs-completed.json').is_file())
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
        self.canonical_lock=self.root/'mustang.lock';self.canonical_lock.touch();self.canonical_lock.chmod(0o444)
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

    def test_cached_compile_collects_and_signs_from_the_same_verified_output_directory(self):
        import mustang_compiler_cache
        cached=self.source/mustang_compiler_cache.OUT
        self.out.rename(cached);self.out=cached
        self.tf=cached/self.tf.name;self.tools=cached/self.tools.name
        for role,row in list(self.artifacts.items()):
            self.artifacts[role]=descriptor(cached/Path(row['path']).name)
        self.outputs['outDir']=str(cached)
        self.candidate['compilerCache']=dict(enabled=True,kind='ccache',outDir=mustang_compiler_cache.OUT)
        completed=self.candidate['stages']['compile']['result'];completed['outDir']=mustang_compiler_cache.OUT
        self.receipt.write_text(json.dumps(self.candidate))
        (self.receipt.parent/'compile-completed.json').write_text(json.dumps(completed))
        original=self.modules;original_sign=self.synthetic_sign;selected=[]
        def modules(name,filename):
            result=original(name,filename)
            if filename=='mustang-build-only.py':
                def collect(source,out_dir):
                    selected.append(out_dir)
                    self.assertEqual(out_dir,mustang_compiler_cache.OUT)
                    return copy.deepcopy(self.outputs)
                result.collect_outputs=collect
            return result
        def sign(argv,cwd,env,*bounds):
            self.assertEqual(env['OUT_DIR'],mustang_compiler_cache.OUT)
            return original_sign(argv,cwd,env,*bounds)
        with patch.object(adapter,'module',side_effect=modules),patch.object(self,'synthetic_sign',side_effect=sign):
            collected=self.collect();self.assertEqual(collected['status'],'complete')
            self.sign()
        self.assertEqual(selected,[mustang_compiler_cache.OUT])
        self.assertIn('signer stand-in',self.calls)

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
        locks=[]; previous=adapter.mustang_existing_lock.shared
        def take_lock(path,*,expected):
            locks.append(str(path))
            return previous(path,expected=expected)
        self.hp.shared_lock=lambda path:(_ for _ in ()).throw(AssertionError('dedicated stage selected write-open lock'))
        with patch.object(adapter,'BASE',self.root/'retired'), \
                patch.object(adapter.mustang_collection,'BASE',self.root/'retired'), \
                patch.object(adapter,'LOCK',str(self.root/'retired.lock')), \
                patch.object(adapter.mustang_existing_lock,'shared',side_effect=take_lock):
            adapter.collect(str(self.receipt),'333','1',lane=profile)
            signed=adapter.sign(str(self.receipt),str(self.bootstrap),'333','1',lane=profile)
            result=adapter.package('333','1','333','1',lane=profile)
        self.assertEqual(locks,[profile['lockPath']]*3)
        self.assertEqual(result['roseEdge'],signed['roseEdge'])
        self.assertEqual(self.calls,['signer stand-in','factory'])
        self.assertFalse((self.root/'retired').exists())
        self.assertFalse((self.root/'retired.lock').exists())

    def test_dedicated_collection_refuses_busy_or_missing_lock_without_creation(self):
        profile=DedicatedPathsTest().profile(self.root)
        self.candidate.update(builder=dict(profile['vm']),workflow=profile['workflow'])
        self.receipt.write_text(json.dumps(self.candidate))
        original=self.receipt.read_bytes()
        before=self.canonical_lock.stat()
        real=adapter.mustang_existing_lock
        with real.shared(self.canonical_lock,expected=self.canonical_lock):
            with self.assertRaisesRegex(real.LockUnavailable,'held'):
                adapter.collect(str(self.receipt),'333','1',lane=profile)
        report=self.actions/'mustang-collect-333-1/compile-collection.json'
        self.assertEqual(json.loads(report.read_text())['status'],'failed')
        self.assertEqual(self.calls,[])
        self.assertEqual(self.receipt.read_bytes(),original)
        self.assertEqual((self.canonical_lock.stat().st_ino,self.canonical_lock.stat().st_mode),
                         (before.st_ino,before.st_mode))
        self.canonical_lock.unlink()
        with self.assertRaises(real.LockUnavailable):
            with adapter.builder_lock(self.hp,self.canonical_lock,profile):pass
        self.assertFalse(self.canonical_lock.exists())

    def test_dedicated_collection_refuses_old_or_foreign_builder_receipts(self):
        profile=DedicatedPathsTest().profile(self.root)
        with self.assertRaisesRegex(ValueError,'exact dedicated Mustang VM/workflow'):
            adapter.collect(str(self.receipt),'333','1',lane=profile)
        self.candidate.update(builder=dict(profile['vm'],instanceId='999'),workflow=profile['workflow'])
        self.receipt.write_text(json.dumps(self.candidate))
        with self.assertRaisesRegex(ValueError,'exact dedicated Mustang VM/workflow'):
            adapter.collect(str(self.receipt),'333','1',lane=profile)
        self.assertEqual(self.calls,[])

    def test_committed_lane_refuses_a_foreign_receipt_before_admission_or_output(self):
        with self.assertRaisesRegex(ValueError,'exact dedicated Mustang VM/workflow'):
            adapter.collect(str(self.receipt),'333','1',lane=lane.configuration())
        self.assertEqual(self.calls,[])
        self.assertFalse((self.actions/'mustang-collect-333-1').exists())
        self.assertFalse(Path('/mnt/disks/mustang-data/roseos/rose-actions/mustang-collect-333-1').exists())

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
        # This fixture tests retained pre-clock edge staging, not a current
        # full-run allocation. Production clock validation is unchanged.
        live_read=ota.read
        def read_legacy(path):
            value=live_read(path)
            return dict(value,status='unbound') if Path(path)==ota.ROOT/'os/build-only/mustang-runtime.json' else value
        with patch.object(ota,'config',return_value=cfg),patch.object(ota,'module',side_effect=module),patch.object(ota,'identities',return_value=pins), \
                patch.object(ota,'read',side_effect=read_legacy), \
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


class GitJournalCodecTest(unittest.TestCase):
    """Raw synthetic Git objects and independent fixture checks, never a service."""
    def setUp(self):
        import mustang_git_objects
        import mustang_numbering
        self.codec=mustang_git_objects;self.contract=mustang_numbering
        self.policy=json.loads((ROOT/'os/build-only/mustang-numbering.json').read_text())
        self.policy.update(status='bound',epoch='1'*64,authority='https://counter.synthetic.invalid/mustang',
                           minimumTimestamp='1000')
        self.objects={};self.calls=[];self.events=[]
        self.limits=self.codec.Limits(commits=8,total_bytes=100000)
        self.head=self.make_commit(None,None)
        self.genesis=self.head

    def put(self,kind,data):
        identity=self.codec.object_oid(kind,data)
        self.objects[identity]=(kind,data)
        return identity

    def get(self,identity,limit):
        self.calls.append((identity,limit))
        return self.objects[identity]

    def make_commit(self,event,parent,blob=None,tree=None,headers=None):
        if tree is None:
            if blob is None:
                blob=self.codec.canonical(dict(schema='roseos.mustang-git-event.v1',
                    policySha256=self.contract.policy(self.policy),event=event))
            blob_oid=self.put('blob',blob)
            tree=self.codec.TREE_PREFIX+bytes.fromhex(blob_oid)
        tree_oid=self.put('tree',tree)
        if headers is None:
            headers=(b'tree '+tree_oid.encode()+b'\n'+
                (b'parent '+parent.encode()+b'\n' if parent else b'')+
                b'author Synthetic Fixture <fixture@example.invalid> 0 +0000\n'+
                b'committer Synthetic Fixture <fixture@example.invalid> 0 +0000')
        return self.put('commit',headers+b'\n\n'+self.codec.MESSAGE)

    def append(self,run='101',epoch='1001'):
        anchor=self.contract.expected_anchor(self.policy,self.events)
        context=dict(repositoryId='1428996648',workflow=self.contract.WORKFLOW,runId=run,attempt=1,source='a'*40)
        proposal=self.contract.propose(self.policy,anchor,self.events,context,epoch)
        self.head=self.make_commit(proposal['reservation'],self.head)
        self.events.append(proposal['reservation'])
        return proposal

    def checkpoint(self):
        return self.codec.Checkpoint(self.head,self.codec.canonical(self.contract.expected_anchor(self.policy,self.events)))

    def decode(self,head=None,limits=None):
        return self.codec.decode_history(self.policy,head or self.head,self.get,limits or self.limits)

    def verifier(self,checkpoint,current=None,available=True):
        # Test-only state is kept separately from the getter's returned bytes.
        # No real authenticated/fresh recovery provider is implemented here.
        expected_head=current or self.head
        def verify(candidate,observed):
            if not available: raise ValueError('fixture provenance/freshness/controls unknown')
            if candidate!=checkpoint or observed.git_oids[-1]!=expected_head:
                raise ValueError('fixture checkpoint stale or authority replayed')
        return verify

    def confirm(self,proposal,checkpoint=None,verify=None,head=None):
        checkpoint=checkpoint or self.checkpoint()
        return self.codec.confirm_from_objects(self.policy,proposal,head or self.head,self.get,self.limits,
                                               checkpoint,verify)

    def test_complete_history_reuses_contract_and_keeps_two_hash_domains_separate(self):
        self.append();self.append('102','1002')
        observed=self.decode()
        self.assertEqual(observed.events,self.events)
        self.assertEqual(observed.anchor,self.contract.expected_anchor(self.policy,self.events))
        self.assertEqual(observed.git_oids[0],self.genesis)
        self.assertEqual(observed.git_oids[-1],self.head)
        self.assertEqual(len(observed.git_oids),3)
        self.assertEqual(observed.events[0]['buildTimestamp'],'1001')
        self.assertEqual(len(self.head),40)
        self.assertEqual(len(observed.anchor['head']),64)
        self.assertGreater(observed.bytes_read,0)
        self.calls.clear()
        with self.assertRaisesRegex(ValueError,'separate'): self.decode(observed.anchor['head'])
        self.assertEqual(self.calls,[])

    def test_object_ids_match_native_git_without_object_or_ref_writes(self):
        self.append()
        for identity,(kind,data) in self.objects.items():
            result=subprocess.run(['git','hash-object','--no-filters','-t',kind,'--stdin'],input=data,
                capture_output=True,timeout=10,cwd='/tmp',env=dict(PATH=os.environ.get('PATH','/usr/bin:/bin'),
                    GIT_CONFIG_GLOBAL='/dev/null',GIT_CONFIG_NOSYSTEM='1',GIT_CEILING_DIRECTORIES='/',
                    GIT_DEFAULT_HASH='sha1',LC_ALL='C'))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip().decode(),identity)

    def test_no_provider_or_unbound_policy_cannot_confirm_and_does_not_read_objects(self):
        proposal=self.append()
        checkpoint=self.checkpoint()
        with self.assertRaisesRegex(ValueError,'verification unavailable'): self.confirm(proposal,checkpoint)
        self.assertEqual(self.calls,[])
        self.policy['status']='unbound'
        with self.assertRaisesRegex(ValueError,'unbound'): self.decode()
        self.assertEqual(self.calls,[])
        result=subprocess.run([sys.executable,str(ROOT/'scripts/mustang_git_objects.py')],
                              capture_output=True,text=True,timeout=10)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('no transport',result.stderr)

    def test_lost_reply_after_advanced_head_is_exact_and_idempotent(self):
        first=self.append();self.append('102','1002')
        checkpoint=self.checkpoint();verify=self.verifier(checkpoint)
        before=copy.deepcopy(self.objects)
        self.assertEqual(self.confirm(first,checkpoint,verify),first['reservation'])
        self.assertEqual(self.confirm(first,checkpoint,verify),first['reservation'])
        self.assertEqual(self.objects,before)
        self.assertEqual(len(self.events),2)

    def test_missing_or_corrupt_objects_never_retry_or_infer_nonconsumption(self):
        self.append();identity=self.head;kind,raw=self.objects[identity]
        del self.objects[identity]
        with self.assertRaises(KeyError): self.decode()
        self.assertEqual(len(self.calls),1)
        self.calls.clear();self.objects[identity]=(kind,raw+b'changed')
        with self.assertRaisesRegex(ValueError,'identity differs'): self.decode()
        self.assertEqual(len(self.calls),1)
        self.calls.clear();self.objects[identity]=('blob',raw)
        with self.assertRaisesRegex(ValueError,'kind/byte'): self.decode()
        self.assertEqual(len(self.calls),1)
        self.calls.clear();self.objects[identity]=(kind,raw)
        del self.objects[self.genesis]
        with self.assertRaises(KeyError): self.decode()
        self.assertEqual(sum(item[0]==self.genesis for item in self.calls),1)
        self.assertEqual(self.calls[-1][0],self.genesis)

    def test_getter_denial_propagates_without_retry_or_fallback(self):
        calls=[]
        def denied(identity,limit):
            calls.append(identity)
            raise PermissionError('synthetic getter denial, not a real service')
        with self.assertRaises(PermissionError):
            self.codec.decode_history(self.policy,self.head,denied,self.limits)
        self.assertEqual(calls,[self.head])

    def test_wrong_tree_modes_names_extra_entries_and_zero_object_id_refuse(self):
        blob=self.put('blob',b'{}\n')
        good=self.codec.TREE_PREFIX+bytes.fromhex(blob)
        for tree in (good.replace(b'100644',b'120000'),good.replace(b'100644',b'100755'),
                     good.replace(b'journal.json',b'../evil.json'),good+good,
                     self.codec.TREE_PREFIX+b'\0'*20):
            with self.subTest(tree=tree[:30]),self.assertRaises(ValueError):
                self.decode(self.make_commit(None,None,tree=tree))

    def test_merge_extra_headers_and_noncanonical_commit_layout_refuse(self):
        self.append();_,raw=self.objects[self.head]
        headers,message=raw.split(b'\n\n',1)
        for changed in (headers+b'\nparent '+self.genesis.encode(),headers+b'\ngpgsig unsupported',
                        headers.replace(b'author ',b'encoding '),headers+b'\0',b''):
            with self.subTest(headers=changed[:40]),self.assertRaises(ValueError):
                self.decode(self.put('commit',changed+b'\n\n'+message))
        with self.assertRaises(ValueError): self.decode(self.put('commit',headers+b'\n\nwrong message\n'))

    def test_noncanonical_duplicate_and_invalid_json_refuse_even_when_git_hashes_match(self):
        for blob in (b'{"schema":"a","schema":"b"}\n',b'\xff',b'{"x": NaN}\n',b'{ "x": 1 }\n'):
            with self.subTest(blob=blob),self.assertRaises(ValueError):
                self.decode(self.make_commit(None,None,blob=blob))

    def test_incomplete_genesis_and_oversized_objects_or_history_refuse(self):
        proposal=self.append()
        with self.assertRaisesRegex(ValueError,'genesis'): self.decode(self.make_commit(proposal['reservation'],None))
        with self.assertRaisesRegex(ValueError,'genesis'): self.decode(self.make_commit(None,self.head))
        observed=self.decode()
        self.assertEqual(self.decode(limits=self.codec.Limits(2,observed.bytes_read)).events,self.events)
        for limits in (self.codec.Limits(1,100000),self.codec.Limits(2,observed.bytes_read-1),
                       self.codec.Limits(True,100000),self.codec.Limits(2,0)):
            with self.subTest(limits=limits),self.assertRaises(ValueError): self.decode(limits=limits)
        kind,raw=self.objects[self.head];self.objects[self.head]=(kind,raw+b'x'*4096)
        with self.assertRaisesRegex(ValueError,'byte bound'): self.decode()

    def test_duplicate_run_changed_policy_and_changed_event_binding_refuse(self):
        first=self.append()
        duplicate=copy.deepcopy(first['reservation'])
        duplicate.update(counter=2,build=self.contract.build_for_counter(2),
                         previous=self.contract.digest(first['reservation']),buildTimestamp='1002')
        with self.assertRaisesRegex(ValueError,'already consumes'):
            self.decode(self.make_commit(duplicate,self.head))
        changed=copy.deepcopy(first['reservation']);changed['build']='9999999999'
        with self.assertRaises(ValueError): self.decode(self.make_commit(changed,self.genesis))
        original=self.policy;self.policy=dict(self.policy,minimumTimestamp='1')
        with self.assertRaisesRegex(ValueError,'policy differs'): self.decode()
        self.policy=original

    def test_typed_checkpoint_and_flags_do_not_prove_provenance_or_freshness(self):
        proposal=self.append();checkpoint=self.checkpoint()
        with self.assertRaisesRegex(ValueError,'unknown'):
            self.confirm(proposal,checkpoint,self.verifier(checkpoint,available=False))
        for verdict in (True,False,{'verified':True}):
            with self.subTest(verdict=verdict),self.assertRaisesRegex(ValueError,'flags'):
                self.confirm(proposal,checkpoint,lambda *args:verdict)
        extra=dict(self.contract.expected_anchor(self.policy,self.events),fresh=True)
        flagged=self.codec.Checkpoint(self.head,self.codec.canonical(extra))
        with self.assertRaises(ValueError): self.confirm(proposal,flagged,lambda *args:None)
        oversized=self.codec.Checkpoint(self.head,b' '*65537)
        with self.assertRaisesRegex(ValueError,'bounded'): self.confirm(proposal,oversized,lambda *args:None)

    def test_an_unimplemented_typed_provider_cannot_become_a_successful_noop(self):
        class MissingProvider(self.codec.CheckpointVerifier):
            pass
        with self.assertRaises(TypeError): MissingProvider()

    def test_stale_checkpoint_joint_stale_snapshot_and_rollback_refuse(self):
        old_head=self.head;old_checkpoint=self.checkpoint()
        first=self.append();current_checkpoint=self.checkpoint()
        # Both old bytes and old hashes are valid. The separate fixture verifier
        # knows the current checkpoint/head; the codec alone cannot know freshness.
        self.assertEqual(self.decode(old_head).events,[])
        verify=self.verifier(current_checkpoint)
        with self.assertRaisesRegex(ValueError,'stale'): self.confirm(first,old_checkpoint,verify)
        with self.assertRaisesRegex(ValueError,'stale'): self.confirm(first,old_checkpoint,verify,old_head)
        with self.assertRaisesRegex(ValueError,'rollback/fork'): self.confirm(first,current_checkpoint,verify,old_head)

    def test_forked_authority_or_unknown_deletion_force_push_controls_refuse(self):
        first=self.append();checkpoint=self.checkpoint()
        fork=copy.deepcopy(first['reservation']);fork['run']['runId']='999'
        fork_head=self.make_commit(fork,self.genesis)
        with self.assertRaisesRegex(ValueError,'rollback/fork'):
            self.confirm(first,checkpoint,self.verifier(checkpoint),fork_head)
        with self.assertRaisesRegex(ValueError,'controls unknown'):
            self.confirm(first,checkpoint,self.verifier(checkpoint,available=False))

    def test_foreign_proposal_cannot_be_confirmed_from_valid_objects_and_checkpoint(self):
        first=self.append();checkpoint=self.checkpoint()
        forged=copy.deepcopy(first);forged['reservation']['run']['source']='b'*40
        with self.assertRaises(ValueError): self.confirm(forged,checkpoint,self.verifier(checkpoint))

    def test_decoded_data_copies_cannot_mutate_the_verified_input(self):
        first=self.append();checkpoint=self.checkpoint()
        def verify(candidate,history):
            self.verifier(checkpoint)(candidate,history)
            history.events[0]['run']['source']='b'*40
            history.anchor['head']='0'*64
        self.assertEqual(self.confirm(first,checkpoint,verify),first['reservation'])

    def pilot_replies(self):
        fixture=json.loads((ROOT/'os/tests/fixtures/mustang-numbering/pilot-error-replies.json').read_text())
        return [row['reply'] for row in fixture['records']]

    def reconcile(self,proposal,attempted,reply,checkpoint=None,verify=None,head=None):
        return self.codec.reconcile_ref_reply(self.policy,proposal,attempted,reply,head or self.head,
            self.get,self.limits,checkpoint or self.checkpoint(),verify)

    def test_recorded_pilot_errors_stay_generic_without_inventing_conflict_codes(self):
        fixture=json.loads((ROOT/'os/tests/fixtures/mustang-numbering/pilot-error-replies.json').read_text())
        self.assertTrue(fixture['mechanicsOnly'])
        self.assertEqual({row['case'] for row in fixture['records']},
                         {'stale-beforeOid','checkpoint-collision','mismatched-noop'})
        for reply in self.pilot_replies():
            self.assertEqual(self.codec.reply_observation(reply),'graphql-errors')
            self.assertTrue(reply['errors'])
            self.assertNotIn('type',reply['errors'][0])
        self.assertEqual(self.codec.reply_observation(None),'no-reply')
        self.assertEqual(self.codec.reply_observation({'data':{'updateRefs':{'clientMutationId':None}}}),
                         'success-shaped-reply')

    def test_unchanged_origin_is_unresolved_even_after_a_success_shaped_reply(self):
        checkpoint=self.checkpoint();old=self.head
        proposal=self.append();attempted=self.head
        before=copy.deepcopy(self.objects)
        replies=[*self.pilot_replies(),None,{'data':{'updateRefs':{'clientMutationId':None}}}]
        for reply in replies:
            with self.subTest(reply=self.codec.reply_observation(reply)):
                with self.assertRaises(self.codec.UnresolvedRefWrite) as caught:
                    self.reconcile(proposal,attempted,reply,checkpoint,self.verifier(checkpoint,current=old),old)
                self.assertFalse(caught.exception.retry_allowed)
                self.assertFalse(caught.exception.reallocation_allowed)
                self.assertEqual(caught.exception.attempted_oid,attempted)
                self.assertEqual(caught.exception.observed_oid,old)
        self.assertEqual(self.objects,before)

    def test_error_or_lost_reply_can_confirm_exact_commit_after_a_later_head(self):
        proposal=self.append();attempted=self.head
        self.append('102','1002');checkpoint=self.checkpoint()
        before=copy.deepcopy(self.objects)
        for reply in [*self.pilot_replies(),None,{'data':{'updateRefs':{}}}]:
            with self.subTest(reply=self.codec.reply_observation(reply)):
                event=self.reconcile(proposal,attempted,reply,checkpoint,self.verifier(checkpoint))
                self.assertEqual(event,proposal['reservation'])
        self.assertNotEqual(attempted,self.head)
        self.assertEqual(self.objects,before)
        self.assertEqual(len(self.events),2)

    def test_same_logical_event_under_a_different_git_oid_does_not_confirm_attempt(self):
        proposal=self.append();attempted=self.head
        _,raw=self.objects[attempted]
        changed=raw.replace(b'> 0 +0000',b'> 1 +0000',1)
        self.head=self.put('commit',changed)
        checkpoint=self.checkpoint()
        self.assertNotEqual(attempted,self.head)
        self.assertEqual(self.decode().events[0],proposal['reservation'])
        with self.assertRaises(self.codec.UnresolvedRefWrite):
            self.reconcile(proposal,attempted,self.pilot_replies()[0],checkpoint,self.verifier(checkpoint))

    def test_competing_event_cannot_release_or_reassign_the_attempted_counter(self):
        proposal=self.append();attempted=self.head
        competitor=copy.deepcopy(proposal['reservation']);competitor['run']['runId']='999'
        self.head=self.make_commit(competitor,self.genesis);self.events=[competitor]
        checkpoint=self.checkpoint();before=copy.deepcopy(self.objects)
        with self.assertRaises(self.codec.UnresolvedRefWrite) as caught:
            self.reconcile(proposal,attempted,self.pilot_replies()[0],checkpoint,self.verifier(checkpoint))
        self.assertFalse(caught.exception.reallocation_allowed)
        self.assertEqual(self.objects,before)
        self.assertEqual(len(self.events),1)

    def test_reconciliation_still_requires_independent_evidence_for_every_reply_kind(self):
        proposal=self.append();attempted=self.head;checkpoint=self.checkpoint()
        for reply in [*self.pilot_replies(),None,{'data':{'updateRefs':{}}}]:
            self.calls.clear()
            with self.assertRaisesRegex(ValueError,'verification unavailable'):
                self.reconcile(proposal,attempted,reply,checkpoint)
            self.assertEqual(self.calls,[])
        with self.assertRaisesRegex(ValueError,'unknown'):
            self.reconcile(proposal,attempted,None,checkpoint,self.verifier(checkpoint,available=False))

    def test_invalid_policy_and_proposal_do_not_become_known_response_conflicts(self):
        proposal=self.append();attempted=self.head;checkpoint=self.checkpoint()
        original=self.policy;self.policy=dict(original,minimumTimestamp='1')
        with self.assertRaisesRegex(ValueError,'policy differs'):
            self.reconcile(proposal,attempted,self.pilot_replies()[0],checkpoint,self.verifier(checkpoint))
        self.policy=original
        invalid=copy.deepcopy(proposal);invalid['reservation']['counter']=True
        with self.assertRaisesRegex(ValueError,'counter outside'):
            self.reconcile(invalid,attempted,None,checkpoint,self.verifier(checkpoint))

    def test_reconciliation_checks_git_oid_domain_and_original_prefix_floor(self):
        old=self.head;checkpoint=self.checkpoint()
        self.append();proposal=self.append('102','1002');attempted=self.head
        self.calls.clear()
        with self.assertRaisesRegex(ValueError,'separate'):
            self.reconcile(proposal,proposal['expected']['head'],None,
                           checkpoint,self.verifier(checkpoint,current=old),old)
        self.assertEqual(self.calls,[])
        with self.assertRaisesRegex(ValueError,'predates'):
            self.reconcile(proposal,attempted,self.pilot_replies()[0],checkpoint,
                           self.verifier(checkpoint,current=old),old)


if __name__=='__main__':
    unittest.main()
