package dev.pixel.backgroundcontrol;

/** Small, deliberately device-independent fold policy; dimensions are physical display dp. */
final class PanePolicy {
    private Object shownSession;
    boolean shouldShow(Object session, boolean ready, int widthDp, int heightDp) {
        boolean expanded = isExpanded(widthDp, heightDp);
        if (!expanded || session == null) { shownSession = null; return false; }
        if (!ready || shownSession == session) return false;
        shownSession = session;
        return true;
    }
    static boolean isExpanded(int widthDp, int heightDp) {
        return Math.min(widthDp, heightDp) >= 600;
    }
}
