"""OCT30-INFRA-S014: scripts/release-allocate.py, the fresh candidate allocation of 'Build & publish latest Pixel 10'.

Synthetic git repositories and observations only: no network, release, builder or dispatch."""
import calendar
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
spec = importlib.util.spec_from_file_location('release_allocate', SCRIPTS/'release-allocate.py')
ra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ra)
CONFIG = json.loads((ROOT/ra.ALLOCATION_CONFIG).read_text())


def epoch(text):
    return calendar.timegm(__import__('time').strptime(text, '%Y-%m-%dT%H:%M:%SZ'))


NOW = epoch('2026-10-30T03:30:00Z')


def manifest(package, code, name):
    return (f'<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{package}" '
        f'android:versionCode="{code}" android:versionName="{name}">\n<application/>\n</manifest>\n')


class Repo:
    """A synthetic RoseOS-1 history: R1 is the published Beta 2026102806 (apps 308/4/28); main then changes Rose Agent
    and declares 310, as the real tree does."""

    def __init__(self, test):
        self.path = Path(tempfile.mkdtemp(prefix='rose-alloc-repo-'))
        test.addCleanup(shutil.rmtree, self.path, True)
        self.git('init', '-q', '-b', 'main')
        self.write(ra.ALLOCATION_CONFIG, json.dumps(CONFIG))
        self.write('workspace/rose-agent/app/AndroidManifest.xml', manifest('dev.pixel.voice', 308, '308-home-tray-beta'))
        self.write('workspace/rose-agent/app/Main.java', 'class Main {}\n')
        self.write('workspace/pixel-calculator-agent/AndroidManifest.xml', manifest('dev.pixel.calculator.agent', 4, '1.3'))
        self.write('os/background-control/agent-pip/AndroidManifest.xml', manifest('dev.pixel.backgroundcontrol', 28, '2.1.5-pip'))
        self.write('README.md', 'rose\n')
        self.r1 = self.commit('Release 2806 source')
        self.pipeline = {'host': {'sign': {'updateNotes': 'os/release-notes/{device}-{build}.md'}}, 'versionBaselines': {'installedEvidence': {'build': '2026102701'}, 'retained': [
                {'build': '2026102806', 'source': self.r1, 'buildTimestamp': 1793220605, 'mustExceed': {'dev.pixel.voice': 308}}]},
            'appRelease': {'packageName': 'dev.pixel.voice', 'mustExceed': 307, 'published': {'versionCode': 302},
                'built': [{'versionCode': v} for v in (303, 304, 306, 307)],
                'reserved': [{'versionCode': 305, 'route': 'os-release'},
                    {'versionCode': 308, 'route': 'os-release', 'osBuild': '2026102806', 'buildTimestamp': 1793220605},
                    {'versionCode': 309, 'route': 'app-only'},
                    {'versionCode': 310, 'route': 'os-release', 'osBuild': '2026102901', 'buildTimestamp': 1793311111}]}}
        self.write(ra.PIPELINE_CONFIG, json.dumps(self.pipeline))
        self.write('workspace/rose-agent/app/Main.java', 'class Main { int hifi; }\n')
        self.write('workspace/rose-agent/app/AndroidManifest.xml', manifest('dev.pixel.voice', 310, '310-hifi-os-beta'))
        self.source = self.commit('HiFi Home and Rose Agent 310')
        # The retained baseline's source predates the pipeline record; give R1 the same record for reading.

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.path), *args], check=True, capture_output=True, text=True,
            env=dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t',
                GIT_COMMITTER_EMAIL='t@t')).stdout.strip()

    def write(self, path, text):
        target = self.path/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, message):
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD')


def observation(releases=None, attempts=None, reservations=None, drafts=True):
    return {'schema': ra.OBSERVATION_SCHEMA,
        'releases': {'complete': True, 'includesDrafts': drafts, 'items': releases if releases is not None else [
            {'tag': 'frankel-2026102806', 'draft': False, 'prerelease': False},
            {'tag': 'frankel-2026102805', 'draft': False, 'prerelease': False},
            {'tag': 'rose-agent-308', 'draft': False, 'prerelease': True}]},
        'attempts': {'complete': True, 'items': attempts if attempts is not None else ['frankel-2026102806']},
        'reservations': {'complete': True, 'items': reservations or []}}


def allocate(repo, obs=None, now=NOW, source=None, run='777'):
    return ra.allocate(source or repo.source, obs or observation(), now, run, '1', repo=repo.path)


