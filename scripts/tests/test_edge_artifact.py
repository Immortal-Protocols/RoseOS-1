"""OS Product's raised-edge artifact check (scripts/edge_artifact.py), as release-pipeline's bytes-verify and ota-verify
run it: the dex reader against real d8 output and synthetic dex, the check's pass and fail rules, its CLI, and the
roseEdge section of os/release-pipeline/frankel.json against the OS sources it names."""
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dex_fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('edge_artifact', ROOT/'scripts/edge_artifact.py')
edge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edge)

FIXTURES = ROOT/'scripts/tests/fixtures/rose-edge-dex'
# The real d8 output, stored as base64 text because the repository ignores *.dex (fixtures/rose-edge-dex/README.md).
D8_SHA256 = {'d8-039.dex': '90eab09dea639d7846bcbc9d73c666e66a4c101b724a371c8378d90187101366',
             'd8-041.dex': '11de081491f2f85ee3a4ffbc70b3ee20488017098d4b1c49670a109aea258deb'}


def d8_dex(name):
    data = base64.b64decode((FIXTURES/f'{name}.b64').read_text())
    if hashlib.sha256(data).hexdigest() != D8_SHA256[name]:
        raise AssertionError(f'{name}.b64 does not decode to the recorded d8 output')
    return data
CONFIG = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
SPEC = CONFIG['roseEdge']
GESTURE, SWIPE = 'Lcom/android/quickstep/fallback/RoseEdgeGesture;', 'Lcom/android/quickstep/fallback/RoseEdgeSwipe;'
CONTROLLER, MIC = 'Lcom/android/wm/shell/roseedge/RoseEdgeController;', 'Lcom/android/wm/shell/RoseEdgeMicSlot;'
DIAG, DESCRIPTOR, MIC_TAG = 'persist.wm.debug.rose_edge_diag', 'com.android.wm.shell.roseedge.IRoseEdge', 'RoseEdgeMic'
L3_APK = 'SYSTEM_EXT/priv-app/Launcher3QuickStep/Launcher3QuickStep.apk'
SYSUI_APK = 'SYSTEM_EXT/priv-app/SystemUI/SystemUI.apk'
PROPS = 'ro.build.type=user\npersist.wm.debug.rose_edge=true\npersist.wm.debug.pixel_agent_tray=1\n'
LAUNCHER3 = [(GESTURE, ['stage']), (SWIPE, ['<init>', 'onMove'])]
SYSTEMUI = [(CONTROLLER, ['<init>', 'raise']), (MIC, ['<init>'])]


def methods_by_class(data):
    found = {}
    for section in edge.sections(data):
        for i in range(section.classes):
            name = section.type_name(edge.u32(data, section.classes_off + 32 * i)).decode()
            found[name] = section.defined_methods(name)
    return found


def target_files(path, props=PROPS, launcher3=None, systemui=None, extra=None):
    """Minimal target-files: SYSTEM/build.prop and the two APKs, each a real-format dex unless given as raw bytes."""
    with zipfile.ZipFile(path, 'w') as z:
        if props is not None:
            z.writestr('SYSTEM/build.prop', props)
        for name, value in ((L3_APK, launcher3), (SYSUI_APK, systemui)):
            if value is None:
                value = dex_fixture.apk([dex_fixture.dex(LAUNCHER3 if name == L3_APK else SYSTEMUI)])
            if value is not False:
                z.writestr(name, value)
        for name, value in (extra or {}).items():
            z.writestr(name, value)
    return path


