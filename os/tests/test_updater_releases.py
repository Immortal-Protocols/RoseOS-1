"""Offline checks of Rango release discovery for the RoseOS Updater (updater-rose-releases.patch, RoseReleases,
--updater-release-index and the NEXT Rango config); no Updater build, feed or network access. The patch was
checked with git apply against the pinned Updater 52c34a55 after updater-rose-channels.patch when written."""
from pathlib import Path
import json
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PATCH = (ROOT/'os/patches/updater-rose-releases.patch').read_text()
SCRIPT = ROOT/'scripts/apply-os-changes.py'
INDEX = 'https://api.github.com/repos/Immortal-Protocols/pixel-ota/releases'
DOWNLOAD = 'https://github.com/Immortal-Protocols/pixel-ota/releases/download/'

def added(path):
    body = PATCH.split(f'diff --git a/{path} b/{path}\n', 1)[1].split('\ndiff --git ', 1)[0]
    return '\n'.join(l[1:] for l in body.splitlines() if l.startswith('+') and not l.startswith('+++'))

def run(*args):
    with tempfile.TemporaryDirectory() as missing:
        return subprocess.run([sys.executable, str(SCRIPT), str(Path(missing)/'no-source'), '--check', *args],
            capture_output=True, text=True)

class ReleasePatchTest(unittest.TestCase):
    def test_touches_only_service_and_config(self):
        self.assertEqual(sorted(re.findall(r'^diff --git a/(\S+) ', PATCH, re.M)),
            ['res/values/config.xml', 'src/app/seamlessupdate/client/Service.java'])

    def test_empty_index_keeps_the_configured_url(self):
        self.assertIn('<string name="release_index" translatable="false"></string>', added('res/values/config.xml'))
        service = added('src/app/seamlessupdate/client/Service.java')
        self.assertIn('baseUrl = getString(R.string.url);', service)
        self.assertIn('if (!releaseIndex.isEmpty()) {', service)
        # Every fetch and the streaming payload go through the one resolved base.
        self.assertIn('final URL url = new URL(baseUrl + path);', service)
        self.assertIn('engine.applyPayload(baseUrl + downloadFile', service)
        self.assertNotIn('getString(R.string.url) + ', service)

    def test_discovery_never_falls_back_or_follows_latest(self):
        service = added('src/app/seamlessupdate/client/Service.java')
        self.assertNotIn('releases/latest', service + PATCH)
        self.assertIn('throw new IOException("release discovery failed: " + choice.error);', service)
        self.assertIn('connection.setInstanceFollowRedirects(false);', service)
        self.assertIn('connection.setSSLSocketFactory(tlsSocketFactory);', service)
        self.assertIn('RoseReleases.choose(releases, false, DEVICE, channel, INCREMENTAL)', service)
        # The metadata/device/channel and package checks after discovery are untouched (nothing removed).
        self.assertEqual([l for l in PATCH.splitlines() if l.startswith('-') and not l.startswith('---')],
            ['-        final URL url = new URL(getString(R.string.url) + path);',
             '-            engine.applyPayload(getString(R.string.url) + downloadFile.replace("-streaming", ""), payloadOffset, 0, headerKeyValuePairs);'])

class PatcherOptionTest(unittest.TestCase):
    def test_index_must_be_the_same_repository_release_list(self):
        for args in (['--updater-url', DOWNLOAD, '--updater-release-index', INDEX + '/latest'],
                     ['--updater-url', 'https://github.com/Immortal-Protocols/pixel-ota/releases/latest/download/', '--updater-release-index', INDEX],
                     ['--updater-url', 'https://github.com/Other/pixel-ota/releases/download/', '--updater-release-index', INDEX],
                     ['--updater-release-index', INDEX, '--keep-upstream-updater']):
            with self.subTest(args=args):
                result = run(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('--updater-release-index must be', result.stderr)
        self.assertNotIn('--updater-release-index must be', run('--updater-url', DOWNLOAD, '--updater-release-index', INDEX).stderr)

    def test_without_the_option_frankel_is_byte_identical(self):
        script = SCRIPT.read_text()
        self.assertIn("if a.updater_release_index:projects['packages/apps/Updater'].append('updater-rose-releases.patch')", script)
        self.assertIn("if str(relative) == UPDATER_RELEASES and not a.updater_release_index:continue", script)
        frankel = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())['host']['integrate']['reconcile']['applyArgs']
        self.assertNotIn('--updater-release-index', frankel)
        self.assertIn('https://github.com/Immortal-Protocols/pixel-ota/releases/latest/download/', frankel)

class NextRangoConfigTest(unittest.TestCase):
    def test_next_config_differs_from_the_retained_recipe_only_in_the_updater(self):
        retained = json.loads((ROOT/'os/build-only/rango-reconcile.json').read_text())
        nxt = json.loads((ROOT/'os/build-only/rango-reconcile-next.json').read_text())
        args = nxt['host']['integrate']['reconcile']['applyArgs']
        self.assertNotIn('--keep-upstream-updater', args)
        self.assertEqual(args[args.index('--updater-url') + 1], DOWNLOAD)
        self.assertEqual(args[args.index('--updater-release-index') + 1], INDEX)
        self.assertEqual(args[args.index('--updater-published-channel') + 1], 'stable')
        expected = [a for a in retained['host']['integrate']['reconcile']['applyArgs'] if a != '--keep-upstream-updater']
        self.assertEqual([a for a in args if a not in ('--updater-url', DOWNLOAD, '--updater-release-index', INDEX,
            '--updater-published-channel', 'stable')], expected)
        a, b = json.loads(json.dumps(retained['host'])), json.loads(json.dumps(nxt['host']))
        a['integrate']['reconcile'].pop('applyArgs'); b['integrate']['reconcile'].pop('applyArgs')
        self.assertEqual(a, b, 'host')
        # RANGO-CURRENT-M001: the NEXT build binds no historical product or 2901 app bytes, only candidate pins.
        self.assertNotIn('product', nxt)
        self.assertNotIn('inputs', nxt)
        pins = nxt['candidateInputs']
        self.assertEqual(pins['appSignerSha256'], retained['inputs']['appSignerSha256'])
        self.assertEqual((pins['sdk'], pins['prototypeControl']), (37, False))
        self.assertEqual({a['package']: a['role'] for a in pins['apps']},
            {a['package']: a['role'] for a in retained['inputs']['apps']})
        # The retained recipe the published 2026103001 is bound to is untouched.
        release = json.loads((ROOT/'os/retained-rango/release.json').read_text())['verification']['updater']['recipe']
        import hashlib
        self.assertEqual(hashlib.sha256((ROOT/release['path']).read_bytes()).hexdigest(), release['sha256'])

if __name__ == '__main__':
    unittest.main()
