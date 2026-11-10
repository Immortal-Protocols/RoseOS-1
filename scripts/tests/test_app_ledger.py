#!/usr/bin/env python3
"""Decision D2: Frankel's Rose Agent versions in the shared ledger (scripts/app-ledger.py, release-allocate.py).

Real allocations from release-allocate.py on test_release_allocate's synthetic histories, a real local bare git
repository as the ledger (test_app_reservation_store's Remote and FakeClient), and fake HTTP openers for the artifact
fetch. No network, token, builder or real ledger is touched.
"""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import unittest
import urllib.error
import zipfile

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))
import app_reservation_store as store_module  # noqa: E402
import os_release_shape as shape  # noqa: E402
import test_app_reservation_store as tstore  # noqa: E402  (fixtures only)
import test_release_allocate as trl  # noqa: E402  (fixtures only)

ROOT = TESTS.parents[1]
spec = importlib.util.spec_from_file_location('app_ledger', ROOT/'scripts/app-ledger.py')
ledger_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger_cli)
ALLOC = ledger_cli.ALLOC  # an unpatched release-allocate instance
STORE = tstore.STORE
REF = 'Immortal-Protocols/RoseOS-1/.github/workflows/os-release.yml@refs/heads/main'


def empty(repo=None, source=None, reader=None):
    return {'reservations': {}, 'floors': []}


def holding(floor=0, reserved=()):
    """A ledger read with one builder floor and other runs' reservations."""
    owner = {'workflowRef': REF, 'runId': '777', 'runAttempt': '1'}
    found = {'reservations': {v: store_module.reservation('dev.pixel.voice', v, 'a' * 40, 'b' * 40, 'c' * 64,
                                                          dict(owner, runId=str(800 + v)), STORE) for v in reserved},
             'floors': [store_module.floor('dev.pixel.voice', floor, 'd' * 64, owner, STORE)] if floor else []}
    return lambda repo, source, reader=None: found


class Fixture(unittest.TestCase):
    def latest(self, ledger=empty, run='901'):
        repo, home = trl.builder_repo(self)
        out = home/f'out-{run}'
        out.mkdir()
        env = {'GITHUB_SHA': repo.source, 'ROSE_DEVICE': 'frankel', 'HOME': str(home), 'GITHUB_RUN_ID': run,
               'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_WORKFLOW_REF': REF}
        record, _ = ALLOC.latest(out/'allocation.json', env, repo=repo.path, fetch=trl.listing([]), clock=lambda: trl.NOW,
                                 ledger=ledger)
        return record, env, out/'allocation.json'

    def app(self, ledger=empty, run='951'):
        repo, home = trl.app_repo(self)
        out = home/f'app-{run}'
        out.mkdir()
        env = {'CANDIDATE_SHA': repo.source, 'HOME': str(home), 'GITHUB_RUN_ID': run, 'GITHUB_RUN_ATTEMPT': '1',
               'GITHUB_WORKFLOW_REF': REF}
        record, _ = ALLOC.app_latest(out/'allocation.json', env, repo=repo.path, fetch=trl.app_listing(),
                                     clock=lambda: trl.NOW, ledger=ledger)
        return record, env

    def ledger(self):
        self.remote = tstore.Remote(self)
        self.client = tstore.FakeClient(self.remote)
        return store_module.Ledger(STORE, store_module.GitReader(STORE, url=str(self.remote.bare)),
                                   store_module.ApiWriter(STORE, self.client), sleep=lambda s: None)


def rehash(allocation, **change):
    """The allocation with CHANGE applied to its dev.pixel.voice package, digest recomputed."""
    edited = json.loads(json.dumps(allocation))
    edited['packages']['dev.pixel.voice'].update(change)
    body = {k: v for k, v in edited.items() if k not in ('allocationSha256', 'candidateSha')}
    edited['allocationSha256'] = hashlib.sha256(ALLOC.canonical(body)).hexdigest()
    return edited