class RealDexTest(unittest.TestCase):
    """The reader against real d8 output (fixtures/rose-edge-dex/README.md): dex 039 and a dex 041 container."""

    def test_reads_the_classes_and_methods_dexdump_lists(self):
        expected = {GESTURE: {'<init>', 'stage'}, SWIPE: {'<init>', 'diag', 'onMove'},
                    MIC: {'<init>', 'tag'}, CONTROLLER: {'<init>', 'descriptor', 'raise'}}
        for name in ('d8-039.dex', 'd8-041.dex'):
            with self.subTest(name=name):
                data = d8_dex(name)
                self.assertEqual(data[:8], b'dex\n039\x00' if '039' in name else b'dex\n041\x00')
                self.assertEqual(methods_by_class(data), expected)  # fields (TAG, DESCRIPTOR, mStage) are not methods

    def test_string_table_holds_whole_literals_only(self):
        for name in ('d8-039.dex', 'd8-041.dex'):
            data = d8_dex(name)
            sections = edge.sections(data)
            for literal, present in ((DIAG, True), (DESCRIPTOR, True), (MIC_TAG, True), ('RoseEdge', True),
                                     ('persist.wm.debug.rose_edge', False), ('com.android.wm.shell.roseedge', False),
                                     ('RoseEdgeMicSlot', False), ('rose_edge_diag', False)):
                with self.subTest(name=name, literal=literal):
                    self.assertEqual(any(s.has_string(literal) for s in sections), present)
                    # OS Product's raw-bytes form of the same rule agrees on real d8 output.
                    self.assertEqual(edge.dex_string(literal) in data, present)

    def test_a_truncated_or_foreign_file_is_not_read(self):
        data = d8_dex('d8-039.dex')
        for bad, message in ((data[:0x60], 'truncated dex header'), (b'PK\x03\x04' + data[4:], 'no dex magic'),
                             (data[:-8], 'dex file size differs from its header'),
                             (data[:0x28] + struct.pack('<I', 0x78563412) + data[0x2c:], 'not a little-endian dex'),
                             (data[:4] + b'034' + data[7:], 'no dex magic')):
            with self.subTest(message=message):
                with self.assertRaisesRegex(edge.DexError, message):
                    edge.sections(bad)