class Allocation(unittest.TestCase):
    def test_fresh_candidate_from_the_captured_source(self):
        repo = Repo(self)
        a = allocate(repo)
        self.assertEqual((a['build'], a['tag'], a['buildTimestamp']), ('2026103001', 'frankel-2026103001', NOW))
        # Rose Agent changed since 2806: a fresh version above everything consumed (310 reserved, 309 app-only).
        # Calculator and controller sources are unchanged: they keep 4 and 28, no gratuitous bump.
        self.assertEqual(a['apps'], [['dev.pixel.voice', 311], ['dev.pixel.calculator.agent', 4], ['dev.pixel.backgroundcontrol', 28]])
        self.assertEqual(a['packages']['dev.pixel.voice']['versionName'], '311-os-beta-2026103001')
        self.assertFalse(a['packages']['dev.pixel.calculator.agent']['changedSinceRelease'])
        self.assertEqual([e['path'] for e in a['delta']], ['workspace/rose-agent/app/AndroidManifest.xml'])
        self.assertEqual(a['previous'], {'build': '2026102806', 'sourceSha': repo.r1})
        self.assertEqual((a['sourceSha'], a['recipeSha'], a['run']), (repo.source, repo.source, {'id': '777', 'attempt': '1'}))
        self.assertIs(ra.check_allocation(a), a)

    def test_allocation_and_candidate_are_deterministic(self):
        repo = Repo(self)
        a, b = allocate(repo), allocate(repo)
        self.assertEqual(a, b)
        c1, c2 = ra.derive(a, repo.path), ra.derive(b, repo.path)
        self.assertEqual(c1, c2)
        self.assertEqual(ra.check_candidate(a, c1, repo.path), c1)
        self.assertEqual(repo.git('rev-list', '--parents', '-n', '1', c1).split()[1:], [repo.source])
        self.assertEqual(repo.git('diff', '--name-only', repo.source, c1).split(),
            ['os/release-notes/frankel-2026103001.md', 'workspace/rose-agent/app/AndroidManifest.xml'])
        self.assertIn('android:versionCode="311" android:versionName="311-os-beta-2026103001"',
            repo.git('show', f'{c1}:workspace/rose-agent/app/AndroidManifest.xml'))
        self.assertEqual(repo.git('rev-parse', 'main'), repo.source)  # no ref moved, nothing pushed

    def test_main_moving_after_dispatch_leaves_the_candidate_unchanged(self):
        repo = Repo(self)
        before = allocate(repo)
        candidate = ra.derive(before, repo.path)
        repo.write('workspace/pixel-calculator-agent/Calc.java', 'class Calc { int later; }\n')
        repo.write('workspace/rose-agent/app/Main.java', 'class Main { int later; }\n')
        later = repo.commit('Later main change')
        after = allocate(repo)  # still the captured source
        self.assertEqual(after, before)
        self.assertEqual(ra.derive(after, repo.path), candidate)
        self.assertNotIn(later, repo.git('log', '--format=%H', candidate))
        with self.assertRaisesRegex(ra.Refused, 'candidate_mismatch'):
            ra.check_candidate(before, later, repo.path)

    def test_fresh_over_drafts_failed_attempts_and_reservations(self):
        repo = Repo(self)
        base = observation()
        obs = copy.deepcopy(base)
        obs['releases']['items'].append({'tag': 'frankel-2026103001', 'draft': True, 'prerelease': False})
        self.assertEqual(allocate(repo, obs)['build'], '2026103002')
        obs['attempts']['items'] += ['frankel-2026103002', 'frankel-2026103003-resume-41']
        self.assertEqual(allocate(repo, obs)['build'], '2026103004')
        # The earlier hand-dispatched -attemptN-<run> directories are consumed builds too.
        legacy = copy.deepcopy(obs)
        legacy['attempts']['items'] += ['frankel-2026102802-attempt1-40634711221', 'frankel-2026103004-attempt2-7']
        self.assertEqual(allocate(repo, legacy)['build'], '2026103005')
        first = allocate(repo, obs)
        record = dict(first, candidateSha=ra.derive(first, repo.path))
        obs['reservations']['items'].append(record)
        second = allocate(repo, obs, now=NOW + 60, run='778')
        # A reserved but unused allocation is consumed: never the same build, version or timestamp again.
        self.assertEqual(second['build'], '2026103005')
        self.assertEqual(second['apps'][0], ['dev.pixel.voice', 312])
        self.assertGreater(second['buildTimestamp'], first['buildTimestamp'])

    def test_timestamp_moves_past_every_known_timestamp(self):
        repo = Repo(self)
        a = allocate(repo, now=epoch('2026-10-29T12:00:00Z'))
        self.assertEqual(a['build'], '2026102902')  # 2026102901 is a reserved OS build
        self.assertEqual(a['buildTimestamp'], 1793311112)

    def test_midnight_sequence_and_clock(self):
        repo = Repo(self)
        obs = observation(attempts=['frankel-2026103001'])
        self.assertEqual(allocate(repo, obs, now=epoch('2026-10-30T23:59:59Z'))['build'], '2026103002')
        self.assertEqual(allocate(repo, obs, now=epoch('2026-10-31T00:00:01Z'))['build'], '2026103101')
        full = observation(attempts=['frankel-2026103099'])
        with self.assertRaisesRegex(ra.Refused, 'build_sequence_exhausted'):
            allocate(repo, full)
        future = observation(attempts=['frankel-2026110101'])
        with self.assertRaisesRegex(ra.Refused, 'clock_behind'):
            allocate(repo, future)

    def test_unchanged_package_keeps_the_previous_release_version(self):
        repo = Repo(self)
        first = allocate(repo)
        published = dict(first, candidateSha=ra.derive(first, repo.path))
        # An automated release bumped Rose Agent to 311 in its candidate only; main still declares 310.
        obs = observation(releases=[{'tag': first['tag'], 'draft': False, 'prerelease': False}],
            attempts=[first['tag']], reservations=[published])
        repo.write('workspace/pixel-calculator-agent/Calc.java', 'class Calc { int changed; }\n')
        source = repo.commit('Calculator change only')
        nxt = allocate(repo, obs, now=NOW + 3600, source=source)
        self.assertEqual(nxt['previous'], {'build': '2026103001', 'sourceSha': repo.source})
        self.assertEqual(nxt['apps'], [['dev.pixel.voice', 311], ['dev.pixel.calculator.agent', 5], ['dev.pixel.backgroundcontrol', 28]])
        # The unchanged Rose Agent keeps the released 311 (not main's lower 310), so the delta carries it.
        self.assertEqual(sorted(e['path'] for e in nxt['delta']),
            ['workspace/pixel-calculator-agent/AndroidManifest.xml', 'workspace/rose-agent/app/AndroidManifest.xml'])
        self.assertEqual(nxt['packages']['dev.pixel.calculator.agent']['versionName'], '1.3')

    def test_missing_or_incomplete_state_refuses(self):
        repo = Repo(self)
        for change, category in ((lambda o: o['releases'].update(complete=False), 'state_unavailable: incomplete releases'),
                                 (lambda o: o['attempts'].pop('items'), 'state_unavailable: incomplete attempts'),
                                 (lambda o: o['releases'].update(includesDrafts=False), 'does not include drafts'),
                                 (lambda o: o['attempts']['items'].append('scratch'), 'unrecognised attempt'),
                                 (lambda o: o['attempts']['items'].append('frankel-2026103001-attempt1'), 'unrecognised attempt'),
                                 (lambda o: o.update(schema='other'), 'no release observation')):
            obs = observation()
            change(obs)
            with self.assertRaisesRegex(ra.Refused, category):
                allocate(repo, obs)
        with self.assertRaisesRegex(ra.Refused, 'previous_release_unknown: no published'):
            allocate(repo, observation(releases=[{'tag': 'frankel-2026102806', 'draft': True, 'prerelease': False}]))
        with self.assertRaisesRegex(ra.Refused, 'previous_release_unknown: the exact source'):
            allocate(repo, observation(releases=[{'tag': 'frankel-2026102901', 'draft': False, 'prerelease': False}]))
        with self.assertRaisesRegex(ra.Refused, 'invalid'):
            allocate(repo, source='main')

    def test_malformed_manifest_refuses(self):
        repo = Repo(self)
        repo.write('workspace/rose-agent/app/AndroidManifest.xml', '<manifest package="dev.pixel.voice">\n</manifest>\n')
        source = repo.commit('Broken manifest')
        with self.assertRaisesRegex(ra.Refused, 'manifest_malformed'):
            allocate(repo, source=source)

    def test_tampered_allocation_or_candidate_refuses(self):
        repo = Repo(self)
        a = allocate(repo)
        bad = copy.deepcopy(a)
        bad['apps'][0][1] = 310
        with self.assertRaisesRegex(ra.Refused, 'allocation_invalid'):
            ra.derive(bad, repo.path)
        with self.assertRaisesRegex(ra.Refused, 'candidate_mismatch'):
            ra.check_candidate(a, repo.source, repo.path)


