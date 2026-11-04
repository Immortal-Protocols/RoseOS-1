#!/usr/bin/env python3
"""FACTORY-TARGETS: the device-scoped archive-signing and delivery targets. Committed files and stand-ins only: no host,
lock, secret, key, ssh-keygen run or network."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
import factory_targets  # noqa: E402


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS/file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fas = load('factory_archive_sign_targets', 'factory-archive-sign.py')
fd = load('factory_delivery_targets', 'factory-delivery.py')


def run(module, argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = module.main(argv)
    return code, json.loads(out.getvalue())


class Targets(unittest.TestCase):
    def test_rango_paths_and_defaults_are_unchanged(self):
        rango = factory_targets.target('rango')
        self.assertEqual((rango['binding'], rango['receipt'], rango['build'], rango['installerPins'], rango['deliverySchema']),
                         ('os/retained-rango/factory-signing.json', 'os/retained-rango/factory-signing-bootstrap-receipt.json',
                          '2026103001', 'scripts/rango-factory-inspect-wiring.py', 'roseos.rango-factory-delivery.v1'))
        self.assertEqual(fas.DEFAULT_BINDING, ROOT/'os/retained-rango/factory-signing.json')
        self.assertEqual(fas.RECEIPT_FILE, 'os/retained-rango/factory-signing-bootstrap-receipt.json')
        self.assertEqual((fd.DEVICE, fd.BUILD, fd.INSTALL, fd.SCHEMA),
                         ('rango', '2026103001', 'rango-install-2026103001.zip', 'roseos.rango-factory-delivery.v1'))
        with self.assertRaises(factory_targets.UnknownTarget):
            factory_targets.target('mustang')  # never inferred from another device

    def test_every_target_binding_names_its_own_device_and_receipt_location(self):
        for device, t in factory_targets.TARGETS.items():
            with self.subTest(device):
                binding = json.loads((ROOT/t['binding']).read_text())
                self.assertEqual((binding['schema'], binding['device']), (fas.BINDING_SCHEMA, device))
                self.assertEqual(Path(t['receipt']).parent, Path(t['binding']).parent)
                self.assertEqual(fas.receipt_file(binding), t['receipt'])


class FrankelSourceContract(unittest.TestCase):
    """Frankel has a binding and a target, but no archive identity, profile, container, grant, key or installer pin."""

    def setUp(self):
        self.binding = json.loads((ROOT/factory_targets.TARGETS['frankel']['binding']).read_text())

    def test_committed_frankel_binding_is_all_null_and_check_refuses(self):
        self.assertEqual({k: v for k, v in self.binding.items() if k not in ('schema', 'device', 'basis', 'sshKeygen')},
                         dict.fromkeys(['build', 'profile', 'project', 'signer', 'keySecret', 'keyVersion', 'passphraseSecret',
                                        'passphraseVersion', 'bootstrap']))
        self.assertEqual(self.binding['sshKeygen'], {'path': None, 'sha256': None})
        code, report = run(fas, ['check', '--device', 'frankel'])
        self.assertEqual((code, report['ready']), (4, False))
        for missing in ('binding.device/build', 'binding.project', 'binding.keySecret', 'binding.passphraseSecret',
                        'binding.keyVersion (from the bootstrap receipt)', 'binding.sshKeygen.path/sha256 (measured installed identity)',
                        'binding.profile: not an openssh-ed25519 bootstrap profile for frankel'):
            self.assertIn(missing, report['missing'])
        flavor = json.loads((ROOT/'os/flavors/frankel.json').read_text())['factorySigning']
        self.assertEqual((flavor['principal'], flavor['publicKey']), (None, None))  # no Frankel archive identity
        profiles = json.loads((ROOT/'os/signing-bootstrap/profiles.json').read_text())['profiles']
        self.assertFalse([n for n, p in profiles.items() if p.get('device') == 'frankel'
                          and (p.get('archive') or {}).get('format') == 'openssh-ed25519'])

    def test_null_secret_names_are_refused_not_read_as_the_text_none(self):
        problems = fas.binding_problems(dict(self.binding, keySecret=None, passphraseSecret=None))
        self.assertIn('binding.keySecret', problems)
        self.assertIn('binding.passphraseSecret', problems)

    def test_a_binding_is_never_retargeted_to_another_device(self):
        code, report = run(fas, ['check', '--device', 'frankel', '--binding', str(ROOT/factory_targets.TARGETS['rango']['binding'])])
        self.assertEqual((code, report), (4, {'refused': 'binding_device: ' + str(ROOT/factory_targets.TARGETS['rango']['binding'])
                                                          + ' is not the frankel binding'}))
        code, report = run(fas, ['check', '--binding', str(ROOT/factory_targets.TARGETS['frankel']['binding'])])  # default rango
        self.assertEqual(code, 4)
        self.assertTrue(report['refused'].startswith('binding_device:'))
        self.assertIn('binding.device: no committed factory-signing target for mustang (factory_targets)',
                      fas.binding_problems(dict(self.binding, device='mustang', build='2026110401')))

    def test_sign_refuses_frankel_before_any_host_or_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetched = []
            with self.assertRaises(fas.Refused) as caught:
                fas.sign_from_binding(self.binding, str(Path(tmp)/'frankel-install-2026110100.zip'), '0' * 64,
                                      str(Path(tmp)/'lock'), str(Path(tmp)/'out'), fetch=lambda *a: fetched.append(a))
            self.assertEqual(fetched, [])
            self.assertFalse((Path(tmp)/'out').exists())
            self.assertIn('binding_unbound', str(caught.exception))

    def test_delivery_refuses_frankel_without_an_installer_pin_and_never_verifies_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'receipt.json'
            with self.assertRaises(fd.Refused) as caught:
                fd.verify(tmp, tmp, str(out), device='frankel', runner=lambda *a, **k: self.fail('no verifier run'))
            self.assertTrue(str(caught.exception).startswith('installer_unpinned:'))
            receipt = json.loads(out.read_text())
            self.assertEqual((receipt['device'], receipt['verified'], receipt['schema'], receipt['build']),
                             ('frankel', False, 'roseos.factory-delivery.v1', None))
            self.assertNotIn('archive', receipt)
        code, report = run(fd, ['verify', '--device', 'frankel', '--installer', '/nonexistent', '--signature', '/nonexistent',
                                '--out', str(Path(tempfile.mkdtemp())/'r.json')])
        self.assertEqual((code, report['verified']), (3, False))


if __name__ == '__main__':
    unittest.main()