class SyntheticDexTest(unittest.TestCase):
    def test_container_sections_share_one_string_table(self):
        data = dex_fixture.container([([('La/A;', ['m'])], []), ([(GESTURE, ['stage'])], [DIAG]), ([('La/C;', [])], [])])
        sections = edge.sections(data)
        self.assertEqual(len(sections), 3)
        self.assertEqual([s.defined_methods(GESTURE) for s in sections], [None, {'stage'}, None])
        self.assertEqual([s.defined_methods('La/C;') for s in sections], [None, None, set()])
        self.assertEqual(len({s.strings_off for s in sections}), 1)
        self.assertTrue(all(s.has_string(DIAG) for s in sections))
        self.assertEqual(sum(s.size for s in sections), len(data))

    def test_versions_035_to_040_are_one_section(self):
        for version in (35, 37, 38, 39, 40):
            with self.subTest(version=version):
                sections = edge.sections(dex_fixture.dex([(GESTURE, ['stage', '<init>'])], version=version))
                self.assertEqual((len(sections), sections[0].defined_methods(GESTURE)), (1, {'stage', '<init>'}))

    def test_container_headers_must_describe_the_file(self):
        good = dex_fixture.container([([('La/A;', ['m'])], []), ([(GESTURE, ['stage'])], [])])
        second = edge.sections(good)[1]
        start = good.index(b'dex\n041\x00', 8)
        for offset, value, message in ((start + 0x74, start + 4, 'container header'), (start + 0x70, len(good) + 4, 'container header'),
                                       (start + 0x20, 0x10, 'container header'), (0x24, 0x70, 'header size')):
            with self.subTest(message=message, offset=offset):
                bad = bytearray(good)
                struct.pack_into('<I', bad, offset, value)
                with self.assertRaisesRegex(edge.DexError, message):
                    edge.sections(bytes(bad))
        self.assertEqual(second.defined_methods(GESTURE), {'stage'})
        # Bytes after the last section, or a section size that does not reach the next header, are refused.
        with self.assertRaisesRegex(edge.DexError, 'container header does not describe this file'):
            edge.sections(good + b'\0' * 8)
        short = bytearray(good)
        struct.pack_into('<I', short, 0x20, start - 8)
        with self.assertRaisesRegex(edge.DexError, f'no dex magic at offset {start - 8}'):
            edge.sections(bytes(short))

    def test_ids_outside_the_dex_are_refused(self):
        data = bytearray(dex_fixture.dex([(GESTURE, ['stage'])]))
        struct.pack_into('<I', data, 0x64, len(data))  # class_defs_off past the end
        with self.assertRaisesRegex(edge.DexError, 'outside the dex'):
            edge.sections(bytes(data))
        data = bytearray(dex_fixture.dex([(GESTURE, ['stage'])]))
        section = edge.sections(bytes(data))[0]
        struct.pack_into('<I', data, section.types_off, 999)  # a type naming a string that does not exist
        with self.assertRaisesRegex(edge.DexError, 'string index 999 out of range'):
            edge.sections(bytes(data))[0].defined_methods(GESTURE)


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'target_files.zip'

    def check(self, **kwargs):
        return edge.check(target_files(self.path, **kwargs), SPEC)

    def test_the_raised_edge_passes(self):
        failures, receipt = self.check()
        self.assertEqual(failures, [])
        self.assertEqual([(p['prop'], p['pass'], p['files']) for p in receipt['props']],
                         [(p, True, ['SYSTEM/build.prop']) for p in SPEC['props']])
        self.assertEqual([(c['class'], c['by'], c['where']) for c in receipt['classes']],
                         [(GESTURE, 'class', L3_APK + '!classes.dex'), (SWIPE, 'class', L3_APK + '!classes.dex'),
                          (CONTROLLER, 'class', SYSUI_APK + '!classes.dex'), (MIC, 'class', SYSUI_APK + '!classes.dex')])
        self.assertEqual(sorted(receipt['apkSha256']), [L3_APK, SYSUI_APK])

    def test_real_d8_dex_in_the_apks_passes(self):
        apk = dex_fixture.apk([d8_dex('d8-041.dex')])
        failures, receipt = self.check(launcher3=apk, systemui=dex_fixture.apk([d8_dex('d8-039.dex')]))
        self.assertEqual(failures, [])
        self.assertEqual({c['by'] for c in receipt['classes']}, {'class'})

    def test_classes_are_found_in_any_dex_of_a_multidex_apk(self):
        systemui = dex_fixture.apk([dex_fixture.dex([('Lcom/android/systemui/A;', ['<init>'])]), dex_fixture.dex([(MIC, ['<init>'])]),
                                    dex_fixture.container([([('La/B;', ['c'])], []), ([(CONTROLLER, ['raise'])], [])])])
        failures, receipt = self.check(systemui=systemui)
        self.assertEqual(failures, [])
        self.assertEqual([c['where'] for c in receipt['classes'][2:]], [SYSUI_APK + '!classes3.dex', SYSUI_APK + '!classes2.dex'])

    def test_switches_must_be_set_and_never_contradicted(self):
        for props, message in (('ro.build.type=user\npersist.wm.debug.pixel_agent_tray=1\n',
                                'raised-edge prop persist.wm.debug.rose_edge=true is not set in'),
                               (PROPS.replace('pixel_agent_tray=1', 'pixel_agent_tray=0'),
                                "raised-edge prop persist.wm.debug.pixel_agent_tray=1 conflicts with ['SYSTEM/build.prop: 0']"),
                               (PROPS + 'persist.wm.debug.rose_edge=false\n',
                                "raised-edge prop persist.wm.debug.rose_edge=true conflicts with ['SYSTEM/build.prop: false']"),
                               ('# persist.wm.debug.rose_edge=true\npersist.wm.debug.pixel_agent_tray=1\n', 'rose_edge=true is not set in'),
                               (None, 'rose_edge=true is not set in')):
            with self.subTest(message=message):
                failures, _ = self.check(props=props)
                self.assertTrue(any(message in f for f in failures), failures)
        # Any listed build.prop may carry a switch; an unlisted file does not count.
        failures, receipt = self.check(props='ro.build.type=user\npersist.wm.debug.rose_edge=true\n',
                                       extra={'SYSTEM_EXT/etc/build.prop': 'persist.wm.debug.pixel_agent_tray=1\n'})
        self.assertEqual((failures, receipt['props'][1]['files']), ([], ['SYSTEM_EXT/etc/build.prop']))
        failures, _ = self.check(props='persist.wm.debug.rose_edge=true\n', extra={'VENDOR/build.prop': 'persist.wm.debug.pixel_agent_tray=1\n'})
        self.assertEqual(len(failures), 1)

    def test_a_missing_or_methodless_gesture_fails_closed(self):
        for launcher3, message, count in (
                (False, 'RoseEdgeGesture; with method stage is not defined in the dex of the 0 matching APK(s)', 2),
                (dex_fixture.apk([dex_fixture.dex([(GESTURE, ['<init>']), LAUNCHER3[1]])]),
                 'RoseEdgeGesture; with method stage is not defined in the dex of the 1 matching APK(s)', 1),
                # R8 renamed it: RoseEdgeGesture has no literal, so a rename fails closed even beside its old name's strings.
                (dex_fixture.apk([dex_fixture.dex([('Lcom/android/quickstep/a;', ['stage']), LAUNCHER3[1]],
                                                  strings=['RoseEdgeGesture', GESTURE])]), 'RoseEdgeGesture; with method stage', 1)):
            with self.subTest(message=message):
                failures, receipt = self.check(launcher3=launcher3)
                self.assertEqual(len(failures), count, failures)
                self.assertIn(message, failures[0])
                self.assertFalse(receipt['classes'][0]['pass'])

    def test_renamed_classes_pass_only_on_their_whole_literal(self):
        renamed = [('Lcom/android/quickstep/fallback/b;', ['onMove']), (GESTURE, ['stage'])]
        cases = ((dex_fixture.dex(renamed, strings=[DIAG]), True),
                 (dex_fixture.dex(renamed, strings=[DIAG + '_x', 'x' + DIAG]), False),  # only inside longer strings
                 (dex_fixture.dex(renamed), False))
        for data, passes in cases:
            with self.subTest(passes=passes):
                failures, receipt = self.check(launcher3=dex_fixture.apk([data]))
                swipe = receipt['classes'][1]
                self.assertEqual((swipe['pass'], swipe['by']), (passes, 'literal' if passes else None))
                self.assertEqual(failures == [], passes)
        # The class is preferred: a dex that defines it passes by class even when an earlier dex holds the literal.
        failures, receipt = self.check(systemui=dex_fixture.apk([dex_fixture.dex([], strings=[DESCRIPTOR, MIC_TAG]),
                                                                 dex_fixture.dex(SYSTEMUI)]))
        self.assertEqual([c['by'] for c in receipt['classes'][2:]], ['class', 'class'])

    def test_only_apks_whose_whole_path_matches_count(self):
        elsewhere = {'VENDOR/priv-app/SystemUI/SystemUI.apk': dex_fixture.apk([dex_fixture.dex(SYSTEMUI)]),
                     'X/SYSTEM_EXT/priv-app/SystemUI/SystemUI.apk': dex_fixture.apk([dex_fixture.dex(SYSTEMUI)]),
                     'SYSTEM_EXT/priv-app/SystemUI/SystemUI.apk.idsig': b'',
                     'SYSTEM_EXT/priv-app/SystemUI/oat/arm64/SystemUI.apk': dex_fixture.apk([dex_fixture.dex(SYSTEMUI)])}
        failures, receipt = self.check(systemui=False, extra=elsewhere)
        self.assertEqual([c['apks'] for c in receipt['classes'][2:]], [[], []])
        self.assertEqual(len(failures), 2)
        # A differently named product package is still a candidate (OS Product's patterns).
        failures, receipt = self.check(launcher3=False, extra={'PRODUCT/priv-app/Launcher3QuickStepGo/Launcher3QuickStepGo.apk':
                                                               dex_fixture.apk([dex_fixture.dex(LAUNCHER3)])})
        self.assertEqual(failures, [])

    def test_unreadable_dex_fails_closed_and_is_named(self):
        for systemui, why in ((dex_fixture.apk([b'dex\n039\x00not a dex']), 'classes.dex: truncated dex header'),
                              (dex_fixture.apk([]), '(apk): no classes*.dex'),
                              (b'not an apk', '(apk): not a readable APK archive')):
            with self.subTest(why=why):
                failures, receipt = self.check(systemui=systemui)
                self.assertEqual(len(failures), 2, failures)
                self.assertIn(f'unreadable: [\'{SYSUI_APK}!{why}', failures[0])
                self.assertEqual(receipt['classes'][2]['unreadable'], [f'{SYSUI_APK}!{why}'])
        # An id that points outside the dex: the scan stops there, so what lies past it counts as absent. Types sort
        # by name, so type 0 is RoseEdgeMicSlot's: the controller's class_def, read first, still passes.
        bad_ids = bytearray(dex_fixture.dex(SYSTEMUI))
        struct.pack_into('<I', bad_ids, edge.sections(bytes(bad_ids))[0].types_off, 999)
        failures, receipt = self.check(systemui=dex_fixture.apk([bytes(bad_ids)]))
        self.assertEqual([c['pass'] for c in receipt['classes']], [True, True, True, False])
        self.assertEqual(receipt['classes'][3]['unreadable'], [f'{SYSUI_APK}!classes.dex: string index 999 out of range'])
        self.assertIn('RoseEdgeMicSlot; is not defined', failures[0])
        # A damaged member (bad CRC) is unreadable too, not an error that stops the check.
        damaged = bytearray(dex_fixture.apk([dex_fixture.dex(SYSTEMUI)]))
        start = damaged.index(b'dex\n039\x00')
        damaged[start + 0x40] ^= 0xff
        failures, receipt = self.check(systemui=bytes(damaged))
        self.assertEqual(receipt['classes'][2]['unreadable'], [f'{SYSUI_APK}!classes.dex: damaged APK member (BadZipFile)'])
        self.assertEqual(len(failures), 2)
        # A dex 041 header shorter than its 0x78 bytes.
        short = dex_fixture.dex(SYSTEMUI, version=41)[:0x74]
        with self.assertRaisesRegex(edge.DexError, 'truncated dex header'):
            edge.sections(short)
        # A readable dex elsewhere in the APK still counts; the unreadable one is still named.
        failures, receipt = self.check(systemui=dex_fixture.apk([b'garbage', dex_fixture.dex(SYSTEMUI)]))
        self.assertEqual(failures, [])
        self.assertEqual(receipt['classes'][3]['unreadable'], [f'{SYSUI_APK}!classes.dex: no dex magic'])

    def test_ids_past_their_tables_make_the_dex_unreadable(self):
        # A class_def naming a type past the type table, or class_data naming a method past the method table, would
        # otherwise read a neighbouring table's bytes as a name.
        data = dex_fixture.dex(SYSTEMUI, strings=[DESCRIPTOR, MIC_TAG])
        section = edge.sections(data)[0]
        past_types = bytearray(data)
        struct.pack_into('<I', past_types, section.classes_off, section.types)  # the controller's class_def
        past_methods = bytearray(data)
        data_off = edge.u32(data, section.classes_off + 24)  # the controller's class_data: four sizes, then <init>'s index
        self.assertLess(section.methods, 0x80)
        past_methods[data_off + 4] = section.methods
        for bad, why in ((past_types, f'type index {section.types} out of range'), (past_methods, f'method index {section.methods} out of range')):
            with self.subTest(why=why):
                failures, receipt = self.check(systemui=dex_fixture.apk([bytes(bad)]))
                controller = receipt['classes'][2]
                self.assertEqual(controller['unreadable'], [f'{SYSUI_APK}!classes.dex: {why}'])
                # Once unreadable, the dex holds nothing for that entry: its literal is not consulted either.
                self.assertEqual((controller['pass'], controller['by']), (False, None))
                self.assertIn('RoseEdgeController; is not defined', failures[0])

    def test_only_classes_dex_members_are_code(self):
        moved = dex_fixture.apk([dex_fixture.dex([('Lcom/android/systemui/A;', ['<init>'])])],
                                {'assets/edge.dex': dex_fixture.dex(SYSTEMUI), 'classes2.dex.bak': dex_fixture.dex(SYSTEMUI),
                                 'lib/classes3.dex': dex_fixture.dex(SYSTEMUI)})
        failures, receipt = self.check(systemui=moved)
        self.assertEqual(len(failures), 2, failures)
        self.assertEqual([c['pass'] for c in receipt['classes'][2:]], [False, False])

    def test_spec_problems_are_refused_before_reading(self):
        self.assertEqual(edge.missing(None), ['roseEdge.props', 'roseEdge.propFiles', 'roseEdge.classes'])
        self.assertEqual(edge.missing({**SPEC, 'classes': []}), ['roseEdge.classes'])
        self.assertEqual(edge.missing(SPEC), [])
        entry = SPEC['classes'][0]
        for bad, message in (({**SPEC, 'props': ['persist.wm.debug.rose_edge']}, 'key=value'),
                             ({**SPEC, 'propFiles': ['SYSTEM/../x/build.prop']}, 'build.prop paths'),
                             ({**SPEC, 'propFiles': ['SYSTEM/build.txt']}, 'build.prop paths'),
                             ({**SPEC, 'classes': [{**entry, 'extra': 1}]}, 'need exactly'),
                             ({**SPEC, 'classes': [{k: v for k, v in entry.items() if k != 'literal'}]}, 'need exactly'),
                             ({**SPEC, 'classes': [{**entry, 'apk': 'SYSTEM/app/x.apk'}]}, r'pattern of \.apk'),
                             ({**SPEC, 'classes': [{**entry, 'apk': r'(\.apk'}]}, 'not a valid pattern'),
                             ({**SPEC, 'classes': [{**entry, 'class': 'com.android.X'}]}, 'class descriptor'),
                             ({**SPEC, 'classes': [{**entry, 'method': 'a b'}]}, 'method name or null'),
                             ({**SPEC, 'classes': [{**entry, 'literal': ''}]}, 'non-empty ASCII'),
                             ({**SPEC, 'classes': [{**entry, 'literal': 'é'}]}, 'non-empty ASCII'),
                             ({**SPEC, 'props': []}, 'setup missing: roseEdge.props')):
            with self.subTest(message=message):
                with self.assertRaisesRegex(edge.SpecError, message):
                    edge.check(target_files(self.path), bad)


