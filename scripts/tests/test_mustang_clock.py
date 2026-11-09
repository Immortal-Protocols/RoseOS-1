"""Actual local reservations with synthetic Actions/public-channel inputs; no live run."""
import base64
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts'), str(Path(__file__).resolve().parent)]
import mustang_clock as clock
import test_mustang_edge_gate as edge_fixtures
import test_mustang_full_delivery as full_fixtures


class ClockAllocationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.home = self.root/'host'; self.home.mkdir()
        self.lane = edge_fixtures.DedicatedPathsTest().profile(self.home)
        (self.home/'rose-actions').mkdir(); (self.home/'mustang.lock').touch()
        for relative in (clock.POLICY, clock.PROOF, 'os/build-only/mustang-allocation-state-20261107.json',
                'os/build-only/mustang-updater-route.json', 'scripts/mustang-allocate.py', 'scripts/release-allocate.py'):
            target = self.root/relative; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT/relative).read_bytes())
        self.cfg = dict(clockNumbering=clock.POLICY, dataFilesystemUuid='synthetic-disk',
            allocationState='os/build-only/mustang-allocation-state-20261107.json')
        self.policy = clock.policy(self.lane, self.cfg, self.root)
        self.source = 'a'*40
        self.initial = (self.root/self.cfg['allocationState']).read_bytes()
        self.env = self.environment('123', self.policy['initializationRunNumber'], '2026-11-09T04:10:00Z')
        self.channel = b'2026110401 1793755347 mustang stable\n'
        self.rows = [dict(tag_name='mustang-2026110401',draft=False,prerelease=False,assets=[dict(
            id=1,name='mustang-stable',size=len(self.channel),digest='sha256:'+hashlib.sha256(self.channel).hexdigest(),
            browser_download_url='https://github.com/Immortal-Protocols/pixel-ota/releases/download/mustang-2026110401/mustang-stable')])]

    def environment(self, run, number, created):
        env = dict(GITHUB_RUN_ID=run,GITHUB_RUN_ATTEMPT='1',GITHUB_SHA=self.source,
            GITHUB_WORKFLOW_REF='Immortal-Protocols/RoseOS-1/'+clock.WORKFLOW+'@refs/heads/main')
        actual = dict(id=int(run),run_attempt=1,workflow_id=408460629,run_number=number,
            head_sha=self.source,head_branch='main',path=clock.WORKFLOW,event='workflow_dispatch',
            status='in_progress',conclusion=None,repository=dict(id=1428996648),head_repository=dict(id=1428996648),
            created_at=created)
        result = clock.actions_context(actual,env,self.policy)
        env['MUSTANG_CLOCK_CONTEXT'] = base64.b64encode(clock.canonical(result)).decode()
        return env

    def reserve(self, env=None, now='2026-11-09T04:20:00Z'):
        return clock.reserve(self.lane,self.cfg,env or self.env,root=self.root,
            clock=lambda:clock.utc(now).timestamp(),fetch=lambda url:copy.deepcopy(self.rows),
            fetch_bytes=lambda url:self.channel)

    @property
    def journal(self):
        return self.home/'rose-actions'/clock.JOURNAL

    def test_actual_local_reservation_keeps_lost_legacy_unknown_and_binds_compiled_time(self):
        record = self.reserve()
        self.assertEqual(record['build'],'2611090400')
        self.assertEqual((self.root/self.cfg['allocationState']).read_bytes(),self.initial)
        self.assertEqual(clock.read(self.journal/'reservations','2611090400.json'),record)
        self.assertEqual(clock.read(self.journal,'head.json')['head'],clock.digest(record))
        self.assertFalse(record['observed']['clock']['legacyAllocationKnown'])
        bundle = dict(clockAllocation=record,build=record['build'],timestamp=str(int(clock.utc(record['reservedAt']).timestamp())),
            sourceSha=self.source,runId='123',runAttempt='1')
        clock.require_bundle(bundle,self.lane,self.cfg,self.root)
        with self.assertRaisesRegex(ValueError,'compiled timestamp'):
            clock.require_bundle(dict(bundle,timestamp='1793755348'),self.lane,self.cfg,self.root)

    def test_one_attempt_per_dispatch_hour_and_next_hour_increases(self):
        first = self.reserve()
        same = self.environment('124',self.policy['initializationRunNumber']+1,'2026-11-09T04:25:00Z')
        with self.assertRaisesRegex(ValueError,'hour already consumed'):
            self.reserve(same,now='2026-11-09T04:30:00Z')
        later = self.environment('125',self.policy['initializationRunNumber']+2,'2026-11-09T05:10:00Z')
        second = self.reserve(later,now='2026-11-09T05:20:00Z')
        self.assertGreater(second['build'],first['build'])
        self.assertEqual(second['observed']['clock']['previous'],clock.digest(first))
        self.assertEqual(clock.read(self.journal,'head.json')['count'],2)

    def test_loss_of_whole_ledger_does_not_reinitialize_on_later_run(self):
        self.reserve(); shutil.rmtree(self.journal)
        later = self.environment('124',self.policy['initializationRunNumber']+1,'2026-11-09T05:10:00Z')
        with self.assertRaisesRegex(ValueError,'recovery required'):
            self.reserve(later,now='2026-11-09T05:20:00Z')
        self.assertFalse(self.journal.exists())

    def test_missing_head_and_orphaned_reservation_refuse_without_new_id(self):
        self.reserve(); (self.journal/'head.json').unlink()
        later = self.environment('124',self.policy['initializationRunNumber']+1,'2026-11-09T05:10:00Z')
        with self.assertRaises((ValueError,OSError,clock.readers._Refused)):
            self.reserve(later,now='2026-11-09T05:20:00Z')
        self.assertEqual(len(list((self.journal/'reservations').iterdir())),1)

    def test_uncertain_head_write_preserves_consumption_and_never_retries(self):
        original = clock.new_file
        def fail(directory,name,value):
            if name=='next-head.json':raise OSError('synthetic interruption after durable reservation')
            return original(directory,name,value)
        with patch.object(clock,'new_file',side_effect=fail):
            with self.assertRaises(OSError):self.reserve()
        self.assertTrue((self.journal/'reservations/2611090400.json').is_file())
        later = self.environment('124',self.policy['initializationRunNumber']+1,'2026-11-09T05:10:00Z')
        with self.assertRaisesRegex(ValueError,'consumption uncertain'):
            self.reserve(later,now='2026-11-09T05:20:00Z')

    def test_clock_rollback_known_future_id_and_rerun_are_refused(self):
        self.reserve()
        earlier = self.environment('124',self.policy['initializationRunNumber']+1,'2026-11-09T03:10:00Z')
        with self.assertRaisesRegex(ValueError,'clock behind'):
            self.reserve(earlier,now='2026-11-09T04:30:00Z')
        with self.assertRaises(ValueError):self.reserve(dict(self.env,GITHUB_RUN_ATTEMPT='2'))
        later = self.environment('125',self.policy['initializationRunNumber']+2,'2026-11-09T05:10:00Z')
        self.rows.append(dict(tag_name='mustang-9999123199',draft=True,prerelease=False,assets=[]))
        with self.assertRaisesRegex(ValueError,'clock behind'):
            self.reserve(later,now='2026-11-09T05:20:00Z')

    def test_corrupt_public_metadata_and_future_public_timestamp_stop_reservation(self):
        self.channel = b'corrupt'
        with self.assertRaisesRegex(ValueError,'digest differs'):self.reserve()
        self.assertFalse(self.journal.exists())
        future = str(int(clock.utc('2026-11-09T04:21:00Z').timestamp()))
        self.channel = ('2026110401 '+future+' mustang stable\n').encode()
        asset = self.rows[0]['assets'][0]; asset['size']=len(self.channel)
        asset['digest']='sha256:'+hashlib.sha256(self.channel).hexdigest()
        with self.assertRaisesRegex(ValueError,'timestamp is not above'):self.reserve()
        self.assertFalse(list((self.journal/'reservations').iterdir()))

    def test_linked_journal_state_is_refused(self):
        self.reserve(); head=self.journal/'head.json'; raw=head.read_bytes(); head.unlink()
        outside=self.root/'outside.json';outside.write_bytes(raw);head.symlink_to(outside)
        later = self.environment('124',self.policy['initializationRunNumber']+1,'2026-11-09T05:10:00Z')
        with self.assertRaises((ValueError,OSError,clock.readers._Refused)):
            self.reserve(later,now='2026-11-09T05:20:00Z')
        self.assertEqual(outside.read_bytes(),raw)

    def test_legacy_generator_cannot_produce_reserved_suffix_and_clock_rollovers_order(self):
        namespace={'Refused':ValueError};code=(ROOT/'os/tests/fixtures/mustang-numbering/legacy-next-build.excerpt').read_text()
        exec(compile(code,'captured-legacy-generator','exec'),namespace)
        for year in (1000,2026,2611,9999):
            now=dt.datetime(year,11,9,tzinfo=dt.timezone.utc)
            for sequence in range(1,100):
                old=[now.strftime('%Y%m%d')+f'{n:02d}' for n in range(1,sequence)]
                self.assertNotEqual(namespace['next_build'](old,now,99)[-2:],'00')
        points=['2026-11-09T23:00:00Z','2026-11-10T00:00:00Z','2026-12-01T00:00:00Z','2027-01-01T00:00:00Z']
        values=[clock.build_for_hour(clock.utc(p)) for p in points]
        self.assertEqual(values,sorted(set(values)))
        with self.assertRaises(ValueError):clock.build_for_hour(clock.utc('2100-01-01T00:00:00Z'))

    def test_foreign_actions_identity_and_unapproved_initialization_number_refuse(self):
        context=json.loads(base64.b64decode(self.env['MUSTANG_CLOCK_CONTEXT']))
        for field,value in (('source','b'*40),('runId','999'),('workflowId',9)):
            changed=dict(context,**{field:value})
            bad=dict(self.env,MUSTANG_CLOCK_CONTEXT=base64.b64encode(clock.canonical(changed)).decode())
            with self.assertRaises(ValueError):self.reserve(bad)
        later=self.environment('124',self.policy['initializationRunNumber']+1,'2026-11-09T05:10:00Z')
        with self.assertRaisesRegex(ValueError,'recovery required'):
            self.reserve(later,now='2026-11-09T05:20:00Z')

    def test_dedicated_guard_selects_disjoint_series_without_claiming_legacy_reconciliation(self):
        fixture=edge_fixtures.DedicatedRuntimeTest('test_actual_evidence_guard_allows_private_and_refuses_stale_or_unbound')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        for relative in (clock.POLICY,clock.PROOF):
            target=fixture.root/relative;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes((ROOT/relative).read_bytes())
        state=fixture.root/fixture.cfg['allocationState'];state.write_bytes(self.initial)
        cfg=dict(fixture.cfg,clockNumbering=clock.POLICY)
        admitted=fixture.admitted(cfg)
        self.assertEqual(admitted,cfg)
        self.assertNotIn('consumedBuilds',admitted)
        self.assertTrue(json.loads((fixture.root/cfg['allocationState']).read_text())['unknown'])
        with self.assertRaisesRegex(ValueError,'allocation state remains unresolved'):
            fixture.admitted(fixture.cfg)
        self.assertEqual(state.read_bytes(),self.initial)

    def test_actual_local_allocation_reaches_same_run_hosted_authentication(self):
        fixture=full_fixtures.FullDeliveryTest('test_full_bundle_contains_measured_tools_pinned_jdk_and_source_receipts')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        bundle,out=fixture.bundle_fixture()
        cfg=json.loads((fixture.root/'os/build-only/mustang-runtime.json').read_text())
        cfg.update(clockNumbering=clock.POLICY,allocationState=self.cfg['allocationState'])
        for relative in (clock.POLICY,clock.PROOF,self.cfg['allocationState'],
                'scripts/mustang-allocate.py','scripts/release-allocate.py','os/build-only/mustang-updater-route.json'):
            target=fixture.root/relative;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes((ROOT/relative).read_bytes())
        (fixture.root/'os/build-only/mustang-runtime.json').write_bytes(clock.canonical(cfg))
        actual,jobs=fixture.metadata();actual.update(workflow_id=408460629,run_number=35,created_at='2026-11-09T04:10:00Z')
        env=dict(fixture.env)
        ctx=clock.actions_context(actual,env,self.policy)
        env['MUSTANG_CLOCK_CONTEXT']=base64.b64encode(clock.canonical(ctx)).decode()
        record=clock.reserve(fixture.lane,cfg,env,root=fixture.root,
            clock=lambda:clock.utc('2026-11-09T04:20:00Z').timestamp(),
            fetch=lambda url:copy.deepcopy(self.rows),fetch_bytes=lambda url:self.channel)
        bundle.update(clockAllocation=record,build=record['build'],timestamp=str(int(clock.utc(record['reservedAt']).timestamp())))
        unsigned=out/bundle['unsigned']['file']
        binding=edge_fixtures.gate.binding(bundle['sourceSha'],bundle['sourceSha'],bundle['build'],bundle['timestamp'],'999','1','999','1')
        bundle['unsignedRaisedEdge']=edge_fixtures.gate.inspect(unsigned,'unsigned',binding,edge_fixtures.descriptor(unsigned))
        (out/'mustang-signing-bundle.json').write_bytes(clock.canonical(bundle))
        env.update(GITHUB_JOB='signer',EXPECTED_FULL_BUNDLE_SHA256=full_fixtures.hosted.sha256_file(out/'mustang-signing-bundle.json'))
        def api(path):return copy.deepcopy(jobs if '/attempts/1/jobs?' in path else actual)
        with patch.object(full_fixtures.collector.mustang_lane,'configuration',return_value=fixture.lane):
            admitted=full_fixtures.hosted.authenticate_full_run(bundle,env,api=api,root=fixture.root)
            self.assertTrue(admitted['actionsRunIdentityVerified'])
            actual['run_number']=36
            with self.assertRaisesRegex(ValueError,'authenticated Actions run'):
                full_fixtures.hosted.authenticate_full_run(bundle,env,api=api,root=fixture.root)


if __name__=='__main__':unittest.main()
