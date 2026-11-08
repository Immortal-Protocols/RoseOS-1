"""Offline vendor preparation admission, output and failure-provenance checks."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'scripts/tests'))
import mustang_vendor_prep as vendor
import mustang_ephemeral as ephemeral
import os_release_shape as shape


class VendorPreparationTest(unittest.TestCase):
    def env(self):
        return {'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REF':'refs/heads/main',
                'GITHUB_WORKFLOW_REF':'Immortal-Protocols/RoseOS-1/.github/workflows/mustang-ota-dedicated.yml@refs/heads/main',
                'GITHUB_REPOSITORY_ID':'1428996648','GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1',
                'GITHUB_SHA':'a'*40,'GITHUB_JOB':'vendor'}

    def test_exclusive_scope_main_and_bound_successful_inputs(self):
        event={'inputs':{'vendor_only':True}}
        lane,run=vendor.guard(self.env(),event)
        self.assertEqual(lane['vm']['instanceId'],'5585425566393384049')
        self.assertEqual(run['runAttempt'],'1')
        for flag in vendor.FLAGS-{'vendor_only'} | {'unknown'}:
            with self.subTest(flag=flag),self.assertRaises(ValueError):
                vendor.guard(self.env(),{'inputs':{'vendor_only':True,flag:True}})
        for change in ({'GITHUB_RUN_ATTEMPT':'2'},{'GITHUB_REF':'refs/heads/other'},{'GITHUB_REPOSITORY_ID':'1'}):
            with self.assertRaises(ValueError):vendor.guard(dict(self.env(),**change),event)
        with patch.object(vendor.inputs,'evidence',side_effect=ValueError('failed input')):
            with self.assertRaises(ValueError):vendor.guard(self.env(),event)

    def test_plan_has_no_process_network_or_mutation(self):
        with patch.object(sys,'argv',['vendor']),patch.object(subprocess,'run',side_effect=AssertionError('process')), \
                patch.object(vendor.mustang_yarn,'install',side_effect=AssertionError('network')):
            self.assertEqual(vendor.main(),4)

    def test_stock_configuration_matches_the_pinned_factory(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp);adev=source/'vendor/adevtool';device=adev/'config/device';(device/'common').mkdir(parents=True)
            (device/'mustang.yml').write_bytes((ROOT/'os/build-only/mustang-inputs/mustang.yml').read_bytes())
            common=device/'common/gen10pixel.yml'
            common.write_bytes(b'includes:\n  - pixel.yml\n\ndevice:\n  platform: laguna\n  build_id: CP3A.261005.005\n')
            lines=json.loads((ROOT/'os/build-only/mustang-inputs/build-index.json').read_text())['lines']
            index=adev/'config/build-index/build-index-main.yml';index.parent.mkdir()
            index.write_text('\n'.join(lines[str(i)] for i in range(1,5))+'\n')
            self.assertEqual(set(vendor.factory_configuration(source,time.monotonic()+10)),{'device','buildSelection','buildIndex'})
            common.write_text('device:\n  build_id: DIFFERENT\n')
            with self.assertRaisesRegex(ValueError,'stock build selection'):
                vendor.factory_configuration(source,time.monotonic()+10)

    def test_catalog_is_complete_and_detects_content_change_and_escaping_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp);directory=source/'vendor';directory.mkdir();(directory/'data').write_bytes(b'first')
            (directory/'alias').symlink_to('data')
            before=vendor.catalog(source,directory,time.monotonic()+10)
            self.assertTrue(before['complete']);self.assertEqual(before['entries'],2)
            (directory/'data').write_bytes(b'changed')
            self.assertNotEqual(vendor.catalog(source,directory,time.monotonic()+10)['sha256'],before['sha256'])
            (directory/'escape').symlink_to('/etc/passwd')
            with self.assertRaisesRegex(ValueError,'link escapes'):
                vendor.catalog(source,directory,time.monotonic()+10)

    def test_catalog_refuses_special_files_and_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp);directory=source/'vendor';directory.mkdir();os.mkfifo(directory/'pipe')
            with self.assertRaisesRegex(ValueError,'special entry'):
                vendor.catalog(source,directory,time.monotonic()+10)
            (directory/'pipe').unlink();(directory/'file').write_bytes(b'too large')
            with patch.object(vendor,'WORK_BYTES',1),self.assertRaisesRegex(ValueError,'byte bound'):
                vendor.catalog(source,directory,time.monotonic()+10)

    def test_output_collision_preserves_bytes_and_records_the_exact_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';source.mkdir();attempt=Path(tmp)/'attempt';attempt.mkdir()
            product=source/'vendor/google_devices/mustang';product.mkdir(parents=True);(product/'keep').write_bytes(b'old bytes')
            with patch.object(vendor.deps,'source_check',side_effect=AssertionError('source process')):
                with self.assertRaisesRegex(ValueError,'output exists'):
                    vendor.prepare(source,attempt,{}, {}, {'builder':'fixture'})
            recorded=json.loads((attempt/vendor.RECEIPT).read_text())
            self.assertFalse(recorded['success']);self.assertEqual(recorded['phase'],'collision-check')
            self.assertFalse(recorded['makeStarted']);self.assertEqual((product/'keep').read_bytes(),b'old bytes')

    def test_workflow_never_gives_vendor_signer_credentials_or_ota_route(self):
        jobs=shape.jobs((ROOT/'.github/workflows/mustang-ota-dedicated.yml').read_text());body=jobs['vendor']
        self.assertIn('if: inputs.vendor_only',body)
        self.assertIn('mustang_vendor_prep.py run --execute',body)
        self.assertIn('mustang_vendor_prep.py report',body)
        for text in ('id-token:','actions-gcp-login','sudo ','secret'):
            self.assertNotIn(text,body)
        self.assertIn('- vendor',jobs['cleanup'].split('steps:',1)[0])
        for role in ('builder','signer'):
            self.assertIn('inputs.vendor_only == false',jobs[role])
        self.assertEqual(ephemeral.queue_decision([{'name':'vendor','status':'in_progress'}],100),'ready')

    def test_successful_sequence_uses_measured_inputs_and_records_generated_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';source.mkdir();attempt=Path(tmp)/'attempt';attempt.mkdir()
            node=source/'node';node.write_bytes(b'synthetic Node');node.chmod(0o700)
            adev=source/'vendor/adevtool';adev.mkdir(parents=True);commands=[]
            def yarn_bundle(directory,*_args,**_kwargs):
                path=directory/'yarn.js';path.write_bytes(b'synthetic Yarn')
                return {'path':str(path),'version':'1.22.22'}
            def checked(command,*_args,**_kwargs):
                if command[-1]=='--version':return '1.22.22' if len(command)==3 else vendor.ephemeral.NODE['version']
                if command[-1]=='HEAD':return vendor.deps.recipe()['adevtoolRevision']
                if command[-1]=='--untracked-files=no':return ''
                raise AssertionError(command)
            def supervise(command,_source,env,_log,_deadline,monitor,*,outcome,**_kwargs):
                self.assertNotIn('GH_TOKEN',env);self.assertNotIn('GCP_SERVICE_ACCOUNT_JSON',env)
                self.assertEqual(env['ADEVTOOL_SKIP_DEP_BUILD'],'1');commands.append(command)
                if len(commands)==1:
                    (adev/'node_modules').mkdir();(adev/'node_modules/dependency.js').write_bytes(b'dependency')
                elif len(commands)==2:
                    (adev/'lib').mkdir();(adev/'lib/index.js').write_bytes(b'compiled source')
                else:
                    product=source/'vendor/google_devices/mustang';product.mkdir(parents=True)
                    (product/'mustang.mk').write_bytes(vendor.deps.readers.VERSION_INCLUDE+b'\n')
                outcome.update(processStarted=True,exitCode=0);monitor();return 0
            free=SimpleNamespace(f_bavail=1 << 40,f_frsize=1,f_favail=1000000)
            with patch.dict(vendor.ephemeral.NODE,{'install':str(node)}), \
                    patch.object(vendor.deps,'source_check',return_value={'projectCount':1057}), \
                    patch.object(vendor,'factory_configuration',return_value={}), \
                    patch.object(vendor.inputs,'host_dependencies',return_value={'distributionRehashed':True}), \
                    patch.object(vendor.inputs,'factory_archive',return_value={'path':'/verified/factory.zip'}), \
                    patch.object(vendor.mustang_yarn,'install',side_effect=yarn_bundle), \
                    patch.object(vendor.deps,'checked',side_effect=checked), \
                    patch.object(vendor.deps,'supervise',side_effect=supervise), \
                    patch.object(vendor.os,'statvfs',return_value=free), \
                    patch.dict(os.environ,{'GH_TOKEN':'secret','GCP_SERVICE_ACCOUNT_JSON':'private'}):
                result=vendor.prepare(source,attempt,{}, {}, {'builder':'fixture'})
            self.assertTrue(result['success']);self.assertFalse(result['makeStarted'])
            self.assertEqual(result['phase'],'complete');self.assertEqual(len(commands),3)
            self.assertEqual(commands[-1],['vendor/adevtool/bin/run','generate-all','-d','mustang'])
            self.assertTrue(result['vendorTree']['complete']);self.assertEqual(result['vendorTree']['entries'],1)
            self.assertEqual(json.loads((attempt/vendor.RECEIPT).read_text()),result)


if __name__=='__main__':unittest.main()
