import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
spec = importlib.util.spec_from_file_location('promotion', ROOT/'scripts/frankel-draft-promotion.py')
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)

class Promotion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)/'ota'; self.home.mkdir()
        self.bytes = b'2026110501 channel\n'
        self.manifest = {'assets':[{'name':'frankel-stable','size':len(self.bytes),'sha256':hashlib.sha256(self.bytes).hexdigest()}]}
        (self.home/'manifest.json').write_text(json.dumps(self.manifest))
        self.release = dict(id=p.RELEASE, tag_name=p.r.TAG, draft=True, prerelease=False, published_at=None,
            body=p.r.SOURCE+f' /actions/runs/{p.RECOVERY_RUN}', assets=[dict(id=123,name='frankel-stable',size=len(self.bytes),state='uploaded',digest='sha256:'+hashlib.sha256(self.bytes).hexdigest())])
        self.client = Mock()
        self.client.get_release.return_value = self.release
        self.client.download.side_effect = lambda ident,path,size: path.write_bytes(self.bytes)
        self.client.public_download.side_effect = lambda url,path,size: path.write_bytes(self.bytes)
        self.client.latest_tag.return_value = p.r.TAG
        self.client._request.return_value = (302, 'https://github.com/Immortal-Protocols/pixel-ota/releases/download/'+p.r.TAG+'/frankel-stable', None)
        self.adapter = p.ExistingDraft(self.client)
        self.cand = dict(sha=p.CANDIDATE,build=p.r.BUILD,device='frankel')

    def verify(self):
        return self.adapter.publish({},self.cand,None,self.home,fresh=True)

    def test_success_reads_then_single_patch_and_channel(self):
        directory = self.verify()
        self.client.promote.assert_not_called()
        public = dict(self.release,draft=False,published_at='2026-11-05T20:00:00Z')
        self.client.get_release.side_effect=[self.release,public]
        self.adapter.promote({},self.cand,directory)
        self.client.promote.assert_called_once_with(p.RELEASE)
        self.client.public_download.assert_called_once()
        for method in ('upload','delete_asset','create_draft','finalize_public'):
            getattr(self.client,method).assert_not_called()
        with self.assertRaises(p.r.Refused):self.adapter.promote({},self.cand,directory)
        self.client.promote.assert_called_once()

    def test_identity_and_asset_mismatches_refuse(self):
        changes=[('id',1),('tag_name','other'),('draft',False),('prerelease',True),('published_at','now'),('body','wrong'),('assets',[])]
        for key,value in changes:
            with self.subTest(key=key), self.assertRaises(p.r.Refused):
                self.adapter.check(dict(self.release,**{key:value}),self.manifest)
        for key,value in [('size',1),('state','new'),('digest','sha256:'+'0'*64)]:
            bad=copy.deepcopy(self.release);bad['assets'][0][key]=value
            with self.assertRaises(p.r.Refused):self.adapter.check(bad,self.manifest)
        self.client.promote.assert_not_called()

    def test_corrupt_download_refuses_without_patch(self):
        self.client.download.side_effect=lambda ident,path,size:path.write_bytes(b'wrong')
        with self.assertRaises(p.r.Refused):self.verify()
        self.client.promote.assert_not_called()

    def test_changed_asset_after_download_refuses_without_patch(self):
        directory=self.verify(); changed=copy.deepcopy(self.release);changed['assets'][0]['id']=999
        self.client.get_release.return_value=changed
        with self.assertRaises(p.r.Refused):self.adapter.promote({},self.cand,directory)
        self.client.promote.assert_not_called()

    def test_uncertain_patch_never_repeated(self):
        directory=self.verify();self.client.promote.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):self.adapter.promote({},self.cand,directory)
        with self.assertRaises(p.r.Refused):self.adapter.promote({},self.cand,directory)
        self.client.promote.assert_called_once()

    def test_auth_read_failure_prevents_mutation(self):
        self.client.get_release.side_effect=PermissionError()
        with self.assertRaises(PermissionError):self.verify()
        self.client.promote.assert_not_called()

    def test_latest_mismatch_stops_after_one_patch(self):
        directory=self.verify();self.client.get_release.side_effect=[self.release,dict(self.release,draft=False,published_at='now')]
        self.client.latest_tag.return_value='other'
        with self.assertRaises(p.r.Refused):self.adapter.promote({},self.cand,directory)
        self.client.promote.assert_called_once()
        self.client.public_download.assert_not_called()

    def test_public_channel_mismatch_reports_failure(self):
        directory=self.verify();self.client.get_release.side_effect=[self.release,dict(self.release,draft=False,published_at='now')]
        self.client.public_download.side_effect=lambda url,path,size:path.write_bytes(b'wrong')
        with self.assertRaises(p.r.Refused):self.adapter.promote({},self.cand,directory)
        self.client.promote.assert_called_once()

    def test_wrong_candidate(self):
        self.cand['sha']='0'*40
        with self.assertRaises(p.r.Refused):self.verify()
        self.client.download.assert_not_called()

    def test_admission(self):
        env=dict(GITHUB_REPOSITORY=p.r.REPO,GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_REF='refs/heads/main',GITHUB_RUN_ATTEMPT='1',GITHUB_WORKFLOW_REF=p.WORKFLOW)
        p.admission(env)
        for key in env:
            with self.subTest(key=key),self.assertRaises(p.r.Refused):p.admission(dict(env,**{key:'wrong'}))

    def test_latest_redirect_is_exact_anonymous_and_bounded(self):
        output=self.home/'channel'
        p.public_channel(self.client,output,len(self.bytes))
        call=self.client._request.call_args
        self.assertFalse(call.kwargs['authenticated'])
        self.assertEqual(call.kwargs['limit'],len(self.bytes))
        self.assertEqual(call.kwargs['expect'],(200,302))
        self.client.public_download.assert_called_once_with(
            'https://github.com/Immortal-Protocols/pixel-ota/releases/download/'+p.r.TAG+'/frankel-stable',output,len(self.bytes))

    def test_wrong_latest_redirect_never_followed(self):
        canonical='https://github.com/Immortal-Protocols/pixel-ota/releases/download/'+p.r.TAG+'/frankel-stable'
        for target in ['https://evil.example/file',canonical+'?token=secret',canonical.replace(p.r.TAG,'frankel-other'),
                       canonical.replace('https:','http:'),canonical.replace('github.com','github.com.evil.example')]:
            with self.subTest(target=target):
                self.client._request.return_value=(302,target,None)
                with self.assertRaises(p.r.Refused):p.public_channel(self.client,self.home/'out',37)
        self.client.public_download.assert_not_called()

    def test_direct_channel_response_needs_no_redirect(self):
        self.client._request.return_value=(200,None,37)
        p.public_channel(self.client,self.home/'out',37)
        self.client.public_download.assert_not_called()

    def test_channel_denial_not_retried_or_alternate_downloaded(self):
        self.client._request.side_effect=p.r.github.PublishError('synthetic denied response')
        with self.assertRaises(p.r.github.PublishError):p.public_channel(self.client,self.home/'out',37)
        self.client._request.assert_called_once()
        self.client.public_download.assert_not_called()

    def test_workflow_hosted_no_inputs_no_oidc_no_builder(self):
        source=(ROOT/'.github/workflows/frankel-draft-promotion.yml').read_text()
        self.assertIn('runs-on: ubuntu-24.04',source)
        self.assertIn('group: os-release-frankel',source)
        self.assertIn('timeout-minutes: 90',source)
        for forbidden in ('id-token:','self-hosted','inputs:','start-builder','stop-builder'):
            self.assertNotIn(forbidden,source)
        self.assertIn('permission-contents: write',source)

if __name__=='__main__':unittest.main()
