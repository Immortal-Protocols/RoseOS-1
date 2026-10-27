"""Pure validation tests; synthetic pins only, no device or private APK needed."""
import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[3]/'scripts'))
from background_apks import validate_policy

class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.apk = Path(self.temp.name)/'Agent.apk'
        self.apk.write_bytes(b'synthetic-validator-test')
        self.policy = {'schemaVersion': 1, 'sdk': 37,
            'agent': {'packageName': 'dev.pixel.backgroundcontrol', 'certificates': ['A'*64], 'apkSha256': hashlib.sha256(self.apk.read_bytes()).hexdigest()},
            'voice': {'packageName': 'dev.pixel.voice', 'certificates': ['A'*64]},
            'targets': [{'packageName': 'example.target', 'certificates': ['B'*64]}]}
    def test_exact_policy(self):
        self.assertIs(validate_policy(self.policy, self.apk), self.policy)
    def test_prototype_controller_needs_no_per_app_list(self):
        self.policy['prototypeControl'] = True
        self.policy['targets'] = []
        self.assertIs(validate_policy(self.policy, self.apk), self.policy)
    def test_rejects_bad_inputs(self):
        variants = []
        p=copy.deepcopy(self.policy);p['voice']['certificates']=['C'*64];variants.append(p)
        p=copy.deepcopy(self.policy);p['targets'][0]['packageName']='*';variants.append(p)
        p=copy.deepcopy(self.policy);p['targets'][0]['certificates']=['*'];variants.append(p)
        p=copy.deepcopy(self.policy);p['targets'][0]['packageName']='com.android.permissioncontroller';variants.append(p)
        p=copy.deepcopy(self.policy);p['targets']*=2;variants.append(p)
        p=copy.deepcopy(self.policy);p['targets']=[];variants.append(p)
        p=copy.deepcopy(self.policy);p['agent']['apkSha256']='0'*64;variants.append(p)
        for p in variants:
            with self.subTest(policy=p), self.assertRaises(ValueError):validate_policy(p,self.apk)
if __name__=='__main__':unittest.main()
