"""Offline Mustang OTA failures and isolation. Synthetic bytes/clients, no live resource or key access."""
import base64
import copy
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import urllib.parse
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mustang_ota as ota

auto = ota.module('mustang_auto_tests', 'mustang-ota-auto.py')
BUILD, EPOCH, SHA = '2026110401', '1793752000', 'a'*40


def enabled():
    cfg = copy.deepcopy(ota.config())
    cfg.update(publicationEnabled=True, automationEnabled=True)
    return cfg


def staged(root):
    root.mkdir()
    name = f'mustang-ota_update-{BUILD}.zip'
    (root/name).write_bytes(b'synthetic OTA, not installable')
    (root/'mustang-stable').write_text(f'{BUILD} {EPOCH} mustang stable\n')
    receipt = dict(schema='roseos.mustang-ota-stage.v1', device='mustang', build=BUILD, timestamp=EPOCH,
                   candidateSource=SHA, recipeSource=SHA, runId='333', contentFingerprint='b'*64, label=ota.LABEL,
                   baselineBuild='2026110302', publicIdentity=ota.identities(),
                   checks=dict(targetVersion=True, baselineTrust=True, otaSignatures=True, packagedUpdater=True,roseEdge=True),
                   ota=dict(file=name, sizeBytes=(root/name).stat().st_size, sha256=ota.digest(root/name)))
    from test_mustang_edge_gate import publication_proofs
    with tempfile.TemporaryDirectory() as proofs:
        receipt.update(publication_proofs(Path(proofs),candidate=SHA,recipe=SHA,build=BUILD,epoch=EPOCH))
    (root/'mustang-ota-receipt.json').write_text(json.dumps(receipt))
    (root/'SHA256SUMS').write_text(''.join(f'{ota.digest(p)}  {p.name}\n' for p in sorted(root.iterdir())))
    return receipt


class Client:
    api, repository = 'https://api.github.com', ota.REPOSITORY
    def __init__(self, fail=None):
        self.rows = [dict(id=1, tag_name='frankel-2026110301', draft=False, assets=[], body='retained'),
                     dict(id=2, tag_name='rango-2026103001', draft=False, assets=[], body='retained'),
                     dict(id=4, tag_name='blazer-2026103001', draft=False, assets=[], body='retained')]
        self.before = copy.deepcopy(self.rows)
        self.log, self.blobs, self.fail = [], {}, fail
    def _request(self, *args):
        page=int(urllib.parse.parse_qs(urllib.parse.urlsplit(args[1]).query)['page'][0])
        return 200, None, self.rows[(page-1)*100:page*100]
    def latest_tag(self):
        return 'frankel-2026110301'
    def create_draft(self, tag, name, body):
        self.log.append(('draft', tag))
        row = dict(id=3, tag_name=tag, draft=True, prerelease=False, assets=[], body=body)
        self.rows.append(row)
        return row
    def get_release(self, rid):
        return next(x for x in self.rows if x['id'] == rid)
    def upload(self, rid, path):
        self.log.append(('upload', path.name))
        if self.fail == 'upload:'+path.name:
            raise ota.publisher.PublishError('synthetic upload failed')
        aid = len(self.blobs)+10
        self.blobs[aid] = path.read_bytes()
        self.get_release(rid)['assets'].append(dict(id=aid, name=path.name, size=path.stat().st_size, state='uploaded'))
    def download(self, aid, path, size):
        name = path.name
        self.log.append(('readback', name))
        path.write_bytes(b'corrupt' if self.fail == 'readback:'+name else self.blobs[aid])
    def finalize_public(self, rid):
        self.log.append(('finalize', rid))
        row = self.get_release(rid)
        row['draft'] = False
        if self.fail == 'uncertain-finalize':
            raise ota.publisher.PublishError('synthetic connection lost')
        return row
    def public_download(self, url, path, size):
        self.log.append(('public', url))
        name = url.rsplit('/', 1)[1]
        asset = next(a for a in self.rows[-1]['assets'] if a['name'] == name)
        path.write_bytes(b'corrupt' if self.fail == 'public' else self.blobs[asset['id']])