class Allocation(Fixture):
    def test_the_builder_counts_the_ledger_and_records_what_it_consumed(self):
        plain, _, _ = self.latest()
        voice = plain['packages']['dev.pixel.voice']
        self.assertTrue(voice['changedSinceRelease'])
        self.assertEqual(voice['consumedThrough'], voice['versionCode'])
        floored, _, _ = self.latest(ledger=holding(floor=400, reserved=(402,)), run='902')
        self.assertEqual(dict(floored['apps'])['dev.pixel.voice'], 403)
        self.assertEqual(floored['packages']['dev.pixel.voice']['consumedThrough'], 403)
        for name, entry in floored['packages'].items():
            self.assertGreaterEqual(entry['consumedThrough'], entry['versionCode'], name)

    def test_the_app_only_route_counts_the_ledger_too(self):
        record, _ = self.app(ledger=holding(floor=500))
        self.assertEqual((record['versionCode'], record['consumedThrough']), (501, 501))

    def test_an_incomplete_ledger_part_refuses(self):
        observation = trl.observation()
        for part in ({'complete': False, 'package': 'dev.pixel.voice', 'reservations': [], 'floor': 0},
                     {'complete': True, 'package': 'dev.pixel.voice', 'reservations': ['369'], 'floor': 0},
                     {'complete': True, 'package': 'dev.pixel.voice', 'reservations': [], 'floor': -1}):
            with self.subTest(part=part), self.assertRaisesRegex(ALLOC.Refused, 'shared version ledger'):
                ALLOC.check_observation(dict(observation, ledger=part))

    def test_the_source_binding_is_read_with_git_and_an_unreadable_ledger_refuses(self):
        repo, _ = trl.builder_repo(self)
        with self.assertRaisesRegex(ALLOC.Refused, 'binds no shared version ledger'):
            ALLOC.shared_ledger(repo.path, repo.source)
        repo.write(ALLOC.LEDGER_CONFIG, json.dumps({'reservation': {'store': STORE}}))
        source = repo.commit('Bind the ledger')
        ledger = self.ledger()
        held = store_module.reservation('dev.pixel.voice', 369, 'a' * 40, 'b' * 40, 'c' * 64,
                                        {'workflowRef': REF, 'runId': '1', 'runAttempt': '1'}, STORE)
        ledger.reserve(held)
        reader = lambda store: store_module.GitReader(store, url=str(self.remote.bare))  # noqa: E731
        self.assertEqual(ALLOC.shared_ledger(repo.path, source, reader=reader)['reservations'], {369: held})
        self.remote.lightweight(store_module.reservation_ref(STORE, 400))
        with self.assertRaisesRegex(ALLOC.Refused, 'shared version ledger is unreadable'):
            ALLOC.shared_ledger(repo.path, source, reader=reader)


class Records(Fixture):
    def test_a_fresh_version_needs_its_reservation_and_the_floor(self):
        allocation, env, _ = self.latest(ledger=holding(floor=400))
        floor, reservation = ledger_cli.expected(allocation, env, STORE)
        voice = allocation['packages']['dev.pixel.voice']
        owner = {'workflowRef': REF, 'runId': '901', 'runAttempt': '1'}
        self.assertEqual(floor, store_module.floor('dev.pixel.voice', voice['consumedThrough'],
                                                   allocation['allocationSha256'], owner, STORE))
        self.assertEqual(reservation, store_module.reservation('dev.pixel.voice', voice['versionCode'], allocation['sourceSha'],
                                                               allocation['candidateSha'], allocation['allocationSha256'],
                                                               owner, STORE))

    def test_a_reused_version_needs_only_the_floor(self):
        allocation, env, _ = self.latest()
        reused = rehash(allocation, changedSinceRelease=False)
        floor, reservation = ledger_cli.expected(reused, env, STORE)
        self.assertIsNone(reservation)
        self.assertEqual(floor['consumedThrough'], reused['packages']['dev.pixel.voice']['consumedThrough'])

    def test_another_runs_or_a_pre_d2_allocation_refuses(self):
        allocation, env, _ = self.latest()
        with self.assertRaisesRegex(ledger_cli.Refused, "not this run's"):
            ledger_cli.expected(allocation, dict(env, GITHUB_RUN_ID='999'), STORE)
        old = json.loads(json.dumps(allocation))
        for entry in old['packages'].values():
            entry.pop('consumedThrough')
        body = {k: v for k, v in old.items() if k not in ('allocationSha256', 'candidateSha')}
        old['allocationSha256'] = hashlib.sha256(ALLOC.canonical(body)).hexdigest()
        with self.assertRaisesRegex(ledger_cli.Refused, 'before decision D2'):
            ledger_cli.expected(old, env, STORE)
        with self.assertRaises(ALLOC.Refused):  # a tampered record never yields records
            ledger_cli.expected(dict(allocation, build='2026103099'), env, STORE)

    def test_an_app_only_allocation_reserves_its_version_and_an_unchanged_one_nothing(self):
        record, env = self.app()
        floor, reservation = ledger_cli.expected(record, env, STORE)
        self.assertEqual((reservation['versionCode'], reservation['candidateSha'], floor['consumedThrough']),
                         (record['versionCode'], record['candidateSha'], record['consumedThrough']))
        self.assertEqual(ledger_cli.record({'verdict': 'unchanged', 'sourceSha': 'a' * 40}, env, None), {'unchanged': True})


