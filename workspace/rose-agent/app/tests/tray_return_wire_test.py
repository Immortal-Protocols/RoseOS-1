#!/usr/bin/env python3
"""1C: Rose Agent's side of the return seam (TrayReturn, AgentTrayService) and the shell's (AgentTrayLink.Return,
AgentTrayPrototype, OS Core's #734) speak exactly protocol v1: the same codes, result values and Bundle keys, and Rose
sends only to the replyTo of RETURN_TARGET, which the shell sets to the Messenger it registered with.

Until the shell side is in this tree the shell half is reported as absent (not checked); once it is, any drift fails.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
APP = ROOT / 'workspace/rose-agent/app/src/dev/pixel/voice'
SHELL = ROOT / 'os/overlays/frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell'
V1 = {'TARGET': 7, 'RESULT': 8, 'RETURN_TO': 101, 'CANCEL': 102, 'RETURNED': 0, 'UNAVAILABLE': 1, 'REFUSED': 2}
KEYS = {'protocolVersion', 'token', 'package', 'userId', 'label'}


def constants(source: str) -> dict:
    return {k: int(v) for k, v in re.findall(r'\b([A-Z_]+) = (\d+)', source)}


class RoseSide(unittest.TestCase):
    def test_rose_speaks_v1(self):
        tray = (APP / 'TrayReturn.java').read_text()
        c = constants(tray)
        self.assertEqual({k: c[k] for k in ('TARGET', 'RESULT', 'RETURNED', 'UNAVAILABLE', 'REFUSED')},
                         {k: V1[k] for k in ('TARGET', 'RESULT', 'RETURNED', 'UNAVAILABLE', 'REFUSED')})
        self.assertEqual((c['RETURN'], c['CANCEL'], c['PROTOCOL']), (V1['RETURN_TO'], V1['CANCEL'], 1))
        service = (APP / 'AgentTrayService.java').read_text()
        read = set(re.findall(r'd\.get(?:Int|Long|String)\("(\w+)"', service))
        self.assertEqual(read, KEYS)
        self.assertIn('b.putLong("token",token)', service)  # RETURN_TO's data
        self.assertIn('Message.obtain(null,what,request,0)', service)  # arg1 = attempt
        # Only after the unchanged SystemUI trust check, and only a registered client's target.
        receive = service.split('private void receive(Message m){', 1)[1]
        self.assertTrue(receive.lstrip().startswith('if(!trusted(m.sendingUid))return;'))
        self.assertIn('if(from==null||!clients.containsKey(from))return;', receive)
        self.assertNotRegex(service + tray, r'startActivity|moveTaskToFront|FLAG_ACTIVITY_NEW_TASK|getLaunchIntent')


class ShellSide(unittest.TestCase):
    def setUp(self):
        link = SHELL / 'AgentTrayLink.java'
        if not link.exists() or 'class Return' not in link.read_text():
            self.skipTest('the shell side of 1C (OS Core) is not in this tree')
        self.link = link.read_text()
        self.proto = (SHELL / 'AgentTrayPrototype.java').read_text()

    def test_shell_speaks_v1(self):
        ret = self.link.split('class Return', 1)[1]
        self.assertEqual({k: v for k, v in constants(ret).items() if k in V1}, V1)

    def test_target_bundle_and_reply(self):
        target = self.proto.split('private void sendReturnTarget()', 1)[1].split('\n    }\n', 1)[0]
        self.assertEqual(set(re.findall(r'data\.put(?:Int|Long|String)\("(\w+)"', target)), KEYS)
        self.assertIn('message.replyTo = reply;', target)
        send = self.proto.split('private boolean send(int action)', 1)[1].split('\n    }\n', 1)[0]
        self.assertIn('message.replyTo = reply;', send)  # REGISTER's client is the Messenger RETURN_TARGET names
        result = self.proto.split('private void sendReturnResult(', 1)[1].split('\n    }\n', 1)[0]
        self.assertIn('Message.obtain(null, AgentTrayLink.Return.RESULT, attempt, result)', result)

    def test_shell_reads_what_rose_sends(self):
        self.assertIn('message.getData().getLong("token"), message.arg1', self.proto)


if __name__ == '__main__':
    result = unittest.main(exit=False, verbosity=1).result
    sys.exit(0 if result.wasSuccessful() else 1)
