package dev.pixel.backgroundcontrol;

import android.app.Activity;
import android.content.res.Configuration;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.net.Uri;
import android.view.View;
import android.view.ViewGroup;
import android.view.SurfaceView;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.TextView;
import java.lang.reflect.Method;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** A real live SurfaceControl mirror of the existing virtual workspace, with no touch routing. */
public class AgentPaneActivity extends Activity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private View mirror;
    private Method setSession;
    private Object attached;
    private String mirrorError = "";
    private boolean checking;
    private AgentPaneFrame frame;
    private final ExecutorService taskStatusReader = Executors.newSingleThreadExecutor();
    private boolean taskStatusPending;
    private long lastTaskStatusRead;
    private void event(String action) {
        android.util.Log.i("FoldAgentPane", "elapsedMs=" + android.os.SystemClock.elapsedRealtime()
            + " event=" + action + " sessionId=" + Controller.id
            + " multiWindow=" + isInMultiWindowMode() + " focused=" + hasWindowFocus());
    }

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        // The viewer never takes keyboard focus from the human's app.
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE
            | WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE);
        getWindow().setDecorFitsSystemWindows(false);
        getWindow().setStatusBarColor(Color.TRANSPARENT);
        getWindow().setNavigationBarColor(Color.TRANSPARENT);
        getWindow().setNavigationBarContrastEnforced(false);
        frame = new AgentPaneFrame(this);
        try {
            Class<?> type = Class.forName(Controller.EXT + "view.MirrorView");
            MirrorWindowContext mirrorContext = new MirrorWindowContext(this);
            mirror = (View)type.getConstructor(android.content.Context.class).newInstance(mirrorContext);
            mirrorContext.setMirror(mirror);
            setSession = type.getMethod("setComputerControlSession",
                Class.forName(Controller.EXT + "ComputerControlSession"));
            type.getMethod("setInteractive", boolean.class).invoke(mirror, false);
            type.getMethod("setScaleType", int.class).invoke(mirror, 1); // FIT_CENTER
            // MirrorView defaults to composition order 0, above its Activity window.
            // Place its video surface below the window so the edge glow can overlay it.
            placeMirrorBelowOverlay(mirror);
            // Preserve the whole app image. The glow overlays it rather than taking space.
            frame.addView(mirror, new FrameLayout.LayoutParams(-1, -1));
        } catch (Throwable e) {
            mirrorError = Controller.root(e).toString();
            TextView unavailable = new TextView(this);
            unavailable.setText("Agent view unavailable");
            frame.addView(unavailable);
            android.util.Log.e("FoldAgentPane", "Mirror creation failed", e);
        }
        setContentView(frame);
    }
    private static void placeMirrorBelowOverlay(View view) throws Exception {
        if (view instanceof SurfaceView) {
            SurfaceView.class.getMethod("setCompositionOrder", int.class).invoke(view, -1);
        }
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++)
                placeMirrorBelowOverlay(group.getChildAt(i));
        }
    }
    private void detach() {
        if (setSession != null && mirror != null) {
            try { setSession.invoke(mirror, new Object[]{null}); attached = null; event("detach_requested"); }
            catch (Throwable e) { mirrorError = Controller.root(e).toString(); }
        }
    }
    private final Runnable refresh = new Runnable() {
        public void run() {
            if (!checking) return;
            if (!PaneCoordinator.expanded(AgentPaneActivity.this)) {
                detach(); finishAndRemoveTask(); return;
            }
            Object current;
            synchronized (Controller.class) {
                current = Controller.session;
            }
            frame.setAgentActive(current != null);
            refreshTaskActivity();
            if (setSession != null && attached != current) {
                try { setSession.invoke(mirror, new Object[]{current}); attached = current; event("attach_requested"); }
                catch (Throwable e) { mirrorError = Controller.root(e).toString(); }
            }
            handler.postDelayed(this, 500);
        }
    };
    private void refreshTaskActivity() {
        if (taskStatusPending || SystemClock.elapsedRealtime() - lastTaskStatusRead < 1000) return;
        taskStatusPending = true;
        lastTaskStatusRead = SystemClock.elapsedRealtime();
        taskStatusReader.execute(() -> {
            boolean active = false;
            try {
                Bundle state = getContentResolver().call(Uri.parse("content://dev.pixel.voice.agent"),
                    "workspace_activity", null, null);
                active = state != null && state.getBoolean("available") && state.getBoolean("running");
            } catch (Exception ignored) { }
            final boolean running = active;
            handler.post(() -> {
                taskStatusPending = false;
                if (checking && !isDestroyed()) frame.setTaskRunning(running);
            });
        });
    }
    // Multi-resume and temporary loss of focus must not hide a pane that is still visible.
    @Override public void onStart() { super.onStart(); PaneCoordinator.visibilityChanged(true); checking = true; handler.post(refresh); }
    @Override public void onStop() { PaneCoordinator.visibilityChanged(false); checking = false; handler.removeCallbacks(refresh); detach(); super.onStop(); }
    @Override public void onDestroy() { checking = false; handler.removeCallbacks(refresh); taskStatusReader.shutdownNow(); detach(); super.onDestroy(); }
    @Override public void onWindowFocusChanged(boolean focus) { super.onWindowFocusChanged(focus); event("focus_changed"); }
    @Override public void onMultiWindowModeChanged(boolean inMultiWindow, Configuration config) { super.onMultiWindowModeChanged(inMultiWindow, config); event("split_changed"); }
    @Override public void onConfigurationChanged(Configuration config) {
        super.onConfigurationChanged(config);
        if (!PaneCoordinator.expanded(this)) { detach(); finishAndRemoveTask(); }
    }
}
