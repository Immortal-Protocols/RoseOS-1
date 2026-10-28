"""Offline checks of the RoseOS Updater channel patch and its patcher options; no Updater build or feed access."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PATCH = (ROOT/'os/patches/updater-rose-channels.patch').read_text()
SCRIPT = ROOT/'scripts/apply-os-changes.py'

def hunks(path):
    """Added and removed lines for one file of the patch."""
    body = PATCH.split(f'diff --git a/{path} b/{path}\n', 1)[1].split('\ndiff --git ', 1)[0]
    lines = body.splitlines()
    return [l[1:] for l in lines if l.startswith('+') and not l.startswith('+++')], \
        [l[1:] for l in lines if l.startswith('-') and not l.startswith('---')]

def new_side(path):
    """The patched file's lines that appear in the patch (context and additions)."""
    body = PATCH.split(f'diff --git a/{path} b/{path}\n', 1)[1].split('\ndiff --git ', 1)[0]
    return [l[1:] for l in body.splitlines() if (l.startswith('+') and not l.startswith('+++')) or l.startswith(' ')]

def run(*args):
    with tempfile.TemporaryDirectory() as missing:
        return subprocess.run([sys.executable, str(SCRIPT), str(Path(missing)/'no-source'), '--check', *args],
            capture_output=True, text=True)

class UpdaterChannelPatchTest(unittest.TestCase):
    def test_touches_only_the_updater_channel_files(self):
        self.assertEqual(sorted(re.findall(r'^diff --git a/(\S+) ', PATCH, re.M)), sorted([
            'res/values/arrays.xml', 'res/values/config.xml', 'res/values/strings.xml',
            'src/app/seamlessupdate/client/BootReceiver.java', 'src/app/seamlessupdate/client/NotificationHandler.java',
            'src/app/seamlessupdate/client/Service.java', 'src/app/seamlessupdate/client/Settings.java']))

    def test_beta_keeps_the_existing_stable_wire_channel(self):
        text = '\n'.join(new_side('res/values/arrays.xml'))
        entries = re.findall(r'@string/(\w+)', text.split('name="channel_entries"')[1].split('</string-array>')[0])
        values = re.findall(r'<item>(\w+)</item>', text.split('name="channel_values"')[1].split('</string-array>')[0])
        self.assertEqual(list(zip(entries, values)), [
            ('rose_channel_prod', 'prod'), ('rose_channel_beta', 'stable'), ('rose_channel_alpha', 'alpha')])

    def test_new_installs_default_to_prod_and_only_stable_is_published(self):
        added, removed = hunks('res/values/config.xml')
        self.assertIn('    <string name="channel_default" translatable="false">prod</string>', added)
        self.assertIn('    <string name="channel_default" translatable="false">stable</string>', removed)
        published = '\n'.join(added[added.index('    <string-array name="channel_published" translatable="false">'):][:3]) + '\n'
        patcher = SCRIPT.read_text()
        # The patcher rewrites exactly this block with the publisher-supplied channels.
        self.assertIn(repr(published)[1:-1], patcher)
        # The url line is not part of the patch; the patcher still replaces it.
        self.assertFalse(any('name="url"' in l for l in added + removed))

    def test_unpublished_channels_are_not_fetched(self):
        added, _ = hunks('src/app/seamlessupdate/client/Service.java')
        text = '\n'.join(added)
        self.assertIn('RoseChannels.shouldFetch(SystemProperties.get("sys.update.channel", ""), channel,', text)
        self.assertIn('return;', text)
        service = PATCH.split('src/app/seamlessupdate/client/Service.java b/', 1)[1]
        # The gate sits after the channel is chosen and before the metadata request.
        self.assertLess(service.index('RoseChannels.shouldFetch'), service.index('fetching metadata for'))

    def test_stock_update_protections_are_untouched(self):
        _, removed = hunks('src/app/seamlessupdate/client/Service.java')
        for kept in ('targetBuildDate <= sourceBuildDate', 'does not match channel', 'does not match device'):
            self.assertFalse(any(kept in l for l in removed), kept)

    def test_migration_runs_on_boot_before_scheduling(self):
        added, removed = hunks('src/app/seamlessupdate/client/BootReceiver.java')
        self.assertEqual(added, ['            Settings.migrateChannel(context, context.getPackageManager().isDeviceUpgrading());'])
        self.assertEqual(removed, [])
        self.assertLess(PATCH.index('Settings.migrateChannel(context'), PATCH.index('PeriodicJob.schedule(context);'))

    def test_selector_marks_unpublished_choices(self):
        added, _ = hunks('src/app/seamlessupdate/client/Settings.java')
        self.assertIn('                    entries[i] = getString(R.string.rose_channel_unavailable_entry, entries[i]);', added)
        strings, _ = hunks('res/values/strings.xml')
        self.assertIn('    <string name="rose_channel_unavailable_entry">%s (not available yet)</string>', strings)

class PublishedChannelOptionTest(unittest.TestCase):
    def test_needs_the_channel_url(self):
        result = run('--updater-published-channel', 'stable')
        self.assertEqual(result.returncode, 2)
        self.assertIn('needs --updater-url', result.stderr)

    def test_rejects_non_token_values(self):
        for value in ('Stable', 'stable/../x', '', 'a b', 'x' * 40):
            result = run('--updater-url', 'https://example.invalid/', '--updater-published-channel', value)
            self.assertEqual(result.returncode, 2, value)
            self.assertIn('lowercase channel token', result.stderr, value)

    def test_accepts_published_tokens(self):
        result = run('--updater-url', 'https://example.invalid/', '--updater-published-channel', 'stable',
            '--updater-published-channel', 'prod')
        self.assertNotIn('channel token', result.stderr)
        self.assertNotIn('needs --updater-url', result.stderr)

if __name__ == '__main__':
    unittest.main()
