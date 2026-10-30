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
    /** A selection request (#156) from the overview's agent card: Android facts, never Rose IDs. */
    static final String EXTRA_SELECT_DISPLAY = "dev.pixel.backgroundcontrol.extra.SELECT_DISPLAY_ID";
    static final String EXTRA_SELECT_PACKAGE = "dev.pixel.backgroundcontrol.extra.SELECT_PACKAGE";
    /** Opaque identifiers: the controller's taskId form (StopLedger) and its UUID session ids both fit. */
    private static final Pattern ID = Pattern.compile("[A-Za-z0-9._:-]{1,128}");
    private static final Pattern PACKAGE = Pattern.compile("[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z][A-Za-z0-9_]*)+");

    /** The controller's own live facts, read under its lock, against which a selection is resolved. */
    interface Live { WatchBinding resolve(int display, String packageName); }

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

    /**
     * Any entry. A selection request (both SELECT extras, display an Integer, package a String) is resolved by
     * the controller into its own Watch pair; Watch extras behave as fromExtras; mixing the two, or a partial or
     * malformed request, is invalid. Neither kind is a legacy unbound entry.
     */
    static WatchBinding fromEntry(boolean hasTask, String task, boolean hasSession, String session,
            boolean hasDisplay, Object display, boolean hasPackage, Object packageName, Live live) {
        boolean selecting = hasDisplay || hasPackage, watching = hasTask || hasSession;
        if (!selecting) return fromExtras(hasTask, task, hasSession, session);
        if (watching || !(display instanceof Integer) || !(packageName instanceof String)) return INVALID;
        int id = (Integer) display;
        String pkg = (String) packageName;
        if (id <= 0 || !PACKAGE.matcher(pkg).matches()) return INVALID;
        WatchBinding resolved = live.resolve(id, pkg);
        return resolved == null || resolved.mode != Mode.BOUND ? INVALID : resolved;
    }

    /**
     * The controller's decision for a selection, from its own state read under its lock: only a ready live
     * session whose own display (from its accessibility windows) is the requested one, whose target package is
     * the requested one and which has an opaque task id is bound, to that session's own pair. Anything else,
     * including a session replaced since the card was read (it has a new display), is invalid.
     */
    static WatchBinding fromSelection(int display, String packageName, boolean live, String status,
            int sessionDisplay, String target, String session, String task) {
        if (!live || !"ready".equals(status) || display <= 0 || display != sessionDisplay) return INVALID;
        if (packageName == null || !packageName.equals(target) || !valid(task) || !valid(session)) return INVALID;
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
