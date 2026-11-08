"""Synthetic preparation and isolated-copy admission; no guest or executable runs."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'scripts'),str(Path(__file__).resolve().parent)]
import mustang_vendor_consumer as consumer
import test_mustang_preparation_inputs as host_fixture
import test_mustang_edge_gate as lane_fixture


class VendorConsumerTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)
        fixture=host_fixture.PreparationInputTest()
        self.source,self.storage,_out,self.host=fixture.host_fixture(self.home)
        self.storage.rename(self.home/'rose-actions');self.storage=self.home/'rose-actions'
        self.lane=lane_fixture.DedicatedPathsTest().profile(self.home)
        self.run=dict(runId='789',runAttempt='1',source='b'*40,
            workflow='Immortal-Protocols/RoseOS-1/'+self.lane['workflow']+'@refs/heads/main')
        identity=dict(builder=self.lane['vm'],diskUuid='fixture',run=dict(self.run,runId='123'))
        self.host['receipt']['identity']=copy.deepcopy(identity)
        self.host['stopObservation']['instanceId']=self.lane['vm']['instanceId']
        self.write_receipt(self.host,'hostdeps','hostdeps-receipt.json')
        deadline=time.monotonic()+30;adev=self.source/'vendor/adevtool'
        v=dict(schema='roseos.mustang-vendor-preparation-run.v1',identity=dict(identity,run=dict(self.run,runId='456')),
            phase='complete',success=True,sourceRoot=str(self.source),executionMode='pinned-typescript-source-loader',
            adevtoolTrackedWorktreeUnchanged=True,makeStarted=False,allocated=False,signingStarted=False,otaAdmitted=False,
            hostDependencies=dict(receiptSha256=self.host['receiptSha256']),
            factoryInput=dict(archiveRehashed=True,sha256=consumer.deps.readers.recipe()['factory']['sha256']),
            commands=[dict(processStarted=True,exitCode=0,argv=[]) for _ in range(3)],sourceLoader={})
        v['commands'][-1]['argv']=['vendor/adevtool/bin/run','generate-all','-d','mustang']
        for key,relative in (('vendorTree','vendor/google_devices/mustang'),
                             ('nodeModules','vendor/adevtool/node_modules'),('adevtoolSource','vendor/adevtool/src')):
            directory=self.source/relative;directory.mkdir(parents=True);(directory/'data').write_bytes(b'prepared bytes')
        (self.source/'vendor/google_devices/mustang/mustang.mk').write_bytes(b'product fixture')
        for name,relative in (('binRun','bin/run'),('packageJson','package.json'),('yarnLock','yarn.lock')):
            path=adev/relative;path.parent.mkdir(exist_ok=True);path.write_bytes(b'loader fixture')
            v['sourceLoader'][name]=consumer.deps.fingerprint(self.source,path,deadline)
        for key,relative in (('vendorTree','vendor/google_devices/mustang'),
                             ('nodeModules','vendor/adevtool/node_modules'),('adevtoolSource','vendor/adevtool/src')):
            v[key]=consumer.preparation.vendor.catalog(self.source,self.source/relative,deadline)
        v['productMakefile']=consumer.deps.fingerprint(self.source,self.source/'vendor/google_devices/mustang/mustang.mk',deadline)
        self.vendor=dict(schema='roseos.mustang-vendor-evidence.v1',receipt=v,
            workflowConclusion='success',cleanupConclusion='success',
            stopObservation=dict(status='TERMINATED',instanceId=self.lane['vm']['instanceId']))
        self.write_receipt(self.vendor,'vendor',consumer.preparation.vendor.RECEIPT)
        self.root=self.home/'checkout';(self.root/'os/build-only').mkdir(parents=True)
        for name in ('os/pinned-manifest.xml','os/build-only/mustang.json'):
            (self.root/name).write_bytes((ROOT/name).read_bytes())
        self.cfg=dict(dataFilesystemUuid='fixture',vendorEvidence='os/build-only/vendor.json',
                      hostDependenciesEvidence='os/build-only/host.json')
        self.write_evidence()
        self.candidate=self.storage/'mustang-prep-789-1/source'
        shutil.copytree(self.source,self.candidate,symlinks=True)
        self.lock=Path(self.lane['lockPath']);self.lock.touch();self.lock.chmod(0o444)

    def write_receipt(self,record,kind,name):
        run=record['receipt']['identity']['run'];record.update(runId=run['runId'],source=run['source'])
        directory=self.storage/('mustang-'+kind+'-'+run['runId']+'-1');directory.mkdir(exist_ok=True)
        raw=(json.dumps(record['receipt'],sort_keys=True)+'\n').encode();(directory/name).write_bytes(raw)
        record['receiptSha256']=hashlib.sha256(raw).hexdigest()

    def write_evidence(self):
        (self.root/self.cfg['vendorEvidence']).write_text(json.dumps(self.vendor))
        (self.root/self.cfg['hostDependenciesEvidence']).write_text(json.dumps(self.host))

    def consume(self):
        return consumer.consume(self.lane,self.cfg,self.candidate,self.run,root=self.root)

    def test_original_and_isolated_copy_are_rehashed_without_generation(self):
        result=self.consume()
        self.assertTrue(result['original']['generatedTreesRehashed'])
        self.assertTrue(result['copy']['sourceLoaderRehashed'])
        self.assertTrue(result['copiedHostDependencies']['distributionRehashed'])
        for field in ('generationStarted','downloadStarted','hashBypassPatchApplied','otaAdmitted','pristineRuntimeAttestation'):
            self.assertIs(result[field],False)

    def test_tampered_original_or_copy_vendor_and_host_bytes_refuse(self):
        for source in (self.source,self.candidate):
            for relative in ('vendor/google_devices/mustang/data','vendor/adevtool/node_modules/data',
                             'vendor/adevtool/bin/run','out_adevtool_deps/host/linux-x86/framework/support.jar'):
                path=source/relative;original=path.read_bytes();path.write_bytes(b'tampered')
                with self.subTest(source=source,relative=relative),self.assertRaises(ValueError):self.consume()
                path.write_bytes(original)

    def test_wrong_attempt_missing_lock_and_changed_disk_receipt_refuse(self):
        with self.assertRaisesRegex(ValueError,'fresh dedicated source copy'):
            consumer.consume(self.lane,self.cfg,self.source,self.run,root=self.root)
        self.lock.unlink()
        with self.assertRaisesRegex(ValueError,'canonical lock'):self.consume()
        self.lock.touch()
        path=self.storage/'mustang-vendor-456-1'/consumer.preparation.vendor.RECEIPT
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'uploaded run evidence'):self.consume()

    def test_binding_rejects_failed_generation_wrong_identity_and_wrong_host_producer(self):
        for field,value in (('makeStarted',True),('phase','failed'),('adevtoolTrackedWorktreeUnchanged',False)):
            original=self.vendor['receipt'][field];self.vendor['receipt'][field]=value;self.write_evidence()
            with self.subTest(field=field),self.assertRaises(ValueError):consumer.binding(self.lane,self.cfg,self.root)
            self.vendor['receipt'][field]=original
        self.vendor['receipt']['hostDependencies']['receiptSha256']='f'*64;self.write_evidence()
        with self.assertRaises(ValueError):consumer.binding(self.lane,self.cfg,self.root)


if __name__=='__main__':unittest.main()