class CandidateNotes(unittest.TestCase):
    """host-sign reads os/release-notes/{device}-{build}.md from the candidate (run 41092991450 refused at sign without it)."""

    def test_candidate_carries_the_generated_notes_for_its_build(self):
        repo = Repo(self)
        a = allocate(repo)
        candidate = ra.derive(a, repo.path)
        text = repo.git('show', f'{candidate}:os/release-notes/frankel-2026103001.md') + '\n'
        self.assertEqual(text, ra.notes(a, repo.path))
        self.assertIn('2026103001', text)
        self.assertIn(f'Notes: os/release-notes/frankel-2026103001.md {hashlib.sha256(text.encode()).hexdigest()}',
            repo.git('cat-file', '-p', candidate))

    def test_repeat_dispatch_of_unchanged_main_gets_fresh_notes(self):
        repo = Repo(self)
        first = allocate(repo)
        published = dict(first, candidateSha=ra.derive(first, repo.path))
        again = allocate(repo, observation(reservations=[published]), run='778')
        candidate = ra.derive(again, repo.path)
        self.assertNotEqual(candidate, repo.source)
        self.assertEqual(ra.check_candidate(again, candidate, repo.path), candidate)
        self.assertIn(again['build'], repo.git('show', f'{candidate}:os/release-notes/frankel-{again["build"]}.md'))

    def test_same_candidate_in_another_checkout(self):
        repo = Repo(self)
        a = allocate(repo)
        candidate = ra.derive(a, repo.path)
        clone = Path(tempfile.mkdtemp(prefix='rose-alloc-clone-'))
        self.addCleanup(shutil.rmtree, clone, True)
        subprocess.run(['git', 'clone', '-q', str(repo.path), str(clone/'r')], check=True)
        self.assertEqual(ra.derive(json.loads(json.dumps(a, sort_keys=True)), clone/'r'), candidate)

    def test_committed_notes_for_the_build_refuse(self):
        repo = Repo(self)
        repo.write('os/release-notes/frankel-2026103001.md', 'hand written 2026103001\n')
        repo.source = repo.commit('Notes for 2026103001')
        with self.assertRaisesRegex(ra.Refused, 'notes_exist'):
            ra.derive(allocate(repo), repo.path)


class Reservation(unittest.TestCase):
    def test_concurrent_or_repeated_reservation_of_one_build_refuses(self):
        repo = Repo(self)
        a = allocate(repo)
        directory = repo.path.parent/(repo.path.name + '-reservations')
        self.addCleanup(shutil.rmtree, directory, True)
        candidate = ra.derive(a, repo.path)
        record = ra.reserve(directory, a, candidate)
        self.assertEqual(record['candidateSha'], candidate)
        self.assertEqual(oct((directory/'2026103001.json').stat().st_mode & 0o777), '0o600')
        with self.assertRaisesRegex(ra.Refused, 'reservation_exists'):
            ra.reserve(directory, a, candidate)
        # The repeated click allocates afresh past the reservation instead of sharing it.
        again = allocate(repo, observation(reservations=[json.loads((directory/'2026103001.json').read_text())]), run='778')
        self.assertEqual((again['build'], again['apps'][0][1]), ('2026103002', 312))


class Notes(unittest.TestCase):
    def test_notes_cover_exactly_the_captured_range(self):
        repo = Repo(self)
        a = allocate(repo)
        repo.write('README.md', 'later\n')
        repo.commit('Later main change')
        text = ra.notes(a, repo.path)
        self.assertIn('HiFi Home and Rose Agent 310', text)
        self.assertNotIn('Later main change', text)
        self.assertNotIn('Release 2806 source', text)
        self.assertIn(f'Source: RoseOS-1 {repo.source}', text)
        self.assertIn('dev.pixel.voice 311 (311-os-beta-2026103001): changed, new version', text)
        self.assertIn('dev.pixel.calculator.agent 4 (1.3): unchanged since the previous Beta', text)
        self.assertIn('No phone runtime acceptance is claimed', text)


class CheckedIn(unittest.TestCase):
    def test_config_names_the_os_apps_and_their_manifests(self):
        names = [p['packageName'] for p in CONFIG['packages']]
        self.assertEqual(names, ['dev.pixel.voice', 'dev.pixel.calculator.agent', 'dev.pixel.backgroundcontrol'])
        for package in CONFIG['packages']:
            text = (ROOT/package['manifest']).read_text()
            self.assertIn(f'package="{package["packageName"]}"', text)
            ra.manifest_version(text, package['manifest'])
            for path in package['sources']:
                self.assertTrue((ROOT/path).exists(), path)
        self.assertEqual((CONFIG['device'], CONFIG['channel'], CONFIG['tagPattern']), ('frankel', 'beta', 'frankel-{build}'))
        pipeline = json.loads((ROOT/ra.PIPELINE_CONFIG).read_text())
        self.assertEqual(pipeline['publication']['tag'], CONFIG['tagPattern'])

    def test_actual_ci_runs_this_suite(self):
        ci = (ROOT/'.github/workflows/release-verification.yml').read_text()
        self.assertIn('          python3 scripts/tests/test_release_allocate.py\n', ci)
        self.assertIn('python3 scripts/tests/test_release_allocate.py\n', (ROOT/'scripts/test-local.sh').read_text())


sys.path.insert(0, str(SCRIPTS))
pspec = importlib.util.spec_from_file_location('release_pipeline_for_latest', SCRIPTS/'release-pipeline.py')
pipeline = importlib.util.module_from_spec(pspec)
pspec.loader.exec_module(pipeline)
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()


