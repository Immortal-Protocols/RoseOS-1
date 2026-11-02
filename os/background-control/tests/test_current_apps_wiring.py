"""#156/#225 current-apps membership wiring (source checks; no device). The controller lists the apps on its live
session display from the session itself, never from task history, without touching the agent's observation; it stays
behind the provider's signer check, is not advertised, and is refused on the phone's app_command path."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT/'os/background-control/agent-pip/src/dev/pixel/backgroundcontrol'
CONTROLLER = (SRC/'Controller.java').read_text()
PROVIDER = (SRC/'AgentProvider.java').read_text()
PHONE = (ROOT/'workspace/rose-agent/app/src/dev/pixel/voice/PhoneControl.java').read_text()
SIGNATURE = 'static synchronized JSONObject currentApps(Context c)'
OBSERVATION = ('snapshotId', 'snapshotTime', 'width', 'height', 'operations', 'observed', 'truncated', 'lastPreview')


def method(source, signature):
    if signature not in source: return ''
    start = source.index(signature)
    depth, i = 0, source.index('{', start)
    while True:
        if source[i] == '{': depth += 1
        elif source[i] == '}':
            depth -= 1
            if depth == 0: return source[start:i + 1]
        i += 1


def writes(body, name):
    return re.findall(r'(?<![\w.])' + name + r'\s*(?:=(?!=)|\+\+|\+=|-=)', body) + re.findall(r'(?:\+\+|--)' + name + r'\b', body)


class CurrentAppsWiringTest(unittest.TestCase):
    body = method(CONTROLLER, SIGNATURE)

    def test_membership_is_the_live_session_only(self):
        self.assertIn('boolean locked=locked(c),live=session!=null&&CurrentApps.live(status);', self.body)
        self.assertIn(':CurrentApps.of(locked,session!=null,status,blocked,target,front,c.getPackageName());', self.body)
        # Nothing from task history: no stop ledger, StopLedger ids, or remembered sets feed the members.
        for source in ('stops.', 'stoppedTaskId', 'StopLedger', 'observed', 'labels', 'nodes'):
            self.assertNotIn(source, self.body, source)
        self.assertIn('if(live&&!locked&&!blocked)try{', self.body, 'blocked or locked sessions are not read')
        self.assertIn('.put("sessionId",listed?id:JSONObject.NULL).put("taskId",listed?nullable(taskId):JSONObject.NULL)', self.body)

    def test_window_read_leaves_the_agent_observation_untouched(self):
        for name in OBSERVATION:
            self.assertEqual(writes(self.body, name), [], name)
        for call in ('clearNodes(', 'inspect(', 'capture(', 'walk(', 'getScreenshot'):
            self.assertNotIn(call, self.body, call)
        self.assertIn('n.recycle();', self.body)
        self.assertIn('w.recycle();', self.body)
        self.assertIn('catch(Throwable e){front=null;}', self.body, 'a failed read lists only the target and says it is incomplete')
        self.assertEqual(sorted(set(re.findall(r'(?<![\w.])(\w+)\s*=(?!=)', self.body)) - {
            'locked', 'live', 'front', 'read', 'windows', 'n', 'pkg', 'r', 'signature', 'listed', 'apps', 'platform', 'b'}), ['appsSignature'])
        self.assertTrue(writes(self.body, 'appsGeneration'), 'the generation rises on change')

    def test_reply_shape(self):
        self.assertEqual(re.findall(r'\.put\("(\w+)"', self.body),
                         ['packageName', 'front', 'target', 'status', 'sessionId', 'taskId', 'sessionStatus', 'generation', 'complete', 'incompleteReason', 'source', 'apps'])
        self.assertIn('.put("sessionStatus",listed?status:JSONObject.NULL)', self.body, 'the live session\'s ready/blocked/launching status, null otherwise')
        self.assertIn('String signature=CurrentApps.signature(r,id,status);', self.body)
        self.assertIn('.put("generation",appsGeneration)', self.body, 'the controller\'s rising counter, never the platform\'s generation')
        self.assertNotIn('platform.generation', self.body)

    def test_same_signer_only_not_advertised_never_a_phone_command(self):
        signer = PROVIDER.index('checkSignatures(android.os.Process.myUid(),Binder.getCallingUid())!=PackageManager.SIGNATURE_MATCH')
        self.assertLess(signer, PROVIDER.index('case "current_apps":result=Controller.currentApps(getContext());break;'))
        caps = re.search(r'case "capabilities":.*?new String\[\]\{([^}]*)\}', PROVIDER).group(1)
        self.assertEqual(caps, '"list_apps","start","get_state","command","stop"')
        guard = PHONE.index('"current_apps".equals(command.optString("operation"))')
        self.assertLess(guard, PHONE.index('context.getContentResolver().call(android.net.Uri.parse("content://"+authority),command.getString("operation")'))


    def test_platform_answer_through_the_existing_route_with_the_fallback_kept(self):
        # The agreed PR361 method on the extensions wrapper, called reflectively like the rest of the API; no hidden class.
        self.assertIn('Bundle b=(Bundle)session.getClass().getMethod("getAutomatedApps").invoke(session);', self.body)
        self.assertNotIn('android.companion.virtual.computercontrol', self.body)
        self.assertIn('platform=CurrentApps.platform(b.get("generation"),b.get("packageNames"),b.get("userId"),b.get("complete"),b.get("closed"),android.os.Process.myUid()/100000);', self.body)
        self.assertIn('if(live&&!locked)try{', self.body.split('getAutomatedApps')[0].rsplit('CurrentApps.Platform platform=null;', 1)[1])
        self.assertIn('}catch(Throwable e){platform=null;}', self.body, 'SecurityException, IllegalStateException or a missing method: fallback')
        self.assertIn('CurrentApps.Result r=platform!=null?CurrentApps.of(locked,session!=null,status,target,platform,front,c.getPackageName())', self.body)
        self.assertIn(':CurrentApps.of(locked,session!=null,status,blocked,target,front,c.getPackageName());', self.body)


if __name__ == '__main__':
    unittest.main()
