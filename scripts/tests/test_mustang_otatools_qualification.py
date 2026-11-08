"""Synthetic ZIPs and receipt mutations; no native executables, guests or network."""
import copy
import hashlib
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import mustang_otatools_qualification as qualification


class QualificationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.reference=qualification.EVIDENCE
        for name in (self.reference,'os/build-only/mustang-lane.json'):
            target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes((ROOT/name).read_bytes())
        self.original=json.loads((self.root/self.reference).read_text())
        self.record=copy.deepcopy(self.original)
        self.archive=self.root/'fixture.zip'

    def write(self,*,rehash=False):
        if rehash:
            self.record['receiptSha256']=qualification.canonical_sha(self.record['receipt'])
            independent=self.record['independentReadback']
            independent['receipt']['nativeReceiptSha256']=self.record['receiptSha256']
            independent['receiptSha256']=qualification.canonical_sha(independent['receipt'])
        (self.root/self.reference).write_text(json.dumps(self.record))

    def package(self,extra=()):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',UserWarning)
            with zipfile.ZipFile(self.archive,'w',compression=zipfile.ZIP_STORED) as archive:
                for name,mode,payload in [(n,stat.S_IFREG|0o755,b'fixture bytes; never execute')
                                           for n in qualification.REQUIRED]+list(extra):
                    info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=mode<<16
                    archive.writestr(info,payload)
        self.bind_archive()

    def bind_archive(self):
        data=self.archive.read_bytes();sha=hashlib.sha256(data).hexdigest()
        self.record['receipt']['package'].update(sha256=sha,sizeBytes=len(data))
        self.record['independentReadback']['receipt'].update(packageSha256=sha,packageBytes=len(data))
        self.write(rehash=True)

    def inspect(self):
        return qualification.inspect_package(self.archive,self.reference,root=self.root)

    def test_real_committed_qualification_is_bound_without_package_execution_or_admission(self):
        value=qualification.binding(root=ROOT)
        self.assertEqual(value['run']['runId'],'42531345133')
        self.assertEqual(value['packageBytes'],536801287)
        self.assertEqual(value['packageSha256'],'9f0f1c8b906641cf262526fb8877adf5f711504c2ab6d508c437bd1005c1cc32')
        for key in ('packageBytesReadThisInvocation','packageExecuted','signerPinsAdmitted','otaAdmitted','pristineRuntimeAttestation'):
            self.assertIs(value[key],False)

    def test_receipt_mutations_cannot_survive_the_raw_encoding_hash(self):
        for field in ('receipt','readback'):
            self.record=copy.deepcopy(self.original)
            if field=='receipt':self.record['receipt']['elapsedSeconds']+=1
            else:self.record['independentReadback']['receipt']['extra']='tampered'
            self.write()
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'receipt digest differs'):
                qualification.binding(root=self.root)

    def test_wrong_vm_disk_run_scope_lunch_and_make_are_rejected(self):
        mutations=[lambda r:r['receipt']['identity']['builder'].update(instanceId='999'),
            lambda r:r['receipt']['identity'].update(diskUuid='other'),
            lambda r:r['receipt']['identity']['run'].update(source='f'*40),
            lambda r:r['receipt']['identity']['run'].update(runAttempt='2'),
            lambda r:r['receipt']['identity']['run'].update(workflow='foreign workflow'),
            lambda r:r['stopObservation'].update(instanceId='999'),
            lambda r:r['receipt'].update(makeStarted=False),lambda r:r['receipt'].update(makeExitCode=False),
            lambda r:r['receipt'].update(goals=['target-files-package']),
            lambda r:r['receipt']['actualLunchIdentity'].update(TARGET_RELEASE='trunk_staging'),
            lambda r:r['receipt'].update(signingStarted=True),lambda r:r.update(signerPinsAdmitted=True)]
        for i,mutate in enumerate(mutations):
            self.record=copy.deepcopy(self.original);mutate(self.record);self.write(rehash=True)
            with self.subTest(mutation=i),self.assertRaises(ValueError):qualification.binding(root=self.root)

    def test_same_run_independent_readback_is_required(self):
        for change in (dict(packageBytes=True),dict(packageBytes=42),dict(packageSha256='f'*64),
                       dict(byteReadbackMatches=False),dict(packageExecuted=True),dict(otaAdmitted=True),
                       dict(run=dict(self.original['independentReadback']['receipt']['run'],runId='999'))):
            self.record=copy.deepcopy(self.original);self.record['independentReadback']['receipt'].update(change)
            self.write(rehash=True)
            with self.subTest(change=change),self.assertRaises(ValueError):qualification.binding(root=self.root)

    def test_failed_workflow_foreign_artifact_and_unsafe_evidence_path_are_rejected(self):
        for change in ('workflow','artifact-name','artifact-id'):
            self.record=copy.deepcopy(self.original)
            if change=='workflow':self.record['workflowConclusion']='failure'
            if change=='artifact-name':self.record['packageArtifact']['name']='other package'
            if change=='artifact-id':self.record['packageArtifact']['id']=self.record['artifact']['id']
            self.write()
            with self.subTest(change=change),self.assertRaises(ValueError):qualification.binding(root=self.root)
        with self.assertRaises(ValueError):qualification.binding('os/build-only/../qualification.json',root=self.root)

    def test_complete_archive_is_hashed_and_inventoried_without_extraction(self):
        self.package([('lib/',stat.S_IFDIR|0o755,b''),('lib/support.jar',stat.S_IFREG|0o644,b'jar fixture')])
        before={str(p.relative_to(self.root)) for p in self.root.rglob('*')}
        result=self.inspect()
        self.assertEqual(result['entryCount'],4)
        self.assertEqual(result['expandedBytes'],2*len(b'fixture bytes; never execute')+len(b'jar fixture'))
        self.assertEqual(result['inventorySha256'],qualification.canonical_sha(result['inventory']))
        self.assertEqual(set(result['requiredExecutables']),set(qualification.REQUIRED))
        self.assertTrue(result['packageBytesReadThisInvocation']);self.assertFalse(result['packageExecuted'])
        self.assertFalse(result['otaAdmitted']);self.assertFalse(result['signerPinsAdmitted'])
        self.assertEqual(before,{str(p.relative_to(self.root)) for p in self.root.rglob('*')})

    def test_partial_or_different_package_is_not_inspected(self):
        self.package();data=self.archive.read_bytes()
        self.archive.write_bytes(data[:-1])
        with self.assertRaisesRegex(ValueError,'size differs'):self.inspect()
        self.archive.write_bytes(b'x'*len(data))
        with self.assertRaisesRegex(ValueError,'digest differs'):self.inspect()

    def test_noncanonical_paths_symlinks_special_modes_duplicates_and_prefix_conflicts_refuse(self):
        cases=[('../escape',stat.S_IFREG|0o644,b'x'),('a//b',stat.S_IFREG|0o644,b'x'),
               ('a/./b',stat.S_IFREG|0o644,b'x'),('a\\b',stat.S_IFREG|0o644,b'x'),
               ('C:drive',stat.S_IFREG|0o644,b'x'),('/absolute',stat.S_IFREG|0o644,b'x'),
               ('lib/link',stat.S_IFLNK|0o777,b'target'),('lib/fifo',stat.S_IFIFO|0o600,b''),
               ('lib/privileged',stat.S_IFREG|0o4755,b'x'),
               (qualification.REQUIRED[0],stat.S_IFREG|0o755,b'duplicate'),
               ('bin',stat.S_IFREG|0o644,b'file conflicts with executable parent')]
        for entry in cases:
            self.record=copy.deepcopy(self.original);self.package([entry])
            with self.subTest(entry=entry[0]),self.assertRaises(ValueError):self.inspect()

    def test_non_executable_entry_and_resource_bounds_are_rejected(self):
        self.package()
        with patch.object(qualification,'MAX_ENTRIES',1):
            with self.assertRaisesRegex(ValueError,'entry bound'):self.inspect()
        with patch.object(qualification,'MAX_EXPANDED',1):
            with self.assertRaisesRegex(ValueError,'expansion bound'):self.inspect()
        with zipfile.ZipFile(self.archive,'w') as archive:
            for name in qualification.REQUIRED:
                info=zipfile.ZipInfo(name);info.external_attr=(stat.S_IFREG|0o644)<<16;archive.writestr(info,b'fixture')
        self.bind_archive()
        with self.assertRaisesRegex(ValueError,'required executable'):self.inspect()

    def test_corrupt_member_crc_is_detected_even_if_outer_package_hash_is_bound(self):
        self.package();data=self.archive.read_bytes()
        self.archive.write_bytes(data.replace(b'fixture bytes',b'changed bytes',1));self.bind_archive()
        with self.assertRaises(zipfile.BadZipFile):self.inspect()

    def test_actual_inspection_record_binds_the_complete_inventory_and_stays_unadmitted(self):
        record=qualification.inspection_record(root=ROOT)
        self.assertEqual(record['entryCount'],500)
        self.assertEqual(record['expandedBytes'],927318429)
        self.assertEqual(len(record['inventory']),500)
        self.assertEqual(record['inventorySha256'],'7e69d987fd0384e169467f1f9ded8c4986d6e9f8e0538f7aa6b4402808dd9912')
        self.assertIs(record['packageExecuted'],False);self.assertIs(record['signerPinsAdmitted'],False)

    def test_inspection_record_refuses_tampered_inventory_counts_identity_and_execution(self):
        original=json.loads((ROOT/qualification.INSPECTION).read_text())
        mutations=[lambda r:r.update(entryCount=501),lambda r:r.update(expandedBytes=42),
            lambda r:r['inventory'][0].update(sha256='f'*64),
            lambda r:r['run'].update(runId='999'),lambda r:r.update(packageExecuted=True),
            lambda r:r['requiredExecutables'][qualification.REQUIRED[0]].update(mode=0o644),
            lambda r:r['outerArtifactReadback'].update(artifactId=999)]
        metadata={'inspectorSource','inspectorScriptSha256','inspectionReceiptSha256','recordedAt','outerArtifactReadback'}
        for i,mutate in enumerate(mutations):
            record=copy.deepcopy(original);mutate(record)
            record['inspectionReceiptSha256']=qualification.canonical_sha({k:v for k,v in record.items() if k not in metadata})
            (self.root/qualification.INSPECTION).write_text(json.dumps(record))
            with self.subTest(mutation=i),self.assertRaises(ValueError):qualification.inspection_record(root=self.root)

    def test_encrypted_flag_is_refused_before_member_read(self):
        self.package();data=bytearray(self.archive.read_bytes())
        offset=data.index(b'PK\x01\x02')+8;data[offset]|=1
        self.archive.write_bytes(data);self.bind_archive()
        with self.assertRaisesRegex(ValueError,'unsafe'):self.inspect()


if __name__=='__main__':unittest.main()