def builder_repo(test):
    repo = Repo(test)
    record = json.loads((repo.path/ra.PIPELINE_CONFIG).read_text())
    home = repo.path.parent/(repo.path.name + '-home')
    (home/'rose-actions'/'frankel-2026102806').mkdir(parents=True)
    test.addCleanup(shutil.rmtree, home, True)
    record['host'] = dict(record['host'], lockPath=str(home/'frankel-build.lock'))
    record['publication'] = {'repository': 'Immortal-Protocols/pixel-ota', 'api': 'https://api.github.com'}
    repo.write(ra.PIPELINE_CONFIG, json.dumps(record))
    repo.source = repo.commit('Pipeline host record')
    return repo, home


def listing(urls, pages=None):
    def fetch(url):
        urls.append(url)
        if pages is not None:
            return pages.pop(0)
        return [{'tag_name': 'frankel-2026102806', 'draft': False, 'prerelease': False},
                {'tag_name': 'rose-agent-308', 'draft': False, 'prerelease': True}]
    return fetch


class Latest(unittest.TestCase):
    def env(self, repo, home, run='901'):
        out = home/f'out-{run}'
        out.mkdir()
        return {'GITHUB_SHA': repo.source, 'ROSE_DEVICE': 'frankel', 'HOME': str(home), 'GITHUB_RUN_ID': run,
            'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_ENV': str(out/'env'), 'GITHUB_OUTPUT': str(out/'output')}, out

    def test_normal_button_allocates_reserves_and_exports(self):
        repo, home = builder_repo(self)
        env, out = self.env(repo, home)
        urls = []
        record, exported = ra.latest(out/'allocation.json', env, repo=repo.path, fetch=listing(urls), clock=lambda: NOW)
        self.assertEqual(urls, ['https://api.github.com/repos/Immortal-Protocols/pixel-ota/releases?per_page=100&page=1'])
        self.assertEqual((exported['BUILD_NUMBER'], exported['BUILD_TIMESTAMP'], exported['CHANNEL']), ('2026103001', str(NOW), 'beta'))
        self.assertEqual(json.loads(exported['APPS']), [['dev.pixel.voice', 311], ['dev.pixel.calculator.agent', 4],
            ['dev.pixel.backgroundcontrol', 28]])
        self.assertEqual(exported['CANDIDATE_SHA'], ra.derive(record, repo.path))
        self.assertEqual(record['sourceSha'], repo.source)
        self.assertEqual(json.loads((home/'rose-actions/allocations/2026103001.json').read_text()), record)
        self.assertEqual(json.loads((out/'allocation.json').read_text()), record)
        self.assertIn(f'CANDIDATE_SHA={exported["CANDIDATE_SHA"]}\n', (out/'env').read_text())
        self.assertIn('build_number=2026103001\n', (out/'output').read_text())
        # The next click (after this attempt failed or was cancelled) moves past the reservation and the attempt.
        (home/'rose-actions/frankel-2026103001').mkdir()
        env2, out2 = self.env(repo, home, run='902')
        again, _ = ra.latest(out2/'allocation.json', env2, repo=repo.path, fetch=listing([]), clock=lambda: NOW + 5)
        self.assertEqual((again['build'], again['apps'][0][1]), ('2026103002', 312))
        self.assertGreater(again['buildTimestamp'], record['buildTimestamp'])

    def test_unavailable_builder_state_refuses_before_reserving(self):
        repo, home = builder_repo(self)
        env, out = self.env(repo, home)

        def failing(url):
            raise ra.Refused('state_unavailable: release listing failed (URLError)')
        for fetch, category in ((failing, 'release listing failed'),
                                (listing([], pages=[[{'tag_name': 'x'}] * 100] * 10), 'longer than scanned'),
                                (listing([], pages=[{'message': 'rate limited'}]), 'unreadable')):
            with self.assertRaisesRegex(ra.Refused, category):
                ra.latest(out/'allocation.json', env, repo=repo.path, fetch=fetch, clock=lambda: NOW)
        self.assertFalse((home/'rose-actions/allocations').exists())
        (home/'rose-actions/allocations').mkdir()
        (home/'rose-actions/allocations/notes.txt').write_text('x')
        with self.assertRaisesRegex(ra.Refused, 'unreadable reservation|unexpected file'):
            ra.latest(out/'allocation.json', env, repo=repo.path, fetch=listing([]), clock=lambda: NOW)
        (home/'rose-actions/allocations/notes.txt').unlink()
        (home/'rose-actions/allocations/2026103001.json').write_text(json.dumps({'schema': ra.SCHEMA, 'build': '2026103001'}))
        with self.assertRaisesRegex(ra.Refused, 'unreadable reservation'):
            ra.latest(out/'allocation.json', env, repo=repo.path, fetch=listing([]), clock=lambda: NOW)
        shutil.rmtree(home/'rose-actions')
        with self.assertRaisesRegex(ra.Refused, 'attempt storage is missing'):
            ra.latest(out/'allocation.json', env, repo=repo.path, fetch=listing([]), clock=lambda: NOW)
        with self.assertRaisesRegex(ra.Refused, 'frankel only'):
            ra.latest(out/'allocation.json', dict(env, ROSE_DEVICE='blazer'), repo=repo.path, fetch=listing([]), clock=lambda: NOW)

    def test_held_lock_refuses(self):
        repo, home = builder_repo(self)
        env, out = self.env(repo, home)
        import fcntl
        fd = os.open(home/'frankel-build.lock', os.O_RDWR | os.O_CREAT)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        with self.assertRaisesRegex(ra.Refused, 'lock_held'):
            ra.latest(out/'allocation.json', env, repo=repo.path, fetch=listing([]), clock=lambda: NOW)