class PublicationTest(unittest.TestCase):
    def setUp(self):
        # These retained publication fixtures predate UTC-hour allocation.
        # Keep their runtime context explicit; the current-route refusal is
        # checked separately below, without changing production validation.
        self.live_read=ota.read
        runtime_path=ota.ROOT/'os/build-only/mustang-runtime.json'
        def read_legacy(path):
            value=self.live_read(path)
            return dict(value,status='unbound') if Path(path)==runtime_path else value
        reader=patch.object(ota,'read',side_effect=read_legacy)
        reader.start();self.addCleanup(reader.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root/'assets'
        self.r = staged(self.assets)
        self.out = self.root/'receipt'
        self.cfg = enabled()
    def test_current_bound_runtime_requires_clock_proof_before_publication(self):
        client=Client()
        with patch.object(ota,'read',side_effect=self.live_read):
            with self.assertRaisesRegex(ValueError,'UTC-hour allocation proof missing'):
                ota.publish(client,self.assets,self.cfg,self.out)
        self.assertEqual(client.log,[])
    def test_order_downloads_before_channel_and_never_moves_other_releases(self):
        client = Client()
        result = ota.publish(client, self.assets, self.cfg, self.out)
        self.assertEqual(result['status'], 'ota-published')
        self.assertTrue(result['publicDownloadVerified'])
        self.assertEqual(client.rows[:len(client.before)], client.before)
        self.assertTrue(all(not item[1].startswith(('frankel', 'blazer', 'rango')) for item in client.log if item[0] == 'upload'))
        self.assertLess(client.log.index(('readback', self.r['ota']['file'])), client.log.index(('upload', 'mustang-stable')))
        self.assertLess(client.log.index(('readback', 'mustang-stable')), client.log.index(('finalize', 3)))
        self.assertEqual(result['latestBefore'], result['latestAfter'])
    def test_ota_upload_and_readback_failures_never_upload_metadata(self):
        for fail in ['upload:'+self.r['ota']['file'], 'readback:'+self.r['ota']['file']]:
            with self.subTest(fail=fail):
                client = Client(fail)
                out = self.root/fail.split(':')[0]
                with self.assertRaises((ValueError, ota.publisher.PublishError)):
                    ota.publish(client, self.assets, self.cfg, out)
                self.assertNotIn(('upload', 'mustang-stable'), client.log)
                self.assertNotIn(('finalize', 3), client.log)
                self.assertFalse(ota.read(out/'publication.json')['public'])
    def test_metadata_readback_failure_keeps_draft(self):
        client = Client('readback:mustang-stable')
        with self.assertRaises(ValueError):
            ota.publish(client, self.assets, self.cfg, self.out)
        self.assertTrue(client.rows[-1]['draft'])
        self.assertNotIn(('finalize', 3), client.log)
    def test_existing_or_newer_tag_is_never_changed(self):
        for build in [BUILD, '2026110499']:
            with self.subTest(build=build):
                client = Client()
                client.rows.append(dict(tag_name='mustang-'+build, draft=True))
                with self.assertRaisesRegex(ValueError, 'already consumes'):
                    ota.publish(client, self.assets, self.cfg, self.root/('blocked-'+build))
                self.assertEqual(client.log, [])
    def test_public_download_failure_reports_already_public(self):
        with self.assertRaises(ValueError):
            ota.publish(Client('public'), self.assets, self.cfg, self.out)
        state = ota.read(self.out/'publication.json')
        self.assertEqual(state['status'], 'failed-or-uncertain')
        self.assertTrue(state['public'])
    def test_uncertain_finalize_is_not_reported_as_private(self):
        with self.assertRaises(ota.publisher.PublishError):
            ota.publish(Client('uncertain-finalize'), self.assets, self.cfg, self.out)
        self.assertIsNone(ota.read(self.out/'publication.json')['public'])
    def test_wrong_channel_missing_proof_changed_bytes_or_foreign_device_stop_before_api(self):
        original = json.dumps(self.r)
        for field, value in [('device', 'frankel'), ('checks', {}), ('publicIdentity', {}),('label','unverified claim')]:
            with self.subTest(field=field):
                altered = dict(self.r, **{field:value})
                (self.assets/'mustang-ota-receipt.json').write_text(json.dumps(altered))
                client = Client()
                with self.assertRaises(ValueError):
                    ota.publish(client, self.assets, self.cfg, self.out)
                self.assertEqual(client.log, [])
        (self.assets/'mustang-ota-receipt.json').write_text(original)
        (self.assets/'mustang-stable').write_text(f'{BUILD} {EPOCH} frankel stable\n')
        with self.assertRaisesRegex(ValueError, 'channel metadata'):
            ota.verify_staged(self.assets, self.cfg)
        (self.assets/'mustang-stable').write_text(f'{BUILD} {EPOCH} mustang stable\n')
        (self.assets/self.r['ota']['file']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum differs'):
            ota.verify_staged(self.assets, self.cfg)
    def test_missing_stale_or_false_edge_proof_stops_before_publisher_api(self):
        original=copy.deepcopy(self.r)
        for kind in ('missing','stale','false','archive','binding'):
            receipt=copy.deepcopy(original)
            if kind=='missing': receipt.pop('roseEdge')
            elif kind=='stale': receipt['roseEdge']['signed']['checker']['specSha256']='0'*64
            elif kind=='false': receipt['roseEdge']['signed']['evidence']['literals'][0]['pass']=False
            elif kind=='archive': receipt['edgeTargetFiles']['unsigned']['sha256']='0'*64
            else: receipt['roseEdge']['unsigned']['binding']['compileRunId']='999'
            (self.assets/'mustang-ota-receipt.json').write_text(json.dumps(receipt))
            # A rewritten checksum must never turn invalid feature proof into an accepted staged artifact.
            (self.assets/'SHA256SUMS').write_text(''.join(
                f'{ota.digest(p)}  {p.name}\n' for p in sorted(self.assets.iterdir()) if p.name!='SHA256SUMS'))
            client=Client()
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError): ota.publish(client,self.assets,self.cfg,self.out)
                self.assertEqual(client.log,[])
    def test_defaults_inactive_before_any_api_or_secret(self):
        inactive=copy.deepcopy(ota.config());inactive['publicationEnabled']=False
        with self.assertRaisesRegex(ValueError, 'activation remains pending'):
            ota.verify_staged(self.assets, inactive)
        self.assertFalse(auto.dispatch({})['dispatched'])
    def test_recorded_read_access_must_be_explicit_and_current(self):
        now = datetime.datetime(2026,11,4,tzinfo=datetime.timezone.utc)
        self.cfg['signingAccess'] = dict(status='pending', approvalRef=None, expiresAt=None)
        with self.assertRaisesRegex(ValueError, 'approved current binding'):
            ota.guard(self.cfg, access=True, now=now)
        self.cfg['signingAccess'] = dict(status='approved', approvalRef='synthetic approval', expiresAt='2026-11-04T00:29:59Z')
        with self.assertRaisesRegex(ValueError, 'less than 30'):
            ota.guard(self.cfg, access=True, now=now)
        self.cfg['signingAccess']['expiresAt']='2026-11-04T01:00:00Z'
        ota.guard(self.cfg, access=True, now=now)
        del self.cfg['signingAccess']['expiresAt']  # stated, or explicitly null: never inferred from absence
        with self.assertRaisesRegex(ValueError, 'approved current binding'):
            ota.guard(self.cfg, access=True, now=now)
    def test_verified_unconditional_read_access_has_no_window(self):
        committed = ota.config()
        self.assertEqual((committed['signingAccess']['status'], committed['signingAccess']['expiresAt']), ('approved', None))
        utc = datetime.timezone.utc
        for now in (None, datetime.datetime(2026,11,5,6,20,18,tzinfo=utc), datetime.datetime(2030,1,1,tzinfo=utc)):
            ota.guard(committed, access=True, now=now)
        windowed = dict(committed['signingAccess'], approvalRef='os/ota/mustang-access-readback-2026-11-05.json')
        with self.assertRaisesRegex(ValueError, 'recorded unconditional readback'):  # a windowed record never counts
            ota.guard(dict(committed, signingAccess=windowed), access=True)
        with self.assertRaisesRegex(ValueError, 'public OTA activation remains pending'):  # the other gates are unchanged
            ota.guard(dict(committed, publicationEnabled=False), access=True)
        with self.assertRaisesRegex(ValueError, 'automatic publication activation remains pending'):
            ota.guard(committed, automatic=True, access=True)
    def test_only_an_exact_unconditional_readback_counts(self):
        ref = 'os/ota/mustang-access-readback-unconditional.json'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (ref, 'os/ota/mustang-access-readback.json', 'os/ota/mustang-access-readback-2026-11-05.json',
                         'os/signing-bootstrap/mustang-access-binding.json'):
                (root/name).parent.mkdir(parents=True, exist_ok=True)
                (root/name).write_bytes((ota.ROOT/name).read_bytes())
            good = json.loads((root/ref).read_text())
            def variant(change):
                record = copy.deepcopy(good)
                change(record)
                (root/ref).write_text(json.dumps(record))
                return ota.unconditional_access(ref, root)
            self.assertTrue(variant(lambda r: None))
            window = {'title': 'window', 'expression': "request.time < timestamp('2026-11-05T06:20:18Z')"}
            for change in (lambda r: r['bindings'][0].update(condition=window),
                           lambda r: r['bindings'][1].pop('condition'),  # a condition not read back is never assumed absent
                           lambda r: r['bindings'][1].update(expiresAt='2026-11-05T06:20:18Z'),
                           lambda r: r['bindings'][1].pop('expiresAt'),
                           lambda r: r.update(expiresAt='2026-11-05T06:20:18Z'),
                           lambda r: r.pop('expiresAt'),
                           lambda r: r['bindings'][0].update(member='serviceAccount:other@protocols-465723.iam.gserviceaccount.com'),
                           lambda r: r['bindings'][0].update(role='roles/secretmanager.admin'),
                           lambda r: r['bindings'].pop(),
                           lambda r: r['bindings'].append(dict(r['bindings'][0], secret='PIXEL_OS_SIGNING_KEYS_FRANKEL_V1')),
                           lambda r: r['bindings'][1].update(secret=r['bindings'][0]['secret']),
                           lambda r: r['bindings'].append(dict(r['bindings'][0])),  # exactly one row per secret
                           lambda r: r.update(project='another-project'),
                           lambda r: r.update(observedAt=None),
                           lambda r: r.update(schema='roseos.other.v1'),
                           lambda r: r.update(bindings='both')):
                self.assertFalse(variant(change))
            variant(lambda r: None)
            for other in ('os/ota/mustang-access-readback.json', 'os/ota/mustang-access-readback-2026-11-05.json',
                          'os/ota/missing.json', 'os/signing-bootstrap/mustang-access-binding.json', '../readback.json'):
                self.assertFalse(ota.unconditional_access(other, root), other)
            (root/'os/readback.json').write_bytes((root/ref).read_bytes())  # a valid record outside os/ota
            self.assertFalse(ota.unconditional_access('os/readback.json', root))
            self.assertFalse(ota.unconditional_access('os/ota/../readback.json', root))
            (root/'os/ota/alias.json').symlink_to(root/ref)
            self.assertFalse(ota.unconditional_access('os/ota/alias.json', root))
            self.assertTrue(ota.unconditional_access(ref, root))
    def test_bad_or_missing_checksums_stop(self):
        (self.assets/'SHA256SUMS').write_text('0'*64+'  '+self.r['ota']['file']+'\n')
        with self.assertRaisesRegex(ValueError,'checksum'):
            ota.verify_staged(self.assets,self.cfg)
    def test_updater_listing_still_fits_after_new_release(self):
        client=Client()
        client.rows += [dict(tag_name='other-'+str(i),draft=False) for i in range(299-len(client.rows))]
        with self.assertRaisesRegex(ValueError,'discovery completeness'):
            ota.publish(client,self.assets,self.cfg,self.out)
        self.assertEqual(client.log,[])


class MetadataTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'ota.zip'
        self.meta = {'pre-device':'mustang', 'post-build-incremental':BUILD, 'post-timestamp':EPOCH,
                     'post-build':f'google/mustang/mustang:17/A/{BUILD}:user/release-keys', 'ota-type':'AB'}
    def write(self, meta):
        with zipfile.ZipFile(self.path,'w') as archive:
            archive.writestr('META-INF/com/android/metadata',''.join(f'{k}={v}\n' for k,v in meta.items()))
    def test_actual_newer_full_mustang(self):
        self.write(self.meta)
        self.assertEqual(ota.ota_metadata(self.path, BUILD, EPOCH, ota.config()['baseline']), self.meta)
    def test_wrong_device_time_version_wipe_downgrade_incremental(self):
        for key, value in [('pre-device','rango'),('post-build-incremental','2026110302'),('post-timestamp','1'),
                           ('ota-type','BLOCK'),('ota-wipe','yes'),('ota-downgrade','yes'),('pre-build','old')]:
            with self.subTest(key=key):
                self.write(dict(self.meta, **{key:value}))
                with self.assertRaises(ValueError):
                    ota.ota_metadata(self.path, BUILD, EPOCH, ota.config()['baseline'])
    def test_no_non_newer_build_or_timestamp(self):
        self.write(self.meta)
        with self.assertRaisesRegex(ValueError,'newer'):
            ota.ota_metadata(self.path, BUILD, EPOCH, dict(build=BUILD,timestamp=EPOCH))


class ObserverTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'target.zip'
        self.cert=b'synthetic certificate DER'
        self.pin=hashlib.sha256(self.cert).hexdigest()
        self.resources={'url':ota.read(ota.ROOT/'os/build-only/mustang-updater-route.json')['updaterUrl'],
                        'release_index':ota.read(ota.ROOT/'os/build-only/mustang-updater-route.json')['releaseIndex'],
                        'channel_default':'prod','channel_published':['stable'],'channel_values':['prod','stable','alpha']}
        trust=io.BytesIO()
        with zipfile.ZipFile(trust,'w') as z:
            z.writestr('releasekey.x509.pem',b'-----BEGIN CERTIFICATE-----\n'+base64.b64encode(self.cert)+b'\n-----END CERTIFICATE-----\n')
        apk=io.BytesIO()
        with zipfile.ZipFile(apk,'w') as z:
            z.writestr('classes.dex',b'Lapp/seamlessupdate/client/RoseChannels; Lapp/seamlessupdate/client/RoseReleases;')
        with zipfile.ZipFile(self.path,'w') as z:
            z.writestr('VENDOR/build.prop',f'ro.product.vendor.device=mustang\nro.vendor.build.version.incremental={BUILD}\nro.vendor.build.date.utc={EPOCH}\n')
            z.writestr('SYSTEM/priv-app/Updater/Updater.apk',apk.getvalue())
            z.writestr('SYSTEM/etc/security/otacerts.zip',trust.getvalue())
    def execute(self, argv, **kwargs):
        if argv[0]=='apksigner':return SimpleNamespace(returncode=0,stdout='Signer #1 certificate SHA-256 digest: '+self.pin)
        if 'badging' in argv:return SimpleNamespace(returncode=0,stdout="package: name='app.seamlessupdate.client'")
        lines=['      config (default) flags=0x02 [offset16]:']
        for i,(name,value) in enumerate(self.resources.items()):
            if isinstance(value,list):
                lines += [f'        resource 0x7f0000{i:02x} app.seamlessupdate.client:array/{name}: <bag>',
                          f'          Parent=0x00000000(Resolved=0x00000000), Count={len(value)}']
                lines += [f'          #{j} (Key=0x01000001): (string8) "{v}"' for j,v in enumerate(value)]
            else:
                lines += [f'        resource 0x7f0000{i:02x} app.seamlessupdate.client:string/{name}: t=0x03 d=0x00000000',
                          f'          (string8) "{value}"']
        return SimpleNamespace(returncode=0,stdout='\n'.join(lines))
    def observe(self, **kwargs):
        return ota.observe_target(self.path, BUILD, EPOCH, dict(aapt='aapt',apksigner='apksigner'),
                                  dict(otaCertificateSha256=self.pin), execute=kwargs.get('execute',self.execute))
    def test_resources_signer_and_actual_system_trust(self):
        r=self.observe();self.assertTrue(r['packagedUpdaterVerified'])
        self.assertEqual(r['trustedOtaCertificateSha256'],[self.pin])
    def test_qualified_aapt2_command_and_real_compiled_resource_format(self):
        dump=(ota.ROOT/'scripts/tests/fixtures/mustang-updater-aapt2.txt').read_text();calls=[]
        def execute(argv,**kwargs):
            calls.append(argv)
            if argv[0]=='aapt2' and 'badging' not in argv:
                self.assertEqual(argv[1:3],['dump','resources']);self.assertEqual(len(argv),4)
                self.assertNotIn('--values',argv)
                return SimpleNamespace(returncode=0,stdout=dump,stderr='')
            return self.execute(argv,**kwargs)
        report=ota.observe_target(self.path,BUILD,EPOCH,dict(aapt='aapt2',apksigner='apksigner'),
                                  dict(otaCertificateSha256=self.pin),execute=execute)
        self.assertTrue(report['packagedUpdaterVerified']);self.assertTrue(any('badging' in a for a in calls))

    def test_aapt2_resources_reject_changed_localized_duplicate_missing_and_malformed_values(self):
        dump=(ota.ROOT/'scripts/tests/fixtures/mustang-updater-aapt2.txt').read_text()
        expected={('string',k):v for k,v in self.resources.items() if isinstance(v,str)}
        expected.update({('array',k):v for k,v in self.resources.items() if isinstance(v,list)})
        self.assertEqual(ota.aapt2_updater_failures(dump,expected),[])
        for altered in (dump.replace('() "prod"','() "beta"'),
                        dump.replace('() "prod"','(fr) "prod"'),
                        dump.replace('size=3','size=4'),
                        dump.replace('["stable"]','["stable", 1]'),
                        dump.replace('["prod", "stable", "alpha"]','["prod", "alpha", "stable"]'),
                        dump.replace('string/url','string/missing_url'),
                        dump.replace('Package name=app.seamlessupdate.client','Package name=other'),
                        dump+'\n    resource 0x7f020000 string/channel_default\n      () "prod"\n',
                        dump.replace('() "prod"','() @string/other'),
                        dump.replace('() "prod"','() "prod"\n      (fr) "beta"')):
            with self.subTest(dump=altered):self.assertTrue(ota.aapt2_updater_failures(altered,expected))

    def test_resource_reader_failure_keeps_bounded_real_tool_error(self):
        def execute(argv,**kwargs):return SimpleNamespace(returncode=1,stdout='',stderr="unknown option '--values'.")
        with self.assertRaisesRegex(ValueError,"unknown option '--values'"):
            self.observe(execute=execute)

    def test_qualified_apksigner_v3_labels_keep_the_exact_certificate_pin(self):
        actual=(ota.ROOT/'scripts/tests/fixtures/mustang-apksigner-v3.txt').read_text().replace(
            '650d47f91b7f5af3c530fef53fe92018aa4521efba065bcb1d6fc9b699060bfb',self.pin)
        for output in (actual,
                       'V3.1 Signer: certificate SHA-256 digest: '+self.pin.upper(),
                       'V3.0 Signer: certificate SHA-256 digest: '+self.pin+'\n'+
                       'V3.1 Signer: certificate SHA-256 digest: '+self.pin):
            def execute(argv,**kwargs):
                if argv[0]=='apksigner':return SimpleNamespace(returncode=0,stdout=output,stderr='')
                return self.execute(argv,**kwargs)
            with self.subTest(output=output):self.assertTrue(self.observe(execute=execute)['packagedUpdaterVerified'])

    def test_foreign_missing_and_malformed_apk_identities_stay_closed(self):
        for output in ('V3.0 Signer: certificate SHA-256 digest: '+'0'*64,
                       'V3.0 Signer: certificate SHA-256 digest: '+self.pin+'\n'+
                       'V3.1 Signer: certificate SHA-256 digest: '+'0'*64,
                       'V3.0 Signer: certificate SHA-256 digest: '+self.pin+'not-hex',
                       'V3.0 Signer: public key SHA-256 digest: '+self.pin,
                       '', 'Signer #1 certificate SHA-256 digest: '+self.pin+'\n'+
                       'V3.1 Signer: certificate SHA-256 digest: short'):
            def execute(argv,**kwargs):
                if argv[0]=='apksigner':return SimpleNamespace(returncode=0,stdout=output,stderr='')
                return self.execute(argv,**kwargs)
            with self.subTest(output=output),self.assertRaises(ValueError):self.observe(execute=execute)

    def test_apk_verification_error_is_not_reported_as_a_certificate_mismatch(self):
        def execute(argv,**kwargs):
            if argv[0]=='apksigner':return SimpleNamespace(returncode=1,stdout='',stderr='actual verifier failure')
            return self.execute(argv,**kwargs)
        with self.assertRaisesRegex(ValueError,'signature verification failed.*actual verifier failure'):
            self.observe(execute=execute)

    def test_installed_pre548_baseline_is_not_subject_to_the_new_edge_gate(self):
        baseline=ota.config()['baseline']
        with zipfile.ZipFile(self.path) as archive:
            entries={name:archive.read(name) for name in archive.namelist()}
        entries['VENDOR/build.prop']=(
            'ro.product.vendor.device=mustang\n'
            f'ro.vendor.build.version.incremental={baseline["build"]}\n'
            f'ro.vendor.build.date.utc={baseline["timestamp"]}\n').encode()
        with zipfile.ZipFile(self.path,'w') as archive:
            for name,data in entries.items():archive.writestr(name,data)
        # This baseline has only Updater/trust, with no edge APKs, props or marker.
        with patch.object(ota.mustang_edge,'configuration',side_effect=AssertionError('baseline must not gate')):
            observed=ota.observe_target(self.path,baseline['build'],baseline['timestamp'],
                dict(aapt='aapt',apksigner='apksigner'),dict(otaCertificateSha256=self.pin),execute=self.execute)
        self.assertTrue(observed['packagedUpdaterVerified'])
    def test_vendor_identity_scopes_duplicates_without_weakening_ota_metadata(self):
        with zipfile.ZipFile(self.path) as archive:
            entries={row.filename:archive.read(row) for row in archive.infolist()}
        entries['VENDOR/build.prop'] += b'other.property=first\nother.property=second\nro.product.vendor.device=mustang\n'
        with zipfile.ZipFile(self.path,'w') as archive:
            for name,data in entries.items():archive.writestr(name,data)
        self.assertTrue(self.observe()['packagedUpdaterVerified'])
        entries['VENDOR/build.prop'] += b'ro.product.vendor.device=frankel\n'
        with zipfile.ZipFile(self.path,'w') as archive:
            for name,data in entries.items():archive.writestr(name,data)
        with self.assertRaisesRegex(ValueError,'actual target-files device/build/time differs'):
            self.observe()
        # init's in-file last definition is the effective value, including after
        # an earlier differing definition. The final wrong device above refused.
        entries['VENDOR/build.prop'] += b'ro.product.vendor.device=mustang\n'
        with zipfile.ZipFile(self.path,'w') as archive:
            for name,data in entries.items():archive.writestr(name,data)
        self.assertTrue(self.observe()['packagedUpdaterVerified'])
        with self.assertRaisesRegex(ValueError,'duplicate metadata'):
            ota.properties(b'pre-device=mustang\npre-device=frankel\n')
    def test_wrong_repository_or_channel_stops(self):
        for key,value in [('url','https://example.invalid/'),('release_index','https://api.github.com/repos/Other/repo/releases'),
                          ('channel_published',['prod']),('channel_values',['stable'])]:
            with self.subTest(key=key):
                old=self.resources[key];self.resources[key]=value
                with self.assertRaises(ValueError):self.observe()
                self.resources[key]=old
    def test_bad_apk_signature_or_wrong_trust_stops(self):
        def bad(argv,**kwargs):
            return SimpleNamespace(returncode=1,stdout='') if argv[0]=='apksigner' else self.execute(argv,**kwargs)
        with self.assertRaisesRegex(ValueError,'signature verification failed'):self.observe(execute=bad)
        self.pin='f'*64
        with self.assertRaisesRegex(ValueError,'OTA trust'):self.observe()


class AutomationTest(unittest.TestCase):
    def setUp(self):
        self.ci=dict(id=10,name=auto.CI_NAME,path=auto.CI_PATH,event='push',head_branch='main',status='completed',
                     conclusion='success',run_attempt=1,head_sha=SHA,head_repository={'full_name':auto.REPO})
        self.trigger=copy.deepcopy(self.ci)
        self.runs=[];self.published=[]
    def decide(self, fingerprint=lambda x:'same'):
        return auto.decide(self.trigger,self.ci,SHA,self.runs,self.published,fingerprint,enabled())
    def test_validated_source_starts_only_unsigned_route(self):
        inputs,reason=self.decide()
        self.assertIsNone(reason);self.assertEqual(inputs,dict(mode='mustang-build',candidate_sha=SHA,mustang_publish_ota='true'))
    def test_failed_foreign_or_incomplete_ci_does_not_dispatch(self):
        for key,value in [('event','pull_request'),('conclusion','failure'),('status','in_progress'),('head_repository',{'full_name':'Other/repo'})]:
            with self.subTest(key=key):
                old=self.ci[key];self.ci[key]=value
                self.assertIsNone(self.decide()[0]);self.ci[key]=old
    def test_active_and_duplicate_attempts_do_not_dispatch(self):
        self.runs=[dict(status='queued')];self.assertIsNone(self.decide()[0])
        self.runs=[dict(status='completed',conclusion='failure',display_title='mustang-build auto-'+SHA)]
        self.assertIsNone(self.decide()[0])
    def test_docs_only_commit_cannot_blindly_retry_failed_same_content(self):
        self.runs=[dict(status='completed',conclusion='failure',display_title='mustang-build auto-'+'b'*40)]
        self.assertIsNone(self.decide()[0])
    def test_newer_unchecked_content_is_not_released(self):
        self.ci['head_sha']='b'*40
        self.assertIsNone(self.decide(fingerprint=lambda x:x)[0])
    def test_latest_published_content_is_unchanged_skip_but_revert_is_eligible(self):
        self.published=[dict(tag_name='mustang-2026110401',draft=False,body='Content fingerprint: same')]
        self.assertIsNone(self.decide()[0])
        self.published.append(dict(tag_name='mustang-2026110402',draft=False,body='Content fingerprint: different'))
        self.assertIsNotNone(self.decide()[0])
    def test_successful_auto_build_dispatches_one_matching_delivery(self):
        self.trigger.update(id=11,path='.github/workflows/os-release.yml',event='workflow_dispatch',display_title='mustang-build auto-'+SHA,mustangCompileSucceeded=True)
        inputs,_=self.decide()
        self.assertEqual(inputs,dict(mode='mustang-delivery',mustang_compile_run='11',mustang_bootstrap_run='41719073037',mustang_publish_ota='true'))
        self.runs=[dict(status='completed',display_title='mustang-delivery ota-11',conclusion='failure')]
        self.assertIsNone(self.decide()[0])
    def test_noop_workflow_cannot_supply_a_compile(self):
        self.trigger.update(id=11,path='.github/workflows/os-release.yml',event='workflow_dispatch',display_title='mustang-build auto-'+SHA,mustangCompileSucceeded=False)
        self.assertIsNone(self.decide()[0])
    def test_fifo_readback_catches_manual_publication_and_duplicate_events(self):
        own=dict(id=12,display_title='mustang-build auto-'+SHA)
        self.assertFalse(auto.skip_admitted(SHA,'12',[own],[],lambda x:'same'))
        prior=dict(id=11,display_title='mustang-build auto-'+'b'*40)
        self.assertTrue(auto.skip_admitted(SHA,'12',[prior,own],[],lambda x:'same'))
        published=[dict(tag_name='mustang-2026110401',draft=False,body='Content fingerprint: same')]
        self.assertTrue(auto.skip_admitted(SHA,'12',[own],published,lambda x:'same'))
    def test_foreign_failed_or_repeated_trigger_stops(self):
        for key,value in [('head_branch','other'),('run_attempt',2),('conclusion','failure')]:
            old=self.trigger[key];self.trigger[key]=value
            self.assertIsNone(self.decide()[0]);self.trigger[key]=old


class ContentTest(unittest.TestCase):
    def test_real_git_content_fingerprint_ignores_docs_and_changes_for_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            def git(*argv):
                return subprocess.check_output(['git','-C',temp,*argv],text=True).strip()
            git('init','-q');git('config','user.email','offline@example.invalid');git('config','user.name','Offline test')
            (root/'os/patches').mkdir(parents=True);(root/'docs').mkdir()
            (root/'os/patches/update.patch').write_text('source 1')
            (root/'docs/note.md').write_text('note 1')
            git('add','.');git('commit','-qm','first')
            first=ota.fingerprint(git('rev-parse','HEAD'),root)
            (root/'docs/note.md').write_text('note 2');git('add','.');git('commit','-qm','docs')
            self.assertEqual(first,ota.fingerprint(git('rev-parse','HEAD'),root))
            (root/'os/patches/update.patch').write_text('source 2');git('add','.');git('commit','-qm','code')
            self.assertNotEqual(first,ota.fingerprint(git('rev-parse','HEAD'),root))
            product=ota.fingerprint(git('rev-parse','HEAD'),root)
            (root/'scripts').mkdir()
            (root/'scripts/mustang_ota.py').write_text('publisher only')
            git('add','.');git('commit','-qm','publisher')
            sha=git('rev-parse','HEAD')
            self.assertEqual(product,ota.fingerprint(sha,root),'Publisher-only changes must not create a version-only OS release')
            self.assertNotEqual(product,ota.fingerprint(sha,root,patterns=ota.VALIDATED_SOURCES),'Operational code must still be validated')


class FactoryBaselineTest(unittest.TestCase):
    """Real archive/receipt logic; controlled extractor/SDK/AVB process responses.

    Fixture images/public-key bytes are synthetic, not cryptographically signed
    images. No installer retrieval, key generation or signing occurs.
    """
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'repo'; self.root.mkdir()
        self.sdk=ObserverTest(); self.sdk.setUp(); self.addCleanup(self.sdk.doCleanups)
        self.image=ota.module('factory_baseline_tests','mustang-baseline-image.py')
        from test_mustang_edge_gate import DedicatedPathsTest
        self.home=Path(self.temp.name)/'mustang'
        self.lane=DedicatedPathsTest().profile(self.home)
        (self.home/'rose-actions').mkdir(parents=True)
        self.archive=self.home/'inputs/baseline/13280698927.zip'; self.archive.parent.mkdir(parents=True)
        config=ota.config(); self.build=config['baseline']['build']; self.epoch=config['baseline']['timestamp']
        config['baseline']['source']=SHA
        self.write('os/ota/mustang.json',config)
        with zipfile.ZipFile(self.sdk.path) as archive:
            self.components={('system.img','/system/priv-app/Updater/Updater.apk'):archive.read('SYSTEM/priv-app/Updater/Updater.apk'),
                ('system.img','/system/etc/security/otacerts.zip'):archive.read('SYSTEM/etc/security/otacerts.zip'),
                ('vendor.img','/build.prop'):f'ro.product.vendor.device=mustang\nro.vendor.build.version.incremental={self.build}\nro.vendor.build.date.utc={self.epoch}\n'.encode()}
        self.public_bytes=b'synthetic AVB public-key fixture; not a credential'
        self.public=dict(otaCertificateSha256=self.sdk.pin,avbPublicKeySha256=hashlib.sha256(self.public_bytes).hexdigest())
        self.vbmeta=self.vbmeta_image([self.hash_descriptor('system'),self.hash_descriptor('vendor')])
        sparse=struct.pack('<I4H4I',0xED26FF3A,1,0,28,12,4096,1,0,0)
        self.members={'script.txt':b'never execute this fixture','flash-all.sh':b'exit 97',
            'android-info.txt':b'require board=mustang\n','avb_pkmd.bin':self.public_bytes,
            'vbmeta.img':self.vbmeta,'boot.img':bytes(4096),'super_1.img':sparse}
        self.tool_rows={}
        for name in sorted(self.image.CORE|{'debugfs','fsckErofs'}):
            file=Path(self.temp.name)/'tools'/name; file.parent.mkdir(exist_ok=True)
            file.write_text('synthetic non-executed tool '+name); file.chmod(0o700)
            self.tool_rows[name]=dict(path=str(file),sha256=ota.digest(file))
        self.runtime=dict(schema='roseos.mustang-dedicated-runtime.v1',status='bound',dataFilesystemUuid='fixture-data',baselineTargetFiles=None,
            baselineFactoryEvidence='os/ota/factory-input.json',tools={k:self.tool_rows[k]['path'] for k in ('aapt','apksigner')})
        self.calls=[]; self.fail_tool=None; self.stop_tool=None; self.fs_kind='ext4'; self.lp_extra=None
        self.env=dict(GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1',GITHUB_SHA='a'*40)
        identity_patch=patch.object(ota,'identities',return_value=self.public); identity_patch.start(); self.addCleanup(identity_patch.stop)
        run_patch=patch.dict(os.environ,self.env); run_patch.start(); self.addCleanup(run_patch.stop)
        self.pack()

    @staticmethod
    def hash_descriptor(name):
        encoded=name.encode(); row=bytearray(((132+len(encoded)+32+7)//8)*8)
        struct.pack_into('>QQQ',row,0,2,len(row)-16,4096); row[24:30]=b'sha256'
        struct.pack_into('>III',row,56,len(encoded),0,32); row[132:132+len(encoded)]=encoded
        return bytes(row)

    def chain_descriptor(self,name,key):
        encoded=name.encode(); row=bytearray(((92+len(encoded)+len(key)+7)//8)*8)
        struct.pack_into('>QQIIII',row,0,4,len(row)-16,1,len(encoded),len(key),0)
        row[92:92+len(encoded)]=encoded; row[92+len(encoded):92+len(encoded)+len(key)]=key
        return bytes(row)

    def vbmeta_image(self,descriptors,public=None):
        key=public or self.public_bytes; desc=b''.join(descriptors)
        auxiliary=key.ljust(64,b'\0')+desc; auxiliary=auxiliary.ljust(((len(auxiliary)+63)//64)*64,b'\0')
        header=bytearray(256); header[:4]=b'AVB0'
        struct.pack_into('>QQ',header,12,64,len(auxiliary)); struct.pack_into('>I',header,28,1)
        struct.pack_into('>QQ',header,64,0,len(key)); struct.pack_into('>QQ',header,96,64,len(desc))
        return bytes(header)+bytes(64)+auxiliary

    def write(self,name,value):
        file=self.root/name; file.parent.mkdir(parents=True,exist_ok=True)
        file.write_text(json.dumps(value)); return file

    def pack(self,extra=None):
        inner=io.BytesIO()
        with zipfile.ZipFile(inner,'w') as archive:
            for name,data in self.members.items(): archive.writestr('mustang-install-2026110302/'+name,data)
            if extra:
                name,data=extra; archive.writestr(name,data)
        name='mustang-install-2026110302.zip'; data=inner.getvalue()
        files={name:data,'CANDIDATE.md':b'synthetic candidate', 'candidate-receipt.json':b'{"fixture": true}'}
        rows=[dict(file=n,sizeBytes=len(d),sha256=hashlib.sha256(d).hexdigest()) for n,d in files.items()]
        with zipfile.ZipFile(self.archive,'w') as archive:
            for n,d in files.items(): archive.writestr(n,d)
            archive.writestr('SHA256SUMS',''.join(f'{r["sha256"]}  {r["file"]}\n' for r in rows))
        base=self.image.BASE
        self.write(base+'delivery.json',dict(artifactId=self.image.ARTIFACT_ID,build=self.build,candidateSource=SHA,
            downloadChecksumsVerified=True,artifactSizeBytes=self.archive.stat().st_size,files=rows))
        self.write(base+'package.json',dict(device='mustang',build=self.build,candidateSource=SHA,
            status='packaged-candidate',output=next(r for r in rows if r['file']==name)))
        self.write(base+'artifacts.json',dict(runId='41738359336',artifacts=[dict(id=self.image.ARTIFACT_ID,
            digest='sha256:'+ota.digest(self.archive),sizeBytes=self.archive.stat().st_size)]))
        historical=ota.read(ota.ROOT/'os/mustang-ota/41761758623/baseline.json')
        historical.update(updaterApkSha256=hashlib.sha256(self.components[('system.img','/system/priv-app/Updater/Updater.apk')]).hexdigest(),
            updaterSignerSha256=self.sdk.pin,trustedOtaCertificateSha256=[self.sdk.pin])
        self.write(base+'sign.json',dict(device='mustang',build=self.build,candidateSource=SHA,
            status='signed-command-complete',outputs=dict(targetFiles=historical['targetFiles'])))
        file=self.write('os/mustang-ota/41761758623/baseline.json',historical)
        stage=self.write('os/mustang-ota/41761758623/mustang-ota-receipt.json',dict(baselineObservationSha256=ota.digest(file)))
        self.write('os/mustang-ota/41761758623/publication.json',dict(stageReceiptSha256=ota.digest(stage),
            status='ota-published',publicDownloadVerified=True))
        pins=self.image.source_pins(self.root)
        archive=dict(pins['artifact'],path=str(self.archive))
        transfer=self.write('os/ota/fixture-transfer.json',dict(schema='roseos.mustang-baseline-transfer.v1',
            approved=True,completed=True,approvalRef='synthetic transfer',retrievalApprovalRef='synthetic retrieval',
            evidenceRef='synthetic receiver',archive=archive,builder=self.lane['vm'],dataFilesystemUuid='fixture-data'))
        self.value=dict(schema=self.image.SCHEMA,status='bound',archive=archive,installer=pins['installer'],sourceRecords=pins['sourceRecords'],
            transferEvidence=dict(path='os/ota/fixture-transfer.json',sha256=ota.digest(transfer)),tools=self.tool_rows,
            limits=dict(seconds=60,workBytes=16<<20,reserveBytes=1))
        self.write('os/ota/factory-input.json',self.value)

    def supervise(self,argv,cwd,env,seconds,limits,**kwargs):
        name=Path(argv[0]).name; self.calls.append((name,argv,env,limits,kwargs))
        result=dict(returncode=1 if self.fail_tool==name else 0,stopped='synthetic stop' if self.stop_tool==name else None,
                    consumedBytes=0,enforcement='sampled-stop',stdout='')
        if result['returncode'] or result['stopped']: return result
        if name=='simg2img': Path(argv[-1]).write_bytes(bytes(4096))
        elif name=='lpunpack':
            directory=Path(argv[-1]); data=bytearray(4096)
            if self.fs_kind=='ext4': data[1080:1082]=b'\x53\xef'
            elif self.fs_kind=='erofs': data[1024:1028]=struct.pack('<I',0xE0F5E1E2)
            for part in ('system_a.img','vendor_a.img'): (directory/part).write_bytes(data)
            if self.lp_extra: (directory/self.lp_extra).write_bytes(b'nonempty')
        elif name=='debugfs':
            tokens=argv[2].split(); inside,destination=tokens[-2:]
            Path(destination).write_bytes(self.components[(Path(argv[-1]).name,inside)])
        elif name=='fsckErofs':
            directory=Path(argv[1].split('=',1)[1]); image=Path(argv[-1]).name
            for (part,inside),data in self.components.items():
                if part!=image: continue
                path=directory/inside.lstrip('/'); path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
        elif name in ('aapt','apksigner'):
            result['stdout']=self.sdk.execute([name,*argv[1:]]).stdout
        return result

    def observe(self,out='observation.json'):
        return self.image.observe(self.value,self.lane,self.runtime,Path(self.temp.name)/out,
                                  root=self.root,supervise=self.supervise)

    def test_factory_images_pass_with_exact_provenance_and_bounded_clean_processes(self):
        before=self.archive.read_bytes(); record=self.observe()
        self.assertEqual(record['status'],'passed'); self.assertEqual(record['evidenceKind'],'factory-images')
        self.assertNotIn('targetFiles',record); self.assertFalse(record['phoneObserved']); self.assertFalse(record['workRetained'])
        self.assertEqual(self.archive.read_bytes(),before)
        self.image.require_receipt(record,self.value,self.lane,self.runtime,self.root)
        avb=next(call for call in self.calls if call[0]=='avbtool')
        self.assertIn('--follow_chain_partitions',avb[1]); self.assertNotIn('--accept_zeroed_hashtree',avb[1])
        for name,argv,env,limits,kwargs in self.calls:
            self.assertNotIn(name,('bash','fastboot','gcloud','gh'))
            self.assertEqual(set(env),{'PATH','LANG','LC_ALL','HOME'})
            self.assertTrue(limits['checkReserve']); self.assertTrue(kwargs['watch'])

    def test_erofs_is_read_only_extraction_after_avb_and_missing_reader_refuses(self):
        self.fs_kind='erofs'; self.observe()
        names=[c[0] for c in self.calls]; self.assertLess(names.index('avbtool'),names.index('fsckErofs'))
        self.value['tools']=dict(self.tool_rows); self.value['tools'].pop('fsckErofs')
        with self.assertRaisesRegex(ValueError,'filesystem tool unavailable'): self.observe('missing-reader.json')

    def test_unbound_conflicting_transfer_or_changed_source_refuses_before_work(self):
        for key,value in [('status','unbound'),('archive',{}),('sourceRecords',{})]:
            changed=dict(self.value,**{key:value})
            with self.subTest(key=key),self.assertRaises(ValueError):
                self.image.contract(changed,self.lane,self.runtime,self.root)
        transfer=self.root/self.value['transferEvidence']['path']; record=json.loads(transfer.read_text())
        for key,value in [('approved',False),('retrievalApprovalRef',''),('builder',dict(self.lane['vm'],instanceId='999'))]:
            transfer.write_text(json.dumps(dict(record,**{key:value})))
            self.value['transferEvidence']['sha256']=ota.digest(transfer)
            with self.assertRaises(ValueError): self.image.contract(self.value,self.lane,self.runtime,self.root)
        self.assertEqual(self.calls,[])

    def test_bad_outer_hash_records_failure_and_never_uses_tools(self):
        data=self.archive.read_bytes(); self.archive.write_bytes(b'x'+data[1:])
        with self.assertRaisesRegex(ValueError,'outer digest'): self.observe()
        record=json.loads((Path(self.temp.name)/'observation.json').read_text())
        self.assertEqual(record['status'],'failed'); self.assertTrue(record['workRetained']); self.assertEqual(self.calls,[])

    def test_unsafe_or_missing_or_noncontiguous_factory_entries_refuse(self):
        cases=[('../escape',b'x'),('mustang-install-2026110302/../escape',b'x'),
               ('mustang-install-2026110302/script.txt',b'duplicate')]
        for i,extra in enumerate(cases):
            self.pack(extra)
            with self.assertRaises(ValueError): self.observe(str(i)+'.json')
            # Only this fixture attempt is removed, never an original input.
            shutil.rmtree(self.home/'rose-actions/mustang-baseline-123-1')
        del self.members['super_1.img']; self.members['super_2.img']=b'not contiguous'; self.pack()
        with self.assertRaisesRegex(ValueError,'noncontiguous'): self.observe('gap.json')

    def test_missing_image_and_ambiguous_slot_refuse(self):
        self.lp_extra='system_b.img'
        with self.assertRaisesRegex(ValueError,'inactive logical partition'): self.observe()

    def test_unsigned_disabled_and_wrong_avb_public_identity_refuse_before_avb_command(self):
        original=self.members['vbmeta.img']
        for i,(offset,value) in enumerate([(28,0),(120,1),(120,2)]):
            changed=bytearray(original); struct.pack_into('>I',changed,offset,value)
            self.members['vbmeta.img']=bytes(changed); self.pack()
            with self.assertRaisesRegex(ValueError,'verification-disabled'): self.observe(str(i)+'.json')
            shutil.rmtree(self.home/'rose-actions/mustang-baseline-123-1')
        self.members['vbmeta.img']=original; self.members['avb_pkmd.bin']=b'wrong'; self.pack()
        with self.assertRaisesRegex(ValueError,'public AVB identity'): self.observe('key.json')
        self.assertNotIn('avbtool',[c[0] for c in self.calls])

    def test_avb_failure_or_timeout_cannot_produce_baseline_pass(self):
        self.fail_tool='avbtool'
        with self.assertRaisesRegex(ValueError,'tool failed'): self.observe()
        self.assertNotIn('debugfs',[c[0] for c in self.calls])
        record=json.loads((Path(self.temp.name)/'observation.json').read_text())
        with self.assertRaises(ValueError): self.image.require_receipt(record,self.value,self.lane,self.runtime,self.root)

    def test_avb_must_cover_both_baseline_images_and_chained_key_must_match(self):
        self.members['vbmeta.img']=self.vbmeta_image([self.hash_descriptor('system')]); self.pack()
        with self.assertRaisesRegex(ValueError,'do not authenticate'): self.observe()
        self.assertNotIn('avbtool',[c[0] for c in self.calls])
        shutil.rmtree(self.home/'rose-actions/mustang-baseline-123-1')
        self.members['vbmeta.img']=self.vbmeta_image([self.hash_descriptor('vendor'),
            self.chain_descriptor('vbmeta_system',self.public_bytes)])
        self.members['vbmeta_system.img']=self.vbmeta_image([self.hash_descriptor('system')])
        self.pack(); self.observe('chain.json')
        changed=self.vbmeta_image([self.hash_descriptor('system')],public=b'unrelated public key')
        self.members['vbmeta_system.img']=changed; self.pack()
        with self.assertRaisesRegex(ValueError,'embedded public key differs'): self.observe('bad-chain.json')

    def test_avb_descriptor_bounds_and_persistent_digests_are_not_trusted(self):
        original=self.members['vbmeta.img']
        bad=bytearray(original); struct.pack_into('>Q',bad,104,len(bad)*10)
        self.members['vbmeta.img']=bytes(bad); self.pack()
        with self.assertRaisesRegex(ValueError,'descriptor area'): self.observe()
        shutil.rmtree(self.home/'rose-actions/mustang-baseline-123-1')
        descriptor=bytearray(self.hash_descriptor('system')); struct.pack_into('>I',descriptor,64,0)
        self.members['vbmeta.img']=self.vbmeta_image([bytes(descriptor),self.hash_descriptor('vendor')]); self.pack()
        with self.assertRaisesRegex(ValueError,'persistent AVB digest'): self.observe('persistent.json')

    def test_changed_tool_and_low_space_refuse_without_a_process(self):
        file=Path(self.tool_rows['simg2img']['path']); file.write_text('changed')
        with self.assertRaisesRegex(ValueError,'tool bytes'): self.observe()
        self.assertEqual(self.calls,[])

    def test_wrong_build_bad_apk_signature_and_wrong_trust_refuse(self):
        original=copy.deepcopy(self.components)
        self.components[('vendor.img','/build.prop')]=b'ro.product.vendor.device=frankel\n'
        with self.assertRaises(ValueError): self.observe()
        self.assertEqual(json.loads((Path(self.temp.name)/'observation.json').read_text())['status'],'failed')
        shutil.rmtree(self.home/'rose-actions/mustang-baseline-123-1'); self.components=copy.deepcopy(original)
        self.sdk.pin='0'*64
        with self.assertRaisesRegex(ValueError,'Updater signer'): self.observe('signature.json')
        shutil.rmtree(self.home/'rose-actions/mustang-baseline-123-1'); self.sdk.pin=self.public['otaCertificateSha256']
        store=io.BytesIO()
        with zipfile.ZipFile(store,'w') as archive:
            archive.writestr('unrelated.pem',b'-----BEGIN CERTIFICATE-----\n'+base64.b64encode(b'other DER')+b'\n-----END CERTIFICATE-----\n')
        self.components[('system.img','/system/etc/security/otacerts.zip')]=store.getvalue()
        with self.assertRaisesRegex(ValueError,'OTA trust'): self.observe('trust.json')

    def test_changed_updater_bytes_refuse_before_sdk_observation(self):
        self.components[('system.img','/system/priv-app/Updater/Updater.apk')]=b'changed'
        with self.assertRaisesRegex(ValueError,'Updater bytes'): self.observe()
        self.assertNotIn('aapt',[c[0] for c in self.calls])

    def test_low_space_work_bound_and_stopped_tools_remain_fail_closed(self):
        with patch.object(self.image.os,'statvfs',return_value=SimpleNamespace(f_bavail=0,f_frsize=4096,f_favail=1)):
            with self.assertRaisesRegex(ValueError,'space bound'): self.observe()
        shutil.rmtree(self.home/'rose-actions/mustang-baseline-123-1')
        self.stop_tool='simg2img'
        with self.assertRaisesRegex(ValueError,'tool failed'): self.observe('stopped.json')
        shutil.rmtree(self.home/'rose-actions/mustang-baseline-123-1'); self.stop_tool=None
        self.value['limits']['workBytes']=1
        with self.assertRaisesRegex(ValueError,'work/space bound'): self.observe('bound.json')

    def test_source_archive_symlink_and_sparse_geometry_refuse(self):
        held=self.archive.with_name('saved.zip'); self.archive.rename(held); self.archive.symlink_to(held)
        with self.assertRaisesRegex(ValueError,'no-link'): self.observe()
        self.archive.unlink(); held.rename(self.archive)
        shutil.rmtree(self.home/'rose-actions/mustang-baseline-123-1')
        bad=bytearray(self.members['super_1.img']); struct.pack_into('<H',bad,4,2)
        self.members['super_1.img']=bytes(bad); self.pack()
        with self.assertRaisesRegex(ValueError,'sparse image header'): self.observe('sparse.json')

    def test_installer_symlink_and_conflicting_board_refuse(self):
        link=zipfile.ZipInfo('mustang-install-2026110302/linked'); link.create_system=3; link.external_attr=0o120777<<16
        self.pack((link,b'/outside'))
        with self.assertRaisesRegex(ValueError,'unsafe'): self.observe()
        shutil.rmtree(self.home/'rose-actions/mustang-baseline-123-1')
        self.members['android-info.txt']=b'require board=mustang\nrequire board=frankel\n'; self.pack()
        with self.assertRaisesRegex(ValueError,'board differs'): self.observe('board.json')

    def test_success_receipt_rejects_missing_stale_or_boolean_only_proofs(self):
        record=self.observe()
        changes=[dict(status='failed'),dict(runId='other'),dict(recipeSource='f'*40),dict(checks={}),
                 dict(checks={k:1 for k in self.image.CHECKS}),dict(images={}),dict(tools={}),
                 dict(checkerSha256='0'*64),dict(transferEvidence={}),dict(archive={})]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.image.require_receipt(dict(record,**change),self.value,self.lane,self.runtime,self.root)

    def test_factory_baseline_is_never_automatically_selected_or_allowed_on_legacy_host(self):
        with self.assertRaisesRegex(ValueError,'exclusive dedicated'):
            ota.observe_baseline(Path(self.temp.name)/'legacy.json',factory=self.value,runtime=self.runtime)
        current=json.loads((ota.ROOT/'os/build-only/mustang-runtime.json').read_text())
        self.assertIsNone(current['baselineFactoryEvidence']); self.assertEqual(current['status'],'bound')
        with self.assertRaisesRegex(ValueError,'explicitly selected'): ota.require_factory_baseline({})

    def test_embedded_factory_proof_is_checked_before_staged_publication_can_pass(self):
        record=self.observe()
        self.write('os/build-only/mustang-runtime.json',self.runtime)
        self.write('os/build-only/mustang-lane.json',self.lane)
        proof_module=self.image
        def modules(name,file):
            if file=='mustang-dedicated.py':
                return SimpleNamespace(reference=lambda ref,root:json.loads((root/ref).read_text()))
            if file=='mustang-baseline-image.py': return proof_module
            raise AssertionError(file)
        directory=Path(self.temp.name)/'stage'
        r=staged(directory)
        from test_mustang_edge_gate import publication_proofs
        with tempfile.TemporaryDirectory() as proofs:
            r.update(publication_proofs(Path(proofs),candidate=r['candidateSource'],recipe=r['recipeSource'],
                                       build=r['build'],epoch=r['timestamp'],run='123'))
        r.update(runId='123',baselineFactoryEvidence=record,
                 baselineObservationSha256=ota.digest(Path(self.temp.name)/'observation.json'))
        def save():
            (directory/'mustang-ota-receipt.json').write_text(json.dumps(r))
            (directory/'SHA256SUMS').write_text(''.join(f'{ota.digest(p)}  {p.name}\n' for p in sorted(directory.iterdir()) if p.name!='SHA256SUMS'))
        save()
        cfg=enabled(); cfg['baseline']['source']=SHA
        with patch.object(ota,'ROOT',self.root),patch.object(ota,'module',side_effect=modules):
            self.assertEqual(ota.verify_staged(directory,cfg)['baselineFactoryEvidence']['status'],'passed')
            r['baselineFactoryEvidence']=dict(record,checks={}); save()
            with self.assertRaises(ValueError): ota.verify_staged(directory,cfg)
            r['baselineFactoryEvidence']=record; r['baselineObservationSha256']='0'*64; save()
            with self.assertRaisesRegex(ValueError,'receipt hash'): ota.verify_staged(directory,cfg)
            r.pop('baselineFactoryEvidence'); save()
            with self.assertRaisesRegex(ValueError,'proof missing'): ota.verify_staged(directory,cfg)


if __name__=='__main__':unittest.main()
