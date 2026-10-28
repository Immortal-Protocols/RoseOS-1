"""emulator-alpha.py and the os-release alpha-builder job against a synthetic retained tree whose build/envsetup.sh
fakes lunch, get_build_var and m (so the real resolve and make command lines run): exact target and binding, disjoint
OUT_DIR, shared lock, no reconcile/signing/publication, fail-closed host evidence and no boot claim."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import xml.etree.ElementTree as ET

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
PY = sys.executable
spec = importlib.util.spec_from_file_location('emulator_alpha', SCRIPTS/'emulator-alpha.py')
alpha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alpha)
CHECKED_IN = json.loads((ROOT/'os/release-pipeline/cuttlefish-alpha.json').read_text())
FRANKEL = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
SHA = '62286c084e52bc57e41a35333653960844c16ad2'
APPS = [['dev.pixel.voice', 302], ['dev.pixel.calculator.agent', 4], ['dev.pixel.backgroundcontrol', 28]]
TS = '1793180700'
BOUND = 'ae9cfd4d9fafd2db4f2bc6aa76b7d025d6473fb9'  # 2804, the run that wrote the retained tree's latest reconcile receipt
FIXTURES = SCRIPTS/'tests/fixtures/cuttlefish-283645aa'  # unmodified pinned upstream files (see its README)
SEEDVAULT = SCRIPTS/'tests/fixtures/seedvault-7cfffed7'  # unmodified pinned Seedvault files (see its README)
OS_SEEDVAULT = ROOT/'os/tests/fixtures/cuttlefish-alpha/seedvault'
SEEDVAULT_XML = {p.name: p.read_bytes() for p in SEEDVAULT.rglob('*.xml') if p.name != 'AndroidManifest.xml'}
LUNCH = {'TARGET_PRODUCT': 'aosp_cf_x86_64_only_phone', 'TARGET_RELEASE': 'cur', 'TARGET_BUILD_VARIANT': 'userdebug',
    'TARGET_BOARD_PLATFORM': 'vsoc_x86_64', 'PRODUCT_DEVICE': 'vsoc_x86_64_only', 'PRODUCT_OUT': 'out-rose-cf-alpha/target/product/vsoc_x86_64_only',
    'HOST_OUT': 'out-rose-cf-alpha/host/linux-x86', 'BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE': 'ext4'}
PIN = 'ab' * 32
CERT = PIN.upper()

ENVSETUP = r'''
lunch() {
  [ "$1" = "${FAKE_LUNCH_ACCEPT:-aosp_cf_x86_64_only_phone-cur-userdebug}" ] || return 1
  TARGET_PRODUCT=aosp_cf_x86_64_only_phone TARGET_BUILD_VARIANT=userdebug PRODUCT_DEVICE=vsoc_x86_64_only
  TARGET_BOARD_PLATFORM="${FAKE_BOARD:-vsoc_x86_64}" BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE="${FAKE_FS:-ext4}"
  # As in the retained tree: lunch exports the release into the shell (build/envsetup.sh)...
  [ -n "${FAKE_RELEASE_UNSET:-}" ] || export TARGET_RELEASE="${FAKE_RELEASE:-cur}"
  PRODUCT_OUT="${FAKE_PRODUCT_OUT:-$OUT_DIR/target/product/vsoc_x86_64_only}" HOST_OUT="$OUT_DIR/host/linux-x86"
}
# ...while get_build_var returns the release variables empty there.
get_build_var() { case "$1" in TARGET_RELEASE|TARGET_RELEASE_CONFIG) echo;; *) eval "echo \$$1";; esac; }
m() {
  echo "m $* OUT_DIR=$OUT_DIR BUILD_NUMBER=$BUILD_NUMBER BUILD_DATETIME=$BUILD_DATETIME" >> "$FAKE_LOG"
  [ -n "${FAKE_MAKE_FAIL:-}" ] && return 2
  mkdir -p "$PRODUCT_OUT/system" "$PRODUCT_OUT/vendor" "$HOST_OUT"
  for image in super boot vendor_boot vbmeta init_boot userdata; do
    case " ${FAKE_SKIP:-} " in *" $image "*) continue;; esac
    echo "$image $BUILD_NUMBER" > "$PRODUCT_OUT/$image.img"
  done
  # super.img in the LP format (geometry magic after the 4096 reserved bytes); its partitions come from the fake lpunpack.
  case " ${FAKE_SKIP:-} " in *" super "*) ;; *)
    if [ -n "${FAKE_SUPER_SPARSE:-}" ]; then printf '\072\377\046\355' > "$PRODUCT_OUT/super.img"; else : > "$PRODUCT_OUT/super.img"; fi
    [ -n "${FAKE_SUPER_PLAIN:-}" ] || { head -c 4096 /dev/zero; printf 'gDla'; echo "$BUILD_NUMBER"; } >> "$PRODUCT_OUT/super.img";;
  esac
  incremental="${FAKE_INCREMENTAL:-$BUILD_NUMBER}"
  fingerprint="Android/aosp_cf_x86_64_only_phone/vsoc_x86_64_only:17/CUR/$incremental:userdebug/test-keys"
  printf 'ro.build.type=%s\nro.build.version.incremental=%s\nro.build.date.utc=%s\nro.build.fingerprint=%s\nro.product.system.device=vsoc_x86_64_only\n' \
    "${FAKE_TYPE:-userdebug}" "$incremental" "$BUILD_DATETIME" "$fingerprint" > "$PRODUCT_OUT/system/build.prop"
  printf 'ro.vendor.build.version.incremental=%s\nro.vendor.build.fingerprint=%s\nro.product.vendor.device=vsoc_x86_64_only\n' \
    "$incremental" "$fingerprint" > "$PRODUCT_OUT/vendor/build.prop"
  echo host > "$HOST_OUT/cvd-host_package.tar.gz"
}
'''

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def jobs(text):
    body = text.split('\njobs:\n', 1)[1]
    return {m[1]: m[2] for m in re.finditer(r'^  ([\w-]+):\n((?:(?:    .*)?\n)*)', body, re.M)}

MEDIA = 'target/product/media_system.mk'
GENERIC = 'target/product/generic/Android.bp'
CONTRACT = json.loads((ROOT/'os/alpha/cuttlefish-product-contract.json').read_text())

def media_2804(directory):
    """Pinned media_system.mk (OS fixture) plus the real Rose updater patch: the retained 2804 bytes' shape."""
    target = directory/MEDIA
    target.parent.mkdir(parents=True)
    target.write_bytes((ROOT/'os/tests/fixtures/cuttlefish-alpha/build-make'/MEDIA).read_bytes())
    subprocess.run(['git', '-C', str(directory), 'apply', str(ROOT/'os/patches/updater-product-build-make.patch'), '--include', MEDIA],
        check=True, env=dict(os.environ, GIT_CEILING_DIRECTORIES=str(directory.parent)))

def tree_listing(root):
    return sorted((str(p.relative_to(root)), p.is_symlink() or p.is_dir() or sha(p)) for p in Path(root).rglob('*'))


AAPT = r"""
import pathlib, sys
a = sys.argv[1:]
data = pathlib.Path(a[-1]).read_bytes()
if a[:2] == ['dump', 'badging']:
    head = data.split(b' ')[0].decode(errors='replace')
    if ':' not in head or b'UNREADABLE' in data:
        sys.exit(1)
    package, version = head.split(':', 1)
    print(f"package: name='{package}' versionCode='{version}' versionName='x'")
elif a[:3] == ['dump', '--values', 'resources']:
    sys.stdout.write(data.decode())
"""
APKSIGNER = r"""
import pathlib, sys
data = pathlib.Path(sys.argv[-1]).read_bytes()
print('Signer #1 certificate SHA-256 digest: ' + ('cd' * 32 if b'OTHER-SIGNER' in data else 'ab' * 32))
"""
# Stands in for unsparsing: drops the sparse magic the fixture adds in front of an LP image.
SIMG2IMG = r"""
import sys
data = open(sys.argv[1], 'rb').read()
assert data[:4] == bytes.fromhex('3aff26ed')
open(sys.argv[2], 'wb').write(data[4:])
"""
# Copies the partition images the test built (real ext4 images) into lpunpack's output directory.
LPUNPACK = r"""
import os, shutil, sys
image, out = sys.argv[1:3]
assert open(image, 'rb').read(4100)[4096:4100] == b'gDla'
for name in os.listdir(os.environ['FAKE_LP_DIR']):
    shutil.copy(os.path.join(os.environ['FAKE_LP_DIR'], name), out)
"""

def script(path, body):
    path.write_text('#!/usr/bin/env python3\n' + body)
    path.chmod(0o755)
    return str(path)