class CommandTest(unittest.TestCase):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = edge.main([str(a) for a in argv])
        return code, out.getvalue(), err.getvalue()

    def test_prints_one_line_per_check_and_exits_by_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = target_files(Path(tmp)/'good.zip')
            code, out, _ = self.run_main(good)
            self.assertEqual(code, 0)
            self.assertEqual(out.splitlines()[:6], [
                'PASS prop persist.wm.debug.rose_edge=true', 'PASS prop persist.wm.debug.pixel_agent_tray=1',
                'PASS class com.android.quickstep.fallback.RoseEdgeGesture.stage in 1 candidate apk(s) by class',
                'PASS class com.android.quickstep.fallback.RoseEdgeSwipe.onMove in 1 candidate apk(s) by class',
                'PASS class com.android.wm.shell.roseedge.RoseEdgeController in 1 candidate apk(s) by class',
                'PASS class com.android.wm.shell.RoseEdgeMicSlot in 1 candidate apk(s) by class'])
            self.assertEqual(json.loads(out.split('\n', 6)[6])['verdict'], 'PASS')
            code, out, _ = self.run_main(target_files(Path(tmp)/'bad.zip', launcher3=False))
            self.assertEqual(code, 1)
            self.assertIn('FAIL class com.android.quickstep.fallback.RoseEdgeGesture.stage in 0 candidate apk(s)', out)
            config = Path(tmp)/'config.json'
            config.write_text(json.dumps({'roseEdge': {'props': SPEC['props']}}))
            code, _, err = self.run_main(good, '--config', config)
            self.assertEqual((code, json.loads(err)['verdict']), (2, 'INVALID'))
            code, _, err = self.run_main(Path(tmp)/'absent.zip')
            self.assertEqual((code, json.loads(err)['verdict']), (2, 'INVALID'))