class Publisher(unittest.TestCase):
    """The publisher re-derives the allocation, requires exactly the allocated apps, promotes, and refuses an existing tag."""

    def setup(self):
        repo, home = builder_repo(self)
        env = {'GITHUB_SHA': repo.source, 'ROSE_DEVICE': 'frankel', 'HOME': str(home), 'GITHUB_RUN_ID': '901', 'GITHUB_RUN_ATTEMPT': '1'}
        record, exported = ra.latest(home/'allocation.json', env, repo=repo.path, fetch=listing([]), clock=lambda: NOW)
        artifact = home/'artifact'
        artifact.mkdir()
        (artifact/'expected-identities.json').write_text(json.dumps({'apps': [
            {'packageName': p, 'versionCode': v, 'signerSha256': 'f' * 64} for p, v in record['apps']]}))
        cand = {'sha': exported['CANDIDATE_SHA'], 'build': record['build'], 'timestamp': str(record['buildTimestamp']),
            'channel': 'beta', 'device': 'frankel'}
        saved = pipeline.ROOT
        pipeline.ROOT = repo.path
        self.addCleanup(setattr, pipeline, 'ROOT', saved)
        args = type('A', (), {'allocation': str(home/'allocation.json'), 'promote': True})
        return repo, record, artifact, cand, args

    def test_notes_only_for_the_exact_promoted_allocation(self):
        repo, record, artifact, cand, args = self.setup()
        text = pipeline.latest_notes(args, cand, artifact)
        self.assertIn('RoseOS Pixel 10 (frankel) Beta build 2026103001', text)
        self.assertIn(f'Source: RoseOS-1 {repo.source}', text)
        for change, message in (({'sha': repo.source}, 'deterministic source'), ({'build': '2026103002'}, 'does not name'),
                                ({'timestamp': str(NOW + 1)}, 'does not name'), ({'channel': 'prod'}, 'does not name')):
            with self.assertRaisesRegex(pipeline.CheckFailed, message):
                pipeline.latest_notes(args, dict(cand, **change), artifact)
        with self.assertRaisesRegex(pipeline.CheckFailed, '--promote is required'):
            pipeline.latest_notes(type('A', (), {'allocation': args.allocation, 'promote': False}), cand, artifact)
        (artifact/'expected-identities.json').write_text(json.dumps({'apps': [{'packageName': 'dev.pixel.voice', 'versionCode': 310}]}))
        with self.assertRaisesRegex(pipeline.CheckFailed, 'not the allocated apps'):
            pipeline.latest_notes(args, cand, artifact)
        tampered = dict(json.loads(Path(args.allocation).read_text()), build='2026103009')
        Path(args.allocation).write_text(json.dumps(tampered))
        with self.assertRaisesRegex(pipeline.CheckFailed, 'allocation refused: allocation_invalid'):
            pipeline.latest_notes(args, cand, artifact)

    def test_explicit_private_allocation_keeps_identity_checks(self):
        repo, record, artifact, cand, args = self.setup()
        args.promote = False
        args.draft_only = True
        self.assertIn(f'Source: RoseOS-1 {repo.source}', pipeline.latest_notes(args, cand, artifact))
        with self.assertRaisesRegex(pipeline.CheckFailed, 'deterministic source'):
            pipeline.latest_notes(args, dict(cand, sha=repo.source), artifact)
        args.promote = True
        with self.assertRaisesRegex(pipeline.CheckFailed, 'without --promote'):
            pipeline.latest_notes(args, cand, artifact)
        args.promote = False
        (artifact/'expected-identities.json').write_text(json.dumps({'apps': []}))
        with self.assertRaisesRegex(pipeline.CheckFailed, 'not the allocated apps'):
            pipeline.latest_notes(args, cand, artifact)

    def test_private_publish_rejects_scope_before_artifact_or_network_access(self):
        for device, channel, promote, allocation in (
                ('mustang', 'beta', False, 'allocation.json'),
                ('frankel', 'prod', False, 'allocation.json'),
                ('frankel', 'beta', True, 'allocation.json'),
                ('frankel', 'beta', False, None)):
            with self.subTest(device=device, channel=channel, promote=promote, allocation=allocation):
                args = type('A', (), dict(draft_only=True, promote=promote, allocation=allocation))
                with self.assertRaisesRegex(pipeline.CheckFailed, '--draft-only requires'):
                    pipeline.publish(args, {}, dict(device=device, channel=channel), object())

    def test_allocated_draft_completes_without_promote_or_finalize(self):
        from unittest.mock import Mock, patch
        repo, record, artifact, cand, args = self.setup()
        args.promote = False
        args.draft_only = True
        args.artifact_dir = str(artifact)
        (artifact/'pipeline-receipt.json').write_text(json.dumps(
            dict(candidate=cand, manifestSha256='digest', simulated=False)))
        (artifact/'assets').mkdir()
        (artifact/'assets/releasekey.x509.pem').write_text('synthetic public certificate')
        config = dict(target=dict(device='frankel'), identities=dict(
            otaCertificateSha256='certificate', avbPublicKeySha256='digest'),
            publication=dict(tag='frankel-{build}', channels=dict(beta=dict(promoteLatest=True))))
        adapter = Mock(simulated=False)
        adapter.publish.return_value = artifact/'downloaded-draft'
        with patch.object(pipeline, 'sha256_file', return_value='digest'), \
                patch.object(pipeline, 'cert_sha256', return_value='certificate'), \
                patch.object(pipeline, 'manifest_tool') as verify:
            report = pipeline.publish(args, config, cand, adapter)
        self.assertFalse(report['promoted'])
        self.assertEqual(report['draft'], 'verified')
        adapter.publish.assert_called_once()
        self.assertTrue(adapter.publish.call_args.kwargs['fresh'])
        adapter.promote.assert_not_called()
        adapter.finalize_public.assert_not_called()
        self.assertEqual(verify.call_count, 3)

    def test_existing_tag_is_never_reconciled_or_overwritten(self):
        calls = []

        class Client:
            def find_release(self, tag):
                calls.append(tag)
                return {'tag_name': tag, 'draft': True}
        saved = pipeline.github_release_publisher.client_from_environment
        pipeline.github_release_publisher.client_from_environment = lambda config: Client()
        self.addCleanup(setattr, pipeline.github_release_publisher, 'client_from_environment', saved)
        os.environ['PIXEL_OTA_TOKEN'] = 'synthetic'
        self.addCleanup(os.environ.pop, 'PIXEL_OTA_TOKEN', None)
        config = {'publication': {'tag': 'frankel-{build}'}, 'target': {'device': 'frankel'}}
        cand = {'sha': 'a' * 40, 'build': '2026103001', 'timestamp': str(NOW), 'channel': 'beta', 'device': 'frankel'}
        with self.assertRaisesRegex(pipeline.CheckFailed, 'already exists in pixel-ota'):
            pipeline.Production().publish(config, cand, Path('/nonexistent'), Path('/nonexistent'), notes='n', fresh=True)
        self.assertEqual(calls, ['frankel-2026103001'])


def guard_script():
    import re
    script = re.search(r'- name: Candidate is an exact commit on main\n(?:.*\n)*?        run: \|\n((?: {10}.*\n)+)', WORKFLOW)[1]
    return '\n'.join(line for line in script.splitlines() if 'git merge-base' not in line)