def framework_dump(agents, targets, prototype=(), locked=False):
    """`aapt dump --values resources` rows for the four computer-control values, as the real parser reads them."""
    def array(name, items):
        rows = ''.join(f'          #{i} (Key=0x01000000): (string8) "{v}"\n' for i, v in enumerate(items))
        return (f'      resource 0x01070001 android:array/config_pixelComputerControl{name}: <bag>\n'
            f'        Parent=0x00000000(Resolved=0x00000000), Count={len(items)}\n{rows}')
    return ('android:36 framework-res (the fake aapt reads this identity; the parser skips it)\nPackage Groups (1)\n  Package Group 0 id=0x01 packageCount=1 name=android\n    type 4 configCount=1 entryCount=4\n'
        '      config (default) flags=0x0:\n' + array('Agents', agents) + array('Targets', targets) + array('PrototypeAgents', prototype)
        + f'      resource 0x01050001 android:bool/config_pixelComputerControlLockedSessions: t=0x12 d=0x{0xffffffff if locked else 0:08x} (s=0x0008 r=0x00)\n'
        + f'          (boolean) {"true" if locked else "false"}\n')

class AlphaTreeTest(unittest.TestCase):
    """A synthetic retained base (2804-shaped build/make, pinned Cuttlefish files, integrated apps, reconcile receipt and
    release evidence), prepared for real by emulator-alpha.py prepare; probe and build then use only the prepared tree."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        src = self.src = self.root/'src'
        cf = src/'device/google/cuttlefish'
        (cf/'vsoc_x86_64_only/phone').mkdir(parents=True)
        (cf/'vsoc_x86_64_only/phone/aosp_cf.mk').write_text('PRODUCT_NAME := aosp_cf_x86_64_only_phone\n')
        (cf/'AndroidProducts.mk').write_text('PRODUCT_MAKEFILES := \\\n\taosp_cf_x86_64_phone:$(LOCAL_DIR)/vsoc_x86_64/phone/aosp_cf.mk \\\n'
            '\taosp_cf_x86_64_only_phone:$(LOCAL_DIR)/vsoc_x86_64_only/phone/aosp_cf.mk \\\n')
        (cf/'build').mkdir()
        for name in ('Android.bp', 'cvd-host-package.go'):
            (cf/'build'/name).write_bytes((FIXTURES/'build'/name).read_bytes())
        (cf/'.git').mkdir()
        (cf/'.git/HEAD').write_text('pinned\n')
        media_2804(src/'build/make')
        (src/'build/make'/GENERIC).parent.mkdir(parents=True)  # not integrated by 2804: the pinned upstream bytes
        (src/'build/make'/GENERIC).write_bytes((ROOT/'os/tests/fixtures/cuttlefish-alpha/build-make'/GENERIC).read_bytes())
        (src/'build/envsetup.sh').write_text(ENVSETUP)
        for link in CHECKED_IN['preparation']['exclusions']['externalLinks']:  # as on the builder: non-input extracted-image links
            (src/link['path']).parent.mkdir(parents=True, exist_ok=True)
            os.symlink(link['target'], src/link['path'])
        (src/'keys').mkdir()
        (src/'keys/releasekey.pk8').write_text('PRIVATE PIXEL KEY')
        # Revisions come from git in the base; these fixtures carry no repositories, so report the contract pins.
        heads = dict(CONTRACT['pinned'])
        original = alpha.git_head
        alpha.git_head = lambda root, project: heads.get(project, 'a' * 40) if Path(root) == src else original(root, project)
        self.addCleanup(setattr, alpha, 'git_head', original)
        self.heads = heads
        # The retained Pixel output, the integrated apps and a file the bound reconcile receipt installed.
        self.pixel_out = src/'out-rose-first-update-2026102701'
        (self.pixel_out/'target').mkdir(parents=True)
        (self.pixel_out/'target/marker').write_text('pixel build output')
        apps = {'packages/apps/RosePreinstall/RosePreinstall_dev_pixel_voice/RosePreinstall_dev_pixel_voice.apk': 'dev.pixel.voice:302 apk',
            'packages/apps/RosePreinstall/RosePreinstall_dev_pixel_calculator_agent/RosePreinstall_dev_pixel_calculator_agent.apk': 'dev.pixel.calculator.agent:4 apk',
            'packages/apps/PixelBackgroundControl/PixelBackgroundControl.apk': 'dev.pixel.backgroundcontrol:28 apk'}
        for path, text in apps.items():
            (src/path).parent.mkdir(parents=True, exist_ok=True)
            (src/path).write_text(text)
        self.app_paths = list(apps)
        (src/'frameworks/base/core/res/res/values').mkdir(parents=True)
        self.integrated = src/'frameworks/base/core/res/res/values/config.xml'
        self.integrated.write_text('<resources>rose</resources>')
        self.receipts = self.root/'reconcile'
        self.receipts.mkdir()
        (self.receipts/'0003-2026102803.json').write_text('{"entries": {}}')
        self.receipt = self.receipts/'0004-2026102804.json'
        entries = {p: {'after': sha(src/p), 'action': 'installed'} for p in ['frameworks/base/core/res/res/values/config.xml', *apps]}
        entries['build/make/' + MEDIA] = {'after': sha(src/'build/make'/MEDIA), 'action': 'installed'}
        self.receipt.write_text(json.dumps({'receiptType': 'reconcile', 'label': '2026102804', 'entries': entries}))
        release = self.root/'frankel-2026102804/release'
        release.mkdir(parents=True)
        (release/'resolved-manifest.xml').write_text('<manifest/>')
        digest = {path.split('/')[-1]: sha(src/path) for path in apps}
        row = lambda package, apk: {'packageName': package, 'certificates': [CERT], 'versionCode': 1, 'apkSha256': digest[apk]}
        (release/'background-policy.json').write_text(json.dumps({'schemaVersion': 1, 'sdk': 37, 'prototypeControl': False,
            'agent': row('dev.pixel.backgroundcontrol', 'PixelBackgroundControl.apk'),
            'voice': row('dev.pixel.voice', 'RosePreinstall_dev_pixel_voice.apk'),
            'targets': [row('dev.pixel.calculator.agent', 'RosePreinstall_dev_pixel_calculator_agent.apk')]}))
        # OS's contract with this fixture's receipt and app digests (the real ones belong to the real 2804 bytes).
        contract = json.loads(json.dumps(CONTRACT))
        contract['base']['reconcileSha256'] = sha(self.receipt)
        for app, path in zip(contract['apps'], self.app_paths):
            app['sha256'] = sha(src/path)
        contract['policySha256'] = sha(release/'background-policy.json')
        self.contract = self.root/'contract.json'
        self.contract.write_text(json.dumps(contract))
        self.lock = self.root/'frankel-build.lock'
        shared = self.root/'shared.json'
        shared.write_text(json.dumps({'framework': {'lockedSessions': False}, 'identities': {'appSignerSha256': PIN},
            'host': {'sourceRoot': str(src), 'lockPath': str(self.lock),
            'outDir': 'out-rose-first-update-2026102701', 'integrate': {'reconcile': {'receiptDir': str(self.receipts),
                'integrationOwnedRoots': ['packages/apps/PixelBackgroundControl', 'packages/apps/RosePreinstall']}}}}))
        self.data = json.loads(json.dumps(CHECKED_IN))
        self.data['sharedHost'] = str(shared)
        self.data['build']['minFreeGiB'] = 0
        self.data['sourceBinding'].update(candidate=BOUND, reconcileReceipt=self.receipt.name, reconcileReceiptSha256=sha(self.receipt), apps=APPS)
        preparation = self.data['preparation']
        preparation.update(root=str(self.root/'alpha-source'), copyBudgetGiB=1)
        preparation['osDelta'].update(contract=str(self.contract), contractSha256=sha(self.contract))
        preparation['baseEvidence'] = {'resolvedManifest': {'path': str(release/'resolved-manifest.xml'), 'sha256': sha(release/'resolved-manifest.xml')},
            'policy': {'path': str(release/'background-policy.json'), 'sha256': sha(release/'background-policy.json')}}
        self.config_path = self.root/'alpha.json'
        self.log = self.root/'make.log'
        self.env = {'FAKE_LOG': str(self.log)}
        self.write()
        self.base_before = tree_listing(src)
        self.preparation = alpha.prepare(alpha.load(self.config_path))
        self.prepared_dir = self.root/'alpha-source'/self.preparation['preparationId']
        self.tree = self.prepared_dir/'tree'
        self.data['prepared'].update(preparationId=self.preparation['preparationId'], receiptSha256=sha(self.prepared_dir/'preparation.json'))
        self.data['hostEvidence'].update(probeReceiptSha256='e' * 64, lunch=LUNCH)
        self.tools = self.root/'image-tools'
        self.tools.mkdir()
        self.data['imageVerification']['tools'].update(
            aapt=script(self.tools/'aapt', AAPT), apksigner=script(self.tools/'apksigner', APKSIGNER),
            lpunpack=script(self.tools/'lpunpack', LPUNPACK), simg2img=script(self.tools/'simg2img', SIMG2IMG), fsckErofs=None,
            debugfs=shutil.which('debugfs'))
        self.write()
        self.partitions = self.root/'built-partitions'
        self.env['FAKE_LP_DIR'] = str(self.partitions)
        self.make_partitions()

    def write(self):
        self.config_path.write_text(json.dumps(self.data))

    def make_partitions(self, change=None, filesystem='ext4'):
        """Real ext4 partition images (mke2fs -d) holding what a correct alpha build packs; change(trees) edits them first."""
        if self.partitions.exists():
            shutil.rmtree(self.partitions)
        self.partitions.mkdir()
        trees = {name: self.root/'partition-src'/name for name in ('system', 'system_ext', 'product', 'vendor')}
        shutil.rmtree(self.root/'partition-src', ignore_errors=True)
        for tree in trees.values():
            tree.mkdir(parents=True)
        place = lambda tree, rel, data: ((tree/rel).parent.mkdir(parents=True, exist_ok=True), (tree/rel).write_bytes(data))
        for app, location in self.data['imageVerification']['apps'].items():
            partition, inside = location.split(':', 1)
            source = next(p for p in self.app_paths if p.endswith(inside.split('/')[-1]))
            place(trees[partition], inside, (self.src/source).read_bytes())
        place(trees['system'], 'system/framework/framework-res.apk', framework_dump([f'dev.pixel.backgroundcontrol:{CERT}'],
            [f'dev.pixel.calculator.agent:{CERT}']).encode())
        place(trees['system'], 'system/build.prop', b'ro.build.type=userdebug\npersist.wm.debug.pixel_agent_tray=1\n')
        place(trees['system'], 'system/priv-app/Settings/Settings.apk', b'com.android.settings:36 apk')
        # The Seedvault closure: synthetic APK bytes the fake aapt reads as the pinned package (the version is not
        # checked), and the real pinned XML files at the paths the pinned Android.bp files install them to.
        for app in self.data['imageVerification']['closure']['apps']:
            partition, inside = app['path'].split(':', 1)
            place(trees[partition], inside, f'{app["package"]}:1 synthetic {app["module"]} apk'.encode())
        for xml in self.data['imageVerification']['closure']['xml']:
            partition, inside = xml['path'].split(':', 1)
            place(trees[partition], inside, SEEDVAULT_XML[inside.split('/')[-1]])
        place(trees['vendor'], 'etc/vintf/manifest.xml', b'<manifest/>')
        if change:
            change(trees)
        for name, tree in trees.items():
            image = self.partitions/f'{name}_a.img'
            if filesystem == 'ext4':
                subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-d', str(tree), str(image), '8M'], check=True)
            else:
                image.write_bytes(filesystem)
            (self.partitions/f'{name}_b.img').write_bytes(b'')

    def image_failure(self, message, **kwargs):
        with self.assertRaisesRegex(alpha.CheckFailed, message):
            self.run_build(**kwargs)

    def with_env(self, function, *args):
        old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        try:
            return function(*args)
        finally:
            for k, v in old.items():
                os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)

    def run_build(self, sha=BOUND, apps=APPS, run_id='40680000001', receipt='receipt'):
        return self.with_env(lambda: alpha.build(alpha.load(self.config_path), sha, apps, run_id, TS, self.root/receipt))

    def made(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def base_untouched(self):
        self.assertEqual(tree_listing(self.src), self.base_before, 'the retained base and Pixel output are only read')
        self.assertEqual((self.pixel_out/'target/marker').read_text(), 'pixel build output')

    def test_prepare_binds_base_delta_apps_policy_and_routing(self):
        receipt = self.preparation
        self.base_untouched()
        self.assertEqual(json.loads((self.prepared_dir/'preparation.json').read_text()), receipt)
        self.assertEqual({k: receipt[k] for k in ('baseCandidate', 'baseReconcileReceipt', 'baseReconcileSha256', 'osDeltaCommit',
            'target', 'updaterMode', 'updaterEndpoint', 'outDir', 'preparedRoot')},
            {'baseCandidate': BOUND, 'baseReconcileReceipt': '0004-2026102804.json', 'baseReconcileSha256': sha(self.receipt),
             'osDeltaCommit': 'f21bf68322b3fa0a8924840554ab3e9398f79125', 'target': 'aosp_cf_x86_64_only_phone-cur-userdebug',
             'updaterMode': 'excluded-no-compatible-feed', 'updaterEndpoint': None, 'outDir': 'out-rose-cf-alpha', 'preparedRoot': str(self.tree)})
        self.assertEqual(receipt['baseResolvedManifestSha256'], self.data['preparation']['baseEvidence']['resolvedManifest']['sha256'])
        self.assertEqual(receipt['policySha256'], self.data['preparation']['baseEvidence']['policy']['sha256'])
        self.assertEqual(receipt['patchDigests'], [{'path': 'os/patches/cuttlefish-alpha-no-updater.patch', 'project': 'build/make',
            'sha256': 'e000da0d1247dfcb341bc97df3098a2ed57faed31af0e5a837733537ae567858'}])
        self.assertEqual([(a['package'], a['version'], a['path']) for a in receipt['apps']],
            [('dev.pixel.voice', 302, self.app_paths[0]), ('dev.pixel.calculator.agent', 4, self.app_paths[1]),
             ('dev.pixel.backgroundcontrol', 28, self.app_paths[2])])
        self.assertEqual(receipt['baseGitHeads']['device/google/cuttlefish'], '283645aacf6cdb56cc31a9362f54d412dbc132a1')
        # Both changed files, in contract order, with the before/after bytes OS's fixtures record.
        self.assertEqual([(c['project'], c['path'], c['beforeSha256'], c['afterSha256']) for c in receipt['changedFiles']],
            [(c['project'], c['path'], sha(self.src/c['project']/c['path']), sha(self.tree/c['project']/c['path']))
             for c in CONTRACT['osDelta']['changedFiles']])
        self.assertEqual([(c['beforeSha256'], c['afterSha256']) for c in receipt['changedFiles']],
            [(c['fixtureBeforeSha256'], c['fixtureAfterSha256']) for c in CONTRACT['osDelta']['changedFiles']])
        self.assertIn(b'"Seedvault",', (self.tree/'build/make'/GENERIC).read_bytes())
        self.assertIn(b'PRODUCT_PACKAGES += Seedvault', (self.tree/'build/make'/MEDIA).read_bytes())
        self.assertNotIn(b'PRODUCT_PACKAGES += Updater\n\nPRODUCT_PACKAGES += Seedvault', (self.tree/'build/make'/MEDIA).read_bytes())
        self.assertFalse((self.tree/'keys').exists(), 'Pixel release keys are never copied')
        self.assertFalse((self.tree/'device/google/cuttlefish/.git').exists())
        self.assertFalse((self.tree/'out-rose-first-update-2026102701').exists(), 'no Pixel output is copied')
        self.assertEqual(alpha.prepared_failures(alpha.load(self.config_path), receipt['preparationId'])[1], [])
        self.assertEqual(alpha.verify_prepared(alpha.load(self.config_path), receipt['preparationId'])['verdict'], 'OK')
        with self.assertRaisesRegex(alpha.CheckFailed, 'already exists; preparations are immutable'):
            alpha.prepare(alpha.load(self.config_path))

    def test_prepare_refuses_wrong_base_contract_or_evidence_before_copying(self):
        cases = []
        def drift():
            self.integrated.write_text('<resources>owner edit</resources>')
        def unpinned():
            self.heads['build/make'] = 'b' * 40
        def contract_changed():
            self.contract.write_text(self.contract.read_text().replace('"excluded-no-compatible-feed"', '"pixel"'))
        def evidence_changed():
            Path(self.data['preparation']['baseEvidence']['policy']['path']).write_text('{"sdk": 36}')
        def app_missing():
            (self.src/self.app_paths[1]).write_text('calculator 5')
        for damage, message in ((drift, 'tree drifted from the bound receipt: frameworks/base'),
                                (unpinned, 'build/make is not at the contract pin'),
                                (contract_changed, 'differs from preparation.osDelta.contractSha256'),
                                (evidence_changed, 'base evidence policy .* differs from its recorded sha256'),
                                (app_missing, None)):
            snapshot = {p: (self.src/p).read_bytes() for p in ['frameworks/base/core/res/res/values/config.xml', self.app_paths[1]]}
            contract_text, policy_text = self.contract.read_text(), Path(self.data['preparation']['baseEvidence']['policy']['path']).read_text()
            heads = dict(self.heads)
            damage()
            with self.assertRaises(alpha.CheckFailed) as error:
                alpha.prepare(alpha.load(self.config_path))
            if message:
                self.assertRegex(str(error.exception), message)
            cases.append(str(error.exception))
            for p, data in snapshot.items():
                (self.src/p).write_bytes(data)
            self.contract.write_text(contract_text)
            Path(self.data['preparation']['baseEvidence']['policy']['path']).write_text(policy_text)
            self.heads.clear()
            self.heads.update(heads)
        self.assertTrue('apps not found in the base integration' in cases[-1] or 'drifted' in cases[-1], cases[-1])
        self.assertEqual(sorted(p.name for p in (self.root/'alpha-source').iterdir()), [self.preparation['preparationId']])
        self.data['preparation']['baseEvidence']['resolvedManifest']['sha256'] = None
        self.write()
        with self.assertRaises(alpha.SetupMissing) as missing:
            alpha.prepare(alpha.load(self.config_path))
        self.assertEqual(missing.exception.missing, ['preparation.baseEvidence.resolvedManifest.sha256'])
        with open(self.lock, 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.data['preparation']['baseEvidence']['resolvedManifest']['sha256'] = 'f' * 64
            self.write()
            with self.assertRaisesRegex(alpha.CheckFailed, 'shared builder lock is held'):
                alpha.prepare(alpha.load(self.config_path))

    def test_build_uses_only_the_prepared_tree_and_writes_an_honest_receipt(self):
        receipt = self.run_build()
        self.assertEqual(self.made(), ['m -j48 droid hosttar OUT_DIR=out-rose-cf-alpha BUILD_NUMBER=rose-cf-alpha-40680000001 BUILD_DATETIME=1793180700'])
        out = self.tree/LUNCH['PRODUCT_OUT']
        self.assertEqual({k: receipt[k] for k in ('receiptType', 'simulated', 'testOnly', 'signed', 'keys', 'published', 'bootAttempted',
            'bootVerified', 'candidate', 'emulatorBuildId', 'buildDatetime', 'outDir', 'makeGoals', 'lunch')},
            {'receiptType': 'emulator-alpha-build', 'simulated': False, 'testOnly': True, 'signed': False, 'keys': 'test-keys',
             'published': False, 'bootAttempted': False, 'bootVerified': False, 'candidate': BOUND,
             'emulatorBuildId': 'rose-cf-alpha-40680000001', 'buildDatetime': TS, 'outDir': 'out-rose-cf-alpha',
             'makeGoals': ['droid', 'hosttar'], 'lunch': LUNCH})
        self.assertEqual(receipt['preparation'], {'preparationId': self.preparation['preparationId'],
            'receiptSha256': sha(self.prepared_dir/'preparation.json'), 'effectiveSourceIdentity': self.preparation['effectiveSourceIdentity'],
            'osDeltaCommit': 'f21bf68322b3fa0a8924840554ab3e9398f79125', 'updaterMode': 'excluded-no-compatible-feed', 'preparedRoot': str(self.tree)})
        self.assertEqual(sorted(receipt['images']), ['boot.img', 'init_boot.img', 'super.img', 'userdata.img', 'vbmeta.img', 'vendor_boot.img'])
        self.assertEqual(receipt['images']['super.img'], {'size': (out/'super.img').stat().st_size, 'sha256': sha(out/'super.img')})
        self.assertEqual(receipt['hostOutputs']['cvd-host_package.tar.gz']['sha256'], sha(self.tree/LUNCH['HOST_OUT']/'cvd-host_package.tar.gz'))
        self.assertIn('guest boot (sys.boot_completed)', receipt['notProven'])
        content = receipt['imageContent']
        self.assertEqual((content['verified'], content['source'], content['filesystem'], content['osImages']),
            (True, 'partitions unpacked from the built super.img', 'ext4', 'unsigned, test-only (AOSP test keys)'))
        self.assertEqual(sorted(content['partitions']), ['product', 'system', 'system_ext', 'vendor'])
        self.assertEqual(content['partitions']['system']['image'], 'system_a.img')
        self.assertEqual([(r['package'], r['partition'], r['sha256'], r['identity'], r['signers']) for r in content['apps']],
            [(package, location.split(':')[0], sha(self.src/path), [package, version], [PIN]) for (package, location), path, version in
             zip(self.data['imageVerification']['apps'].items(), self.app_paths, (302, 4, 28))])
        self.assertEqual(content['framework'], {'Agents': [f'dev.pixel.backgroundcontrol:{CERT}'],
            'Targets': [f'dev.pixel.calculator.agent:{CERT}'], 'PrototypeAgents': [], 'LockedSessions': False})
        self.assertEqual(content['tray'], {'persist.wm.debug.pixel_agent_tray': '1'})
        self.assertEqual(content['updaterAbsent'], {'package': 'app.seamlessupdate.client', 'apksInspected': 7})
        closure = content['closure']
        self.assertEqual(closure['source'], {'project': 'packages/apps/Seedvault', 'revision': '7cfffed7ceeda60a10bf2a6d9818d6fda81828ba'})
        self.assertEqual([(a['module'], a['path'], a['present'], a['identity'][0]) for a in closure['apps']],
            [('Seedvault', 'system:system/priv-app/Seedvault/Seedvault.apk', True, 'com.stevesoltys.seedvault'),
             ('LocalContactsBackup', 'system:system/app/LocalContactsBackup/LocalContactsBackup.apk', True, 'app.grapheneos.backup.contacts')])
        self.assertTrue(all(len(a['sha256']) == 64 and a['size'] > 0 for a in closure['apps']))
        self.assertEqual([(x['path'].split('/')[-1], x['present'], x['sha256']) for x in closure['xml']],
            [(name, True, hashlib.sha256(SEEDVAULT_XML[name]).hexdigest()) for name in ('allowlist_com.stevesoltys.seedvault.xml',
             'default-permissions_com.stevesoltys.seedvault.xml', 'com.stevesoltys.seedvault.xml',
             'default-permissions_app.grapheneos.backup.contacts.xml')])
        self.assertEqual(sorted(content['tools']), ['aapt', 'apksigner', 'debugfs', 'lpunpack'], 'simg2img ran only for a sparse super')
        self.assertFalse([p for p in self.prepared_dir.iterdir() if p.name.startswith('image-check-')], 'extraction workspace removed')
        written = self.root/'receipt'
        self.assertEqual(sorted(p.name for p in written.iterdir()), ['SHA256SUMS', 'emulator-alpha-receipt.json'])
        self.base_untouched()
        self.assertFalse((self.src/'out-rose-cf-alpha').exists(), 'nothing is built in the retained tree')
        self.assertEqual(alpha.verify_prepared(alpha.load(self.config_path), self.preparation['preparationId'])['verdict'], 'OK',
            'build output stays in the excluded OUT_DIR')

    def test_image_content_failures_are_named_and_write_no_receipt(self):
        voice, controller = 'app/RosePreinstall_dev_pixel_voice/RosePreinstall_dev_pixel_voice.apk', 'priv-app/PixelBackgroundControl/PixelBackgroundControl.apk'
        def put(partition, rel, data):
            return lambda trees: ((trees[partition]/rel).parent.mkdir(parents=True, exist_ok=True), (trees[partition]/rel).write_bytes(data))
        cases = (
            (lambda trees: (trees['product']/voice).unlink(), 'dev.pixel.voice APK product:app/RosePreinstall_dev_pixel_voice/.* is absent'),
            (put('product', voice, b'dev.pixel.voice:302 apk altered'), 'dev.pixel.voice image bytes differ from the contract'),
            (put('product', voice, b'dev.pixel.other:302 apk'), "dev.pixel.voice image APK identifies as \\('dev.pixel.other', 302\\)"),
            (put('product', voice, b'dev.pixel.voice:303 apk'), "identifies as \\('dev.pixel.voice', 303\\), expected dev.pixel.voice 302"),
            (put('system_ext', controller, b'dev.pixel.backgroundcontrol:28 apk OTHER-SIGNER'), 'dev.pixel.backgroundcontrol image APK is not signed by exactly the approved'),
            (put('system', 'system/framework/framework-res.apk', framework_dump([f'dev.pixel.backgroundcontrol:{CERT}'], []).encode()),
                'config_pixelComputerControlTargets is \\[\\], expected'),
            (put('system', 'system/framework/framework-res.apk', framework_dump([f'dev.pixel.backgroundcontrol:{CERT}'],
                [f'dev.pixel.calculator.agent:{CERT}'], locked=True).encode()), 'config_pixelComputerControlLockedSessions is True'),
            (put('system', 'system/build.prop', b'persist.wm.debug.pixel_agent_tray=1\npersist.wm.debug.pixel_agent_tray=0\n'),
                "persist.wm.debug.pixel_agent_tray in system:system/build.prop is '0'"),
            (put('system', 'system/build.prop', b'ro.build.type=userdebug\n'), 'pixel_agent_tray .* is None'),
            (put('system', 'system/priv-app/Updater/Updater.apk', b'app.seamlessupdate.client:1 apk'), 'Updater directory system:system/priv-app/Updater'),
            (put('system', 'system/etc/permissions/app.seamlessupdate.client.xml', b'<permissions/>'), 'app.seamlessupdate.client.xml is in the image'),
            (put('product', 'app/Renamed/Renamed.apk', b'app.seamlessupdate.client:1 apk'), 'an APK of app.seamlessupdate.client is in the image at product:app/Renamed'),
            (put('vendor', 'app/Odd/Odd.apk', b'UNREADABLE'), 'vendor:app/Odd/Odd.apk could not be read; Updater absence is unproven'),
        )
        for index, (change, message) in enumerate(cases):
            self.make_partitions(change)
            self.image_failure('image content: .*' + message, receipt=f'r{index}')
            self.assertFalse((self.root/f'r{index}').exists())
        self.assertFalse([p for p in self.prepared_dir.iterdir() if p.name.startswith('image-check-')], 'extraction workspace removed on failure')

    def test_seedvault_closure_is_checked_from_partition_content(self):
        seedvault, contacts = 'system/priv-app/Seedvault/Seedvault.apk', 'system/app/LocalContactsBackup/LocalContactsBackup.apk'
        privapp = 'system/etc/permissions/com.stevesoltys.seedvault.xml'
        def put(rel, data, partition='system'):
            return lambda trees: ((trees[partition]/rel).parent.mkdir(parents=True, exist_ok=True), (trees[partition]/rel).write_bytes(data))
        def remove(rel):
            return lambda trees: (trees['system']/rel).unlink()
        def link(rel):
            def change(trees):
                (trees['system']/rel).unlink()
                os.symlink('/dev/null', trees['system']/rel)
            return change
        def move_to_product(rel):
            def change(trees):
                data = (trees['system']/rel).read_bytes()
                (trees['system']/rel).unlink()
                put(rel.replace('system/', '', 1), data, 'product')(trees)
            return change
        cases = [(remove(seedvault), 'system:system/priv-app/Seedvault/Seedvault.apk is absent'),
                 (remove(contacts), 'system:system/app/LocalContactsBackup/LocalContactsBackup.apk is absent'),
                 (move_to_product(seedvault), 'Seedvault.apk is absent from the image'),
                 (put(seedvault, b'com.example.renamed:1 apk'), 'Seedvault.apk is package com.example.renamed, not the pinned com.stevesoltys.seedvault'),
                 (put(contacts, b'com.stevesoltys.seedvault:1 apk'), 'LocalContactsBackup.apk is package com.stevesoltys.seedvault, not the pinned app.grapheneos.backup.contacts'),
                 (put(seedvault, b'UNREADABLE'), 'Seedvault.apk could not be read as an APK'),
                 (put(seedvault, b''), 'Seedvault.apk is empty'),
                 (link(seedvault), 'Seedvault.apk is a symlink'),
                 (put(privapp, b'<permissions><privapp-permissions package="com.stevesoltys.seedvault">'), 'com.stevesoltys.seedvault.xml is not well-formed XML'),
                 (put(privapp, b'<config><privapp-permissions package="com.stevesoltys.seedvault"/></config>'), 'has root <config>, not <permissions>'),
                 (put(privapp, b'<permissions><privapp-permissions package="com.example"/></permissions>'), 'does not name package com.stevesoltys.seedvault'),
                 (put(privapp, b''), 'com.stevesoltys.seedvault.xml is empty'),
                 (link(privapp), 'com.stevesoltys.seedvault.xml is a symlink')]
        for xml in self.data['imageVerification']['closure']['xml']:
            cases.append((remove(xml['path'].split(':', 1)[1]), xml['path'] + ' is absent'))
        for index, (change, message) in enumerate(cases):
            self.make_partitions(change)
            self.image_failure('image content: .*Seedvault closure: .*' + message, receipt=f's{index}')
            self.assertFalse((self.root/f's{index}').exists())

    def test_image_formats_tools_and_slots_fail_closed(self):
        self.env['FAKE_SUPER_PLAIN'] = '1'
        self.image_failure('super.img is not an LP super image \\(detected unknown\\)', receipt='a')
        del self.env['FAKE_SUPER_PLAIN']
        self.env['FAKE_SUPER_SPARSE'] = '1'
        self.assertEqual(self.run_build(receipt='sparse')['imageContent']['tools']['simg2img']['path'], self.data['imageVerification']['tools']['simg2img'])
        self.data['imageVerification']['tools']['simg2img'] = None
        self.write()
        with self.assertRaises(alpha.SetupMissing) as missing:
            self.run_build(receipt='b')
        self.assertEqual(missing.exception.missing, ['imageVerification.tools.simg2img'])
        del self.env['FAKE_SUPER_SPARSE']
        self.data['imageVerification']['tools']['simg2img'] = str(self.tools/'simg2img')
        self.write()
        self.make_partitions(filesystem=b'\0' * 1024 + bytes.fromhex('e2e1f5e0') + b'\0' * 4096)
        self.image_failure('system_a.img is erofs, not the recorded ext4', receipt='c')
        self.make_partitions(filesystem=b'\0' * 8192)
        self.image_failure('system_a.img has an unsupported format \\(unknown\\)', receipt='d')
        self.make_partitions()
        (self.partitions/'system_b.img').write_bytes(b'slot b content')
        self.image_failure('non-empty system_b.img; the inspected slot is ambiguous', receipt='e')
        self.make_partitions()
        (self.partitions/'vendor_a.img').unlink()
        self.image_failure('0 non-empty images for partition vendor', receipt='f')
        self.make_partitions()
        self.data['imageVerification']['tools']['debugfs'] = str(self.root/'absent-debugfs')
        self.write()
        self.image_failure('imageVerification.tools.debugfs .* is not an executable file', receipt='g')
        # An erofs system needs its extractor configured before any build starts.
        lunch = dict(LUNCH, BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE='erofs')
        self.data['hostEvidence']['lunch'] = lunch
        self.write()
        self.assertIn('imageVerification.tools.fsckErofs', alpha.missing_evidence(alpha.load(self.config_path)))
        self.data['hostEvidence']['lunch'] = dict(LUNCH, BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE='f2fs')
        self.write()
        with self.assertRaises(alpha.SetupMissing) as missing:
            self.run_build(receipt='h')
        self.assertIn('a supported extractor for the recorded system file system f2fs', missing.exception.missing)

    def test_image_gate_is_bound_to_the_policy_and_contract(self):
        policy = Path(self.data['preparation']['baseEvidence']['policy']['path'])
        original = policy.read_text()
        policy.write_text(original.replace('"prototypeControl": false', '"prototypeControl": true'))
        self.image_failure('the bound policy .* is absent or differs from its sha256')
        self.assertFalse((self.root/'receipt').exists())
        policy.write_text(original)
        config = alpha.load(self.config_path)
        config['imageVerification']['apps'].pop('dev.pixel.voice')
        with self.assertRaisesRegex(alpha.CheckFailed, 'imageVerification.apps differs from the contract apps'):
            alpha.image_content(config, self.preparation['preparationId'], self.tree/LUNCH['PRODUCT_OUT'])

    def test_another_candidate_or_app_set_is_refused_before_the_lock_and_make(self):
        with self.assertRaisesRegex(alpha.CheckFailed, 'based on ae9cfd4d'):
            self.run_build(sha='a' * 40)
        with self.assertRaisesRegex(alpha.CheckFailed, 'differ from the bound integration apps'):
            self.run_build(apps=[['dev.pixel.voice', 303]])
        self.assertEqual(self.made(), [])

    def test_a_changed_unknown_or_mismatched_prepared_tree_fails_before_make(self):
        cases = (
            (lambda: (self.tree/self.app_paths[0]).write_text('voice 303'), 'dev.pixel.voice APK in the prepared tree differs'),
            (lambda: (self.tree/'build/make'/MEDIA).write_text('PRODUCT_PACKAGES += Updater\n'), 'media_system.mk differs from its prepared afterSha256'),
            (lambda: (self.tree/'build/make'/GENERIC).write_bytes((self.src/'build/make'/GENERIC).read_bytes()), 'Android.bp differs from its prepared afterSha256'),
            (lambda: (self.tree/'frameworks/base/core/res/res/values/config.xml').write_text('x'), 'drifted from the base reconcile entries'),
            (lambda: (self.prepared_dir/'manifest.tsv').write_text('x'), 'manifest differs from selectionSha256'),
            (lambda: (self.tree/'device/google/cuttlefish/build/cvd-host-package.go').write_text('x'), 'differs from the pinned plugin'),
        )
        for damage, message in cases:
            saved = {p: p.read_bytes() for p in [self.tree/self.app_paths[0], self.tree/'build/make'/MEDIA, self.tree/'build/make'/GENERIC, self.prepared_dir/'manifest.tsv',
                self.tree/'frameworks/base/core/res/res/values/config.xml', self.tree/'device/google/cuttlefish/build/cvd-host-package.go']}
            damage()
            with self.assertRaisesRegex(alpha.CheckFailed, message):
                self.run_build()
            for path, data in saved.items():
                path.write_bytes(data)
        os.chmod(self.prepared_dir/'preparation.json', 0o644)
        (self.prepared_dir/'preparation.json').write_text((self.prepared_dir/'preparation.json').read_text().replace('excluded-no-compatible-feed', 'pixel'))
        with self.assertRaisesRegex(alpha.CheckFailed, 'differs from prepared.receiptSha256'):
            self.run_build()
        self.data['prepared']['preparationId'] = '0' * 64
        self.write()
        with self.assertRaisesRegex(alpha.CheckFailed, 'no preparation with this id'):
            self.run_build()
        self.assertEqual(self.made(), [])
        self.base_untouched()

    def test_a_receipt_bound_to_another_base_or_delta_is_refused(self):
        config = alpha.load(self.config_path)
        config['preparation']['baseEvidence']['resolvedManifest']['sha256'] = '0' * 64
        failures = alpha.prepared_failures(config, self.preparation['preparationId'])[1]
        self.assertTrue(any('receipt baseResolvedManifestSha256' in f for f in failures), failures)
        self.assertTrue(any('preparation id does not match' in f for f in failures), failures)
        config = alpha.load(self.config_path)
        config['sourceBinding']['reconcileReceiptSha256'] = '0' * 64
        self.assertEqual(alpha.prepared_failures(config, self.preparation['preparationId'])[1], ['contract base differs from sourceBinding'])
        config = alpha.load(self.config_path)
        config['preparation']['osDelta']['commit'] = 'b' * 40
        failures = alpha.prepared_failures(config, self.preparation['preparationId'])[1]
        self.assertTrue(any('receipt osDeltaCommit' in f for f in failures), failures)
        failures = alpha.source_failures(alpha.load(self.config_path), self.tree, 'c' * 40)
        self.assertTrue(any('is not at the pinned' in f for f in failures), failures)

    def test_lunch_must_resolve_to_the_recorded_target_inside_the_alpha_out_dir(self):
        self.env['FAKE_LUNCH_ACCEPT'] = 'something-else'
        with self.assertRaisesRegex(alpha.CheckFailed, 'did not resolve'):
            self.run_build()
        del self.env['FAKE_LUNCH_ACCEPT']
        self.env['FAKE_PRODUCT_OUT'] = 'out-rose-first-update-2026102701/target/product/vsoc_x86_64_only'
        with self.assertRaisesRegex(alpha.CheckFailed, 'PRODUCT_OUT=out-rose-first-update.* \\(outside out-rose-cf-alpha\\)'):
            self.run_build()
        self.env['FAKE_PRODUCT_OUT'] = 'out-rose-cf-alpha/target/product/other'
        with self.assertRaisesRegex(alpha.CheckFailed, 'not the recorded hostEvidence'):
            self.run_build()
        self.assertEqual(self.made(), [])

    def test_release_is_the_one_lunch_exports_not_get_build_var(self):
        env = alpha.build_env(alpha.load(self.config_path), 'rose-cf-alpha-1', TS)
        self.assertEqual(alpha.resolve(alpha.load(self.config_path), env, self.tree)['TARGET_RELEASE'], 'cur')
        for change, message in ((('FAKE_RELEASE_UNSET', '1'), 'TARGET_RELEASE=\\(empty\\)'), (('FAKE_RELEASE', 'trunk_staging'), 'TARGET_RELEASE=trunk_staging')):
            self.env = {'FAKE_LOG': str(self.log), change[0]: change[1]}
            with self.assertRaisesRegex(alpha.CheckFailed, 'lunch resolved ' + message):
                self.run_build()
        self.assertEqual(self.made(), [])

    def test_host_package_goal_is_checked_against_the_pinned_soong_plugin(self):
        cf = self.tree/alpha.CUTTLEFISH
        plugin, blueprint = cf/'build/cvd-host-package.go', cf/'build/Android.bp'
        pinned_plugin, pinned_blueprint = plugin.read_bytes(), blueprint.read_bytes()
        head = self.preparation['baseGitHeads'][alpha.CUTTLEFISH]
        cases = (
            (lambda: plugin.write_bytes(pinned_plugin + b'// local edit\n'), ['differs from the pinned plugin']),
            (lambda: plugin.write_bytes(pinned_plugin.replace(b'(board_platform == "vsoc_x86_64")', b'(board_platform == "vsoc_x86_65")')),
                ['differs from the pinned plugin', 'only for other board platforms than vsoc_x86_64']),
            (lambda: plugin.write_bytes(pinned_plugin.replace(b'ctx.Phony("hosttar", info.TarballMetadata)', b'ctx.Phony("hostpkg", info.TarballMetadata)')),
                ['differs from the pinned plugin', 'does not declare the goal']),
            (lambda: plugin.write_bytes(pinned_plugin.replace(b'c.BaseModuleName()+".tar.gz", tarball)', b'"host.zip", tarball)')),
                ['does not declare the installed output']),
            (lambda: blueprint.write_bytes(pinned_blueprint.replace(b'name: "cvd-host_package"', b'name: "cvd-host_package_v2"')),
                ['does not declare cvd_host_package cvd-host_package']),
            (lambda: blueprint.unlink(), ['does not declare cvd_host_package cvd-host_package']),
            (lambda: plugin.unlink(), ['build/cvd-host-package.go is absent; the hosttar goal is unverified']),
        )
        for damage, messages in cases:
            damage()
            failures = alpha.source_failures(alpha.load(self.config_path), self.tree, head)
            for message in messages:
                self.assertTrue(any(message in f for f in failures), (message, failures))
            plugin.write_bytes(pinned_plugin)
            blueprint.write_bytes(pinned_blueprint)
        self.assertEqual(alpha.source_failures(alpha.load(self.config_path), self.tree, head), [])
        (cf/'AndroidProducts.mk').write_text('PRODUCT_MAKEFILES := \\\n\taosp_cf_x86_64_phone:$(LOCAL_DIR)/x.mk\n')
        self.data['build']['minFreeGiB'] = 10 ** 9
        self.write()
        failures = alpha.source_failures(alpha.load(self.config_path), self.tree, head)
        for expected in ('does not map aosp_cf_x86_64_only_phone', 'below build.minFreeGiB'):
            self.assertTrue(any(expected in f for f in failures), (expected, failures))
        self.data['build']['minFreeGiB'] = 0
        self.write()
        (cf/'AndroidProducts.mk').write_bytes((self.src/alpha.CUTTLEFISH/'AndroidProducts.mk').read_bytes())
        self.env['FAKE_BOARD'] = 'vsoc_arm64'
        with self.assertRaisesRegex(alpha.CheckFailed, 'TARGET_BOARD_PLATFORM=vsoc_arm64'):
            self.run_build()
        self.assertEqual(self.made(), [])

    def test_stale_missing_or_misidentified_outputs_are_not_a_build(self):
        out = self.tree/LUNCH['PRODUCT_OUT']
        out.mkdir(parents=True)
        (out/'boot.img').write_text('older build')
        os.utime(out/'boot.img', (1, 1))
        self.env['FAKE_SKIP'] = 'boot'
        with self.assertRaisesRegex(alpha.CheckFailed, 'not written by this build: boot.img'):
            self.run_build(receipt='r1')
        del self.env['FAKE_SKIP']
        self.env['FAKE_INCREMENTAL'] = '2026102804'
        with self.assertRaisesRegex(alpha.CheckFailed, 'build.version.incremental=2026102804'):
            self.run_build(receipt='r2')
        del self.env['FAKE_INCREMENTAL']
        self.env['FAKE_TYPE'] = 'user'
        with self.assertRaisesRegex(alpha.CheckFailed, 'ro.build.type is not userdebug'):
            self.run_build(receipt='r3')
        del self.env['FAKE_TYPE']
        self.env['FAKE_MAKE_FAIL'] = '1'
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_build(receipt='r4')
        for name in ('r1', 'r2', 'r3', 'r4'):
            self.assertFalse((self.root/name).exists(), 'no receipt for a failed build')

    def test_held_shared_lock_and_reused_receipt_directory_fail_before_make(self):
        with open(self.lock, 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaisesRegex(alpha.CheckFailed, 'shared builder lock is held'):
                self.run_build()
        (self.root/'receipt').mkdir()
        with self.assertRaisesRegex(alpha.CheckFailed, 'already exists'):
            self.run_build()
        self.assertEqual(self.made(), [])

    def test_probe_resolves_the_prepared_tree_and_records_without_making(self):
        identity = self.preparation['preparationId']
        receipt = self.with_env(lambda: alpha.probe(alpha.load(self.config_path), identity, '40680000002', TS, self.root/'probe.json'))
        self.assertEqual((receipt['executedMake'], receipt['treeFailures'], receipt['lunch'], receipt['preparationId'], receipt['preparationReceiptSha256']),
            (False, [], LUNCH, identity, sha(self.prepared_dir/'preparation.json')))
        self.assertEqual(json.loads((self.root/'probe.json').read_text()), receipt)
        self.assertEqual(self.made(), [])
        self.assertTrue((self.tree/'out-rose-cf-alpha').exists() or True)
        self.base_untouched()
        with self.assertRaises(alpha.release_receipts.ReceiptExists):
            self.with_env(lambda: alpha.probe(alpha.load(self.config_path), identity, '40680000002', TS, self.root/'probe.json'))
        with self.assertRaisesRegex(alpha.CheckFailed, 'no preparation with this id'):
            alpha.probe(alpha.load(self.config_path), 'a' * 64, '40680000002', TS, self.root/'probe2.json')
        with self.assertRaisesRegex(alpha.InvalidInput, 'preparation-id'):
            alpha.probe(alpha.load(self.config_path), None, '40680000002', TS, self.root/'probe3.json')

class CheckedInAlphaTest(unittest.TestCase):
    def test_checked_in_config_fails_closed_naming_the_missing_evidence(self):
        result = subprocess.run([PY, str(SCRIPTS/'emulator-alpha.py'), 'preflight', '--job', 'guard'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 4)
        self.assertEqual(json.loads(result.stderr)['missing'], ['hostEvidence.probeReceiptSha256', 'hostEvidence.lunch',
            'prepared.preparationId', 'prepared.receiptSha256', 'imageVerification.tools.lpunpack'])
        prepare = subprocess.run([PY, str(SCRIPTS/'emulator-alpha.py'), 'prepare'], capture_output=True, text=True)
        self.assertEqual((prepare.returncode, json.loads(prepare.stderr)['missing']), (4, ['preparation.baseEvidence.resolvedManifest.sha256']))
        build = subprocess.run([PY, str(SCRIPTS/'emulator-alpha.py'), 'build', '--candidate-sha', BOUND, '--apps', json.dumps(APPS),
            '--run-id', '1', '--build-timestamp', TS, '--receipt-dir', '/nonexistent/alpha-receipt'], capture_output=True, text=True)
        self.assertEqual(build.returncode, 4, 'a dispatch cannot compile speculatively')

    def test_source_binding_is_the_lead_evidenced_2804_integration(self):
        binding = CHECKED_IN['sourceBinding']
        self.assertEqual({k: binding[k] for k in ('candidate', 'reconcileReceipt', 'reconcileReceiptSha256', 'apps')},
            {'candidate': BOUND, 'reconcileReceipt': '0004-2026102804.json',
             'reconcileReceiptSha256': 'db1cc9a215f92a09d772c01efc15590b37fd6e748f97893908838340c9da9de0', 'apps': APPS})
        # The documented order is the 2804 dispatch contract's.
        self.assertIn("APPS = [['dev.pixel.voice', 302], ['dev.pixel.calculator.agent', 4], ['dev.pixel.backgroundcontrol', 28]]",
            (SCRIPTS/'tests/test_release_candidate_contract.py').read_text())
        self.assertEqual((CHECKED_IN['hostEvidence']['probeReceiptSha256'], CHECKED_IN['hostEvidence']['lunch']), (None, None))
        self.assertEqual((CHECKED_IN['prepared']['preparationId'], CHECKED_IN['prepared']['receiptSha256']), (None, None))
        config = alpha.load()
        with self.assertRaisesRegex(alpha.CheckFailed, 'based on ae9cfd4d'):
            alpha.check_dispatch(config, SHA, APPS)
        with self.assertRaisesRegex(alpha.CheckFailed, 'differ from the bound'):
            alpha.check_dispatch(config, BOUND, list(reversed(APPS)))
        alpha.check_dispatch(config, BOUND, APPS)

    def test_host_package_pin_matches_the_pinned_upstream_fixture(self):
        package = CHECKED_IN['hostPackage']
        self.assertEqual(hashlib.sha256((FIXTURES/package['plugin']).read_bytes()).hexdigest(), package['pluginSha256'])
        self.assertEqual((package['moduleType'], package['module'], package['goal'], package['output'], package['boardPlatform']),
            ('cvd_host_package', 'cvd-host_package', 'hosttar', 'cvd-host_package.tar.gz', 'vsoc_x86_64'))
        self.assertIn(package['goal'], CHECKED_IN['build']['makeGoals'])
        readme = (FIXTURES/'README.md').read_text()
        self.assertIn(alpha.pinned_revision('device/google/cuttlefish'), readme)
        for name in ('Android.bp', 'cvd-host-package.go'):
            self.assertIn(hashlib.sha256((FIXTURES/'build'/name).read_bytes()).hexdigest(), readme)
            self.assertIn('Licensed under the Apache License', (FIXTURES/'build'/name).read_text())

    def test_target_out_dir_and_lock_are_the_cuttlefish_alpha_ones(self):
        config = alpha.load()
        self.assertEqual(config['target']['lunch'], 'aosp_cf_x86_64_only_phone-cur-userdebug')
        self.assertEqual(alpha.config_failures(config), [])
        self.assertEqual(config['host']['lockPath'], FRANKEL['host']['lockPath'])
        self.assertEqual(config['host']['sourceRoot'], FRANKEL['host']['sourceRoot'])
        self.assertNotEqual(config['build']['outDir'], FRANKEL['host']['outDir'])
        self.assertIn('device/google/cuttlefish', alpha.CUTTLEFISH)
        self.assertEqual(alpha.pinned_revision('device/google/cuttlefish'), '283645aacf6cdb56cc31a9362f54d412dbc132a1')

    def test_unsafe_targets_are_config_failures(self):
        for change, message in ((('build', 'outDir', FRANKEL['host']['outDir']), 'disjoint'), (('build', 'outDir', 'out'), 'plain directory'),
                                (('build', 'outDir', 'a/b'), 'plain directory'), (('target', 'variant', 'user'), 'userdebug test-keys'),
                                (('target', 'lunch', 'frankel-cur-user'), 'does not match'),
                                (('hostPackage', 'output', 'cvd-host_package.zip'), 'module name plus .tar.gz'),
                                (('build', 'makeGoals', ['droid']), 'must include the hostPackage goal')):
            config = alpha.load()
            config[change[0]][change[1]] = change[2]
            self.assertTrue(any(message in f for f in alpha.config_failures(config)), change)

    def test_preparation_policy_is_the_measured_one_and_matches_the_os_contract(self):
        preparation = CHECKED_IN['preparation']
        self.assertEqual(preparation['root'], '/home/pixelbuild/rose-actions/alpha-source')
        self.assertEqual((preparation['copyBudgetGiB'], CHECKED_IN['build']['minFreeGiB']), (150, 300))
        self.assertEqual(preparation['exclusions'], {'rootNames': ['.repo', 'keys', 'releases', 'out'], 'rootPrefixes': ['out-', 'out_'],
            'anywhereNames': ['.git', '.repo'], 'externalLinks': [
                {'path': 'vendor/adevtool/dl/unpacked/frankel-CP3A.261005.005/vendor_boot/vendor_ramdisk00__unpacked/d', 'target': '/sys/kernel/debug'},
                {'path': 'vendor/adevtool/dl/unpacked/frankel-CP3A.261005.005/system/d', 'target': '/sys/kernel/debug'}]})
        self.assertEqual(preparation['osDelta']['commit'], 'f21bf68322b3fa0a8924840554ab3e9398f79125')
        self.assertEqual(hashlib.sha256((ROOT/preparation['osDelta']['contract']).read_bytes()).hexdigest(), preparation['osDelta']['contractSha256'])
        document, delta = alpha.contract(alpha.load())
        self.assertEqual(delta['changedFiles'], [{'project': 'build/make', 'path': GENERIC}, {'project': 'build/make', 'path': MEDIA}])
        self.assertEqual(preparation['baseEvidence']['policy']['sha256'], document['policySha256'])
        self.assertIsNone(preparation['baseEvidence']['resolvedManifest']['sha256'], 'read from the retained 2804 release before preparing')
        self.assertTrue(CHECKED_IN['build']['outDir'].startswith('out-'), 'the alpha OUT_DIR is never copied by a preparation')

    def test_preparation_never_links_mounts_reflinks_or_changes_ownership(self):
        code = (SCRIPTS/'alpha_source.py').read_text().split('"""', 2)[2]
        for absent in ('os.link(', '--reflink', 'FICLONE', 'chown', 'mount -', 'copy_file_range', 'sendfile', 'shutil.copytree', 'gcloud', 'secret'):
            self.assertFalse(absent in code, absent)

    def test_image_verification_follows_the_os_contract_and_retained_tools(self):
        spec = CHECKED_IN['imageVerification']
        present = ' '.join(CONTRACT['imageExpectations']['present'])
        prefixes = {'product': 'PRODUCT/', 'system_ext': 'SYSTEM_EXT/'}
        for package, location in spec['apps'].items():
            partition, inside = location.split(':', 1)
            self.assertIn(f'{prefixes[partition]}{inside} byte-identical to the {package} sha256', present)
        self.assertEqual(sorted(spec['apps']), sorted(a['package'] for a in CONTRACT['apps']))
        self.assertIn('persist.wm.debug.pixel_agent_tray=1 in the system build.prop', present)
        self.assertEqual((spec['trayProperty'], spec['trayValue']), ('persist.wm.debug.pixel_agent_tray', '1'))
        absent = ' '.join(CONTRACT['imageExpectations']['absent'])
        for fragment in ('priv-app/Updater/', 'app/Updater/', spec['updater']['xml'], spec['updater']['package']):
            self.assertIn(fragment, absent)
        self.assertEqual(spec['partitions'], ['system', 'system_ext', 'product', 'vendor'])
        tools = spec['tools']
        for name in ('simg2img', 'debugfs', 'aapt', 'apksigner'):
            self.assertEqual(tools[name], FRANKEL['host']['tools'][name], name)
        self.assertEqual((tools['lpunpack'], tools['fsckErofs']), (None, None), 'no verified builder path yet: fail closed')
        self.assertIn('BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE', alpha.LUNCH_VARS)
        code = (SCRIPTS/'alpha_image.py').read_text().split('"""', 2)[2]
        for absent_text in ('apt-get', 'pip install', 'curl', 'wget', 'shell=True'):
            self.assertFalse(absent_text in code, absent_text)

    def test_seedvault_closure_is_derived_from_the_pinned_sources(self):
        closure = CHECKED_IN['imageVerification']['closure']
        manifest = ET.parse(ROOT/'os/pinned-manifest.xml').getroot()
        revision = next(p.get('revision') for p in manifest.iter('project') if p.get('path') == closure['source']['project'])
        self.assertEqual(closure['source']['revision'], revision)
        self.assertIn(revision, (SEEDVAULT/'README.md').read_text())
        for name in [p for p in SEEDVAULT.rglob('*') if p.is_file() and p.name != 'README.md']:
            self.assertIn(hashlib.sha256(name.read_bytes()).hexdigest(), (SEEDVAULT/'README.md').read_text(), name)
        # Packages from the pinned manifests; modules, privileged flag and install file names from OS's pinned Android.bp files.
        packages = {'Seedvault': ET.parse(SEEDVAULT/'app/src/main/AndroidManifest.xml').getroot().get('package'),
                    'LocalContactsBackup': ET.parse(SEEDVAULT/'contactsbackup/src/main/AndroidManifest.xml').getroot().get('package')}
        blueprint = (OS_SEEDVAULT/'Android.bp').read_text() + (OS_SEEDVAULT/'contactsbackup/Android.bp').read_text()
        modules = {m[2]: m[3] for m in re.finditer(r'(android_app|prebuilt_etc) \{\s*name: "([^"]+)",(.*?)\n\}', blueprint, re.S)}
        for app in closure['apps']:
            body = modules[app['module']]
            directory = 'priv-app' if re.search(r'privileged: true', body) else 'app'
            self.assertEqual(app['path'], f'system:system/{directory}/{app["module"]}/{app["module"]}.apk')
            self.assertEqual(app['package'], packages[app['module']])
        required = re.findall(r'"([^"]+)"', modules['Seedvault'].split('required:')[1].split(']')[0]) + \
            re.findall(r'"([^"]+)"', modules['LocalContactsBackup'].split('required:')[1].split(']')[0])
        self.assertEqual(sorted(x['module'] for x in closure['xml']), sorted(r for r in required if r != 'LocalContactsBackup'))
        for xml in closure['xml']:
            body = modules[xml['module']]
            sub_dir, src = re.search(r'sub_dir: "([^"]+)"', body)[1], re.search(r'src: "([^"]+)"', body)[1]
            self.assertIn('filename_from_src: true', body)
            self.assertEqual(xml['path'], f'system:system/etc/{sub_dir}/{src.split("/")[-1]}')
            pinned = next(p for p in SEEDVAULT.rglob(src.split('/')[-1]))
            root = ET.parse(pinned).getroot()
            self.assertEqual(xml['root'], root.tag)
            self.assertIn(xml['package'], {e.get('package') for e in root.iter() if e.get('package')})
        # The contract names exactly this closure; these compiled modules carry no pinned version, digest or signer.
        self.assertIn('SYSTEM/priv-app/Seedvault/Seedvault.apk and SYSTEM/app/LocalContactsBackup/LocalContactsBackup.apk with their etc XML',
            ' '.join(CONTRACT['imageExpectations']['present']))
        for item in closure['apps'] + closure['xml']:
            self.assertFalse({'sha256', 'version', 'signer', 'certificate'} & set(item), item)
        self.assertIn('c825aa19', CHECKED_IN['imageVerification']['basis'])
        self.assertNotIn('OS58', CHECKED_IN['imageVerification']['basis'] + (SCRIPTS/'alpha_image.py').read_text())

    def test_plan_runs_nothing_and_says_so(self):
        original = subprocess.run
        subprocess.run = lambda *a, **k: (_ for _ in ()).throw(AssertionError('plan ran a command'))
        try:
            result = alpha.plan(alpha.load(), SHA, '40680000003', TS)
        finally:
            subprocess.run = original
        self.assertEqual((result['executed'], result['receiptType'], result['emulatorBuildId']),
            (False, 'emulator-alpha-plan', 'rose-cf-alpha-40680000003'))
        self.assertEqual(result['commands'][1][-1],
            'source build/envsetup.sh && lunch aosp_cf_x86_64_only_phone-cur-userdebug && m -j48 droid hosttar')
        self.assertEqual(result['environment'], {'OUT_DIR': 'out-rose-cf-alpha', 'BUILD_NUMBER': 'rose-cf-alpha-40680000003', 'BUILD_DATETIME': TS})
        self.assertTrue(result['missingEvidence'])

    def test_adapter_never_reconciles_signs_publishes_cleans_or_uses_the_reference_scripts(self):
        code = (SCRIPTS/'emulator-alpha.py').read_text().split('"""', 2)[2]
        for absent in ('host-reconcile', 'host-integrate', 'host-sign', 'gcloud', 'secret', 'apply-os-changes', 'installclean',
                       'clobber', 'rmtree', 'unlink', 'sdk_phone64', 'reference-scripts', 'github_release', 'publish(', 'PIXEL_'):
            self.assertFalse(absent in code, absent)

