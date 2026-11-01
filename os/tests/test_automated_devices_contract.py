"""#156 platform: the ComputerControl session device-owner snapshot (onAutomatedDevicesChanged), as agreed with the
Launcher3 consumer. Applies os/patches/background-control-modern-frameworks-base.patch to the six pinned frameworks/base
84183aff sources it touches, compiles the patched production AutomatedPackagesRepository and AutomatedPackageListener
with behavioural stubs of the Android types they use (os/tests/automated-devices/stubs) and the listener interface
generated from the patched AIDL, and runs the nine paired scenarios (AutomatedDevicesContractTest). The frozen
interface, the unchanged recents/HOME gate and the owner-uid, caller-user and close plumbing are checked in the patched
sources. Source and fixture logic only: no framework build, device or runtime."""
from pathlib import Path
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from background_apks import BACKGROUND_FRAMEWORK_PATCHES  # noqa: E402

FIXTURES = ROOT/'os/tests/fixtures/automated-devices'
HARNESS = ROOT/'os/tests/automated-devices'
VIRTUAL = 'services/companion/java/com/android/server/companion/virtual'
CORE = 'core/java/android/companion/virtual'
FILES = {
    f'{VIRTUAL}/computercontrol/AutomatedPackagesRepository.java': 'f2b05ab0175a387a29ae9c4c3ba1781df1e9b4d7c013eeeab3460365bbdeecc0',
    f'{CORE}/computercontrol/IAutomatedPackageListener.aidl': 'f517dec31a738885227ffb7271be38f14f5acc904e3a164ca624e52ef153a5da',
    f'{CORE}/computercontrol/AutomatedPackageListener.java': 'e39e3f93a046664ae045473c2bebd3a7c856cc58ff22a7019bd722ac2f3b0fe3',
    f'{CORE}/VirtualDeviceManager.java': '71fcaaea4eb03c53c6f89b4803c56014fe31218acef5a3d396ffc143e7185e1f',
    f'{VIRTUAL}/VirtualDeviceManagerService.java': '2ce9b1b44835e5ed2ee3e66a1cfe49d5d976e9961f605787ba7bf116850afd2e',
    f'{VIRTUAL}/VirtualDeviceImpl.java': 'c7ad5bcd9dfda3735522c6bf0e737d0c8e7fd36cf3450f37a049962e8b6c40c8',
}

def flat(text):
    return re.sub(r'\s+', ' ', text)

def aidl_to_java(text):
    """The Java interface a one-interface AIDL file defines, for an offline compile of its callers."""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    package = re.search(r'package ([\w.]+);', text).group(1)
    name, body = re.search(r'interface (\w+) \{(.*)\}', text, re.S).groups()
    methods = [re.sub(r'\b(in|out|inout) ', '', m.strip()) for m in body.split(';') if m.strip()]
    imports = ''.join(f'import {i};\n' for i in re.findall(r'import ([\w.]+);', text))
    return (f'package {package};\n{imports}import java.util.List;\npublic interface {name} {{\n'
        + ''.join(f'    {m} throws android.os.RemoteException;\n' for m in methods) + '}\n')

class AutomatedDevicesContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='automated-devices-'))
        cls.tree = cls.tmp/'frameworks-base'
        for path in FILES:
            (cls.tree/path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURES/Path(path).name, cls.tree/path)
        subprocess.run(['git', 'init', '-q', str(cls.tree)], check=True)
        cls.applied = []
        for patch in BACKGROUND_FRAMEWORK_PATCHES:
            result = subprocess.run(['git', '-C', str(cls.tree), 'apply', *[f'--include={p}' for p in FILES],
                str(ROOT/'os/patches'/patch)], capture_output=True, text=True)
            cls.applied.append((patch, result.returncode, result.stderr))
        cls.source = {Path(p).name: (cls.tree/p).read_text() for p in FILES}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_fixtures_are_pinned_and_the_patch_applies_cleanly(self):
        for path, digest in FILES.items():
            self.assertEqual(hashlib.sha256((FIXTURES/Path(path).name).read_bytes()).hexdigest(), digest, path)
        for patch, code, stderr in self.applied:
            self.assertEqual(code, 0, f'{patch}: {stderr}')
        markers = {'AutomatedPackagesRepository.java': 'notifyAutomatedDevicesChangedLocked',
            'IAutomatedPackageListener.aidl': 'onAutomatedDevicesChanged', 'AutomatedPackageListener.java': 'onAutomatedDevicesChanged',
            'VirtualDeviceManager.java': 'onAutomatedDevicesChanged', 'VirtualDeviceManagerService.java': 'onDeviceClosed',
            'VirtualDeviceImpl.java': 'mOwnerUid, runningUids'}
        for name, marker in markers.items():
            self.assertTrue(marker in self.source[name], f'{name} lacks {marker}')

    def test_the_patched_repository_meets_the_contract_in_all_nine_scenarios(self):
        build = self.tmp/'build'
        generated = build/'gen/android/companion/virtual/computercontrol'
        generated.mkdir(parents=True)
        (generated/'IAutomatedPackageListener.java').write_text(aidl_to_java(self.source['IAutomatedPackageListener.aidl']))
        sources = [str(p) for p in sorted((HARNESS/'stubs').rglob('*.java'))] + [str(generated/'IAutomatedPackageListener.java'),
            str(self.tree/VIRTUAL/'computercontrol/AutomatedPackagesRepository.java'),
            str(self.tree/CORE/'computercontrol/AutomatedPackageListener.java'), str(HARNESS/'AutomatedDevicesContractTest.java')]
        compiled = subprocess.run([sys.executable and 'java', '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-nowarn',
            '-d', str(build/'classes'), *sources], capture_output=True, text=True)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        run = subprocess.run(['java', '-cp', str(build/'classes'),
            'com.android.server.companion.virtual.computercontrol.AutomatedDevicesContractTest'], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertRegex(run.stdout, r'AutomatedDevicesContractTest: \d+ checks passed')

    def test_the_interface_is_exactly_the_frozen_one(self):
        self.assertIn('default void onAutomatedDevicesChanged(long generation, @NonNull int[] deviceIds, '
            '@NonNull String[] ownerPackages, @NonNull int[] ownerUids) {}', flat(self.source['AutomatedPackageListener.java']))
        self.assertIn('@hide */ default void onAutomatedDevicesChanged(', flat(self.source['AutomatedPackageListener.java']))
        self.assertIn('void onAutomatedDevicesChanged(long generation, in int[] deviceIds, in String[] ownerPackages, '
            'in int[] ownerUids);', flat(self.source['IAutomatedPackageListener.aidl']))
        self.assertIn('oneway interface IAutomatedPackageListener', self.source['IAutomatedPackageListener.aidl'])
        delegate = flat(self.source['VirtualDeviceManager.java'])
        self.assertIn('public void onAutomatedDevicesChanged(long generation, @NonNull int[] deviceIds, '
            '@NonNull String[] ownerPackages, @NonNull int[] ownerUids) { Binder.withCleanCallingIdentity(() -> '
            'mExecutor.execute(() -> mListener.onAutomatedDevicesChanged( generation, deviceIds, ownerPackages, ownerUids)));',
            delegate)

    def test_gate_user_scope_owner_uid_and_close_are_plumbed_without_new_access(self):
        service, original = self.source['VirtualDeviceManagerService.java'], (FIXTURES/'VirtualDeviceManagerService.java').read_text()
        register = flat(service[service.index('public void registerAutomatedPackageListener('):])
        self.assertTrue(register.startswith('public void registerAutomatedPackageListener(IAutomatedPackageListener listener) '
            '{ checkCallerIsRecentsOrHomeRoleHolder();'), 'the recents/HOME gate still comes first')
        self.assertIn('mAutomatedPackagesRepository.registerAutomatedPackageListener(listener, UserHandle.getCallingUserId());',
            register.split('}')[0] + '}')
        # No new permission, role or public-display path: the gate and every permission check are as pinned.
        for marker in ('enforceCallingPermission', 'enforceCallingOrSelfPermission', 'checkCallerIsRecentsOrHomeRoleHolder()',
                'VIRTUAL_DISPLAY_FLAG_PUBLIC', 'ROLE_HOME'):
            self.assertEqual(service.count(marker), original.count(marker), marker)
        self.assertIn('mAutomatedPackagesRepository.onDeviceClosed(deviceId);', service)
        self.assertLess(service.index('mVirtualDevices.remove(deviceId);'), service.index('mAutomatedPackagesRepository.onDeviceClosed(deviceId);'))
        self.assertIn('mAutomatedPackagesRepository.update(deviceId, deviceOwnerPackageName, deviceOwnerUid,', service)
        self.assertIn('onRunningAppsChanged(deviceId, deviceOwnerPackageName, -1 /* unknown owner uid: never listed */,', service)
        self.assertIn('mDeviceId, mOwnerPackageName, mOwnerUid, runningUids, newAllRunningUidPackagePairs);',
            self.source['VirtualDeviceImpl.java'])
        # The owner fields the snapshot relies on are final, and device ids are never reused, in the pinned source.
        impl = (FIXTURES/'VirtualDeviceImpl.java').read_text()
        self.assertIn('private final int mOwnerUid;', impl)
        self.assertIn('private final String mOwnerPackageName;', impl)
        self.assertIn('final int deviceId = sNextUniqueIndex.getAndIncrement();', original)
        # The snapshot never names automated packages: only device ids, owner packages and owner uids are sent.
        repository = self.source['AutomatedPackagesRepository.java']
        for call in re.findall(r'onAutomatedDevicesChanged\(\s*generation,[^;]*;', repository):
            self.assertEqual(flat(call), 'onAutomatedDevicesChanged( generation, snapshot.deviceIds, snapshot.ownerPackages, '
                'snapshot.ownerUids);')

if __name__ == '__main__':
    unittest.main()