class Workflow(unittest.TestCase):
    def test_the_button_is_named_and_is_the_default(self):
        self.assertTrue(WORKFLOW.startswith('name: Build & publish latest Pixel 10\n'))
        self.assertIn('        options: [latest-beta, os-release, ', WORKFLOW)
        self.assertIn('        default: latest-beta\n      device:', WORKFLOW)
        for name in ('candidate_sha', 'build_number', 'build_timestamp', 'apps'):
            block = WORKFLOW.split(f'      {name}:\n', 1)[1].split('\n      ', 4)
            self.assertIn("required: false", '\n'.join(block[:3]), name)
        self.assertIn('publishes a PUBLIC pixel-ota release and makes it the Beta Latest only after verification', WORKFLOW)
        self.assertIn('not the production channel; older releases stay for rollback', WORKFLOW)
        self.assertNotIn('schedule:', WORKFLOW)
        self.assertNotIn('push:', WORKFLOW.split('\njobs:', 1)[0])
        self.assertIn('  group: os-release-frankel\n  cancel-in-progress: false\n', WORKFLOW)

    def test_guard_admits_the_normal_button_only_without_pins(self):
        base = dict(os.environ, MODE='latest-beta', ROSE_DEVICE='frankel', CHANNEL='beta', CANDIDATE_SHA='', BUILD_NUMBER='',
            BUILD_TIMESTAMP='', APPS='', RESUME_STAGE='none', RESUME_UNSIGNED_SHA256='', RESUME_SOURCE_RUN='',
            RESUME_SIGNED_SHA256='', REUSE_BINDING='', PROMOTE='false', APP_PUBLISH='false', ALPHA_RECOVER='false',
            GITHUB_SHA='a' * 40, BOOTSTRAP_PROFILE='none', MODELS_BOOTSTRAP_RUN='', MUSTANG_PUBLISH_OTA='false',
            MUSTANG_COMPILE_RUN='', MUSTANG_BOOTSTRAP_RUN='')
        cases = [({}, True), ({'DRAFT_ONLY': 'true'}, True),
            ({'DRAFT_ONLY': 'true', 'CAMPAIGN': 'start'}, False),
            ({'DRAFT_ONLY': 'true', 'PROMOTE': 'true'}, False),
            ({'DRAFT_ONLY': 'garbage'}, False),
            ({'DRAFT_ONLY': 'true', 'MODE': 'os-release', 'CANDIDATE_SHA': 'b' * 40,
              'BUILD_NUMBER': '2026103001', 'BUILD_TIMESTAMP': str(NOW), 'APPS': '[["dev.pixel.voice", 311]]'}, False), ({'CANDIDATE_SHA': 'b' * 40}, False), ({'BUILD_NUMBER': '2026103001'}, False),
            ({'APPS': '[["dev.pixel.voice", 311]]'}, False), ({'CHANNEL': 'prod'}, False), ({'PROMOTE': 'true'}, False),
            ({'RESUME_STAGE': 'sign', 'RESUME_UNSIGNED_SHA256': 'c' * 64}, False), ({'APP_PUBLISH': 'true'}, False),
            ({'MODE': 'os-release'}, False), ({'MUSTANG_PUBLISH_OTA': 'true'}, False),
            ({'MODE': 'os-release', 'CANDIDATE_SHA': 'b' * 40, 'BUILD_NUMBER': '2026103001', 'BUILD_TIMESTAMP': str(NOW),
              'APPS': '[["dev.pixel.voice", 311]]'}, True),
            # RELEASE020: a campaign chains latest-beta only and has a fixed shape.
            ({'CAMPAIGN': 'start'}, True), ({'CAMPAIGN': f'7.{NOW}.2'}, True), ({'CAMPAIGN': 'forever'}, False),
            ({'CAMPAIGN': f'7.{NOW}.1'}, False),
            ({'MODE': 'os-release', 'CANDIDATE_SHA': 'b' * 40, 'BUILD_NUMBER': '2026103001', 'BUILD_TIMESTAMP': str(NOW),
              'APPS': '[["dev.pixel.voice", 311]]', 'CAMPAIGN': 'start'}, False)]
        for change, ok in cases:
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', guard_script()], cwd=ROOT, env={**base, **change},
                capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, ok, (change, result.stderr[-300:]))
        self.assertIn('            git merge-base --is-ancestor "$GITHUB_SHA" origin/main\n', WORKFLOW)

    def test_private_mode_uses_existing_input_and_normalizes_allocator_environment(self):
        import os_release_shape as shape
        self.assertLessEqual(len(shape.inputs(WORKFLOW)), 25)
        self.assertIn('latest-draft', shape.modes(WORKFLOW))
        normal = shape.dispatch_env(WORKFLOW)
        private = shape.dispatch_env(WORKFLOW.replace('        default: latest-beta\n', '        default: latest-draft\n', 1))
        self.assertEqual((normal['MODE'], normal['DRAFT_ONLY']), ('latest-beta', 'false'))
        self.assertEqual((private['MODE'], private['DRAFT_ONLY']), ('latest-beta', 'true'))

    def test_publisher_executes_draft_without_promotion_and_retains_allocation(self):
        import re
        import textwrap
        body = WORKFLOW.split('- name: Revalidate, draft, download-check and promote if requested', 1)[1]
        script = re.search(r'        run: \|\n((?: {10}.*\n)+)', body)[1]
        script = textwrap.dedent(script)
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp)/'python3'
            stub.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
            stub.chmod(0o755)
            base = dict(os.environ, PATH=tmp+os.pathsep+os.environ['PATH'], CAPTURE=tmp+'/args',
                        MODE='latest-beta', DRAFT_ONLY='true', EXPLICIT_PROMOTE='false', CAMPAIGN='',
                        RUNNER_TEMP=tmp, ROSE_DEVICE='frankel', CANDIDATE_SHA='a'*40,
                        BUILD_NUMBER='2026110501', BUILD_TIMESTAMP=str(NOW), CHANNEL='beta')
            for change, ok, promoted in [({}, True, False), ({'DRAFT_ONLY': 'false'}, True, True),
                    ({'DRAFT_ONLY': 'true', 'EXPLICIT_PROMOTE': 'true'}, False, False),
                    ({'CAMPAIGN': 'start'}, False, False),
                    ({'MODE': 'os-release'}, False, False),
                    ({'MODE': 'os-release', 'DRAFT_ONLY': 'false', 'EXPLICIT_PROMOTE': 'true'}, True, True)]:
                capture = Path(base['CAPTURE'])
                capture.unlink(missing_ok=True)
                result = subprocess.run(['bash', '-euo', 'pipefail', '-c', script], env={**base, **change},
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, ok, (change, result.stderr))
                self.assertEqual(capture.exists(), ok)
                if ok:
                    args = capture.read_text().splitlines()
                    self.assertEqual('--promote' in args, promoted)
                    self.assertEqual('--draft-only' in args, not promoted)
                    self.assertEqual('--allocation' in args, change.get('MODE', 'latest-beta') == 'latest-beta')
        self.assertIn("DRAFT_ONLY: ${{ inputs.mode == 'latest-draft' }}", WORKFLOW)
        self.assertIn("if: always() && inputs.mode == 'latest-beta' && inputs.campaign != ''", WORKFLOW)

    def test_builder_allocates_before_any_identity_is_used_and_publisher_takes_it(self):
        import re
        body = WORKFLOW.split('\njobs:\n', 1)[1]
        jobs = {m[1]: m[2] for m in re.finditer(r'^  ([\w-]+):\n((?:(?:    .*)?\n)*)', body, re.M)}
        builder, publisher = jobs['builder'], jobs['publisher']
        order = [builder.index(s) for s in ('release-pipeline.py preflight --job builder',
            'python3 scripts/release-allocate.py latest --out "$RUNNER_TEMP/allocation.json"', 'name: os-release-allocation-${{ github.run_id }}',
            'name: Prepare stage receipts', 'actions-gcp-login.py login', 'os-release-stage.sh build build')]
        self.assertEqual(order, sorted(order))
        self.assertIn("    if: inputs.mode == 'os-release' || (inputs.mode == 'latest-beta' || inputs.mode == 'latest-draft')\n", builder)
        self.assertIn('      build_number: ${{ steps.candidate.outputs.build_number }}\n', builder)
        self.assertIn('      CANDIDATE_SHA: ${{ needs.builder.outputs.candidate_sha }}\n', publisher)
        self.assertIn('      BUILD_NUMBER: ${{ needs.builder.outputs.build_number }}\n', publisher)
        # nothing publishes unless the builder verified the candidate (or, dedicated route, signed-builder did)
        self.assertIn('    needs: [builder, signed-builder]\n', publisher)
        self.assertIn("    if: always() && needs.builder.result == 'success' && (needs.signed-builder.result == 'success' || (needs.builder.outputs.dedicated != 'true' && needs.signed-builder.result == 'skipped'))\n", publisher)
        self.assertIn('extra=(--allocation "$RUNNER_TEMP/allocation/allocation.json")', publisher)
        self.assertIn('          name: os-release-allocation-${{ github.run_id }}\n          path: ${{ runner.temp }}/allocation\n', publisher)
        self.assertIn('          fetch-depth: 0  # latest-beta re-derives', publisher)
        for job in (builder, publisher):
            self.assertNotIn('actions: write', job)
            # Only the existing pixel-ota App token of the publisher writes; no repository write grant is added.
            self.assertEqual(job.count('contents: write'), job.count('permission-contents: write'))
        self.assertEqual(publisher.count('permission-contents: write'), 1)
        cleanup = jobs['cleanup']
        self.assertIn('builder', cleanup.split('needs: [', 1)[1].split(']')[0].split(', '))
        self.assertIn("    if: always() && needs.controller.result != 'skipped'\n", cleanup)


