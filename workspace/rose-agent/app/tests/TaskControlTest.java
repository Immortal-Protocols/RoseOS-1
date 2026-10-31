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

        // watchState (APP-WATCH-1031-C): canWatch is exactly watchable; otherwise one fixed, truthful reason.
        String[][] rows = {
            // task, active, status, session, controllerTask, expected reason ("" = can watch)
            {"t1", "true", "ready", "s1", "t1", ""},
            {"t1", "true", "blocked", "s1", "t1", ""},
            {"t1", "true", "launching", "s1", "t1", ""},
            {"t1", "true", "ready", "s1", "t2", TaskControl.WATCH_OTHER_TASK},
            {"t1", "true", "ready", "s1", null, TaskControl.WATCH_UNLINKED},
            {"t1", "true", "ready", "null", "t1", TaskControl.WATCH_INVALID},
            {"t1", "true", "ready", "", "t1", TaskControl.WATCH_INVALID},
            {"t1", "false", "ready", "s1", "t1", TaskControl.WATCH_NO_TASK},
            {"", "true", "ready", "s1", "", TaskControl.WATCH_NO_TASK},
            {null, "true", "ready", "s1", null, TaskControl.WATCH_NO_TASK},
            {"t1", "true", "starting", "s1", "t1", TaskControl.WATCH_STARTING},
            {"t1", "true", "scheduled", null, "t1", TaskControl.WATCH_STARTING},
            {"t1", "true", "consent_required", "s1", "t1", TaskControl.WATCH_CONSENT},
            {"t1", "true", "target_not_allowed", "s2", "t1", TaskControl.WATCH_NOT_ALLOWED},
            {"t1", "true", "stopped", "s1", "t1", TaskControl.WATCH_STOPPED},
            {"t1", "true", "closed", null, "t1", TaskControl.WATCH_STOPPED},
            {"t1", "true", "user_stopped", "s1", "t1", TaskControl.WATCH_STOPPED},
            {"t1", "true", "failed", "s1", "t1", TaskControl.WATCH_FAILED},
            {"t1", "true", "rejected", "s1", "t1", TaskControl.WATCH_FAILED},
            {"t1", "true", "idle", null, "t1", TaskControl.WATCH_IDLE},
            {"t1", "true", "idle", null, null, TaskControl.WATCH_IDLE},
            {"t1", "true", "idle", "s1", "t1", TaskControl.WATCH_INVALID},
            {"t1", "true", "launching", null, "t1", TaskControl.WATCH_INVALID},
            {"t1", "true", "blocked", "s1", "", TaskControl.WATCH_UNLINKED},
            {"t1", "true", "error", null, null, TaskControl.WATCH_READ_FAILED},
            {"t1", "true", "cleanup_failed", "s1", "t1", TaskControl.WATCH_STOPPED},
            {"t1", "true", "", "", "", TaskControl.WATCH_INVALID},
            {"t1", "true", null, null, null, TaskControl.WATCH_INVALID},
            {"t1", "true", "something_new", "s1", "t1", TaskControl.WATCH_UNKNOWN_STATE},
        };
        for (String[] r : rows) {
            boolean active = Boolean.parseBoolean(r[1]);
            TaskControl.WatchState w = TaskControl.watchState(r[0], active, r[2], r[3], r[4]);
            String label = java.util.Arrays.toString(r);
            expect(w.canWatch == r[5].isEmpty() && w.reason.equals(r[5]), "watchState " + label + " -> " + w.canWatch + " '" + w.reason + "'");
            expect(w.canWatch == TaskControl.watchable(r[0], active, r[2], r[3], r[4]), "watchable agrees with watchState " + label);
            expect(w.canWatch || !w.reason.isEmpty(), "A refusal always has a reason " + label);
        }
        expect(!TaskControl.watchState("t1", true, "ready", "s1", "t2").reason.contains("t2"), "The reason never names another task");

        // Watch binding (APP-FIX-004, V1): the selected task and its own session, read after the tap.
        expect(java.util.Arrays.equals(TaskControl.watchBinding("A", true, "ready", "s1", "A"), new String[] {"A", "s1"}),
                "Ordinary Watch binds the selected task to its live session");
        expect(TaskControl.watchBinding("A", true, "ready", "s2", "B") == null,
                "A replaced by B in a new session: Watch for A opens nothing, never B");
        expect(TaskControl.watchBinding("A", true, "ready", "s1", "B") == null,
                "A replaced by B reusing the same session: Watch for A opens nothing");
        expect(TaskControl.watchBinding("A", false, "ready", "s1", "A") == null,
                "The shown task changed while the controller was being read: the old tap opens nothing");
        expect(TaskControl.watchBinding("A", true, "idle", "", "") == null, "Session ended before the tap was handled");
        expect(TaskControl.watchBinding("", true, "ready", "s1", "") == null, "No selected task, no binding");
        String[] bound = TaskControl.watchBinding("A", true, "launching", "s1", "A");
        expect(bound != null && bound[0].equals("A") && bound[1].equals("s1"), "The binding carries the selected id, not whatever is current");
        // The read adapter (APP-WATCH-1031-C-M002): each failure is a fixed category, never idle, never its exception text.
        Object[][] reads = {
            {(TaskControl.StateCall) () -> { throw new TaskControl.NoProvider(); }, TaskControl.ReadFailure.NO_PROVIDER, TaskControl.WATCH_NO_PROVIDER},
            {(TaskControl.StateCall) () -> { throw new SecurityException("Caller signing identity does not match uid=10123"); }, TaskControl.ReadFailure.DENIED, TaskControl.WATCH_DENIED},
            {(TaskControl.StateCall) () -> { throw new TaskControl.InvalidReply(); }, TaskControl.ReadFailure.INVALID_RESPONSE, TaskControl.WATCH_INVALID},
            {(TaskControl.StateCall) () -> null, TaskControl.ReadFailure.INVALID_RESPONSE, TaskControl.WATCH_INVALID},
            {(TaskControl.StateCall) () -> new String[] {"ready"}, TaskControl.ReadFailure.INVALID_RESPONSE, TaskControl.WATCH_INVALID},
            {(TaskControl.StateCall) () -> new String[] {null, "s1", "t1"}, TaskControl.ReadFailure.INVALID_RESPONSE, TaskControl.WATCH_INVALID},
            {(TaskControl.StateCall) () -> new String[] {"ready", null, "t1"}, TaskControl.ReadFailure.INVALID_RESPONSE, TaskControl.WATCH_INVALID},
            {(TaskControl.StateCall) () -> new String[] {"error", null, null}, TaskControl.ReadFailure.READ_FAILED, TaskControl.WATCH_READ_FAILED},
            {(TaskControl.StateCall) () -> { throw new IllegalStateException("binder died /data/user/0/secret"); }, TaskControl.ReadFailure.READ_FAILED, TaskControl.WATCH_READ_FAILED},
            {(TaskControl.StateCall) () -> { throw new java.io.IOException("x"); }, TaskControl.ReadFailure.READ_FAILED, TaskControl.WATCH_READ_FAILED},
            {(TaskControl.StateCall) () -> new String[] {"idle", null, null}, TaskControl.ReadFailure.NONE, TaskControl.WATCH_IDLE},
            {(TaskControl.StateCall) () -> new String[] {"mystery", null, null}, TaskControl.ReadFailure.NONE, TaskControl.WATCH_UNKNOWN_STATE},
            {(TaskControl.StateCall) () -> new String[] {"ready", "s1", "t1"}, TaskControl.ReadFailure.NONE, ""},
        };
        java.util.Set<String> failureReasons = new java.util.HashSet<>();
        for (Object[] r : reads) {
            TaskControl.ControllerRead read = TaskControl.readController((TaskControl.StateCall) r[0]);
            TaskControl.WatchState w = TaskControl.watchState("t1", true, read);
            expect(read.failure == r[1], "read category " + r[1] + " got " + read.failure);
            expect(w.reason.equals(r[2]) && w.canWatch == ((String) r[2]).isEmpty(), "read reason for " + r[1] + ": '" + w.reason + "'");
            if (read.failure != TaskControl.ReadFailure.NONE) {
                expect(read.status.isEmpty() && read.sessionId.isEmpty() && read.taskId.isEmpty(), "A failed read carries no fields");
                expect(!w.reason.equals(TaskControl.WATCH_IDLE), "A failed read is never idle");
                expect(!w.reason.contains("uid") && !w.reason.contains("/data") && !w.reason.contains("binder"), "No exception text leaks");
                expect(TaskControl.watchBinding("t1", true, read) == null, "A failed read never binds");
                failureReasons.add(w.reason);
            }
        }
        expect(failureReasons.size() == 4, "Absent provider, denied, malformed and failed reads each have their own reason");
        expect(!TaskControl.WATCH_IDLE.equals(TaskControl.WATCH_READ_FAILED) && !TaskControl.WATCH_IDLE.contains("phone"), "Idle is not 'unavailable on this phone'");
        // Before the first read nothing is claimed; an inactive task still says there is nothing to watch.
        expect(TaskControl.watchState("t1", true, TaskControl.ControllerRead.PENDING_READ).reason.equals(TaskControl.WATCH_CHECKING), "Pending read says checking");
        expect(TaskControl.watchState("t1", false, TaskControl.ControllerRead.failed(TaskControl.ReadFailure.DENIED)).reason.equals(TaskControl.WATCH_NO_TASK), "No task beats a failed read");
        expect(TaskControl.watchState("t1", true, (TaskControl.ControllerRead) null).reason.equals(TaskControl.WATCH_READ_FAILED), "No read is a failed read");
        // Binding from a validated read: same task and session only; a task or session replaced after the tap never binds.
        TaskControl.ControllerRead live = TaskControl.readController(() -> new String[] {"ready", "s1", "A"});
        expect(java.util.Arrays.equals(TaskControl.watchBinding("A", true, live), new String[] {"A", "s1"}), "Valid same-task read binds its session");
        expect(TaskControl.watchBinding("A", false, live) == null, "Task replaced between tap and open");
        expect(TaskControl.watchBinding("B", true, live) == null, "Wrong task identity never binds");
        expect(TaskControl.watchBinding("A", true, TaskControl.readController(() -> new String[] {"ready", "s2", "B"})) == null, "Session replaced by another task's");
        expect(TaskControl.watchBinding("A", true, TaskControl.readController(() -> new String[] {"ready", "s1", null})) == null, "Missing task identity never binds");
        expect(TaskControl.watchBinding("A", true, TaskControl.readController(() -> new String[] {"idle", null, null})) == null, "Idle never binds");
        expect(TaskControl.EXTRA_WATCH_TASK_ID.equals("dev.pixel.backgroundcontrol.extra.WATCH_TASK_ID")
                && TaskControl.EXTRA_WATCH_SESSION_ID.equals("dev.pixel.backgroundcontrol.extra.WATCH_SESSION_ID"),
                "Extra names agreed with the OS viewer (APP-OS-WATCH-002)");

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
