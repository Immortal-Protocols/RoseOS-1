"""Checked-in candidate contract for build 2026102806 (Rose Agent 308, controller 28, Calculator 4), the published
2026102805 and 2026102804 Betas and the immutable 2026102802/2026102803 drafts it supersedes, the app-only versions it must
pass (303 delivered, 304, 306 and 307 published), the SystemUI tray gate derived from the pinned sources, and the
controller's async builder start/stop, using the retained nonsecret evidence in this repository and a fake gcloud."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('release_pipeline', SCRIPTS/'release-pipeline.py')
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)
from background_apks import installed_precedence  # noqa: E402

CONFIG = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
# INFRA-CANDIDATE-115: Rose Agent 308 (308-home-tray-beta), controller 28 and Calculator 4 unchanged, build 2026102806 at
# 1793220605 (2026-10-28 21:00:05 UTC), above the published 2805 and the published app-only 307.
APPS = [['dev.pixel.voice', 308], ['dev.pixel.calculator.agent', 4], ['dev.pixel.backgroundcontrol', 28]]
DRAFT = {'build': '2026102806', 'timestamp': '1793220605'}
NEXT = APPS
# The verified 2026102802 draft's notes are immutable evidence (pixel-ota draft frankel-2026102802).
NOTES_2802_SHA256 = 'a84fac7a77cd521d53226bcf1d8b89528f6f23b0f8a0eb42b73570055a4f865f'
NOTES_2803_SHA256 = '04f91df28af8e021177a10740731849da9e605d52af2faee78f33d6fc96579cb'  # draft 426456114, immutable
NOTES_2804_SHA256 = '5e206e3db4d656f508dc322f23809b6251b47288ba9f6cf2370d3ffb58710a0e'  # published Beta 2804 UPDATE.md
NOTES_2805_SHA256 = '5b720da82a9a16f8d014aba8327978e35cb5de0f0363c067376ed20332519496'  # published Beta 2805 UPDATE.md
SYSTEMUI = SCRIPTS/'tests/fixtures/systemui-84183aff'  # unmodified pinned frameworks/base files (see its README)
TRAY_OVERLAY = ROOT/'os/overlays/frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell'

def manifest_version(path):
    text = (ROOT/path).read_text()
    return (re.search(r'package="([^"]+)"', text).group(1), int(re.search(r'android:versionCode="(\d+)"', text).group(1)),
        re.search(r'android:versionName="([^"]+)"', text).group(1))

class CheckedInCandidateTest(unittest.TestCase):
    def test_host_paths_are_the_retained_p1_paths(self):
        host = CONFIG['host']
        self.assertEqual(host['sign']['otatools'],
            'host/linux-x86/obj/ETC/otatools-packagelinux_glibc_x86_64_intermediates/otatools-packagelinux_glibc_x86_64')
        self.assertEqual(host['tools']['debugfs'], '/usr/sbin/debugfs')
        self.assertEqual(host['tools']['simg2img'],
            '/home/pixelbuild/grapheneos-2026100600/releases/2026102801/release-frankel-2026102801/bin/simg2img')
        self.assertEqual(pipeline.preflight('guard', CONFIG, {'ROSEOS_BUILDER_RUNNER_READY': 'true',
            'GCP_WORKLOAD_IDENTITY_PROVIDER': 'x', 'GCP_CONTROLLER_SERVICE_ACCOUNT': 'x', 'GCP_SIGNER_SERVICE_ACCOUNT': 'x',
            'PIXEL_OTA_PUBLISHER': 'github-app', 'PIXEL_OTA_APP_ID': '1', 'PIXEL_OTA_APP_KEY_PRESENT': 'true'}, 'production'),
            [])

    def test_updater_expectations_follow_the_patch_and_the_recipe(self):
        patch = (ROOT/'os/patches/updater-rose-channels.patch').read_text()
        updater = CONFIG['updater']
        self.assertIn(f'+    <string name="channel_default" translatable="false">{updater["channelDefault"]}</string>', patch)
        values = patch.split('<string-array name="channel_values" translatable="false">')[1].split('</string-array>')[0]
        self.assertEqual(re.findall(r'^[ +]\s*<item>(\w+)</item>', values, re.M), updater['channelValues'])
        self.assertIn('diff --git a/src/app/seamlessupdate/client/', patch)  # the Updater's own package source tree
        self.assertEqual(updater['packageName'], 'app.seamlessupdate.client')
        expected = pipeline.updater_expected(CONFIG)
        self.assertEqual(expected[('string', 'url')], 'https://github.com/Immortal-Protocols/pixel-ota/releases/latest/download/')
        self.assertEqual(expected[('array', 'channel_published')], ['stable'])  # Beta keeps the existing stable wire channel

    def test_updater_signer_is_the_release_key_pin_and_the_channel_code_is_ours(self):
        self.assertEqual(CONFIG['updater']['signer'], '2ce41f9e7efb04cd7fa61b32e09091719304f7101405c3ef7ffcf0f0f5ec268a')
        self.assertEqual(CONFIG['updater']['signer'], CONFIG['identities']['otaCertificateSha256'])
        source = (ROOT/'os/overlays/packages/apps/Updater/src/app/seamlessupdate/client/RoseChannels.java').read_text()
        self.assertTrue(source.startswith('package app.seamlessupdate.client;'))
        self.assertEqual(pipeline.ROSE_CHANNELS_DEX, b'Lapp/seamlessupdate/client/RoseChannels;')

    def test_updater_layout_is_the_one_the_os_inclusion_documents(self):
        docs = ' '.join((ROOT/'docs/OS-INTEGRATION.md').read_text().split())
        updater = CONFIG['updater']
        self.assertIn(f'`{updater["apkPath"]}` (`{updater["packageName"]}`)', docs)
        self.assertIn('`SYSTEM/etc/{permissions,default-permissions,sysconfig}/app.seamlessupdate.client.xml`', docs)
        self.assertEqual(updater['requiredFiles'], [f'SYSTEM/etc/{kind}/app.seamlessupdate.client.xml'
            for kind in ('permissions', 'default-permissions', 'sysconfig')])
        self.assertIn('updater-product-build-make.patch', (ROOT/'scripts/apply-os-changes.py').read_text())

    def test_source_manifests_match_the_allocated_versions(self):
        # 2805 bundled 305; 303 was delivered through the catalog and 304, 306 and 307 published on GitHub; the 2806
        # candidate bundled 308, reserved for the OS route. AGENT309-DELIVERY-001 reserved the app-only 309.
        # FRANKEL310-ALLOC-001 gave 310 to the Frankel 2026102901 OS candidate's Rose Agent. AGENT311-APP-ONLY: the
        # owner-requested app-only build of the APP-OS-VISUAL-1030 visuals declares 311, above every reservation.
        self.assertEqual(manifest_version('workspace/rose-agent/app/AndroidManifest.xml'), ('dev.pixel.voice', 311, '311-hifi-visual-beta'))
        reserved = {row['versionCode']: row for row in CONFIG['appRelease']['reserved']}
        self.assertEqual((reserved[309]['route'], reserved[309]['versionName'], reserved[309]['candidate']), ('app-only', '309-hifi-beta', None))
        self.assertEqual((reserved[310]['route'], reserved[310]['versionName'], reserved[310]['candidate'], reserved[310]['osBuild'],
            reserved[310]['buildTimestamp']), ('os-release', '310-hifi-os-beta', '02b21bc69f9afd281422d42e3dcf8a456e471de1', '2026102901', 1793311111))
        self.assertEqual((reserved[305]['route'], reserved[305]['versionName'], reserved[305]['candidate']),
            ('os-release', '305-home-beta', '7fb94ae6ed535066d50879a99b7c8e427da8e853'))
        self.assertEqual((reserved[308]['route'], reserved[308]['versionName'], reserved[308]['candidate'], reserved[308]['osBuild'],
            reserved[308]['buildTimestamp']), ('os-release', '308-home-tray-beta', '93ad1c2bd7094d6b65c695ba239d05d272b87c57', DRAFT['build'], int(DRAFT['timestamp'])))
        # Delivered (FAM-02-INFRA-S001): the row carries the delivered bytes and runs; it is not an app-only build.
        self.assertEqual((reserved[308]['sha256'], reserved[308]['size'], reserved[308]['signerSha256'], reserved[308]['osRun'],
            reserved[308]['run'], reserved[308]['release'], reserved[308]['delivered']['at'], reserved[308]['delivered']['publicRelease']),
            ('f4163ca86cd836861206139be74cc2cbe6a82f644bc8afd5533ab1dc6c6e7a41', 1393821, CONFIG['identities']['appSignerSha256'], 40778491188, 40787416518, 427306781,
             '2026-10-28T23:38Z', True))
        self.assertNotIn(308, [b['versionCode'] for b in CONFIG['appRelease']['built']])
        self.assertNotIn(307, reserved, 'the consumed 307 reservation is gone')
        built = {row['versionCode']: row for row in CONFIG['appRelease']['built']}
        self.assertEqual((built[307]['candidate'], built[307]['sha256'], built[307]['size'], built[307]['release'], built[307]['run']),
            ('d27dded6acac8567ebe0084d928145521da4d3bf', '8fc7e46e32408a57d3f1f616d5c81a4b88aaa96b2f703b731bc79014bdfc4f60', 1393821,
             427169403, 40765131690))
        # AGENT311-APP-ONLY: 311 is published as rose-agent-311 by run 41081939395; the 307 floor stays for the 308 replay.
        self.assertEqual((reserved[311]['route'], reserved[311]['candidate'], reserved[311]['sha256'], reserved[311]['size'], reserved[311]['run']),
            ('app-only', 'd4bb0fd23741ef472689e3878e6d3aa4f6331d23', '0e5e549f1707dd48880b8025e42c59ed896d7e45ab08628c425a785d529288cb', 1524893,
             41081939395))
        self.assertNotIn(311, built)
        self.assertEqual(CONFIG['appRelease']['mustExceed'], 307)
        self.assertEqual(CONFIG['appRelease']['publication']['preserveLatest'], 'frankel-2026102901')
        self.assertEqual(manifest_version('workspace/pixel-calculator-agent/AndroidManifest.xml')[:2], ('dev.pixel.calculator.agent', 4))
        self.assertEqual(manifest_version('os/background-control/agent-pip/AndroidManifest.xml'),
            ('dev.pixel.backgroundcontrol', 28, '2.1.5-pip'))

    def test_release_notes_describe_the_allocated_2026102901_candidate(self):
        # FRANKEL310-ALLOC-001: host-sign reads os/release-notes/frankel-2026102901.md from the candidate commit.
        path = CONFIG['host']['sign']['updateNotes'].format(device='frankel', build='2026102901')
        flat = ' '.join((ROOT/path).read_text().split())
        for expected in ('2026102901', 'frankel', 'user build', 'versionCode 310 (310-hifi-os-beta)', '28 (2.1.5-pip, unchanged)',
                '4 (unchanged)', 'not promoted', 'Beta channel (pixel-ota Latest, 2026102806) is unchanged', 'not been accepted on a phone',
                'locked sessions off', 'prototype agent list empty', 'not yet verified at runtime', 'Included since 2026102806',
                'overview button is shown but not available yet', 'only Beta is configured'):
            self.assertIn(expected, flat)
        for claim in ('downgrade', 'available on Alpha', 'available on Prod', 'battery'):
            self.assertNotIn(claim, flat)
        reserved = {row['versionCode']: row for row in CONFIG['appRelease']['reserved']}
        self.assertEqual(path, f"os/release-notes/frankel-{reserved[310]['osBuild']}.md")

    def test_watch_extras_match_between_producer_and_consumer(self):
        producer = (ROOT/'workspace/rose-agent/app/src/dev/pixel/voice/TaskControl.java').read_text()
        consumer = (ROOT/'os/background-control/agent-pip/src/dev/pixel/backgroundcontrol/WatchBinding.java').read_text()
        for extra in ('dev.pixel.backgroundcontrol.extra.WATCH_TASK_ID', 'dev.pixel.backgroundcontrol.extra.WATCH_SESSION_ID'):
            self.assertIn(f'"{extra}"', producer)
            self.assertIn(f'"{extra}"', consumer)

    def test_release_notes_bind_to_the_candidate_and_describe_the_draft(self):
        for build, digest in (('2026102802', NOTES_2802_SHA256), ('2026102803', NOTES_2803_SHA256), ('2026102804', NOTES_2804_SHA256),
                ('2026102805', NOTES_2805_SHA256)):
            self.assertEqual(hashlib.sha256((ROOT/f'os/release-notes/frankel-{build}.md').read_bytes()).hexdigest(), digest)
        path = CONFIG['host']['sign']['updateNotes'].format(device='frankel', build=DRAFT['build'])
        notes = (ROOT/path).read_text()
        flat = ' '.join(notes.split())
        for expected in ('2026102806', 'frankel', 'user build', 'versionCode 308 (308-home-tray-beta)', '28 (2.1.5-pip, unchanged)',
                '4 (unchanged)', 'not promoted', 'Beta channel (pixel-ota Latest, 2026102805) is unchanged', 'not been accepted on a phone',
                'locked sessions off', 'prototype agent list empty', 'not yet verified at runtime', 'while the screen is off',
                'reuses the Home screen', 'three trial switches', 'Stored settings are left as they are', 'Home dock labels',
                'directly from RoseOS\'s public GitHub releases', 'screen-off code in the image\'s System UI'):
            self.assertIn(expected, flat)
        for claim in ('downgrade', 'available on Alpha', 'available on Prod', 'battery'):
            self.assertNotIn(claim, flat)
        # host-sign reads the notes from the candidate commit with git show; HEAD holds the same bytes as the tree.
        head = subprocess.run(['git', '-C', str(ROOT), 'show', f'HEAD:{path}'], capture_output=True)
        if head.returncode == 0:
            self.assertEqual(head.stdout.decode(), notes)

    def test_systemui_tray_gate_follows_the_pinned_build_and_the_overlay_sources(self):
        tray = CONFIG['systemUiTray']
        shell = (SYSTEMUI/'Shell-Android.bp').read_text()
        # Every tray overlay is compiled by the Shell source glob (no build-file change), into WindowManager-Shell.
        self.assertRegex(shell, r'filegroup \{\n    name: "wm_shell-sources",\n    srcs: \[\n        "src/\*\*/\*\.java",\n    \],\n    path: "src",\n\}')
        self.assertIn('":wm_shell-sources",', shell)
        overlays = sorted(p.name for p in TRAY_OVERLAY.glob('*.java'))
        self.assertTrue({'AgentTrayLink.java', 'AgentTrayPrototype.java'} <= set(overlays), overlays)
        systemui = (SYSTEMUI/'SystemUI-Android.bp').read_text()
        app = systemui.split('android_app {\n    name: "SystemUI",', 1)[1].split('\n}\n', 1)[0]
        for line in ('system_ext_specific: true,', 'privileged: true,', '"--multi-dex"'):
            self.assertIn(line, app)
        self.assertEqual(tray['apkPath'], 'SYSTEM_EXT/priv-app/SystemUI/SystemUI.apk')
        proguard = (SYSTEMUI/'SystemUI-proguard_common.flags').read_text()
        self.assertIn('-keep class com.android.wm.shell.* {', proguard)  # class names in this package survive R8
        self.assertIn('-assumenosideeffects class android.util.Log {\n  static *** v(...);\n  static *** isLoggable(...);\n}', proguard)
        self.assertIn('-maximumremovedandroidloglevel 2', proguard)  # only verbose logs are stripped; Log.w stays
        # The markers: the two overlay classes, the service the tray binds, and OS #85's only new literal (a Log.w).
        link, prototype = (TRAY_OVERLAY/'AgentTrayLink.java').read_text(), (TRAY_OVERLAY/'AgentTrayPrototype.java').read_text()
        self.assertEqual(tray['markers'], ['Lcom/android/wm/shell/AgentTrayPrototype;', 'dev.pixel.voice.AgentTrayService',
            'Lcom/android/wm/shell/AgentTrayLink;', 'Voice binding already released'])
        self.assertIn('final class AgentTrayLink {', link)
        self.assertIn('"dev.pixel.voice.AgentTrayService"', prototype)
        self.assertIn('Log.w(TAG, "Voice binding already released", e)', prototype)
        self.assertIn('new AgentTrayLink(', prototype)  # the prototype drives the new lifecycle, so the class is reachable
        for other in TRAY_OVERLAY.glob('*.java'):
            if other.name != 'AgentTrayPrototype.java':
                self.assertNotIn('Voice binding already released', other.read_text())
        self.assertEqual(pipeline.preflight('guard', {k: v for k, v in CONFIG.items() if k != 'systemUiTray'}, {}, 'production')[-2:],
            ['systemUiTray.apkPath', 'systemUiTray.markers'])

    def test_reconcile_passes_the_delivered_identities_baseline(self):
        reconcile = CONFIG['host']['integrate']['reconcile']
        args = reconcile['applyArgs']
        self.assertEqual(args[args.index('--installed-identities') + 1], '{installed_identities}')
        self.assertEqual(reconcile['installedIdentities'], CONFIG['versionBaselines']['installedEvidence']['identities'])
        installed = json.loads((ROOT/reconcile['installedIdentities']).read_text())
        signer = CONFIG['identities']['appSignerSha256'].upper()
        rows = [{'packageName': p, 'versionCode': v, 'certificates': [signer]} for p, v in APPS]
        issues, notes = installed_precedence(rows, installed)
        self.assertEqual(issues, [])
        self.assertEqual(len(notes), 1)  # unchanged calculator 4 equals the delivered copy; voice and controller are newer

    def test_reconcile_and_gate_carry_the_permission_module(self):
        reconcile = CONFIG['host']['integrate']['reconcile']
        self.assertIn('packages/modules/Permission', reconcile['projects'])
        self.assertIn('--preinstall-apk', reconcile['applyArgs'])  # the patch applies only to a build preinstalling Rose Agent
        self.assertIn("ROSE_HOME_PATCHES = {'packages/modules/Permission': ['rose-home-role-permission.patch']}",
            (ROOT/'scripts/background_apks.py').read_text())
        self.assertEqual(pipeline.permission_apex.missing(CONFIG['permissionApex']), [])
        self.assertEqual(CONFIG['host']['tools']['fsckErofs'],
            '/home/pixelbuild/grapheneos-2026100600/releases/2026102801/release-frankel-2026102801/bin/fsck.erofs')

    def test_version_baselines_admit_the_draft_and_refuse_older_candidates(self):
        # 2806/308, 2901/310 and 3012/320 are delivered, retained baselines (FAM-02-INFRA-S001, AGENT311-APP-ONLY,
        # APP-IDSIG-1030-M005): dispatching any again is refused. A next candidate (2026103101 at 1793500000, Rose Agent
        # 321, controller 28 and Calculator 4 unchanged) moves past all of them and past the published app-only 311.
        nxt, nxt_apps = {'build': '2026103101', 'timestamp': '1793500000'}, [['dev.pixel.voice', 321], *APPS[1:]]
        pipeline.check_version_baselines(CONFIG, nxt, nxt_apps)
        # 3012 demands only a newer build and BUILD_DATETIME: an unchanged Rose Agent keeps its published 320.
        with self.assertRaisesRegex(pipeline.CheckFailed, '^build number and BUILD_DATETIME must exceed retained 2026103012 '
                '\\(1793397918\\)$'):
            pipeline.check_version_baselines(CONFIG, {'build': '2026103012', 'timestamp': '1793397918'}, [['dev.pixel.voice', 320], *APPS[1:]])
        reserved = {row['versionCode']: row for row in CONFIG['appRelease']['reserved']}
        self.assertEqual((reserved[310]['osBuild'], reserved[310]['buildTimestamp']), ('2026102901', 1793311111))
        with self.assertRaisesRegex(pipeline.CheckFailed, 'versionCode 310 must exceed retained 2026102901 versionCode 310'
                '.*must exceed retained 2026102901 \\(1793311111\\)'):
            pipeline.check_version_baselines(CONFIG, {'build': '2026102901', 'timestamp': '1793311111'}, [['dev.pixel.voice', 310], *APPS[1:]])
        for version in (311, 309):
            with self.assertRaisesRegex(pipeline.CheckFailed, f'versionCode {version} must exceed 311, the newest app-only version published, built or reserved'):
                pipeline.check_version_baselines(CONFIG, nxt, [['dev.pixel.voice', version], *APPS[1:]])
        with self.assertRaisesRegex(pipeline.CheckFailed, 'versionCode 308 must exceed retained 2026102806 versionCode 308.*'
                'must exceed retained 2026102806 \\(1793220605\\)'):
            pipeline.check_version_baselines(CONFIG, DRAFT, APPS)
        for apps, cand, message in (
                ([['dev.pixel.voice', 307], *APPS[1:]], DRAFT, 'versionCode 307 must exceed 311, the newest app-only version published, built or reserved'),
                ([['dev.pixel.voice', 306], *APPS[1:]], DRAFT, 'versionCode 306 must exceed 311, the newest app-only version'),
                ([['dev.pixel.voice', 305], *APPS[1:]], DRAFT, 'dev.pixel.voice versionCode 305 must exceed retained 2026102805 versionCode 305'),
                ([['dev.pixel.voice', 302], *APPS[1:]], DRAFT, 'dev.pixel.voice versionCode 302 must exceed retained 2026102804 versionCode 302'),
                ([['dev.pixel.voice', 301], *APPS[1:]], DRAFT, 'dev.pixel.voice versionCode 301 must exceed retained 2026102803 versionCode 301'),
                ([['dev.pixel.voice', 300], *APPS[1:]], DRAFT, 'dev.pixel.voice versionCode 300 must exceed retained 2026102802 versionCode 300'),
                ([*APPS[:2], ['dev.pixel.backgroundcontrol', 27]], DRAFT,
                    'dev.pixel.backgroundcontrol versionCode 27 must exceed retained 2026102802 versionCode 27'),
                ([['dev.pixel.voice', 299], *APPS[1:]], DRAFT, 'must exceed retained 2026102801 versionCode 299'),
                ([['dev.pixel.voice', 297], *APPS[1:]], DRAFT, 'below delivered 2026102701 versionCode 298'),
                (APPS[1:], DRAFT, 'dev.pixel.voice versionCode None must exceed'),
                (APPS, {'build': '2026102806', 'timestamp': '1793206861'}, 'must exceed retained 2026102805 \\(1793206861\\)'),
                (APPS, {'build': '2026102805', 'timestamp': '1793220605'}, 'must exceed retained 2026102805'),
                (nxt_apps, {'build': '2026103001', 'timestamp': '1793311111'}, 'must exceed retained 2026102901 \\(1793311111\\)')):
            with self.assertRaisesRegex(pipeline.CheckFailed, message):
                pipeline.check_version_baselines(CONFIG, cand, apps)
        retained = {r['build']: r for r in CONFIG['versionBaselines']['retained']}
        self.assertEqual((retained['2026102805']['source'], retained['2026102805']['buildTimestamp'], retained['2026102805']['mustExceed']),
            ('7fb94ae6ed535066d50879a99b7c8e427da8e853', 1793206861, {'dev.pixel.voice': 305}))
        self.assertEqual((retained['2026102806']['source'], retained['2026102806']['buildTimestamp'], retained['2026102806']['mustExceed']),
            ('93ad1c2bd7094d6b65c695ba239d05d272b87c57', 1793220605, {'dev.pixel.voice': 308}))
        self.assertEqual((retained['2026102901']['source'], retained['2026102901']['buildTimestamp'], retained['2026102901']['mustExceed']),
            ('02b21bc69f9afd281422d42e3dcf8a456e471de1', 1793311111, {'dev.pixel.voice': 310}))
        without = {k: v for k, v in CONFIG.items() if k != 'versionBaselines'}
        with self.assertRaises(pipeline.SetupMissing):
            pipeline.check_version_baselines(without, DRAFT, APPS)

    def test_unchanged_apps_since_the_published_latest_pass_the_baselines(self):
        # Campaign 41132175338 gen 5 (run 41144465393, unchanged Rose Agent since 2026103012): the allocator keeps an
        # unchanged package's published version, so no retained row may demand exceeding the version the newest
        # published Beta carries. Latest is modelled from this checkout (every package unchanged) with the newest
        # retained build's delivered apps (3012 and 3014: Rose Agent 320, Calculator 4, controller 28).
        spec = importlib.util.spec_from_file_location('release_allocate', SCRIPTS/'release-allocate.py')
        allocate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(allocate)
        head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], check=True, capture_output=True,
            text=True).stdout.strip()
        newest = max(CONFIG['versionBaselines']['retained'], key=lambda r: int(r['build']))
        latest = str(int(newest['build']) + 2)
        apps = [['dev.pixel.voice', 320], ['dev.pixel.calculator.agent', 4], ['dev.pixel.backgroundcontrol', 28]]
        observation = {'schema': allocate.OBSERVATION_SCHEMA,
            'releases': {'complete': True, 'includesDrafts': True, 'items': [
                {'tag': f'frankel-{b}', 'draft': False, 'prerelease': False} for b in (newest['build'], latest)]},
            'attempts': {'complete': True, 'items': []},
            'reservations': {'complete': True, 'items': [{'schema': allocate.SCHEMA, 'build': latest, 'sourceSha': head,
                'buildTimestamp': newest['buildTimestamp'] + 3600, 'apps': apps}]}}
        now = newest['buildTimestamp'] + 7200
        allocation = allocate.allocate(head, observation, now, 1, 1, repo=ROOT)
        self.assertEqual(allocation['apps'], apps)
        self.assertFalse(any(p['changedSinceRelease'] for p in allocation['packages'].values()))
        pipeline.check_version_baselines(CONFIG, {'build': allocation['build'],
            'timestamp': str(allocation['buildTimestamp'])}, allocation['apps'])

class BuilderControlTest(unittest.TestCase):
    """Fake gcloud: describe pops the next status from a queue (repeating the last); start/stop exit as configured."""
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.gcloud = self.root/'gcloud'
        self.gcloud.write_text('#!/usr/bin/env python3\n' + textwrap.dedent(f'''
            import json, sys
            root = {str(self.root)!r}
            open(root + '/argv.log', 'a').write(json.dumps(sys.argv[1:]) + '\\n')
            if sys.argv[3] == 'describe':
                states = json.load(open(root + '/states.json'))
                print(states.pop(0) if len(states) > 1 else states[0])
                json.dump(states, open(root + '/states.json', 'w'))
            else:
                sys.exit(int(open(root + '/action-exit').read()))'''))
        self.gcloud.chmod(0o755)
        self.now = [0]

    def control(self, action, states, action_exit=0, timeout=50):
        (self.root/'states.json').write_text(json.dumps(states))
        (self.root/'action-exit').write_text(str(action_exit))
        sleep = lambda seconds: self.now.__setitem__(0, self.now[0] + seconds)
        return pipeline.Production().control_builder(action, gcloud=str(self.gcloud), timeout=timeout, interval=10,
            sleep=sleep, clock=lambda: self.now[0])

    def calls(self):
        return [json.loads(line) for line in (self.root/'argv.log').read_text().splitlines()]

    def test_start_submits_async_once_and_polls_describe_only(self):
        self.assertEqual(self.control('start', ['TERMINATED', 'TERMINATED', 'STAGING', 'RUNNING']), 'RUNNING')
        calls = self.calls()
        requests = [c for c in calls if c[2] != 'describe']
        self.assertEqual(requests, [['compute', 'instances', 'start', 'immortal-os-builder', '--project=protocols-465723',
            '--zone=us-west2-a', '--async', '--quiet']])
        self.assertTrue(all(c[:3] in (['compute', 'instances', 'describe'], ['compute', 'instances', 'start']) for c in calls))
        self.assertTrue(all('--format=value(status)' in c for c in calls if c[2] == 'describe'))
        self.assertFalse(any('operations' in ' '.join(c) for c in calls))

    def test_stop_and_already_in_target_or_transition_send_no_duplicate_request(self):
        self.assertEqual(self.control('stop', ['RUNNING', 'STOPPING', 'TERMINATED']), 'TERMINATED')
        (self.root/'argv.log').unlink()
        self.assertEqual(self.control('start', ['RUNNING']), 'RUNNING')
        self.assertEqual(self.control('start', ['STAGING', 'RUNNING']), 'RUNNING')
        self.assertEqual([c for c in self.calls() if c[2] != 'describe'], [])

    def test_denied_request_fails_at_once_and_uncertain_submission_is_reconciled(self):
        with self.assertRaisesRegex(pipeline.CheckFailed, 'start request failed .*observed TERMINATED'):
            self.control('start', ['TERMINATED'], action_exit=1)
        self.assertEqual(len([c for c in self.calls() if c[2] != 'describe']), 1)
        (self.root/'argv.log').unlink()
        self.assertEqual(self.control('start', ['TERMINATED', 'PROVISIONING', 'RUNNING'], action_exit=1), 'RUNNING')
        self.assertEqual(len([c for c in self.calls() if c[2] != 'describe']), 1)

    def test_timeout_and_wrong_state_report_the_last_observed_state(self):
        with self.assertRaisesRegex(pipeline.CheckFailed, 'did not reach RUNNING within 50s; last observed STAGING'):
            self.control('start', ['TERMINATED', 'STAGING'])
        with self.assertRaisesRegex(pipeline.CheckFailed, 'moved to STOPPING while waiting for RUNNING'):
            self.control('start', ['TERMINATED', 'STAGING', 'STOPPING'])
        (self.root/'argv.log').unlink()
        with self.assertRaisesRegex(pipeline.CheckFailed, 'builder is SUSPENDED; refusing to start'):
            self.control('start', ['SUSPENDED'])
        self.assertEqual([c for c in self.calls() if c[2] != 'describe'], [])

if __name__ == '__main__':
    unittest.main()