class AppOnlyRoute(unittest.TestCase):
    def test_app_only_versions_must_pass_latest_beta_reservations(self):
        aspec = importlib.util.spec_from_file_location('host_app_release_for_latest', SCRIPTS/'host-app-release.py')
        release = importlib.util.module_from_spec(aspec)
        aspec.loader.exec_module(release)
        home = Path(tempfile.mkdtemp(prefix='rose-alloc-home-'))
        self.addCleanup(shutil.rmtree, home, True)
        self.assertEqual(release.allocated_floor(home), 0)
        (home/'rose-actions/allocations').mkdir(parents=True)
        (home/'rose-actions/allocations/2026103001.json').write_text(json.dumps({'apps': [['dev.pixel.voice', 311],
            ['dev.pixel.calculator.agent', 4]]}))
        self.assertEqual(release.allocated_floor(home), 311)
        (home/'rose-actions/allocations/2026103002.json').write_text('not json')
        with self.assertRaisesRegex(release.AdapterFailed, 'unreadable latest-beta reservation'):
            release.allocated_floor(home)
        self.assertIn("floor = allocated_floor(env.get('HOME', ''))", (SCRIPTS/'host-app-release.py').read_text())


def app_repo(test):
    """builder_repo plus the app publication tag pattern the app allocator reads (rose-agent-{versionCode})."""
    repo, home = builder_repo(test)
    record = json.loads((repo.path/ra.PIPELINE_CONFIG).read_text())
    record['appRelease'] = dict(record['appRelease'], publication={'tag': 'rose-agent-{versionCode}'})
    repo.write(ra.PIPELINE_CONFIG, json.dumps(record))
    repo.source = repo.commit('App publication tag')
    return repo, home


def app_listing(*extra):
    return lambda url: [{'tag_name': 'frankel-2026103014', 'draft': False, 'prerelease': False},
                        {'tag_name': 'rose-agent-311', 'draft': False, 'prerelease': True}, *extra]


