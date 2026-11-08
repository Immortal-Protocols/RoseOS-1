"""Synthetic input receipts/distributions only; no guest, network or processes."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import mustang_preparation_inputs as inputs
import mustang_hostdeps as deps


class PreparationInputTest(unittest.TestCase):
    def identity(self):
        return {'builder': {'instanceId':'123','instance':'synthetic'},'diskUuid':'fixture',
                'run': {'runId':'123','runAttempt':'1','source':'a'*40}}

    def record(self, storage, kind, name, payload, schema):
        run = payload['identity']['run']
        attempt = storage/('mustang-'+kind+'-'+run['runId']+'-'+run['runAttempt']);attempt.mkdir()
        deps.receipt(attempt/name, payload)
        return {'schema':schema,'runId':run['runId'],'source':run['source'],
                'workflowConclusion':'success','cleanupConclusion':'success','receipt':payload,
                'receiptSha256':hashlib.sha256((attempt/name).read_bytes()).hexdigest(),
                'stopObservation':{'status':'TERMINATED','instanceId':'123'}}

    def host_fixture(self, directory):
        source = Path(directory)/'source';source.mkdir();storage = Path(directory)/'storage';storage.mkdir()
        spec = deps.recipe();out = source/spec['outDir'];binary = out/'host/linux-x86/bin';binary.mkdir(parents=True)
        for tool in spec['tools']:
            path = binary/tool;path.write_bytes(b'synthetic executable');path.chmod(0o755)
        framework = out/'host/linux-x86/framework';framework.mkdir();(framework/'support.jar').write_bytes(b'jar fixture')
        marker = out/'adevtool_revision';marker.write_text(spec['adevtoolRevision']+'\n')
        payload = {'schema':'roseos.mustang-hostdeps-run.v1','identity':self.identity(),'success':True,
                   'makeExitCode':0,'recipe':spec,'sourceRoot':str(source),'outDir':str(out),
                   'manifestSha256':hashlib.sha256((ROOT/'os/pinned-manifest.xml').read_bytes()).hexdigest(),
                   'source':{'projectCount':1057,'check':'project-heads-and-git-status'},
                   'revisionMarker':deps.fingerprint(source, marker, time.monotonic()+30),
                   'distribution':deps.inventory(source, out, time.monotonic()+30)}
        record = self.record(storage, 'hostdeps','hostdeps-receipt.json', payload,'roseos.mustang-hostdeps-evidence.v1')
        return source, storage, out, record

    def test_successful_host_distribution_is_fully_rehashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, storage, out, record = self.host_fixture(tmp)
            observed = inputs.host_dependencies(source, storage, record, self.identity())
            self.assertTrue(observed['distributionRehashed'])
            self.assertFalse(observed['otaAdmitted'])
            self.assertEqual(observed['distributionEntries'],15)
            (out/'host/linux-x86/framework/support.jar').write_bytes(b'changed bytes')
            with self.assertRaisesRegex(ValueError, 'distribution differs'):
                inputs.host_dependencies(source, storage, record, self.identity())

    def test_revision_marker_alone_is_not_a_successful_build_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, storage, out, record = self.host_fixture(tmp)
            (out/'host/linux-x86/bin/aapt2').unlink()
            with self.assertRaises(ValueError):inputs.host_dependencies(source, storage, record, self.identity())

    def test_changed_receipt_wrong_builder_and_failed_make_are_rejected(self):
        for change in ('receipt','builder','make'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                source, storage, _out, record = self.host_fixture(tmp)
                identity = self.identity()
                if change == 'receipt':(storage/'mustang-hostdeps-123-1/hostdeps-receipt.json').write_text('changed')
                if change == 'builder':identity['builder']['instanceId']='other'
                if change == 'make':
                    record['receipt']['makeExitCode']=False
                    path = storage/'mustang-hostdeps-123-1/hostdeps-receipt.json'
                    path.write_text(json.dumps(record['receipt']));record['receiptSha256']=hashlib.sha256(path.read_bytes()).hexdigest()
                with self.assertRaises(ValueError):inputs.host_dependencies(source, storage, record, identity)

    def test_factory_archive_is_rehashed_instead_of_trusting_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp);directory = storage/inputs.factory.DIRECTORY;directory.mkdir()
            data=b'stock archive fixture';pin={'file':'factory.zip','url':'https://dl.google.com/dl/android/aosp/factory.zip',
                'sizeBytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            archive=directory/pin['file'];archive.write_bytes(data)
            payload={'schema':'roseos.mustang-factory-input.v1','identity':self.identity(),'success':True,
                'download':{'verified':True,'path':str(archive),'url':pin['url'],'sha256':pin['sha256'],'downloadedBytes':len(data)},
                'readback':{'matchesPinnedFactory':True}}
            record=self.record(storage,'factory',inputs.factory.RECEIPT,payload,'roseos.mustang-factory-input-evidence.v1')
            with patch.object(deps.readers,'recipe',return_value={'factory':pin}):
                self.assertTrue(inputs.factory_archive(storage,record,self.identity())['archiveRehashed'])
                archive.write_bytes(b'x'*len(data))
                with self.assertRaisesRegex(ValueError, 'actual factory input differs'):
                    inputs.factory_archive(storage,record,self.identity())

    def test_evidence_reader_rejects_failed_runs_and_path_tricks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/'os/build-only';directory.mkdir(parents=True)
            good={'schema':'fixture','workflowConclusion':'success','cleanupConclusion':'success',
                  'stopObservation':{'status':'TERMINATED'},'receiptSha256':'a'*64}
            (directory/'evidence.json').write_text(json.dumps(good))
            self.assertEqual(inputs.evidence('os/build-only/evidence.json','fixture',root),good)
            for path in ('os/build-only/./evidence.json','os/build-only/../evidence.json','/os/build-only/evidence.json',
                         'os//build-only/evidence.json','elsewhere/evidence.json'):
                with self.assertRaises(ValueError):inputs.evidence(path,'fixture',root)
            good['workflowConclusion']='failure';(directory/'evidence.json').write_text(json.dumps(good))
            with self.assertRaises(ValueError):inputs.evidence('os/build-only/evidence.json','fixture',root)


if __name__ == '__main__':unittest.main()
