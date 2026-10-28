package dev.pixel.backgroundcontrol;

import java.util.regex.Pattern;

/**
 * Which controller session the viewer may show. Rose Agent's Watch names the selected task and the
 * session it observed (APP-OS-WATCH-002): the viewer then shows only that exact pair and closes when the
 * controller's current session or task differs, instead of following whatever is current. Closing the
 * viewer never stops the work. Pure logic, no Android types.
 */
final class WatchBinding {
    static final String EXTRA_TASK = "dev.pixel.backgroundcontrol.extra.WATCH_TASK_ID";
    static final String EXTRA_SESSION = "dev.pixel.backgroundcontrol.extra.WATCH_SESSION_ID";
    /** Opaque identifiers: the controller's taskId form (StopLedger) and its UUID session ids both fit. */
    private static final Pattern ID = Pattern.compile("[A-Za-z0-9._:-]{1,128}");

    enum Mode { UNBOUND, BOUND, INVALID }
    static final WatchBinding UNBOUND = new WatchBinding(Mode.UNBOUND, null, null);
    static final WatchBinding INVALID = new WatchBinding(Mode.INVALID, null, null);

    final Mode mode;
    final String task, session;

    private WatchBinding(Mode mode, String task, String session) {
        this.mode = mode; this.task = task; this.session = session;
    }

    /**
     * From an entry's extras. Neither extra is a legacy unbound entry (notification, app reveal). Any
     * extra present makes it a Watch: both must be valid, or the entry is invalid and never unbound.
     */
    static WatchBinding fromExtras(boolean hasTask, String task, boolean hasSession, String session) {
        if (!hasTask && !hasSession) return UNBOUND;
        if (!valid(task) || !valid(session)) return INVALID;
        return new WatchBinding(Mode.BOUND, task, session);
    }

    /** A new intent: an unbound one keeps the existing binding; a Watch, valid or not, replaces it. */
    static WatchBinding next(WatchBinding current, WatchBinding incoming) {
        if (incoming.mode == Mode.UNBOUND && current != null) return current;
        return incoming;
    }

    /** Whether the controller's current session may be shown. Checked at attach and on every refresh. */
    boolean allows(String currentSession, String currentTask) {
        switch (mode) {
            case UNBOUND: return true;
            case BOUND: return session.equals(currentSession) && task.equals(currentTask);
            default: return false;
        }
    }

    @Override public boolean equals(Object other) {
        if (!(other instanceof WatchBinding)) return false;
        WatchBinding o = (WatchBinding) other;
        return mode == o.mode && java.util.Objects.equals(task, o.task) && java.util.Objects.equals(session, o.session);
    }
    @Override public int hashCode() { return java.util.Objects.hash(mode, task, session); }

    private static boolean valid(String value) { return value != null && ID.matcher(value).matches(); }
}
