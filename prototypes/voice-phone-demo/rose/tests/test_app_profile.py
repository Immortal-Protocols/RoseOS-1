"""Backend profiles preserve Android identity while leaving backend stack choices to the agent."""
import json
import tempfile
import unittest
from pathlib import Path
from rose.app_builder import appprofile as P, template

PKG = 'app.rose.gen.p0123456789abcdef'

class Profiles(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        template.write_skeleton(self.root, PKG, 'Notes')

    def backend(self):
        (self.root / P.FILE).write_text(json.dumps({'schemaVersion': 1, 'profile': P.CLIENT_BACKEND_V1}))
        (self.root / 'contract').mkdir(exist_ok=True)
        p = self.root / 'AndroidManifest.xml'
        p.write_text(p.read_text().replace('<application', '<uses-permission android:name="android.permission.INTERNET"/><application'))

    def test_plain_apps_stay_plain_and_cannot_drop_backend_data(self):
        self.assertEqual(P.check(self.root, PKG).name, P.APP_V1)
        for bound in (P.CLIENT_SERVER_V1, P.CLIENT_BACKEND_V1):
            with self.assertRaisesRegex(P.ProfileRefused, 'data lives there'):
                P.check(self.root, PKG, bound)

    def test_python_rust_and_other_build_files_are_authored_freely(self):
        self.backend()
        for name in ('main.py', 'requirements.txt', 'Cargo.toml', 'Cargo.lock', 'build.rs', '.cargo/config.toml',
                     'package.json', 'start', 'src/main.rs'):
            p = self.root / 'backend' / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('author supplied')
        self.assertEqual(P.check(self.root, PKG).name, P.CLIENT_BACKEND_V1)
        self.assertEqual(P.check(self.root, PKG, P.CLIENT_SERVER_V1).name, P.CLIENT_BACKEND_V1)

    def test_backend_paths_still_cannot_escape_the_project(self):
        self.backend()
        (self.root / 'backend').mkdir()
        (self.root / 'backend/escape').symlink_to('/etc/passwd')
        with self.assertRaises(template.TemplateRefused):
            P.check(self.root, PKG)

    def test_legacy_declaration_is_readable_but_never_buildable(self):
        (self.root / P.FILE).write_text(json.dumps({'schemaVersion': 1, 'profile': P.CLIENT_SERVER_V1}))
        self.assertEqual(P.read(self.root).name, P.CLIENT_SERVER_V1)
        with self.assertRaisesRegex(P.ProfileRefused, 'retired'):
            P.check(self.root, PKG)

    def test_client_contract_internet_and_reserved_package_still_apply(self):
        self.backend()
        (self.root / 'contract').rmdir()
        with self.assertRaisesRegex(P.ProfileRefused, 'contract/'):
            P.check(self.root, PKG)
        (self.root / 'contract').mkdir()
        p = self.root / 'src' / PKG.replace('.', '/') / 'rose'
        p.mkdir()
        with self.assertRaisesRegex(P.ProfileRefused, 'written by the builder'):
            P.check(self.root, PKG)

    def test_unknown_profile_and_duplicate_keys_are_refused(self):
        for text in ('{"schemaVersion":1,"profile":"other"}', '{"schemaVersion":1,"schemaVersion":1}'):
            (self.root / P.FILE).write_text(text)
            with self.assertRaises(P.ProfileRefused):
                P.check(self.root, PKG)
