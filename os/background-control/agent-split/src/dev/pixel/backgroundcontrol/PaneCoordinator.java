package dev.pixel.backgroundcontrol;

import android.content.Context;
import android.content.Intent;
import android.graphics.Rect;
import android.os.Handler;
import android.os.Looper;
import android.view.WindowManager;

/** Owns presentation only. Never launches the target app, changes a session, or sends input. */
final class PaneCoordinator {
    private static final Handler handler = new Handler(Looper.getMainLooper());
    private static final PanePolicy policy = new PanePolicy();
    private static Context context;
    static String error = "";
    static int physicalWidthDp, physicalHeightDp;
    private static boolean scheduled;
    private static boolean paneVisible;
    static void visibilityChanged(boolean visible) { paneVisible = visible; }
    static void watch(Context c) {
        context = c.getApplicationContext();
        if (!scheduled) { scheduled = true; handler.post(tick); }
    }
    static boolean expanded(Context c) {
        // Maximum metrics describe the physical display, unlike a narrow split-screen window.
        Rect bounds = c.getSystemService(WindowManager.class).getMaximumWindowMetrics().getBounds();
        float density = c.getResources().getDisplayMetrics().density;
        physicalWidthDp = Math.round(bounds.width()/density);
        physicalHeightDp = Math.round(bounds.height()/density);
        return PanePolicy.isExpanded(physicalWidthDp, physicalHeightDp);
    }
    private static final Runnable tick = new Runnable() {
        public void run() {
            Object session; boolean ready;
            synchronized (Controller.class) {
                session = Controller.session;
                ready = "ready".equals(Controller.status) && !Controller.blocked;
            }
            try {
                expanded(context);
                boolean needsPane = policy.shouldShow(session, ready, physicalWidthDp, physicalHeightDp);
                // A visible pane's refresh loop attaches the new session itself. Bringing its
                // task forward again moves focus away from the human side of split screen.
                if (needsPane && !paneVisible) {
                    context.startActivity(new Intent(context, AgentPaneActivity.class)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_LAUNCH_ADJACENT
                            | Intent.FLAG_ACTIVITY_SINGLE_TOP));
                    error = "";
                }
            } catch (Throwable e) { error = Controller.root(e).toString(); }
            synchronized (Controller.class) {
                if (Controller.session != null || "starting".equals(Controller.status)
                    || "launching".equals(Controller.status) || "consent_required".equals(Controller.status)) {
                    handler.postDelayed(this, 750);
                } else { policy.shouldShow(null, false, 0, 0); scheduled = false; }
            }
        }
    };
}
