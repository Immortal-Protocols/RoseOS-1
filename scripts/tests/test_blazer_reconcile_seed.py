"""BLAZER-RECONCILE-SEED: scripts/blazer-reconcile-seed.py selects the one Frankel reconcile receipt whose every entry
matches the isolated copy, stores it byte-identical as 0001-<label>.json with a provenance file, and host-reconcile.py's
own latest_receipt/plan then treat exactly those bytes as task-owned. Synthetic trees and receipts only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed = load('blazer_reconcile_seed', 'scripts/blazer-reconcile-seed.py')
reconcile = load('host_reconcile', 'scripts/host-reconcile.py')
sha = lambda data: hashlib.sha256(data).hexdigest()
UPDATER = 'packages/apps/Updater/res/values/config.xml'
ROLE = 'packages/modules/Permission/service/java/com/android/role/RoleService.java'


class Seed(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp/'src'
        for path, data in ((UPDATER, b'frankel route\n'), (ROLE, b'role patched\n')):
            (self.src/path).parent.mkdir(parents=True, exist_ok=True)
            (self.src/path).write_bytes(data)
        self.frankel = self.tmp/'reconcile'
        self.frankel.mkdir()
        self.write('0040-2026103101.json', {UPDATER: sha(b'frankel route\n'), ROLE: sha(b'role patched\n')})
        self.write('0041-2026110301.json', {UPDATER: sha(b'newer frankel route\n'), ROLE: sha(b'role patched\n')})
        (self.frankel/'0041-2026110301-backup').mkdir()
        self.out = self.tmp/'blazer-reconcile'
        self.inventory = self.tmp/'inventory.json'
        self.inventory.write_text('{"copy": "synthetic inventory"}')

    def write(self, name, afters, kind='reconcile', label=None):
        record = {'receiptType': kind, 'label': label or name[5:-5], 'changed': sorted(afters),
                  'entries': {p: {'before': None, 'after': a, 'action': 'installed'} for p, a in afters.items()}}
        (self.frankel/name).write_text(json.dumps(record))

    def run_seed(self, **change):
        args = dict(frankel_receipts=self.frankel, source_root=self.src, source_run='41554911849', receipt_dir=self.out,
                    inventory=self.inventory, inventory_sha256=sha(self.inventory.read_bytes()))
        args.update(change)
        return seed.seed(**args)

    def test_the_matching_receipt_is_seeded_byte_identical_and_host_reconcile_owns_exactly_its_bytes(self):
        record = self.run_seed()
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ['0001-2026103101.json', 'seed-provenance.json'])
        self.assertEqual((self.out/'0001-2026103101.json').read_bytes(), (self.frankel/'0040-2026103101.json').read_bytes())
        self.assertEqual((record['source']['name'], record['source']['sequence'], record['entriesChecked']),
                         ('0040-2026103101.json', 40, 2))
        self.assertEqual((record['sourceRun'], record['sourceInventory']),
                         ('41554911849', {'path': str(self.inventory), 'sha256': sha(self.inventory.read_bytes())}))
        self.assertEqual(record['newerReceiptsNotMatching'], [{'receipt': '0041-2026110301.json', 'firstMismatch': UPDATER}])
        previous, count = reconcile.latest_receipt(self.out)
        self.assertEqual(count, 1)
        self.assertEqual(previous['label'], '2026103101')
        actions, conflicts = reconcile.plan(self.src, [], [], {UPDATER: (b'blazer route\n', None)}, previous)
        self.assertEqual((sorted(actions), conflicts), (sorted([ROLE, UPDATER]), []))  # ROLE: the candidate no longer changes it
        (self.src/UPDATER).write_bytes(b'someone else\n')
        _, conflicts = reconcile.plan(self.src, [], [], {UPDATER: (b'blazer route\n', None)}, previous)
        self.assertEqual(conflicts, [UPDATER])  # unknown bytes still refuse

    def test_no_entire_match_refuses_and_writes_nothing(self):
        (self.src/ROLE).write_bytes(b'unknown\n')
        with self.assertRaisesRegex(seed.Refused, 'no Frankel reconcile receipt matches'):
            self.run_seed()
        self.assertFalse(os.path.lexists(self.out))

    def test_absent_after_must_be_absent_and_links_never_match(self):
        self.write('0042-2026110302.json', {UPDATER: sha(b'frankel route\n'), ROLE: sha(b'role patched\n'),
                                            'build/make/gone.mk': None})
        self.assertEqual(self.run_seed()['source']['sequence'], 42)
        out = self.tmp/'again'
        (self.src/'build/make').mkdir(parents=True)
        (self.src/'build/make/gone.mk').write_text('present')
        self.assertEqual(self.run_seed(receipt_dir=out)['source']['sequence'], 40)
        third = self.tmp/'third'
        (self.src/UPDATER).unlink()
        (self.src/UPDATER).symlink_to(self.tmp/'elsewhere')
        with self.assertRaises(seed.Refused):
            self.run_seed(receipt_dir=third)

    def test_a_linked_parent_directory_never_matches_even_with_equal_bytes(self):
        elsewhere = self.tmp/'elsewhere/values'
        elsewhere.mkdir(parents=True)
        (elsewhere/'config.xml').write_bytes(b'frankel route\n')
        values = self.src/'packages/apps/Updater/res/values'
        (values/'config.xml').unlink()
        values.rmdir()
        values.symlink_to(elsewhere)
        with self.assertRaisesRegex(seed.Refused, 'no Frankel reconcile receipt matches'):
            self.run_seed()

    def test_a_null_after_means_truly_absent_and_a_dangling_link_fails(self):
        self.write('0042-2026110302.json', {UPDATER: sha(b'frankel route\n'), ROLE: sha(b'role patched\n'),
                                            'build/make/gone.mk': None})
        (self.src/'build/make').mkdir(parents=True)
        (self.src/'build/make/gone.mk').symlink_to(self.tmp/'nowhere')
        with self.assertRaises(seed.Refused):
            self.run_seed()
        self.assertFalse(os.path.lexists(self.out))

    def test_inputs_and_receipt_shapes_refuse(self):
        for change, word in (({'inventory_sha256': '0' * 64}, 'not the named copy inventory'),
                             ({'inventory_sha256': 'X'}, 'lowercase sha256'), ({'inventory': Path('rel.json')}, 'absolute'),
                             ({'source_run': '0'}, 'numeric'), ({'receipt_dir': self.tmp/'src/inside'}, 'outside'),
                             ({'receipt_dir': self.frankel/'x'}, 'outside'), ({'receipt_dir': Path('rel')}, 'new absolute'),
                             ({'frankel_receipts': self.tmp/'none'}, 'existing absolute directory')):
            with self.subTest(change=change), self.assertRaisesRegex(seed.Refused, word):
                self.run_seed(**change)
        self.out.mkdir()
        with self.assertRaisesRegex(seed.Refused, 'new absolute'):
            self.run_seed()
        self.out.rmdir()
        self.write('0043-2026110303.json', {}, kind='other')
        with self.assertRaisesRegex(seed.Refused, 'not a reconcile receipt'):
            self.run_seed()
        (self.frankel/'0043-2026110303.json').unlink()
        self.write('0043-2026110303.json', {'../escape': None})
        with self.assertRaisesRegex(seed.Refused, 'relative source path'):
            self.run_seed()
        (self.frankel/'0043-2026110303.json').unlink()
        self.write('0043-2026110303.json', {UPDATER: sha(b'frankel route\n'), ROLE: sha(b'role patched\n')}, label='other')
        with self.assertRaisesRegex(seed.Refused, 'not its file label'):
            self.run_seed()

    def prior(self, build, afters=None, seeded_only=False, label=None):
        """A prior blazer integration directory: the seeded Frankel copy, plus (unless seeded_only) its own receipt."""
        directory = self.actions/f'blazer-reconcile-{build}'
        directory.mkdir(parents=True)
        (directory/'0001-2026103101.json').write_text((self.frankel/'0040-2026103101.json').read_text())
        if not seeded_only:
            record = {'receiptType': 'reconcile', 'label': label or f'blazer-{build}', 'changed': [UPDATER],
                      'entries': {p: {'before': None, 'after': a, 'action': 'installed'} for p, a in (afters or {}).items()}}
            (directory/f'0002-blazer-{build}.json').write_text(json.dumps(record))
        return directory

    def reintegrated(self):
        """The copy after a completed blazer integration: its Updater file now holds blazer bytes."""
        self.actions = self.tmp/'rose-actions'
        (self.src/UPDATER).write_bytes(b'blazer route\n')
        return {UPDATER: sha(b'blazer route\n'), ROLE: sha(b'role patched\n')}

    def test_a_reintegration_seeds_from_the_newest_completed_blazer_integration(self):
        afters = self.reintegrated()
        with self.assertRaisesRegex(seed.Refused, 'no Frankel reconcile receipt matches'):
            self.run_seed()  # without --prior-integrations the reconciled copy matches no Frankel receipt
        self.prior('2026110301', seeded_only=True)  # a stopped integration: only its seeded copy, skipped
        self.prior('2026110303', afters)
        self.out = self.tmp/'blazer-reconcile-new'
        record = self.run_seed(prior_integrations=self.actions)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ['0001-blazer-2026110303.json', 'seed-provenance.json'])
        self.assertEqual((record['source']['kind'], record['source']['build'], record['source']['name']),
                         ('blazer', '2026110303', '0002-blazer-2026110303.json'))
        self.assertEqual((self.out/'0001-blazer-2026110303.json').read_bytes(),
                         (self.actions/'blazer-reconcile-2026110303/0002-blazer-2026110303.json').read_bytes())
        self.assertIn('blazer reconcile receipt', record['claim'])
        previous, count = reconcile.latest_receipt(self.out)
        self.assertEqual((count, {p: e['after'] for p, e in previous['entries'].items()}), (1, afters))
        changes = {UPDATER: (b'candidate route\n', None)}
        actions, conflicts = reconcile.plan(self.src, ['packages/apps/Updater', 'packages/modules/Permission'], set(),
                                            changes, previous)
        self.assertEqual(conflicts, [])  # the blazer bytes are task-owned through the seeded receipt
        self.assertIn(UPDATER, actions)

    def test_the_newest_matching_prior_wins_and_misses_are_recorded(self):
        afters = self.reintegrated()
        self.prior('2026110303', afters)
        self.prior('2026110304', {UPDATER: sha(b'something else\n'), ROLE: sha(b'role patched\n')})
        self.out = self.tmp/'blazer-reconcile-new'
        record = self.run_seed(prior_integrations=self.actions)
        self.assertEqual(record['source']['build'], '2026110303')
        self.assertEqual([m['kind'] for m in record['newerReceiptsNotMatching']], ['blazer'])
        self.setUp()
        afters = self.reintegrated()
        self.prior('2026110303', afters)
        self.prior('2026110304', afters)
        self.out = self.tmp/'blazer-reconcile-new'
        self.assertEqual(self.run_seed(prior_integrations=self.actions)['source']['build'], '2026110304')

    def test_with_no_matching_prior_the_frankel_receipt_still_seeds(self):
        self.actions = self.tmp/'rose-actions'
        self.prior('2026110303', {UPDATER: sha(b'blazer route\n'), ROLE: sha(b'role patched\n')})
        self.out = self.tmp/'blazer-reconcile-new'
        record = self.run_seed(prior_integrations=self.actions)  # the copy is still Frankel's: the prior does not match
        self.assertEqual((record['source']['kind'], record['source']['name']), ('frankel', '0040-2026103101.json'))

    def test_prior_links_labels_and_names_refuse_or_are_ignored(self):
        afters = self.reintegrated()
        target = self.prior('2026110303', afters)
        moved = self.tmp/'moved-integration'
        target.rename(moved)
        (self.actions/'blazer-reconcile-2026110303').symlink_to(moved)
        self.out = self.tmp/'blazer-reconcile-new'
        with self.assertRaisesRegex(seed.Refused, 'not a plain directory'):
            self.run_seed(prior_integrations=self.actions)
        self.setUp()
        afters = self.reintegrated()
        directory = self.prior('2026110303', afters)
        receipt = directory/'0002-blazer-2026110303.json'
        receipt.rename(self.tmp/'receipt.json')
        receipt.symlink_to(self.tmp/'receipt.json')
        self.out = self.tmp/'blazer-reconcile-new'
        with self.assertRaisesRegex(seed.Refused, 'not a regular file'):
            self.run_seed(prior_integrations=self.actions)
        self.setUp()
        afters = self.reintegrated()
        self.prior('2026110303', afters, label='blazer-2026110399')
        self.out = self.tmp/'blazer-reconcile-new'
        with self.assertRaisesRegex(seed.Refused, 'names label'):
            self.run_seed(prior_integrations=self.actions)
        self.setUp()
        afters = self.reintegrated()
        (self.actions/'blazer-reconcile-x').mkdir(parents=True)  # not a ten-digit build: never a candidate
        (self.actions/'blazer-reconcile-x/0002-blazer-x.json').write_text('{}')
        self.prior('2026110303', afters)
        self.out = self.tmp/'blazer-reconcile-new'
        self.assertEqual(self.run_seed(prior_integrations=self.actions)['source']['build'], '2026110303')

    def test_the_source_tree_and_frankel_receipts_are_never_written(self):
        before = {p: p.read_bytes() for p in [*self.src.rglob('*'), *self.frankel.rglob('*')] if p.is_file()}
        self.run_seed()
        self.assertEqual({p: p.read_bytes() for p in [*self.src.rglob('*'), *self.frankel.rglob('*')] if p.is_file()}, before)


if __name__ == '__main__':
    unittest.main()
