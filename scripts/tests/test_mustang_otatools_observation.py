"""Fixed-attempt private classification and read-only observation tests."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'scripts/tests'))
import mustang_otatools_observation as observation
import os_release_shape as shape


class ObservationTest(unittest.TestCase):
    def test_binding_excludes_other_attempts_and_unverified_receipts(self):
        actual=observation.binding();self.assertEqual(actual['runId'],'42524960701')
        for field,value in (('runId','42492662025'),('source','a'*40),('receiptSha256','0'*64),
                            ('workflowConclusion','success'),('cleanupConclusion','failure')):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);path=root/observation.EVIDENCE;path.parent.mkdir(parents=True)
                changed=copy.deepcopy(actual);changed[field]=value;path.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):observation.binding(root)

    def test_classifier_exports_fixed_categories_and_source_locations_without_raw_text(self):
        secret=b'synthetic-private-diagnostic-value'
        data=b'build/make/core/version_util.mk:21: error: '+secret+b'\n/tmp/private:3: error: '+secret+b'\nAssertionError: Mustang lunch identity differs\n'
        result=observation.classify(data)
        self.assertEqual(result['sourceLocations'],[{'path':'build/make/core/version_util.mk','line':21}])
        self.assertEqual(result['categories'],['lunch-identity-assertion','make-error'])
        self.assertFalse(result['rawTextExported']);self.assertNotIn(secret.decode(),json.dumps(result))
        self.assertEqual(result['logSha256'],hashlib.sha256(data).hexdigest())

    def test_classifier_bounds_lines_bytes_locations_and_refuses_traversal(self):
        result=observation.classify(b'build/../private/file.mk:2: error: secret\n'+b'x'*4097+b'\n')
        self.assertEqual(result['sourceLocations'],[]);self.assertEqual(result['longLinesOmitted'],1)
        many=b''.join(('build/core/file.mk:'+str(i)+': error: private\n').encode() for i in range(40))
        self.assertEqual(len(observation.classify(many)['sourceLocations']),32)
        with patch.object(observation.deps,'LOG_BYTES',1),self.assertRaises(ValueError):observation.classify(b'xx')

    def fixture(self,tmp):
        source=Path(tmp)/'source';source.mkdir();storage=Path(tmp)/'rose-actions';storage.mkdir()
        old=storage/'mustang-otatools-42524960701-1';old.mkdir()
        record=copy.deepcopy(observation.binding());r=record['receipt'];r['sourceRoot']=str(source)
        r['outDir']=str(source/observation.prep.OUT)
        raw=json.dumps(r,sort_keys=True).encode()+b'\n';(old/observation.prep.RECEIPT).write_bytes(raw)
        record['receiptSha256']=hashlib.sha256(raw).hexdigest()
        identity=dict(r['identity'],run=dict(r['identity']['run'],runId='456'))
        return source,storage,old,record,identity

    def test_actual_files_are_observed_without_moving_or_rebuilding(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,storage,old,record,identity=self.fixture(tmp)
            lunch=json.dumps(observation.EXPECTED).encode();(old/'lunch-identity.json').write_bytes(lunch)
            log=b'build/make/core/main.mk:4: error: synthetic-private-diagnostic\n'
            (old/'make.log').write_bytes(log)
            output=source/observation.prep.OUT;output.mkdir();(output/'partial').write_bytes(b'preserved bytes')
            os.mkfifo(output/'status-pipe')
            before={p.name:(p.stat().st_ino,p.read_bytes()) for p in old.iterdir()}
            with patch.object(subprocess,'run',side_effect=AssertionError('execution')):
                result=observation.observe(source,storage,record,identity)
            self.assertTrue(result['lunch']['matchesExpected']);self.assertTrue(result['output']['complete'])
            self.assertEqual(result['output']['specialEntries'][0]['type'],'fifo')
            self.assertEqual(result['privateDiagnostic']['sourceLocations'],[{'path':'build/make/core/main.mk','line':4}])
            self.assertFalse(result['makeStarted']);self.assertFalse(result['outputMoved'])
            self.assertEqual(before,{p.name:(p.stat().st_ino,p.read_bytes()) for p in old.iterdir()})
            self.assertEqual((output/'partial').read_bytes(),b'preserved bytes')

    def test_absence_wrong_lunch_and_unexpected_values_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,storage,old,record,identity=self.fixture(tmp)
            result=observation.observe(source,storage,record,identity)
            self.assertEqual(result['lunch']['state'],'absent');self.assertEqual(result['output']['state'],'absent')
            wrong=dict(observation.EXPECTED,TARGET_PRODUCT='private-value-not-exported',TARGET_DEVICE='frankel')
            (old/'lunch-identity.json').write_text(json.dumps(wrong))
            result=observation.observe(source,storage,record,identity)
            self.assertFalse(result['lunch']['matchesExpected'])
            self.assertEqual(result['lunch']['fields']['TARGET_PRODUCT'],'unexpected')
            self.assertEqual(result['lunch']['fields']['TARGET_DEVICE'],'frankel')
            self.assertNotIn('private-value-not-exported',json.dumps(result))

    def test_changed_receipt_other_disk_and_linked_log_refuse(self):
        for change in ('receipt','disk','symlink','fifo'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as tmp:
                source,storage,old,record,identity=self.fixture(tmp)
                if change=='receipt':(old/observation.prep.RECEIPT).write_text('{}')
                if change=='disk':identity['diskUuid']='other'
                if change=='symlink':(old/'make.log').symlink_to('/etc/passwd')
                if change=='fifo':os.mkfifo(old/'make.log')
                with self.assertRaises((ValueError,observation.deps.readers._Refused)):
                    observation.observe(source,storage,record,identity)

    def test_plan_never_spawns_or_observes_and_workflow_is_exclusive_dry_run(self):
        with patch.object(sys,'argv',['observe']),patch.object(observation,'execute',side_effect=AssertionError('executed')):
            self.assertEqual(observation.main(),4)
        jobs=shape.jobs((ROOT/'.github/workflows/mustang-ota-dedicated.yml').read_text())
        self.assertIn('if: inputs.dry_run\n      run: python3 scripts/mustang_otatools_observation.py --check-binding',jobs['guard'])
        self.assertIn('if: inputs.dry_run\n      timeout-minutes: 2\n      run: python3 scripts/mustang_otatools_observation.py --execute',jobs['builder'])
        self.assertIn('otatools-failure-observation.json',jobs['builder'])

    def test_only_exclusive_fresh_dry_run_can_reach_guest_identity(self):
        env={'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REF':'refs/heads/main',
             'GITHUB_WORKFLOW_REF':'Immortal-Protocols/RoseOS-1/.github/workflows/mustang-ota-dedicated.yml@refs/heads/main',
             'GITHUB_REPOSITORY_ID':'1428996648','GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1',
             'GITHUB_SHA':'a'*40,'GITHUB_JOB':'builder','MUSTANG_DRY_RUN':'true'}
        host=SimpleNamespace(host_identity=lambda *_args,**_kwargs:(_ for _ in ()).throw(AssertionError('identity-check')))
        with patch.object(observation.pwd,'getpwuid',return_value=SimpleNamespace(pw_name='mustang-runner')), \
                patch.object(observation.mustang_ota,'module',return_value=host):
            with patch.object(observation.deps,'read_event',return_value={'inputs':{'dry_run':True}}):
                with self.assertRaisesRegex(AssertionError,'identity-check'):observation.execute(env)
            for flag in observation.prep.FLAGS-{'dry_run'} | {'unknown'}:
                with self.subTest(flag=flag),patch.object(observation.deps,'read_event',return_value={'inputs':{'dry_run':True,flag:True}}):
                    with self.assertRaises(ValueError):observation.execute(env)


if __name__=='__main__':unittest.main()
