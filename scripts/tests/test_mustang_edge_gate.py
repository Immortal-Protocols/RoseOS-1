"""Offline adapter boundaries only: synthetic archives, signer/generator stand-ins, no live actions."""
import copy
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
import dex_fixture
import test_edge_artifact as fixtures

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
        self.actions = self.root/'actions'; self.actions.mkdir()
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

    def stage(self):
        signed=adapter.read(adapter.run_root('sign','333','1')/'mustang-sign-receipt.json')
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
        with patch.object(ota,'module',side_effect=module),patch.object(ota,'identities',return_value=pins), \
                patch.object(ota.host_secrets,'certificate_sha256',return_value=pin), \
                patch.object(ota,'observe_target',return_value={'packagedUpdaterVerified':True}), \
                patch.object(ota,'ota_metadata',return_value={'pre-device':'mustang'}), \
                patch.object(ota,'fingerprint',return_value='d'*64), \
                patch.object(ota.subprocess,'run',return_value=SimpleNamespace(returncode=0)):
            result=ota.stage('333','1',str(baseline),self.root/'public-stage')
            ota.verify_staged(self.root/'public-stage',ota.config())
        return result

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


if __name__=='__main__':
    unittest.main()
