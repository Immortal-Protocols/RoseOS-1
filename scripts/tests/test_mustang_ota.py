"""Offline Mustang OTA failures and isolation. Synthetic bytes/clients, no live resource or key access."""
import base64
import copy
import datetime
import hashlib
import io
import json
from pathlib import Path
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
                   checks=dict(targetVersion=True, baselineTrust=True, otaSignatures=True, packagedUpdater=True),
                   ota=dict(file=name, sizeBytes=(root/name).stat().st_size, sha256=ota.digest(root/name)))
    (root/'mustang-ota-receipt.json').write_text(json.dumps(receipt))
    (root/'SHA256SUMS').write_text(''.join(f'{ota.digest(p)}  {p.name}\n' for p in sorted(root.iterdir())))
    return receipt


class Client:
    api, repository = 'https://api.github.com', ota.REPOSITORY
    def __init__(self, fail=None):
        self.rows = [dict(id=1, tag_name='frankel-2026110301', draft=False, assets=[], body='retained'),
                     dict(id=2, tag_name='rango-2026103001', draft=False, assets=[], body='retained')]
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
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root/'assets'
        self.r = staged(self.assets)
        self.out = self.root/'receipt'
        self.cfg = enabled()
    def test_order_downloads_before_channel_and_never_moves_other_releases(self):
        client = Client()
        result = ota.publish(client, self.assets, self.cfg, self.out)
        self.assertEqual(result['status'], 'ota-published')
        self.assertTrue(result['publicDownloadVerified'])
        self.assertEqual(client.rows[:2], client.before)
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
        client.rows += [dict(tag_name='other-'+str(i),draft=False) for i in range(297)]
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
        with self.assertRaisesRegex(ValueError,'signer'):self.observe(execute=bad)
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


if __name__=='__main__':unittest.main()
