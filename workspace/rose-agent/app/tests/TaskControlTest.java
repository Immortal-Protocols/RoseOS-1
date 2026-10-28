package dev.pixel.voice;

/** Response shapes follow server/task-stop.mjs request(): {status:'not_active'};
 * {status:'stale_task',taskId:current}; {status:'stopping',taskId,requestedAt,duplicate:true};
 * {status:'stopping',taskId,requestedAt,worker:'interrupt_requested'|'interrupt_failed: …',
 * workspace:'closed'|'close_failed: …'}. Controller get_state follows Controller.state (schema 3). */
public final class TaskControlTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    public static void main(String[] args) {
        // Watch: only a live session tagged with the same task id.
        expect(TaskControl.watchable("t1", true, "ready", "s1", "t1"), "Live session for this task");
        expect(TaskControl.watchable("t1", true, "blocked", "s1", "t1"), "Blocked session can still be inspected");
        expect(!TaskControl.watchable("t1", true, "ready", "s1", "t2"), "Session of another task is never shown");
        expect(!TaskControl.watchable("t1", true, "ready", "s1", null), "Untagged session is not assumed to be this task");
        expect(!TaskControl.watchable("t1", true, "idle", null, "t1"), "No session, nothing to watch");
        expect(!TaskControl.watchable("t1", true, "ready", "null", "t1"), "JSON null session is no session");
        expect(!TaskControl.watchable("t1", false, "ready", "s1", "t1"), "Finished task has no live workspace");

        // Stop outcomes.
        TaskControl ok = TaskControl.stopResult(200, "stopping", "t1", "interrupt_requested", "closed", false, "t1");
        expect(ok.outcome == TaskControl.Outcome.STOPPING && ok.message.startsWith("Stop requested") && !ok.message.startsWith("Stopped") && ok.message.contains("Waiting"),
                "Accepted is stopping, not stopped");
        TaskControl closeFailed = TaskControl.stopResult(200, "stopping", "t1", "interrupt_requested", "close_failed: timeout", false, "t1");
        expect(closeFailed.outcome == TaskControl.Outcome.STOPPING && closeFailed.message.contains("didn’t confirm it closed"),
                "Workspace close failure is surfaced");
        expect(TaskControl.stopResult(200, "stopping", "t1", "interrupt_failed: gone", "closed", false, "t1").message.contains("couldn’t interrupt"),
                "Worker interrupt failure is surfaced");
        expect(TaskControl.stopResult(200, "stopping", "t1", null, null, true, "t1").outcome == TaskControl.Outcome.ALREADY_STOPPING,
                "Repeated Stop is idempotent");
        expect(TaskControl.stopResult(200, "stale_task", "t2", null, null, false, "t1").outcome == TaskControl.Outcome.CHANGED,
                "Task changed while confirming: nothing stopped, never redirected");
        expect(TaskControl.stopResult(200, "not_active", null, null, null, false, "t1").outcome == TaskControl.Outcome.NOT_RUNNING,
                "Task finished while confirming");
        expect(TaskControl.stopResult(200, "stopping", "t2", null, null, false, "t1").outcome == TaskControl.Outcome.FAILED,
                "An acknowledgment for a different task is not success");
        expect(TaskControl.stopResult(0, null, null, null, null, false, "t1").outcome == TaskControl.Outcome.FAILED,
                "Transport failure or timeout");
        expect(TaskControl.stopResult(401, null, null, null, null, false, "t1").message.contains("401"), "Refused request");
        expect(TaskControl.stopResult(200, "weird", "t1", null, null, false, "t1").outcome == TaskControl.Outcome.FAILED,
                "Unknown answer is not success");

        // Authoritative end: only the selected task's own state.
        expect(!TaskControl.settled("t1", "t1", "stopping"), "Stopping is not stopped");
        expect(TaskControl.settled("t1", "t1", "interrupted"), "Interrupted is the stopped state");
        expect(TaskControl.settled("t1", "t1", "completed"), "Finished first: nothing left to stop");
        expect(TaskControl.settled("t1", "t2", "running"), "Selected task replaced: stop waiting, never retarget");

        System.out.println("TaskControlTest: " + checks + " checks passed");
    }
}