class Ledger(Fixture):
    def test_record_then_wait_on_a_real_git_ledger_and_record_again_changes_nothing(self):
        allocation, env, _ = self.latest()
        ledger = self.ledger()
        first = ledger_cli.record(allocation, env, ledger)
        self.assertEqual(first['created'], [True, True])
        version = allocation['packages']['dev.pixel.voice']['versionCode']
        self.assertEqual(first['refs'], [store_module.reservation_ref(STORE, version),
                                         store_module.floor_ref(STORE, {'runId': '901', 'runAttempt': '1'})])
        self.assertEqual(ledger_cli.wait(allocation, env, ledger, timeout=0)['reservation'], version)
        self.assertEqual(ledger_cli.record(allocation, env, ledger)['created'], [False, False])

    def test_a_version_another_lane_holds_first_is_never_built(self):
        allocation, env, _ = self.latest()
        ledger = self.ledger()
        version = allocation['packages']['dev.pixel.voice']['versionCode']
        ledger.reserve(store_module.reservation('dev.pixel.voice', version, 'a' * 40, 'b' * 40, 'c' * 64,
                                                {'workflowRef': REF, 'runId': '4242', 'runAttempt': '1'}, STORE))
        with self.assertRaisesRegex(store_module.Refused, 'already held by another record'):
            ledger_cli.record(allocation, env, ledger)
        self.assertEqual(ledger.read('dev.pixel.voice')['floors'], [], 'no floor after the version was refused')
        now = [0]
        with self.assertRaisesRegex(ledger_cli.Refused, 'held by another run'):  # at once, not at the deadline
            ledger_cli.wait(allocation, env, ledger, timeout=900, clock=lambda: now[0],
                            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
        self.assertEqual(now[0], 0)

    def test_wait_gives_up_at_its_deadline_and_retries_unreadable_reads(self):
        allocation, env, _ = self.latest()
        reads, now = [], [0]

        class Flaky:
            store = STORE

            def read(self, package):
                reads.append(package)
                if len(reads) == 1:
                    raise store_module.Refused('transient')
                return {'reservations': {}, 'floors': []}

        def sleep(seconds):
            now[0] += seconds
        with self.assertRaisesRegex(ledger_cli.Refused, r'within 60 s \(missing\)'):
            ledger_cli.wait(allocation, env, Flaky(), timeout=60, clock=lambda: now[0], sleep=sleep)
        self.assertGreater(len(reads), 2)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Artifact(unittest.TestCase):
    ENV = {'GITHUB_TOKEN': 'job-token', 'GITHUB_REPOSITORY': 'Immortal-Protocols/RoseOS-1', 'GITHUB_RUN_ID': '4300',
           'GITHUB_API_URL': 'https://api.example.test'}
    NAME = 'os-release-allocation-4300'

    def setUp(self):
        import tempfile
        import shutil
        self.tmp = Path(tempfile.mkdtemp(prefix='rose-ledger-artifact-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.calls, self.storage = [], []

    def archive(self, entries=None):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as z:
            for name, data in (entries or {'allocation.json': b'{"schema": "x"}\n'}).items():
                z.writestr(name, data)
        return buffer.getvalue()

    def openers(self, artifacts_by_poll, jobs=None, location='https://storage.example.test/a.zip', data=None):
        test = self

        class Api:
            def open(self, request, timeout=None):
                test.calls.append((request.full_url, request.get_header('Authorization')))
                if '/artifacts?' in request.full_url:
                    found = artifacts_by_poll.pop(0) if len(artifacts_by_poll) > 1 else artifacts_by_poll[0]
                    return Response(json.dumps({'artifacts': found}).encode())
                if '/jobs?' in request.full_url:
                    return Response(json.dumps({'jobs': jobs or [{'name': 'builder', 'status': 'in_progress'}]}).encode())
                raise urllib.error.HTTPError(request.full_url, 302, 'Found', {'Location': location}, None)

        class Storage:
            def open(self, request, timeout=None):
                test.storage.append((request.full_url, request.get_header('Authorization')))
                return Response(data if data is not None else test.archive())
        return Api(), Storage()

    def artifact(self, **change):
        return dict({'id': 7, 'name': self.NAME, 'expired': False,
                     'archive_download_url': 'https://api.example.test/repos/x/actions/artifacts/7/zip'}, **change)

    def fetch(self, api, storage, **kw):
        return ledger_cli.fetch_allocation(self.NAME, self.tmp/'out', self.ENV, kw.pop('timeout', 600), opener=api,
                                           plain=storage, clock=lambda: 0, sleep=lambda s: None, **kw)

    def test_it_waits_for_the_artifact_and_never_sends_the_token_to_storage(self):
        api, storage = self.openers([[], [self.artifact()]])
        result = self.fetch(api, storage)
        self.assertEqual((self.tmp/'out/allocation.json').read_bytes(), b'{"schema": "x"}\n')
        self.assertEqual(result['artifact'], 7)
        self.assertTrue(all(auth == 'Bearer job-token' for _, auth in self.calls))
        self.assertEqual(self.storage, [('https://storage.example.test/a.zip', None)])

    def test_a_builder_that_finished_without_one_means_nothing_to_record(self):
        api, storage = self.openers([[]], jobs=[{'name': 'builder', 'status': 'completed'}])
        self.assertIsNone(self.fetch(api, storage))
        self.assertFalse((self.tmp/'out').exists())

    def test_anything_but_one_plain_allocation_refuses(self):
        cases = {'two artifacts': dict(artifacts=[[self.artifact(), self.artifact(id=8)]]),
                 'plain-http storage': dict(artifacts=[[self.artifact()]], location='http://storage.example.test/a.zip'),
                 'extra files': dict(artifacts=[[self.artifact()]],
                                     data=self.archive({'allocation.json': b'{}', 'x': b'1'}))}
        for label, case in cases.items():
            api, storage = self.openers(case['artifacts'], location=case.get('location', 'https://storage.example.test/a.zip'),
                                        data=case.get('data'))
            with self.subTest(label), self.assertRaises(ledger_cli.Refused):
                self.fetch(api, storage)
            self.assertFalse((self.tmp/'out').exists())
        api, storage = self.openers([[]])
        with self.assertRaisesRegex(ledger_cli.Refused, 'within 0 s'):
            self.fetch(api, storage, timeout=0)

    def test_the_cli_needs_its_tokens(self):
        allocation = self.tmp/'allocation.json'
        allocation.write_text('{}')
        code = ledger_cli.main(['record', '--allocation', str(allocation)], env={})
        self.assertEqual(code, 4)
        code = ledger_cli.main(['fetch-allocation', '--name', self.NAME, '--job', 'builder', '--out', str(self.tmp/'o')], env={})
        self.assertEqual(code, 4)


def step(job, name):
    found = re.search(r'(?ms)^      - name: ' + re.escape(name) + r'\n(.*?)(?=^      - |\Z)', job)
    assert found, name
    return found[0]


class Workflow(unittest.TestCase):
    def setUp(self):
        self.jobs = shape.jobs()

    def test_the_builder_waits_for_the_ledger_before_it_builds_or_signs(self):
        builder = self.jobs['builder']
        names = re.findall(r'(?m)^      - name: (.+)$', builder)
        wait = "The shared version ledger holds this run's Rose Agent versions"
        self.assertEqual(names.index(wait), names.index('Allocation record') + 1)
        self.assertLess(names.index(wait), names.index('Signer access for app signing'))
        self.assertIn("        if: (inputs.mode == 'latest-beta' || inputs.mode == 'latest-draft')\n"
                      '        run: python3 scripts/app-ledger.py wait --allocation "$RUNNER_TEMP/allocation.json" --timeout 900\n',
                      step(builder, wait))
        app = self.jobs['app-builder']
        names = re.findall(r'(?m)^      - name: (.+)$', app)
        self.assertLess(names.index('Allocate Rose Agent (apps=auto)'), names.index('App allocation record'))
        self.assertLess(names.index(wait), names.index('Signer access for app signing'))
        self.assertIn("        if: env.APPS == 'auto' && env.APP_SKIP != 'true'\n", step(app, wait))
        for job in (builder, app):
            self.assertNotIn('PIXEL_OTA', job)
            self.assertNotIn('create-github-app-token', job)

    def test_only_the_hosted_app_ledger_job_writes_with_the_publisher_token(self):
        job = self.jobs['app-ledger']
        self.assertIn("    needs: controller\n    if: inputs.mode == 'latest-beta' || inputs.mode == 'latest-draft' || "
                      "(inputs.mode == 'app-only' && inputs.apps == 'auto')\n    runs-on: ubuntu-24.04\n", job)
        self.assertIn('    permissions:\n      contents: read\n      actions: read', job)
        self.assertNotIn('id-token', job)
        self.assertEqual(re.findall(r'(?m)^      - name: (.+)$', job),
                         ["This run's allocation record", 'Mint pixel-ota publisher token (the shared ledger)',
                          "Record this run's Rose Agent floor and fresh version in the shared ledger"])
        self.assertIn('          repositories: pixel-ota\n          permission-contents: write\n', job)
        for name in ('Mint pixel-ota publisher token (the shared ledger)',
                     "Record this run's Rose Agent floor and fresh version in the shared ledger"):
            self.assertIn("        if: steps.allocation.outputs.found == 'true'\n", step(job, name))


if __name__ == '__main__':
    unittest.main()