class ContractTest(unittest.TestCase):
    """frankel.json's roseEdge is OS Product's checker, bound to the OS sources that put each item in the image."""
    OVERLAY = ROOT/'os/overlays/packages/apps/Launcher3/quickstep/src/com/android/quickstep/fallback'

    def test_spec_is_os_products_checker(self):
        self.assertEqual(edge.validate(SPEC), SPEC)
        self.assertEqual(SPEC['props'], ['persist.wm.debug.rose_edge=true', 'persist.wm.debug.pixel_agent_tray=1'])
        self.assertEqual(SPEC['propFiles'], ['SYSTEM/build.prop', 'SYSTEM/etc/build.prop', 'PRODUCT/etc/build.prop', 'SYSTEM_EXT/etc/build.prop'])
        l3 = r'(SYSTEM_EXT|SYSTEM|PRODUCT)/priv-app/[^/]*Launcher3QuickStep[^/]*/[^/]+\.apk'
        sysui = r'(SYSTEM_EXT|SYSTEM|PRODUCT)/priv-app/SystemUI[^/]*/[^/]+\.apk'
        self.assertEqual([(c['apk'], c['class'], c['method'], c['literal']) for c in SPEC['classes']],
                         [(l3, GESTURE, 'stage', None), (l3, SWIPE, 'onMove', DIAG),
                          (sysui, CONTROLLER, None, DESCRIPTOR), (sysui, MIC, None, MIC_TAG)])
        # The SystemUI path the tray gate already reads is one of the candidates.
        self.assertRegex(CONFIG['systemUiTray']['apkPath'], '^' + sysui + '$')

    def test_props_come_from_the_patches_frankel_registers(self):
        self.assertIn('+PRODUCT_SYSTEM_PROPERTIES += persist.wm.debug.rose_edge=true\n',
                      (ROOT/'os/patches/rose-edge-build-make.patch').read_text())
        self.assertIn('+PRODUCT_SYSTEM_PROPERTIES += persist.wm.debug.pixel_agent_tray=1\n',
                      (ROOT/'os/patches/agent-tray-build-make.patch').read_text())
        apply = (ROOT/'scripts/apply-os-changes.py').read_text()
        self.assertIn("if a.agent_tray:projects['build/make'].append('rose-edge-build-make.patch')", apply)
        self.assertIn('--agent-tray', CONFIG['host']['integrate']['reconcile']['applyArgs'])

    def test_launcher3_classes_methods_and_literal(self):
        gesture, swipe = (self.OVERLAY/'RoseEdgeGesture.java').read_text(), (self.OVERLAY/'RoseEdgeSwipe.java').read_text()
        self.assertIn('package com.android.quickstep.fallback;', gesture)
        self.assertRegex(gesture, r'\n(public )?(final )?class RoseEdgeGesture\b')
        self.assertRegex(gesture, r'\n    public static int stage\(')
        self.assertRegex(swipe, r'\n(public )?(final )?class RoseEdgeSwipe\b')
        self.assertRegex(swipe, r'\n    public int onMove\(')
        self.assertIn(f'= "{DIAG}";', swipe)
        # The literal is RoseEdgeSwipe's alone among the Launcher3 sources RoseOS adds.
        sources = [p for p in (ROOT/'os/overlays/packages/apps/Launcher3').rglob('*.java')]
        sources += sorted((ROOT/'os/patches').glob('*launcher3*.patch'))
        holders = [p.name for p in sources if f'"{DIAG}"' in p.read_text()]
        self.assertEqual(holders, ['RoseEdgeSwipe.java'])

    def test_shell_classes_and_literals(self):
        patch = (ROOT/'os/patches/rose-edge-wmshell.patch').read_text()
        files = dict(re.findall(r'\n\+\+\+ b/libs/WindowManager/Shell/src/(\S+)\n(.*?)(?=\ndiff --git |\Z)', patch, re.S))
        controller = files['com/android/wm/shell/roseedge/RoseEdgeController.java']
        aidl = files['com/android/wm/shell/roseedge/IRoseEdge.aidl']
        mic = files['com/android/wm/shell/RoseEdgeMicSlot.java']
        self.assertIn('+package com.android.wm.shell.roseedge;', controller)
        self.assertIn('+public class RoseEdgeController ', controller)
        self.assertIn('IRoseEdge.DESCRIPTOR', controller)  # the generated interface's descriptor names it
        self.assertIn('+package com.android.wm.shell.roseedge;', aidl)
        self.assertIn('+interface IRoseEdge {', aidl)
        self.assertIn('+package com.android.wm.shell;', mic)
        self.assertIn('+public final class RoseEdgeMicSlot {', mic)
        self.assertIn(f'+    private static final String TAG = "{MIC_TAG}";', mic)
        # No other Shell or SystemUI source RoseOS adds holds either literal.
        others = [p for p in (ROOT/'os/overlays/frameworks/base').rglob('*.java')] + sorted((ROOT/'os/patches').glob('*.patch'))
        for literal, owner in ((f'"{MIC_TAG}"', 'rose-edge-wmshell.patch'), (f'"{DESCRIPTOR}"', None)):
            with self.subTest(literal=literal):
                self.assertEqual([p.name for p in others if literal in p.read_text()], [owner] if owner else [])

    def test_r8_keeps_shell_names_but_not_its_subpackages(self):
        # SystemUI's keep rule covers com.android.wm.shell.* only: RoseEdgeMicSlot's name survives, RoseEdgeController's
        # (com.android.wm.shell.roseedge) may not, so its entry needs the literal. Launcher3's gesture has none: fail closed.
        proguard = (ROOT/'scripts/tests/fixtures/systemui-84183aff/SystemUI-proguard_common.flags').read_text()
        self.assertIn('-keep class com.android.wm.shell.* {', proguard)
        self.assertNotIn('com.android.wm.shell.**', proguard)
        self.assertNotIn('roseedge', proguard)
        literals = {c['class']: c['literal'] for c in SPEC['classes']}
        self.assertIsNotNone(literals[CONTROLLER])
        self.assertIsNone(literals[GESTURE])


if __name__ == '__main__':
    unittest.main()
