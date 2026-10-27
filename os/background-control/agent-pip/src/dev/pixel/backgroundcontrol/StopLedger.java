package dev.pixel.backgroundcontrol;

import java.util.Iterator;
import java.util.LinkedHashSet;
import java.util.regex.Pattern;

/** Pure record of why the last session ended, so a restart cannot undo the owner's Stop. */
final class StopLedger {
    static final String USER = "user", AGENT = "agent", DEADLINE = "deadline";
    static final int REMEMBERED_TASKS = 32;
    private static final Pattern TASK_ID = Pattern.compile("[A-Za-z0-9._:-]{1,128}");
    private final LinkedHashSet<String> userStopped = new LinkedHashSet<>();
    private String reason = "", sessionId, taskId;

    /** Null when absent; rejects anything that is not a short opaque identifier. */
    static String taskId(String value) {
        if (value == null) return null;
        if (!TASK_ID.matcher(value).matches()) throw new IllegalArgumentException("Invalid taskId");
        return value;
    }

    void stopped(String why, String session, String task) {
        if (!USER.equals(why) && !AGENT.equals(why) && !DEADLINE.equals(why))
            throw new IllegalArgumentException("Unknown stop reason");
        // A later agent cleanup of the same session must not hide that the owner stopped it.
        if (USER.equals(reason) && !USER.equals(why) && java.util.Objects.equals(sessionId, session)) return;
        reason = why; sessionId = session; taskId = task;
        if (USER.equals(why) && task != null) {
            userStopped.remove(task);
            userStopped.add(task);
            if (userStopped.size() > REMEMBERED_TASKS) {
                Iterator<String> oldest = userStopped.iterator();
                oldest.next();
                oldest.remove();
            }
        }
    }

    /** A new session starts; the owner's earlier Stop of a task still refuses that task. */
    void started() { reason = ""; sessionId = null; taskId = null; }

    boolean refuses(String task) { return task != null && userStopped.contains(task); }
    String reason() { return reason; }
    String sessionId() { return sessionId; }
    String taskId() { return taskId; }
}
