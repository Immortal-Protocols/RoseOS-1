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
        self.assertEqual(self.reasons('StopReceiver.java'), ['USER'])
        self.assertEqual(self.reasons('MainActivity.java'), ['USER'])

    def test_agent_and_deadline_paths(self):
        # The provider's stop goes through Controller.agentStop (generation-checked).
        self.assertEqual(self.reasons('AgentProvider.java'), [])
        # agentStop, close before a new start, the 600 s deadline, and the agent's home command.
        self.assertEqual(self.reasons('Controller.java'), ['AGENT', 'AGENT', 'DEADLINE', 'AGENT'])

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
        self.assertIn('case "stop":result=Controller.agentStop(getContext(),p);break;', provider)
        stop = text[text.index('static synchronized JSONObject agentStop('):]
        self.assertLess(stop.index('stops.taskMismatch(task,taskId)'), stop.index('stop(c,StopLedger.AGENT)'))

    def test_schema_advertised(self):
        self.assertIn('.put("schemaVersion",3)', (SOURCE/'AgentProvider.java').read_text())

if __name__ == '__main__':unittest.main()