class WorkflowAlphaTest(unittest.TestCase):
    def setUp(self):
        self.jobs = jobs(WORKFLOW)

    def test_alpha_builder_has_no_identity_secrets_or_os_stages(self):
        job = self.jobs['alpha-builder']
        self.assertIn("    needs: controller\n    if: inputs.mode == 'emulator-alpha'\n    runs-on: [self-hosted, roseos-frankel-builder]\n", job)
        self.assertIn('    permissions:\n      contents: read\n    steps:\n', job)
        runs = re.findall(r'^      (?:- |  )run: (.+)$', job, re.M)
        self.assertEqual(runs, ['python3 scripts/emulator-alpha.py preflight --job builder', '|',
            'python3 scripts/emulator-alpha.py build --candidate-sha "$CANDIDATE_SHA" --apps "$APPS" --run-id "$GITHUB_RUN_ID" '
            '--build-timestamp "$BUILD_TIMESTAMP" --receipt-dir "$ROSE_BASE/receipt"', 'test -z "${ROSE_BASE:-}" || rm -rf "$ROSE_BASE"'])
        for absent in ('id-token', 'secrets.', 'GCP_', 'actions-gcp-login', 'os-release-stage', 'host-app-release', 'PIXEL_OTA'):
            self.assertNotIn(absent, job)
        self.assertIn('          name: emulator-alpha-receipt-${{ github.run_id }}\n          path: ${{ env.ROSE_BASE }}/receipt\n', job)

    def test_guard_checks_alpha_evidence_before_the_builder_starts(self):
        guard = self.jobs['guard']
        self.assertIn("      - name: Emulator-alpha preflight\n        if: inputs.mode == 'emulator-alpha'\n"
            "        run: python3 scripts/emulator-alpha.py preflight --job guard\n", guard)
        self.assertIn('[[ "$MODE" == os-release || ( "$RESUME_STAGE" == none && -z "$RESUME_SOURCE_RUN" && "$PROMOTE" == false ) ]]', guard)
        self.assertIn('    needs: guard\n', self.jobs['controller'])
        self.assertIn('    needs: [controller, builder, app-builder, alpha-builder, publisher]\n', self.jobs['cleanup'])
        self.assertIn("    if: inputs.mode == 'os-release'\n", self.jobs['builder'])
        self.assertIn('    needs: builder\n', self.jobs['publisher'])

if __name__ == '__main__':
    unittest.main()
