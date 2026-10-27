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
        self.assertEqual(self.reasons('AgentProvider.java'), ['AGENT'])
        # Close before a new start, the 600 s deadline, and the agent's home command.
        self.assertEqual(self.reasons('Controller.java'), ['AGENT', 'DEADLINE', 'AGENT'])

    def test_no_untyped_stop_remains(self):
        for path in SOURCE.glob('*.java'):
            self.assertNotRegex(path.read_text(), r'\bstop\((?:c|this|getContext\(\))\)', path.name)

    def test_only_user_stop_announces_and_cleanup_failure_is_not_acknowledged(self):
        text = (SOURCE/'Controller.java').read_text()
        self.assertIn('if(StopLedger.USER.equals(why))announceOwnerStop(c,id,taskId);', text)
        self.assertEqual(text.count('announceOwnerStop('), 2)
        # A failed close leaves status cleanup_failed, never "stopped".
        self.assertRegex(text, r'catch\(Throwable e\)\{session=old;error=root\(e\)\.toString\(\);status="cleanup_failed";\}')

    def test_schema_advertised(self):
        self.assertIn('.put("schemaVersion",3)', (SOURCE/'AgentProvider.java').read_text())

if __name__ == '__main__':unittest.main()
