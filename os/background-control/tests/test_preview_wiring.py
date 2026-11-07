"""#225 read-only preview wiring (source checks; no device). The agent's screenshot is its observation: it clears the
node map and issues a new snapshot id, so a passive poll through it would invalidate the agent's next action. The
preview is a separate path that leaves every piece of observation state untouched, is bound to the live session and
task (PreviewPolicy), stays behind the provider's signer check, is not advertised, and is refused on the phone's
app_command path so the Mac can never reach it."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT/'os/background-control/agent-pip/src/dev/pixel/backgroundcontrol'
CONTROLLER = (SRC/'Controller.java').read_text()
PROVIDER = (SRC/'AgentProvider.java').read_text()
PHONE = (ROOT/'workspace/rose-agent/app/src/dev/pixel/voice/PhoneControl.java').read_text()

# The observation state an agent action depends on (fresh(), checkPoint(), node lookups, the operation limit).
OBSERVATION = ('snapshotId', 'snapshotTime', 'width', 'height', 'operations', 'observed', 'truncated')


def method(source, signature):
    start = source.index(signature)
    depth, i = 0, source.index('{', start)
    while True:
        if source[i] == '{': depth += 1
        elif source[i] == '}':
            depth -= 1
            if depth == 0: return source[start:i + 1]
        i += 1


def writes(body, name):
    """Assignments or increments of the Controller static `name` (not reads, not other objects' fields)."""
    return re.findall(r'(?<![\w.])' + name + r'\s*(?:=(?!=)|\+\+|\+=|-=)', body) + re.findall(r'(?:\+\+|--)' + name + r'\b', body)


class PreviewWiringTest(unittest.TestCase):
    capture = method(CONTROLLER, 'static JSONObject capture()')
    preview = method(CONTROLLER, 'static synchronized JSONObject preview(Context c,JSONObject p)') \
        if 'static synchronized JSONObject preview(Context c,JSONObject p)' in CONTROLLER else ''

    def test_the_agent_screenshot_is_an_observation_that_a_poll_would_invalidate(self):
        # Why the existing screenshot command cannot serve a preview: each call ends the agent's current observation.
        self.assertIn('clearNodes();snapshotId=UUID.randomUUID().toString();snapshotTime=SystemClock.elapsedRealtime();', self.capture)
        self.assertTrue(writes(self.capture, 'width') and writes(self.capture, 'height'))
        self.assertIn('static void clearNodes(){', CONTROLLER)
        self.assertIn('snapshotId=null;visualSnapshotId=null;}', method(CONTROLLER, 'static void clearNodes()'), 'clearing an observation drops both tree and vision snapshot ids')
        self.assertIn('fresh(p);', method(CONTROLLER, 'static synchronized JSONObject command(Context c,JSONObject p)'),
                      'every action needs the latest snapshot id, so a polled screenshot breaks it')

    def test_preview_leaves_every_piece_of_observation_state_untouched(self):
        for name in OBSERVATION:
            self.assertEqual(writes(self.preview, name), [], name + ' is never written by the preview')
        for call in ('clearNodes(', 'capture(', 'inspect(', 'stop(', 'start(', 'nodes.', 'labels.', 'parents.'):
            self.assertNotIn(call, self.preview, call)
        self.assertEqual(sorted(set(re.findall(r'(?<![\w.])(\w+)\s*=(?!=)', self.preview)) - {
            'requestedSession', 'requestedTask', 'now', 'refused', 'image', 'all', 'crop', 'small', 'size', 'plane', 'out', 'jpeg', 'w', 'h'}),
            ['lastPreview'], 'the only Controller state the preview writes is its own rate limit')
        self.assertNotIn('snapshotId', self.preview.split('return new JSONObject().put("status",PreviewPolicy.OK)')[1],
                         'no snapshot id is returned, so a preview can never authorise an action')
        self.assertIn('static synchronized JSONObject preview(', CONTROLLER, 'serialised with commands, never during an agent capture')

    def test_bound_to_the_live_session_and_task_before_any_frame(self):
        at = self.preview.index
        self.assertLess(at('PreviewPolicy.refusal(requestedSession,requestedTask,locked(c),session!=null,status,blocked,now-started>DEADLINE,id,taskId,now,lastPreview)'),
                        at('getScreenshot'))
        self.assertIn('if(refused!=null)return new JSONObject().put("status",refused);', self.preview)
        # The frame is always the controller's own session's display: no display, package or session object is taken from the caller.
        for key in ('displayId', 'packageName', 'targetPackage")', 'snapshotId'):
            self.assertNotIn('p.opt' + 'String("' + key, self.preview)
        self.assertEqual(re.findall(r'\bp\.(\w+)\("(\w+)"', self.preview), [('isNull', 'sessionId'), ('optString', 'sessionId'), ('isNull', 'taskId'), ('optString', 'taskId')])
        self.assertIn('session.getClass().getMethod("getScreenshot").invoke(session)', self.preview)

    def test_output_is_bounded_and_failures_are_fixed_words(self):
        self.assertIn('int[] size=PreviewPolicy.size(w,h);', self.preview)
        self.assertIn('Bitmap.createScaledBitmap(crop,size[0],size[1],true)', self.preview)
        self.assertIn('for(int quality:PreviewPolicy.QUALITIES)', self.preview)
        self.assertIn('if(PreviewPolicy.fits(out.size()))', self.preview)
        self.assertIn('catch(Throwable e){return new JSONObject().put("status",PreviewPolicy.NO_FRAME);}', self.preview)
        self.assertNotIn('getMessage', self.preview)
        self.assertNotIn('toString()', self.preview.replace('.toString(jpeg', ''), 'no exception text leaves the preview')
        ok = self.preview.split('PreviewPolicy.OK)')[1].split('}catch(Throwable e)')[0]
        keys = re.findall(r'\.put\("(\w+)"', ok)
        self.assertEqual(keys, ['sessionId', 'taskId', 'targetPackage', 'capturedAt', 'width', 'height', 'mimeType', 'imageBase64'])
        self.assertIn('image.close();', self.preview)

    def test_same_signer_only_and_not_advertised(self):
        signer = PROVIDER.index('checkSignatures(android.os.Process.myUid(),Binder.getCallingUid())!=PackageManager.SIGNATURE_MATCH')
        self.assertLess(signer, PROVIDER.index('case "preview":result=Controller.route(getContext(),"preview",p);break;'))
        self.assertIn('case "preview":return in(MultiSession.target(view(false),sid).slot,()->preview(c,p));', CONTROLLER, 'the route reaches this preview in the named slot')
        caps = re.search(r'case "capabilities":.*?new String\[\]\{([^}]*)\}', PROVIDER).group(1)
        self.assertEqual(caps, '"list_apps","start","get_state","command","stop"', 'capabilities are unchanged: the Mac is never offered preview')

    def test_the_phone_command_path_never_reaches_the_preview(self):
        guard = PHONE.index('if(pkg.equals("dev.pixel.backgroundcontrol")&&("preview".equals(command.optString("operation"))||"current_apps".equals(command.optString("operation"))||"owner_stop".equals(command.optString("operation"))))return new JSONObject().put("status","unavailable")')
        self.assertLess(PHONE.index('if(!AgentAppTrust.allows(pkg,sameSigner,certificates))'), guard)
        self.assertLess(guard, PHONE.index('context.getContentResolver().call(android.net.Uri.parse("content://"+authority),command.getString("operation")'))


if __name__ == '__main__':
    unittest.main()