class AppAllocation(unittest.TestCase):
    """AGENT-BETA-AUTO: app-only allocation (release-allocate.py app) on the builder."""

    def env(self, repo, home, run='951'):
        out = home/f'app-out-{run}'
        out.mkdir()
        return {'CANDIDATE_SHA': repo.source, 'HOME': str(home), 'GITHUB_RUN_ID': run, 'GITHUB_RUN_ATTEMPT': '1',
            'GITHUB_ENV': str(out/'env'), 'GITHUB_OUTPUT': str(out/'output')}, out

    def run_app(self, repo, home, fetch, run='951', now=NOW):
        env, out = self.env(repo, home, run)
        record, exported = ra.app_latest(out/'allocation.json', env, repo=repo.path, fetch=fetch, clock=lambda: now)
        return record, exported, out

    def test_fresh_version_exceeds_every_consumed_one_including_drafts(self):
        repo, home = app_repo(self)
        # 325 is published, 326 is only a draft: both are consumed. 325 is never reused and 326 is never assumed free.
        fetch = app_listing({'tag_name': 'rose-agent-325', 'draft': False, 'prerelease': True},
                            {'tag_name': 'rose-agent-326', 'draft': True, 'prerelease': True})
        record, exported, out = self.run_app(repo, home, fetch)
        self.assertEqual((record['verdict'], record['versionCode'], record['versionName']), ('allocated', 327, '327-auto-beta'))
        self.assertEqual(json.loads(exported['APPS']), [['dev.pixel.voice', 327]])
        self.assertEqual((exported['APP_SKIP'], exported['APP_SOURCE_SHA']), ('false', repo.source))
        self.assertEqual(exported['CANDIDATE_SHA'], ra.derive_app(record, repo.path))
        self.assertEqual(json.loads((home/'rose-actions/app-allocations/327.json').read_text()), {k: v for k, v in record.items() if k != 'verdict'})
        self.assertIn(f'CANDIDATE_SHA={exported["CANDIDATE_SHA"]}\n', (out/'env').read_text())
        self.assertIn('app_skip=false\n', (out/'output').read_text())
        # The next allocation passes this reservation even though it was never published.
        again, _, _ = self.run_app(repo, home, fetch, run='952')
        self.assertEqual(again['versionCode'], 328)

    def test_candidate_is_the_source_plus_only_the_version_delta(self):
        repo, home = app_repo(self)
        record, exported, _ = self.run_app(repo, home, app_listing())
        candidate = exported['CANDIDATE_SHA']
        self.assertEqual(repo.git('rev-list', '--parents', '-n', '1', candidate).split()[1:], [repo.source])
        self.assertEqual(repo.git('diff', '--name-only', repo.source, candidate).split(), ['workspace/rose-agent/app/AndroidManifest.xml'])
        self.assertIn('android:versionCode="312" android:versionName="312-auto-beta"',
            repo.git('show', f'{candidate}:workspace/rose-agent/app/AndroidManifest.xml'))
        self.assertEqual(ra.derive_app(record, repo.path), candidate, 'deterministic')
        self.assertEqual(repo.git('rev-parse', 'main'), repo.source, 'no ref moved, nothing pushed')
        with self.assertRaisesRegex(ra.Refused, 'reservation_exists'):
            ra.reserve_app(home/'rose-actions/app-allocations', record, candidate)
        with self.assertRaisesRegex(ra.Refused, 'allocation_invalid'):
            ra.derive_app(dict(record, versionCode=313), repo.path)

    def test_already_published_sources_are_skipped_and_changed_ones_rebuilt(self):
        repo, home = app_repo(self)
        first, _, _ = self.run_app(repo, home, app_listing())
        published = app_listing({'tag_name': f'rose-agent-{first["versionCode"]}', 'draft': False, 'prerelease': True})
        repo.write('README.md', 'only docs changed\n')
        repo.source = repo.commit('Docs only')
        skipped, exported, out = self.run_app(repo, home, published, run='953')
        self.assertEqual((skipped['verdict'], exported['APP_SKIP']), ('unchanged', 'true'))
        self.assertNotIn('CANDIDATE_SHA', exported)
        self.assertIn('already published from the same Rose Agent sources', skipped['reason'])
        self.assertEqual(sorted(p.name for p in (home/'rose-actions/app-allocations').iterdir()), [f'{first["versionCode"]}.json'])
        # A draft or an unpublished reservation of the same sources is not "published": it is rebuilt fresh.
        draft_only = app_listing({'tag_name': f'rose-agent-{first["versionCode"]}', 'draft': True, 'prerelease': True})
        rebuilt, _, _ = self.run_app(repo, home, draft_only, run='954')
        self.assertEqual((rebuilt['verdict'], rebuilt['versionCode']), ('allocated', first['versionCode'] + 1))
        repo.write('workspace/rose-agent/app/Main.java', 'class Main { int newer; }\n')
        repo.source = repo.commit('Rose Agent change')
        changed, _, _ = self.run_app(repo, home, published, run='955')
        self.assertEqual((changed['verdict'], changed['versionCode']), ('allocated', first['versionCode'] + 2))

    def test_a_published_app_without_a_builder_record_is_never_assumed_unchanged(self):
        repo, home = app_repo(self)
        record, _, _ = self.run_app(repo, home, app_listing())  # newest published 311 has no app reservation here
        self.assertEqual((record['verdict'], record['previous']), ('allocated', {'versionCode': 311, 'sourceSha': None}))

    def test_unreadable_state_refuses_before_reserving(self):
        repo, home = app_repo(self)
        (home/'rose-actions/app-allocations').mkdir()
        (home/'rose-actions/app-allocations/400.json').write_text(json.dumps({'schema': ra.APP_SCHEMA, 'versionCode': 400}))
        with self.assertRaisesRegex(ra.Refused, 'unreadable app reservation'):
            self.run_app(repo, home, app_listing())
        (home/'rose-actions/app-allocations/400.json').unlink()
        with self.assertRaisesRegex(ra.Refused, 'invalid: CANDIDATE_SHA'):
            ra.app_latest(home/'x.json', {'CANDIDATE_SHA': 'main', 'HOME': str(home)}, repo=repo.path, fetch=app_listing())
        obs = observation()
        with self.assertRaisesRegex(ra.Refused, 'no app reservations observed'):
            ra.allocate_app(repo.source, obs, NOW, '1', '1', repo=repo.path)
        with self.assertRaisesRegex(ra.Refused, 'incomplete appReservations'):
            ra.allocate_app(repo.source, dict(obs, appReservations={'complete': False, 'items': []}), NOW, '1', '1', repo=repo.path)
        self.assertEqual(list((home/'rose-actions/app-allocations').iterdir()), [])

    def test_the_os_button_counts_app_reservations_and_app_tags(self):
        repo, home = app_repo(self)
        app, _, _ = self.run_app(repo, home, app_listing({'tag_name': 'rose-agent-330', 'draft': True, 'prerelease': True}))
        self.assertEqual(app['versionCode'], 331)
        env = {'GITHUB_SHA': repo.source, 'ROSE_DEVICE': 'frankel', 'HOME': str(home), 'GITHUB_RUN_ID': '960', 'GITHUB_RUN_ATTEMPT': '1'}
        record, _ = ra.latest(home/'latest.json', env, repo=repo.path, clock=lambda: NOW,
            fetch=lambda url: [{'tag_name': 'frankel-2026102806', 'draft': False, 'prerelease': False},
                               {'tag_name': 'rose-agent-330', 'draft': True, 'prerelease': True}])
        self.assertEqual(record['apps'][0], ['dev.pixel.voice', 332], 'an OS candidate never reuses an app-only version')

    def test_cli_and_workflow_route(self):
        self.assertIn("sub.add_parser('app')", (SCRIPTS/'release-allocate.py').read_text())


if __name__ == '__main__':
    unittest.main()
