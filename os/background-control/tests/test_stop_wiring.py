"""Source checks that each PiP controller Stop entry point reports the right reason; no device needed."""
from pathlib import Path
import re
import unittest

SOURCE = Path(__file__).resolve().parents[1]/'agent-pip/src/dev/pixel/backgroundcontrol'

def calls(name):
    return re.findall(r'Controller\.stop\(\w+(?:\(\))?,StopLedger\.(\w+)\)|\bstop\(c,StopLedger\.(\w+)\)',
        (SOURCE/name).read_text())

class StopWiringTest(unittest.TestCase):
    def reasons(self, name):
        return [a or b for a, b in calls(name)]

    def test_owner_controls_report_user(self):
        # The owner's Stop on a notification stops that notification's session only (MultiSession.ownerStop).
        self.assertIn('Controller.ownerStop(c,i.getStringExtra("sessionId"));', (SOURCE/'StopReceiver.java').read_text())
        self.assertIn('if(i<0)return;Slot was=enter(slots[i]);try{stop(c,StopLedger.USER);}finally{leave(was);}', (SOURCE/'Controller.java').read_text())
        # The controller's own Stop button is the owner's Stop of every occupied slot (Controller.stopAll).
        self.assertIn('org.json.JSONObject s=Controller.stopAll(this,StopLedger.USER);', (SOURCE/'MainActivity.java').read_text())

    def test_agent_and_deadline_paths(self):
        # The provider's stop goes through Controller.agentStop (generation-checked).
        self.assertEqual(self.reasons('AgentProvider.java'), [])
        # agentStop, close before a new start, the 600 s deadline, and the agent's home command.
        # ... and the owner's notification Stop routed to its own slot (ownerStop).
        # ... and Rose Agent's own native owner_stop of one named session (route).
        # ... and the viewer's confirmed Stop of its own bound pair (viewerStop, #410 Nathan-approved): the one added owner Stop.
        self.assertEqual(self.reasons('Controller.java'), ['AGENT', 'AGENT', 'DEADLINE', 'AGENT', 'USER', 'USER', 'USER'])

    def test_no_untyped_stop_remains(self):
        for path in SOURCE.glob('*.java'):
            self.assertNotRegex(path.read_text(), r'\bstop\((?:c|this|getContext\(\))\)', path.name)

    def test_only_user_stop_announces_and_cleanup_failure_is_not_acknowledged(self):
        text = (SOURCE/'Controller.java').read_text()
        self.assertIn('if(StopLedger.USER.equals(why))announceOwnerStop(c,id,taskId);', text)
        self.assertEqual(text.count('announceOwnerStop('), 2)
        # A failed close leaves status cleanup_failed, never "stopped".
        self.assertRegex(text, r'catch\(Throwable e\)\{session=old;error=root\(e\)\.toString\(\);status="cleanup_failed";\}')

    def test_refused_start_names_rejected_task_not_current_workspace(self):
        text = (SOURCE/'Controller.java').read_text()
        start = text[text.index('static synchronized JSONObject start('):text.index('static void attachLifecycle(')]
        refusal = start.index('if(stops.refuses(task))return refusal(task,state(c));')
        # Refusal returns before anything about the current workspace (task B) changes.
        for mutation in ('stop(c,', 'stops.started()', 'taskId=', 'target=pkg', 'generation'):
            self.assertLess(refusal, start.index(mutation), mutation)
        self.assertNotIn('state(c).put("status","user_stopped")', text)
        self.assertIn('static JSONObject refusal(String task,JSONObject current)', text)

    def test_tagged_commands_and_agent_stops_check_generation_first(self):
        text = (SOURCE/'Controller.java').read_text()
        command = text[text.index('static synchronized JSONObject command('):text.index('static AccessibilityNodeInfo node(')]
        self.assertLess(command.index('stops.taskMismatch(taskParam(p),taskId)'), command.index('require(p)'))
        provider = (SOURCE/'AgentProvider.java').read_text()
        self.assertIn('case "stop":result=Controller.route(getContext(),"stop",p);break;', provider)
        self.assertIn('return in(r.slot,()->agentStop(c,p)).put("sessions",summary());', text)
        stop = text[text.index('static synchronized JSONObject agentStop('):]
        self.assertLess(stop.index('stops.taskMismatch(task,taskId)'), stop.index('stop(c,StopLedger.AGENT)'))

    def test_schema_advertised(self):
        self.assertIn('.put("schemaVersion",4)', (SOURCE/'AgentProvider.java').read_text())

class ActionFeedbackTest(unittest.TestCase):
    """#225: button outcomes are shown apart from the refreshed status, and survive recreation."""
    def test_action_notice_logic(self):
        import shutil, subprocess, tempfile
        out = tempfile.mkdtemp(prefix='action-notice-')
        self.addCleanup(shutil.rmtree, out, True)
        subprocess.run(['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-source', '11', '-target', '11',
            '-Xlint:-options', '-d', out, str(SOURCE/'ActionNotice.java'),
            str(Path(__file__).resolve().parent/'ActionNoticeTest.java')], check=True)
        result = subprocess.run(['java', '-cp', out, 'dev.pixel.backgroundcontrol.ActionNoticeTest'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_main_activity_keeps_outcomes_out_of_the_refreshed_status(self):
        text = (SOURCE/'MainActivity.java').read_text()
        refresh = text[text.index('final Runnable refresh'):text.index('void show()')]
        self.assertIn('status.setText(', refresh)
        self.assertNotIn('result.setText(', refresh, 'the 500 ms refresh never overwrites an outcome')
        self.assertNotIn('status.setText(e', text, 'errors no longer go into the refreshed status')
        self.assertIn('notice.consent(Controller.requestConsent(this));', text)
        self.assertIn('notice.failed("Android consent",e);', text)
        self.assertIn('notice.stopped(s.optString("status"),s.optString("error"));', text)
        self.assertIn('b.putString(ActionNotice.SAVED_KEY,notice.text());', text)
        self.assertIn('if(b!=null)notice.restore(b.getString(ActionNotice.SAVED_KEY));', text)

    def test_consent_reports_none_pending_and_keeps_the_automatic_caller(self):
        text = (SOURCE/'Controller.java').read_text()
        self.assertIn('static boolean requestConsent(Activity a)throws Exception{IntentSender pending; '
            'synchronized(Controller.class){pending=consent;}if(pending==null)return false;'
            'a.startIntentSenderForResult(pending,3,null,0,0,0);return true;}', text)
        self.assertIn('case "onSessionPending":consent=(IntentSender)args[0];status="consent_required";'
            'Activity a=MainActivity.visible.get();if(a!=null)requestConsent(a);break;', text)

if __name__ == '__main__':unittest.main()
