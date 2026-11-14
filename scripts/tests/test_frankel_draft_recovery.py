#!/usr/bin/env python3
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
spec = importlib.util.spec_from_file_location('recovery', ROOT/'scripts/frankel-draft-recovery.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class Recovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def producer(self):
        run = dict(id=r.RUN, head_sha=r.SOURCE, run_attempt=1, head_branch='main', event='workflow_dispatch',
                   path='.github/workflows/os-release.yml', status='completed', conclusion='failure')
        jobs = [dict(name=n, conclusion='success') for n in ('guard', 'builder', 'cleanup')]
        jobs[1]['steps'] = [dict(name=n, conclusion='success') for n in (
            'Integrate (apps signed first) and build', 'Sign', 'Verify the signed candidate', 'Transfer')]
        jobs.append(dict(name='publisher', conclusion='failure'))
        return run, jobs

    def test_producer_exact_and_required_steps(self):
        run, jobs = self.producer()
        r.check_producer(run, jobs)
        for key, value in [('id', 1), ('head_sha', 'a'*40), ('run_attempt', 2), ('head_branch', 'other'),
                           ('event', 'push'), ('path', 'other'), ('status', 'in_progress'), ('conclusion', 'success')]:
            with self.subTest(key=key), self.assertRaises(r.Refused):
                r.check_producer(dict(run, **{key:value}), jobs)
        for index in range(len(jobs)):
            bad = copy.deepcopy(jobs)
            bad[index]['conclusion'] = 'skipped'
            with self.assertRaises(r.Refused): r.check_producer(run, bad)
        for index in range(4):
            bad = copy.deepcopy(jobs)
            bad[1]['steps'][index]['conclusion'] = 'failure'
            with self.assertRaises(r.Refused): r.check_producer(run, bad)
        with self.assertRaises(r.Refused): r.check_producer(run, jobs+[jobs[1]])

    def test_artifact_identity_size_digest_expiry_and_run(self):
        pin = r.ARTIFACTS['allocation']
        meta = dict(id=pin[0], name=pin[1], size_in_bytes=pin[2], digest='sha256:'+pin[3],
                    expired=False, workflow_run=dict(id=r.RUN))
        r.check_artifact(meta, pin)
        for key, value in [('id', 1), ('name', 'other'), ('size_in_bytes', 1), ('digest', 'sha256:'+'f'*64),
                           ('expired', True), ('workflow_run', dict(id=1))]:
            with self.subTest(key=key), self.assertRaises(r.Refused):
                r.check_artifact(dict(meta, **{key:value}), pin)

    def test_manual_main_first_attempt_only(self):
        env = dict(GITHUB_REPOSITORY=r.REPO, GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main',
                   GITHUB_RUN_ATTEMPT='1', GITHUB_WORKFLOW_REF=r.REPO+'/.github/workflows/frankel-draft-recovery.yml@refs/heads/main')
        r.admission(env)
        for key in env:
            with self.subTest(key=key), self.assertRaises(r.Refused): r.admission(dict(env, **{key:'wrong'}))

    def test_download_strips_token_and_checks_actual_archive(self):
        payload = b'synthetic archive'
        pin = (1, 'test', len(payload), hashlib.sha256(payload).hexdigest())
        client = Mock()
        def request(method, url, label, **kw):
            if kw.get('authenticated') is False:
                self.assertEqual(kw['limit'], len(payload))
                Path(kw['stream_to']).write_bytes(payload)
                return 200, None, len(payload)
            return 302, 'https://productionresultssa3.blob.core.windows.net/fixed?sig=synthetic', None
        client._request.side_effect = request
        r.download(client, pin, self.home/'archive')
        self.assertEqual(client._request.call_count, 2)
        with self.assertRaises(r.Refused): r.download(client, (*pin[:3], 'f'*64), self.home/'bad')

    def test_download_refuses_arbitrary_redirect_without_following(self):
        for url in ('http://productionresultssa3.blob.core.windows.net/a', 'https://evil.example/a',
                    'https://blob.core.windows.net.evil.example/a', 'https://user@a.blob.core.windows.net/a'):
            client = Mock()
            client._request.return_value = (302, url, None)
            with self.subTest(url=url), self.assertRaises(r.Refused):
                r.download(client, (1, 'a', 1, 'a'*64), self.home/'x')
            client._request.assert_called_once()

    def test_download_denial_not_retried(self):
        client = Mock()
        client._request.side_effect = r.github.PublishError('HTTP 403')
        with self.assertRaises(r.github.PublishError):
            r.download(client, r.ARTIFACTS['allocation'], self.home/'x')
        client._request.assert_called_once()

    def archive(self, entries):
        path = self.home/'input.zip'
        with zipfile.ZipFile(path, 'w') as out:
            for name, value in entries: out.writestr(name, value)
        return path

    def test_unpack_exact_set(self):
        path = self.archive([('allocation.json', '{}')])
        r.unpack(path, self.home/'out', {'allocation.json'}, 64)
        self.assertEqual((self.home/'out/allocation.json').read_text(), '{}')

    def test_unpack_refusals(self):
        link = zipfile.ZipInfo('allocation.json')
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases = [[('../outside', 'x')], [('/absolute', 'x')], [('allocation.json', 'x'*65)],
                 [('allocation.json','x'), ('allocation.json','y')], [('unexpected','x')], [(link, '/outside')], []]
        for i, entries in enumerate(cases):
            with self.subTest(i=i), self.assertRaises(r.Refused):
                r.unpack(self.archive(entries), self.home/f'out{i}', {'allocation.json'}, 64)
        self.assertFalse((self.home.parent/'outside').exists())

    def test_allocation_requires_producer_and_derived_candidate(self):
        record = dict(sourceSha=r.SOURCE, recipeSha=r.SOURCE, build=r.BUILD, device='frankel', channel='beta',
                      tag=r.TAG, run={'id': str(r.RUN), 'attempt':'1'}, candidateSha='a'*40)
        (self.home/'allocation').mkdir()
        path = self.home/'allocation/allocation.json'
        module = Mock()
        module.check_allocation.side_effect = lambda x:x
        module.derive.return_value = 'a'*40
        with patch.object(r, 'load', return_value=module):
            path.write_text(json.dumps(record)); self.assertEqual(r.allocation(self.home), record)
            for key in ('sourceSha','recipeSha','build','device','channel','tag','run','candidateSha'):
                path.write_text(json.dumps(dict(record, **{key:'wrong'})))
                with self.subTest(key=key), self.assertRaises(r.Refused): r.allocation(self.home)

    def publishing(self):
        record = dict(candidateSha='a'*40, buildTimestamp=123)
        pipe = Mock()
        client = Mock()
        client.find_release.return_value = None
        adapter = pipe.Production.return_value
        adapter.release = dict(id=123)
        adapter.client.get_release.return_value = dict(id=123, tag_name=r.TAG, draft=True, published_at=None,
                                                      html_url='https://github.com/Immortal-Protocols/pixel-ota/releases/tag/'+r.TAG)
        pipe.publish.return_value = dict(stage='publish', draft='verified', promoted=False)
        return record, pipe, client

    def test_publish_only_draft_and_original_candidate(self):
        record, pipe, client = self.publishing()
        with patch.object(r, 'allocation', return_value=record), patch.object(r, 'load', return_value=pipe), \
                patch.object(r.github, 'client_from_environment', return_value=client):
            result = r.publish(self.home)
        args, config, cand, adapter = pipe.publish.call_args.args
        self.assertTrue(args.draft_only); self.assertFalse(args.promote)
        self.assertEqual(cand['sha'], record['candidateSha'])
        self.assertEqual(cand['build'], r.BUILD)
        self.assertTrue(result['draft']); self.assertFalse(result['promoted'])
        pipe.Production.return_value.promote.assert_not_called()
        pipe.Production.return_value.finalize_public.assert_not_called()

    def test_existing_tag_refuses_before_publisher(self):
        record, pipe, client = self.publishing()
        for existing in (dict(draft=True), dict(draft=False)):
            client.find_release.return_value = existing
            with patch.object(r, 'allocation', return_value=record), patch.object(r, 'load', return_value=pipe), \
                    patch.object(r.github, 'client_from_environment', return_value=client), self.assertRaises(r.Refused):
                r.publish(self.home)
        pipe.publish.assert_not_called()

    def test_tag_race_and_mutating_alternatives_refuse(self):
        client = Mock()
        client.find_release.side_effect = [None, dict(draft=True)]
        guarded = r.NewDraftOnly(client)
        self.assertIsNone(guarded.find_release(r.TAG))
        with self.assertRaises(r.Refused): guarded.find_release(r.TAG)
        for name in ('delete_asset', 'promote', 'finalize_public'):
            with self.subTest(name=name), self.assertRaises(r.Refused): getattr(guarded, name)(123)
            getattr(client, name).assert_not_called()

    def test_uncertain_draft_readback_no_retry(self):
        record, pipe, client = self.publishing()
        pipe.Production.return_value.client.get_release.return_value = dict(tag_name=r.TAG, draft=False)
        with patch.object(r, 'allocation', return_value=record), patch.object(r, 'load', return_value=pipe), \
                patch.object(r.github, 'client_from_environment', return_value=client), self.assertRaises(r.Refused):
            r.publish(self.home)
        pipe.publish.assert_called_once()

    def test_failed_producer_prevents_download(self):
        client = Mock()
        client._request.return_value = (200,None,{})
        with patch.object(r, 'download') as download, self.assertRaises(r.Refused):
            r.ingest(self.home/'new', client)
        download.assert_not_called()

    def test_workflow_is_hosted_manual_no_builder_or_oidc(self):
        workflow = (ROOT/'.github/workflows/frankel-draft-recovery.yml').read_text()
        self.assertIn('workflow_dispatch:', workflow)
        self.assertIn('runs-on: ubuntu-24.04', workflow)
        self.assertIn('timeout-minutes: 90', workflow)
        self.assertIn('group: os-release-frankel', workflow)
        self.assertIn('actions: read', workflow)
        for forbidden in ('self-hosted', 'id-token:', 'start-builder', '--promote', 'schedule:', 'push:'):
            self.assertNotIn(forbidden, workflow)
        self.assertLess(workflow.index('recovery.py ingest'), workflow.index('Mint existing'))
        self.assertIn('persist-credentials: false', workflow)


if __name__ == '__main__': unittest.main()
