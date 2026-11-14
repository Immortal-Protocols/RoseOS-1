"""FAM-03a: os/devices/pixel10.json against the pinned public sources (fixtures in os/tests/fixtures/adevtool-17df3b79,
provenance in its PROVENANCE.json). Source facts only: a pass says nothing about downloaded factory images, generated
vendor trees, an executed lunch, a build, a signature or a phone. Offline; no network or historical checkout."""
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT/'os/devices/pixel10.json'
FIXTURES = ROOT/'os/tests/fixtures/adevtool-17df3b79'
DEVICES = ('frankel', 'blazer', 'rango', 'mustang')
FIXTURE_REPOS = {'https://github.com/GrapheneOS/adevtool': 'vendor/adevtool', 'https://github.com/GrapheneOS/platform_build': 'build/make'}
PROBES = {'inUse', 'notExecuted'}
GENERATED = {'priorRetainedBuilderEvidence', 'unknown'}
FOLD_PORT = 'os/fold-port'

def git_blob(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()

def provenance_failures(fixtures, pins):
    """Every fixture file is listed, unchanged since it was copied, and from the pinned revision of its project."""
    failures = []
    listed = json.loads((fixtures/'PROVENANCE.json').read_text())['files']
    names = {row['fixture'] for row in listed}
    present = {str(p.relative_to(fixtures)) for p in fixtures.rglob('*') if p.is_file()} - {'PROVENANCE.json', 'README.md'}
    failures += [f'fixture {n} is not in PROVENANCE.json' for n in sorted(present - names)]
    for row in listed:
        path = fixtures/row['fixture']
        if not path.is_file():
            failures.append(f'fixture {row["fixture"]} is missing')
            continue
        if git_blob(path.read_bytes()) != row['gitBlob']:
            failures.append(f'fixture {row["fixture"]} differs from its recorded git blob')
        project = FIXTURE_REPOS.get(row['repository'])
        if project is None or row['revision'] != pins.get(project):
            failures.append(f'fixture {row["fixture"]} is from {row["repository"]}@{row["revision"]}, not the pinned {project}')
        if row['kind'] == 'excerpt':
            doc = json.loads(path.read_text())
            if (doc['revision'], doc['path'], doc['upstreamGitBlob']) != (row['revision'], row['path'], row['upstreamGitBlob']):
                failures.append(f'excerpt {row["fixture"]} does not match its provenance row')
    return failures

def text(fixtures, rel):
    return (fixtures/rel).read_text()

def excerpt(fixtures, rel):
    return list(json.loads((fixtures/(rel + '.excerpt.json')).read_text())['lines'].values())

def inherits(makefile):
    return re.findall(r'^\$\(call inherit-product(?:-if-exists)?, (\S+?)\)\s*$', makefile, re.M)

def reachable(fixtures, start):
    """Product makefiles named by inherit-product from start, following those present as fixtures."""
    seen, queue = set(), [start]
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        if name.startswith('vendor/adevtool/'):
            local = fixtures/'adevtool'/name[len('vendor/adevtool/'):]
        elif name.startswith('$(SRC_TARGET_DIR)/'):
            local = fixtures/'platform_build/target'/name[len('$(SRC_TARGET_DIR)/'):]
        else:
            continue
        if local.is_file():
            content = local.read_text()
            queue += inherits(content) + re.findall(r'^include (vendor/adevtool/\S+)$', content, re.M)
    return {n.replace('$(SRC_TARGET_DIR)/', 'target/') for n in seen}

def rose_products(root):
    """The build/make product files Rose edits: the products its build/make patches touch, and its preinstall makefile."""
    tool = (root/'scripts/apply-os-changes.py').read_text()
    patches = sorted(set(re.findall(r"'([\w-]+-build-make\.patch)'", tool)))
    products = {m for p in patches for m in re.findall(r'^\+\+\+ b/(target/product/\S+)', (root/'os/patches'/p).read_text(), re.M)}
    products.add(re.search(r"^PRODUCT_MAKEFILE = '([^']+)'", tool, re.M)[1])
    return patches, products

def rose_projects(root):
    tool = (root/'scripts/apply-os-changes.py').read_text() + (root/'scripts/background_apks.py').read_text()
    found = set(re.findall(r"projects(?:\[|\.setdefault\()'([^']+)'", tool)) | set(re.findall(r"\{'(packages/modules/[^']+)':", tool))
    return found - {'device/generic/goldfish'}  # goldfish is the emulator target, never a Pixel product

def failures(contract, fixtures=FIXTURES, root=ROOT):
    f = []
    source, shared, pins = contract['source'], contract['shared'], contract['source']['projects']
    f += provenance_failures(fixtures, pins)

    manifest = ET.parse(root/source['manifest']).getroot()
    revisions = {p.get('path', p.get('name')): p.get('revision') for p in manifest.findall('project')}
    for path, revision in pins.items():
        if revisions.get(path) != revision:
            f.append(f'{path} is pinned {revision}, the manifest has {revisions.get(path)}')
    if manifest.find('default').get('revision') != source['aospDefault']:
        f.append('aospDefault differs from the manifest default revision')
    remotes = {r.get('name'): r.get('revision') for r in manifest.findall('remote')}
    if remotes.get('grapheneos') != 'refs/tags/' + source['release']:
        f.append(f'release {source["release"]} is not the manifest GrapheneOS tag')
    if any(p.startswith(('device/google/frankel', 'device/google/blazer', 'device/google/rango', 'vendor/google_devices')) for p in revisions):
        f.append('the manifest now carries a Pixel device or vendor tree; this contract assumes adevtool generates them')

    devices = contract['devices']
    if list(devices) != list(DEVICES):
        f.append(f'devices are {list(devices)}, not exactly {list(DEVICES)} in that order')
    family = re.findall(r'^\s*-\s*(\w+)\.yml', text(fixtures, 'adevtool/' + shared['adevtoolDeviceList']), re.M)
    for name in set(family) - set(devices):
        if name not in contract['excluded']:
            f.append(f'adevtool lists {name} in the family; it is neither a target nor explicitly excluded')
    for name in set(devices) & set(contract['excluded']):
        f.append(f'{name} is both a target and excluded')

    gen10 = text(fixtures, 'adevtool/config/device/' + shared['include'])
    if not re.search(rf'^\s*platform: {shared["platform"]}$', gen10, re.M) or not re.search(rf'^\s*build_id: {re.escape(shared["vendorBuildId"])}$', gen10, re.M):
        f.append('common/gen10pixel.yml platform or build_id differs')
    common = text(fixtures, 'adevtool/config/mk/google_devices/common/product-common.mk')
    if '$(SRC_TARGET_DIR)/product/core_64_bit_only.mk' not in inherits(common) or shared['abis'] != ['arm64-v8a']:
        f.append('the shared product is no longer core_64_bit_only arm64-v8a')

    patches, products = rose_products(root)
    if set(contract['roseInclusion']['products']) != products:
        f.append(f'Rose edits products {sorted(products)}, the contract names {sorted(contract["roseInclusion"]["products"])}')
    if set(contract['roseInclusion']['projects']) | {'build/make'} != rose_projects(root):
        f.append(f'Rose patches projects {sorted(rose_projects(root))}, the contract names {sorted(contract["roseInclusion"]["projects"])}')
    patch_text = ''.join(p.read_text() for p in sorted((root/'os/patches').glob('*.patch')) if p.name != 'goldfish.patch')
    if re.search(r'^\+\+\+ b/(device/google|vendor/google_devices)', patch_text, re.M):
        f.append('a Rose patch edits a Pixel device or vendor tree')
    if 'fold-port' in (root/'scripts/apply-os-changes.py').read_text():
        f.append('apply-os-changes.py references os/fold-port')
    if not (root/FOLD_PORT).is_dir():
        f.append('os/fold-port is gone; the prohibition no longer names a real patch set')

    for name in DEVICES:
        device = devices.get(name)
        if device is None:
            continue
        where = f'{name}:'
        config = text(fixtures, 'adevtool/' + device['adevtoolConfig'])
        if device['adevtoolConfig'] != f'config/device/{name}.yml' or not re.search(rf'^\s*name: {name}$', config, re.M):
            f.append(f'{where} adevtool config does not name the device')
        if f'- {shared["include"]}' not in config:
            f.append(f'{where} config does not include {shared["include"]}')
        size = re.search(r'stable_display_size: \{ width: (\d+), height: (\d+) \}', config)
        display = device['display']['source']
        if not size or (int(size[1]), int(size[2])) != (display['width'], display['height']):
            f.append(f'{where} stable display size differs from the adevtool config')

        mk = text(fixtures, f'adevtool/config/mk/google_devices/device/{name}/device.mk')
        chain = [shared['inheritance'][1]]
        if inherits(mk) != chain or f'include {shared["inheritance"][1].replace("product-common", "device")}' not in mk:
            f.append(f'{where} device.mk no longer inherits the shared laguna product')
        kernel = re.search(r'^TARGET_KERNEL_DIR \?= (\S+)$', mk, re.M)
        if not kernel or kernel[1] != device['kernelDir'] or not device['kernelDir'].startswith(next(p for p in pins if 'laguna-kernels' in p) + '/'):
            f.append(f'{where} kernel dir differs from device.mk or the pinned laguna kernels')
        reached = reachable(fixtures, f'vendor/adevtool/config/mk/google_devices/device/{name}/device.mk')
        for product in sorted(products):
            if product not in reached:
                f.append(f'{where} does not inherit Rose product {product}')

        lines = excerpt(fixtures, f'adevtool/vendor-skels/google_devices/{name}/{name}.mk')
        expect = [f'$(call inherit-product, vendor/adevtool/config/mk/google_devices/device/{name}/device.mk)',
            f'PRODUCT_NAME := {device["product"]}', f'PRODUCT_DEVICE := {device["product"]}', f'PRODUCT_MODEL := {device["model"]}',
            f'PRODUCT_SHIPPING_API_LEVEL := {shared["shippingApiLevel"]}', f'TARGET_SCREEN_DENSITY := {display["densityDpi"]}',
            f'ifneq ($(BUILD_ID),{shared["vendorBuildId"]})']
        f += [f'{where} {name}.mk lacks {line!r}' for line in expect if line not in lines]
        rotation = [l.split(':= ')[1] for l in lines if l.startswith('TARGET_RECOVERY_DEFAULT_ROTATION := ')]
        if rotation != ([display['recoveryRotation']] if 'recoveryRotation' in display else []):
            f.append(f'{where} recovery rotation differs')
        secondary = any('pixel-display-secondary' in l for l in lines)
        if secondary != (display.get('secondaryDisplayFragment') == 'pixel-display-secondary'):
            f.append(f'{where} secondary display fact differs')
        services = [l.strip() for l in lines if re.fullmatch(r'\s*system_ext:\S+-services', l)]
        if services != ([device['sourceDifferences']['systemServerJar']] if 'sourceDifferences' in device else []):
            f.append(f'{where} system server jar differs')

        if text(fixtures, f'adevtool/vendor-skels/google_devices/{name}/AndroidProducts.mk').strip().splitlines()[-1] != f'PRODUCT_MAKEFILES += $(LOCAL_DIR)/{name}.mk':
            f.append(f'{where} AndroidProducts.mk does not name {name}.mk')
        if f'export BUILD_ID_{name}="{shared["vendorBuildId"]}"' not in text(fixtures, f'adevtool/vendor-skels/google_devices/{name}/cmds-for-envsetup.sh'):
            f.append(f'{where} envsetup BUILD_ID differs')
        info = text(fixtures, f'adevtool/vendor-skels/google_devices/{name}/firmware/android-info.txt').split('\n')
        firmware = shared['firmware']
        wanted = [f'require board={name}', f'require version-bootloader={firmware["bootloader"]}',
            f'require version-baseband={firmware["baseband"]}'] + [f'require partition-exists={p}' for p in firmware['partitionExists']]
        if sorted(l for l in info if l) != sorted(wanted):
            f.append(f'{where} firmware requirements differ from android-info.txt')

        image = device['vendorInputs']['factoryImage']
        index = excerpt(fixtures, 'adevtool/config/build-index/build-index-main.yml')
        if f'{name} {shared["vendorBuildId"]}:' not in index or f'  factory: {image["sha256"]} {image["file"]}' not in index:
            f.append(f'{where} factory image entry differs from the adevtool build index')
        if not image['file'].startswith(f'{name}-{shared["vendorBuildId"].lower()}-factory-' + image['sha256'][:8]):
            f.append(f'{where} factory image name does not match its device, build or digest')
        if image['status'] != 'indexed':
            f.append(f'{where} factory image status is {image["status"]}; this contract only records the public index')

        # Expected lunch versus an executed probe; indexed versus generated vendor inputs.
        lunch = device['lunch']
        if lunch['expected'] != f'{device["product"]}-cur-user':
            f.append(f'{where} expected lunch is not <product>-cur-user')
        if lunch['probe'] not in PROBES or (lunch['probe'] != 'notExecuted') != bool(lunch['evidence']):
            f.append(f'{where} lunch probe {lunch["probe"]} needs evidence exactly when it is not notExecuted')
        generated = device['vendorInputs']['generated']
        if generated not in GENERATED or (generated != 'unknown') != bool(device['vendorInputs']['evidence']):
            f.append(f'{where} generated vendor inputs {generated} need evidence exactly when not unknown')
        if name != 'frankel' and (lunch['probe'] != 'notExecuted' or generated != 'unknown'):
            f.append(f'{where} claims a probe or generated vendor inputs that no evidence in this repository supports')
        if name == 'frankel':
            pipeline = json.loads((root/'os/release-pipeline/frankel.json').read_text())['target']
            if (pipeline['device'], pipeline['lunch']) != (name, lunch['expected']):
                f.append('frankel: the in-use lunch differs from os/release-pipeline/frankel.json')

        # Source display facts versus an unmeasured physical address.
        if device['display']['referenceDisplayAddress'] is not None:
            f.append(f'{where} a reference display address is configured without a measurement')
        status = 'unmeasured' if secondary else 'notApplicable'
        if device['display']['referenceDisplayStatus'] != status:
            f.append(f'{where} reference display status must be {status}')

        if FOLD_PORT not in device['forbiddenPatchSets']:
            f.append(f'{where} does not forbid {FOLD_PORT}')
        if any(FOLD_PORT in json.dumps(v) for k, v in device.items() if k not in ('forbiddenPatchSets', 'forbiddenNote')):
            f.append(f'{where} associates {FOLD_PORT} with the device')

    rango = devices.get('rango', {})
    if rango.get('kernelDir', '').rsplit('/', 1)[-1] != 'rango' or rango.get('kernelDir') == devices.get('frankel', {}).get('kernelDir'):
        f.append('rango: the supported rango kernel dir is no longer distinct')
    if 'sourceDifferences' in rango:
        config = text(fixtures, 'adevtool/' + rango['adevtoolConfig'])
        included = re.findall(r'^\s*- (\S+\.(?:jar|apex))$', config, re.M)
        if included != rango['sourceDifferences']['fileInclusions']:
            f.append('rango: file inclusions differ from rango.yml')
        if len(re.findall(r'com\.android\.systemui:\S*rango_unfolded', config)) != rango['sourceDifferences']['systemUiUnfoldedOverlays']:
            f.append('rango: unfolded SystemUI overlay count differs from rango.yml')
    return f

class ContractTest(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads(CONTRACT.read_text())

    def check(self, contract=None, fixtures=FIXTURES):
        return failures(contract or self.contract, fixtures)

    def test_the_contract_matches_the_pinned_sources(self):
        self.assertEqual(self.check(), [])

    def test_rose_reaches_every_device_through_shared_products(self):
        patches, products = rose_products(ROOT)
        self.assertEqual(patches, ['agent-tray-build-make.patch', 'background-control-build-make.patch',
            'rose-edge-build-make.patch', 'rose-keyboard-build-make.patch', 'updater-product-build-make.patch'])
        self.assertEqual(products, {'target/product/generic_system.mk', 'target/product/handheld_system_ext.mk',
            'target/product/media_system.mk', 'target/product/handheld_product.mk', 'target/product/handheld_system.mk'})
        for name in DEVICES:
            reached = reachable(FIXTURES, f'vendor/adevtool/config/mk/google_devices/device/{name}/device.mk')
            self.assertTrue(products <= reached, name)
            self.assertIn('target/product/handheld_system.mk', reached)  # generic_system -> handheld_system -> media_system

    def test_a_pass_records_no_probe_download_generation_or_measurement(self):
        devices = self.contract['devices']
        self.assertEqual([devices[d]['lunch']['probe'] for d in DEVICES], ['inUse', 'notExecuted', 'notExecuted', 'notExecuted'])
        self.assertEqual([devices[d]['vendorInputs']['generated'] for d in DEVICES],
            ['priorRetainedBuilderEvidence', 'unknown', 'unknown', 'unknown'])
        self.assertEqual({devices[d]['vendorInputs']['factoryImage']['status'] for d in DEVICES}, {'indexed'})
        self.assertEqual({devices[d]['display']['referenceDisplayAddress'] for d in DEVICES}, {None})

class NegativeControlTest(unittest.TestCase):
    """Each mutation of a correct contract or fixture set must be named by the checker."""
    def setUp(self):
        self.base = json.loads(CONTRACT.read_text())

    def failing(self, mutate, expect):
        contract = copy.deepcopy(self.base)
        mutate(contract)
        found = failures(contract)
        self.assertTrue(any(expect in line for line in found), f'{expect!r} not in {found}')

    def test_wrong_pins_hashes_firmware_and_kernels(self):
        def wrong(path, value):
            def mutate(c):
                node = c
                for key in path[:-1]:
                    node = node[key]
                node[path[-1]] = value
            return mutate
        cases = [
            (('devices', 'blazer', 'vendorInputs', 'factoryImage', 'sha256'), '0' * 64, 'blazer: factory image entry differs'),
            (('devices', 'rango', 'vendorInputs', 'factoryImage', 'file'), 'frankel-cp3a.261005.005-factory-3721e6ac.zip', 'rango: factory image'),
            (('shared', 'firmware', 'bootloader'), 'deepspace-17.3-1', 'firmware requirements differ'),
            (('shared', 'firmware', 'baseband'), 'g5400i-260604-260807-B-0', 'firmware requirements differ'),
            (('shared', 'vendorBuildId'), 'CP2A.260605.016', 'build_id differs'),
            (('devices', 'blazer', 'kernelDir'), 'device/google/laguna-kernels/6.6/grapheneos/rango', 'blazer: kernel dir differs'),
            (('devices', 'rango', 'kernelDir'), 'device/google/laguna-kernels/6.6/grapheneos/muzel', 'rango: the supported rango kernel dir'),
            (('source', 'projects', 'vendor/adevtool'), '0' * 40, 'vendor/adevtool is pinned'),
            (('source', 'projects', 'vendor/state'), '1' * 40, 'vendor/state is pinned'),
            (('source', 'projects', 'device/google/laguna-kernels/6.6'), '2' * 40, 'laguna-kernels/6.6 is pinned'),
            (('source', 'release'), '2026100500', 'is not the manifest GrapheneOS tag'),
            (('devices', 'rango', 'display', 'source', 'densityDpi'), 420, "rango: rango.mk lacks 'TARGET_SCREEN_DENSITY := 420'"),
            (('devices', 'frankel', 'model'), 'Pixel 10 Pro', "frankel: frankel.mk lacks 'PRODUCT_MODEL := Pixel 10 Pro'"),
            (('devices', 'mustang', 'model'), 'Pixel 10 Pro', "mustang: mustang.mk lacks 'PRODUCT_MODEL := Pixel 10 Pro'"),
            (('devices', 'mustang', 'display', 'source', 'densityDpi'), 420, "mustang: mustang.mk lacks 'TARGET_SCREEN_DENSITY := 420'"),
            (('devices', 'mustang', 'display', 'source', 'width'), 1280, 'mustang: stable display size differs'),
            (('devices', 'mustang', 'kernelDir'), 'device/google/laguna-kernels/6.6/grapheneos/rango', 'mustang: kernel dir differs'),
            (('devices', 'mustang', 'vendorInputs', 'factoryImage', 'sha256'), 'cd7d12c215f7cf1c45d6c77291690bfab1bf360cddd00d557390aed70a3418e3', 'mustang: factory image entry differs'),
            (('devices', 'mustang', 'vendorInputs', 'factoryImage', 'file'), 'blazer-cp3a.261005.005-factory-cd7d12c2.zip', 'mustang: factory image'),
            (('devices', 'mustang', 'adevtoolConfig'), 'config/device/blazer.yml', 'mustang: adevtool config does not name the device'),
        ]
        for path, value, expect in cases:
            with self.subTest(path=path):
                self.failing(wrong(path, value), expect)

    def test_unknown_alias_case_and_excluded_devices_are_rejected(self):
        for name in ('Mustang', 'Frankel', 'pixel10', 'yogi', 'tegu'):
            with self.subTest(name=name):
                self.failing(lambda c, n=name: c['devices'].__setitem__(n, copy.deepcopy(c['devices']['frankel'])), 'not exactly')
        # mustang is a target since 6aa5d2c: dropping it without excluding it, or also excluding it, is named.
        self.failing(lambda c: c['devices'].pop('mustang'), 'adevtool lists mustang in the family; it is neither a target nor explicitly excluded')
        self.failing(lambda c: c['excluded'].update(mustang='x'), 'mustang is both a target and excluded')
        self.failing(lambda c: c['devices'].pop('blazer'), 'not exactly')
        self.failing(lambda c: c.__setitem__('devices', dict(reversed(list(c['devices'].items())))), 'in that order')

    def test_probes_vendor_generation_and_display_address_cannot_be_claimed(self):
        self.failing(lambda c: c['devices']['blazer']['lunch'].update(probe='inUse', evidence='said so'), 'blazer: claims a probe')
        self.failing(lambda c: c['devices']['rango']['lunch'].update(probe='executed', evidence='x'), 'rango: lunch probe executed')
        self.failing(lambda c: c['devices']['blazer']['lunch'].update(probe='inUse'), 'needs evidence exactly')
        self.failing(lambda c: c['devices']['rango']['lunch'].update(expected='rango-trunk_staging-user'), 'expected lunch')
        self.failing(lambda c: c['devices']['rango']['vendorInputs'].update(generated='priorRetainedBuilderEvidence', evidence='x'),
            'rango: claims a probe or generated')
        self.failing(lambda c: c['devices']['blazer']['vendorInputs']['factoryImage'].update(status='verified'), 'blazer: factory image status')
        # Mustang fixtures are pinned public source only: they never support a probe, generated inputs or a verified image.
        self.failing(lambda c: c['devices']['mustang']['lunch'].update(probe='inUse', evidence='mustang OTA 2026110401'), 'mustang: claims a probe')
        self.failing(lambda c: c['devices']['mustang']['vendorInputs'].update(generated='priorRetainedBuilderEvidence', evidence='x'),
            'mustang: claims a probe or generated')
        self.failing(lambda c: c['devices']['mustang']['vendorInputs']['factoryImage'].update(status='verified'), 'mustang: factory image status')
        self.failing(lambda c: c['devices']['mustang']['display'].update(referenceDisplayStatus='unmeasured'), 'mustang: reference display status must be notApplicable')
        self.failing(lambda c: c['devices']['mustang']['lunch'].update(expected='mustang-trunk_staging-user'), 'mustang: expected lunch')
        self.failing(lambda c: c['devices']['rango']['display'].update(referenceDisplayAddress='4619827259835644672'),
            'rango: a reference display address is configured')
        self.failing(lambda c: c['devices']['rango']['display'].update(referenceDisplayStatus='measured'), 'must be unmeasured')
        self.failing(lambda c: c['devices']['rango']['display']['source'].pop('secondaryDisplayFragment'), 'rango: secondary display fact differs')
        self.failing(lambda c: c['devices']['rango']['display']['source'].pop('recoveryRotation'), 'rango: recovery rotation differs')

    def test_yogi_fold_port_never_applies_to_rango(self):
        self.failing(lambda c: c['devices']['rango']['forbiddenPatchSets'].clear(), 'rango: does not forbid os/fold-port')
        self.failing(lambda c: c['devices']['rango'].__setitem__('patchSets', ['os/fold-port']), 'rango: associates os/fold-port')
        self.failing(lambda c: c['devices']['rango'].__setitem__('kernelDir', 'os/fold-port/stock-kernel'), 'rango: associates os/fold-port')

    def test_rose_inclusion_must_match_the_patches_and_inheritance(self):
        self.failing(lambda c: c['roseInclusion']['products'].pop('target/product/media_system.mk'), 'Rose edits products')
        self.failing(lambda c: c['roseInclusion']['projects'].pop('packages/modules/Permission'), 'Rose patches projects')
        # #156: the Launcher3 fallback-overview patch is registered, so its declaration is required.
        self.failing(lambda c: c['roseInclusion']['projects'].pop('packages/apps/Launcher3'), 'Rose patches projects')
        self.failing(lambda c: c['devices']['rango']['sourceDifferences'].update(systemUiUnfoldedOverlays=28), 'overlay count differs')

    def mutated_fixtures(self, rel, old, new):
        tmp = Path(tempfile.mkdtemp(prefix='device-contract-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        shutil.copytree(FIXTURES, tmp/'f')
        path = tmp/'f'/rel
        data = path.read_text()
        self.assertIn(old, data)
        path.write_text(data.replace(old, new))
        # Re-record the blob so only the semantic check, not provenance, can catch the change.
        prov = json.loads((tmp/'f/PROVENANCE.json').read_text())
        for row in prov['files']:
            if row['fixture'] == rel:
                row['gitBlob'] = git_blob(path.read_bytes())
        (tmp/'f/PROVENANCE.json').write_text(json.dumps(prov))
        return failures(self.base, tmp/'f')

    def test_a_missing_inherited_rose_product_fails(self):
        common = 'adevtool/config/mk/google_devices/common/product-common.mk'
        found = self.mutated_fixtures(common, '$(call inherit-product, $(SRC_TARGET_DIR)/product/generic_system.mk)\n', '')
        for name in DEVICES:
            self.assertIn(f'{name}: does not inherit Rose product target/product/generic_system.mk', found)
            self.assertIn(f'{name}: does not inherit Rose product target/product/media_system.mk', found)
        found = self.mutated_fixtures('platform_build/target/product/handheld_system.mk',
            '$(call inherit-product, $(SRC_TARGET_DIR)/product/media_system.mk)', '')
        self.assertIn('rango: does not inherit Rose product target/product/media_system.mk', found)
        found = self.mutated_fixtures('adevtool/config/mk/google_devices/device/blazer/device.mk', 'platform/laguna/product-common.mk', 'platform/other/product-common.mk')
        self.assertIn('blazer: device.mk no longer inherits the shared laguna product', found)

    def test_mustang_facts_come_from_its_own_pinned_fixtures(self):
        found = self.mutated_fixtures('adevtool/config/mk/google_devices/device/mustang/device.mk', 'grapheneos/muzel', 'grapheneos/rango')
        self.assertIn('mustang: kernel dir differs from device.mk or the pinned laguna kernels', found)
        self.assertNotIn('blazer: kernel dir differs from device.mk or the pinned laguna kernels', found)
        found = self.mutated_fixtures('adevtool/vendor-skels/google_devices/mustang/firmware/android-info.txt', 'board=mustang', 'board=blazer')
        self.assertIn('mustang: firmware requirements differ from android-info.txt', found)
        found = self.mutated_fixtures('adevtool/config/device/mustang.yml', 'width: 1344', 'width: 1280')
        self.assertIn('mustang: stable display size differs from the adevtool config', found)
        found = self.mutated_fixtures('adevtool/vendor-skels/google_devices/mustang/cmds-for-envsetup.sh', 'CP3A.261005.005', 'CP2A.260605.016')
        self.assertIn('mustang: envsetup BUILD_ID differs', found)

    def test_tampered_or_unlisted_fixtures_fail_provenance(self):
        tmp = Path(tempfile.mkdtemp(prefix='device-contract-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        shutil.copytree(FIXTURES, tmp/'f')
        info = tmp/'f/adevtool/vendor-skels/google_devices/rango/firmware/android-info.txt'
        info.write_text(info.read_text().replace('16119390', '16119391'))
        (tmp/'f/adevtool/extra.yml').write_text('x')
        found = failures(self.base, tmp/'f')
        self.assertIn('fixture adevtool/vendor-skels/google_devices/rango/firmware/android-info.txt differs from its recorded git blob', found)
        self.assertIn('fixture adevtool/extra.yml is not in PROVENANCE.json', found)

if __name__ == '__main__':
    unittest.main()
