package dev.pixel.backgroundcontrol;

import android.app.Activity;
import android.app.PictureInPictureParams;
import android.content.pm.PackageManager;
import android.content.Intent;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Rect;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Rational;
import android.view.View;
import android.view.ViewGroup;
import android.view.SurfaceView;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.Toast;
import java.lang.reflect.Method;

/** A PiP presentation of the existing session. Closing it never closes the agent. A Watch from Rose
 * Agent binds it to one task and session (WatchBinding); it then closes rather than show any other. */
public final class AgentPipActivity extends Activity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private View mirror;
    private Method setSession;
    private Object attached;
    private FrameLayout frame;
    private boolean visible;
    private WatchBinding binding = WatchBinding.UNBOUND;
    private WatchBinding attachedBinding;
    // Full screen for an explicit Watch; picture-in-picture for the automatic show and unbound reopens (ViewerPresentation).
    private ViewerPresentation.Mode presentation;

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        // A recreated viewer keeps the pair it was bound to; its original intent is not re-read.
        boolean recreated = saved != null && saved.containsKey(KEY_MODE);
        binding = recreated ? restore(saved) : fromIntent(getIntent());
        if (binding.mode == WatchBinding.Mode.INVALID) { unavailable(); return; }
        presentation = recreated ? ViewerPresentation.restore(saved.getString(KEY_PRESENTATION))
            : ViewerPresentation.forEntry(binding.mode == WatchBinding.Mode.BOUND, null);
        if (!getPackageManager().hasSystemFeature(PackageManager.FEATURE_PICTURE_IN_PICTURE)) {
            fail("Picture-in-picture is unavailable on this phone", null);
            return;
        }
        synchronized (Controller.class) {
            if (Controller.session == null) {
                fail("Start an agent task first, then tap its notification to watch", null);
                return;
            }
            if (!binding.allows(Controller.id, Controller.taskId)) { unavailable(); return; }
        }
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        getWindow().setDecorFitsSystemWindows(false);
        getWindow().setStatusBarColor(Color.TRANSPARENT);
        getWindow().setNavigationBarColor(Color.TRANSPARENT);
        getWindow().setNavigationBarContrastEnforced(false);
        frame = new FrameLayout(this);
        frame.setBackgroundColor(Color.BLACK);
        try {
            Class<?> type = Class.forName(Controller.EXT + "view.MirrorView");
            MirrorWindowContext context = new MirrorWindowContext(this);
            mirror = (View) type.getConstructor(android.content.Context.class).newInstance(context);
            context.setMirror(mirror);
            setSession = type.getMethod("setComputerControlSession",
                Class.forName(Controller.EXT + "ComputerControlSession"));
            type.getMethod("setInteractive", boolean.class).invoke(mirror, false);
            type.getMethod("setScaleType", int.class).invoke(mirror, 1);
            placeBelowWindow(mirror);
            frame.addView(mirror, new FrameLayout.LayoutParams(-1, -1));
            setContentView(frame);
            if (ViewerPresentation.entersPip(presentation)) frame.post(() -> enterFloatingView());
        } catch (Throwable error) {
            fail("The agent view could not open", error);
        }
    }

    private PictureInPictureParams parameters() {
        int width, height;
        synchronized (Controller.class) { width = Controller.width; height = Controller.height; }
        if (width <= 0 || height <= 0) { width = 9; height = 20; }
        // Android's standard PiP window permits ratios from 1:2.39 to 2.39:1.
        double ratio = Math.max(100.0 / 239, Math.min(239.0 / 100, (double) width / height));
        PictureInPictureParams.Builder builder = new PictureInPictureParams.Builder()
            .setAspectRatio(new Rational((int) Math.round(ratio * 10000), 10000))
            .setAutoEnterEnabled(true)
            .setSeamlessResizeEnabled(false);
        Rect bounds = new Rect();
        if (frame != null && frame.getGlobalVisibleRect(bounds)) builder.setSourceRectHint(bounds);
        return builder.build();
    }

    private void enterFloatingView() {
        if (isFinishing() || isDestroyed() || isInPictureInPictureMode()) return;
        try {
            if (!enterPictureInPictureMode(parameters())) fail("Could not enter the floating view", null);
        } catch (Throwable error) { fail("Could not enter the floating view", error); }
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        // An unbound reopen keeps the current Watch; a new Watch replaces it only once validated below.
        WatchBinding incoming = fromIntent(intent);
        binding = WatchBinding.next(binding, incoming);
        if (binding.mode == WatchBinding.Mode.INVALID) { unavailable(); return; }
        // An explicit Watch is shown full screen and is never pushed back into the floating view here; launching the
        // viewer's task brings it out of picture-in-picture. An unbound reopen keeps the current presentation.
        presentation = ViewerPresentation.forEntry(incoming.mode == WatchBinding.Mode.BOUND, presentation);
        if (visible) { handler.removeCallbacks(refresh); handler.post(refresh); }
        if (frame != null && ViewerPresentation.entersPip(presentation)) frame.post(() -> enterFloatingView());
    }

    private static final String KEY_MODE = "watchMode", KEY_TASK = "watchTask", KEY_SESSION = "watchSession",
        KEY_PRESENTATION = "presentation";
    static WatchBinding fromIntent(Intent intent) {
        if (intent == null) return WatchBinding.UNBOUND;
        // getStringExtra returns null for a non-String value, which makes the Watch invalid. An overview selection
        // (#156) is resolved by the controller against its own live session into that session's Watch pair.
        Bundle extras = intent.getExtras();
        return WatchBinding.fromEntry(intent.hasExtra(WatchBinding.EXTRA_TASK), intent.getStringExtra(WatchBinding.EXTRA_TASK),
            intent.hasExtra(WatchBinding.EXTRA_SESSION), intent.getStringExtra(WatchBinding.EXTRA_SESSION),
            intent.hasExtra(WatchBinding.EXTRA_SELECT_DISPLAY), extras == null ? null : extras.get(WatchBinding.EXTRA_SELECT_DISPLAY),
            intent.hasExtra(WatchBinding.EXTRA_SELECT_PACKAGE), extras == null ? null : extras.get(WatchBinding.EXTRA_SELECT_PACKAGE),
            Controller::resolveSelection);
    }
    private static WatchBinding restore(Bundle saved) {
        if (!WatchBinding.Mode.BOUND.name().equals(saved.getString(KEY_MODE))) return WatchBinding.UNBOUND;
        return WatchBinding.fromExtras(true, saved.getString(KEY_TASK), true, saved.getString(KEY_SESSION));
    }
    @Override protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putString(KEY_MODE, binding.mode.name());
        if (presentation != null) out.putString(KEY_PRESENTATION, presentation.name());
        if (binding.mode == WatchBinding.Mode.BOUND) { out.putString(KEY_TASK, binding.task); out.putString(KEY_SESSION, binding.session); }
    }
    /** The selected task is not the one in the agent view now. Close the view only; the work continues. */
    private void unavailable() {
        detach();
        Toast.makeText(this, "That task is no longer in the agent view", Toast.LENGTH_SHORT).show();
        finishAndRemoveTask();
    }

    private static void placeBelowWindow(View view) throws Exception {
        if (view instanceof SurfaceView)
            SurfaceView.class.getMethod("setCompositionOrder", int.class).invoke(view, -1);
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) placeBelowWindow(group.getChildAt(i));
        }
    }

    private final Runnable refresh = new Runnable() {
        @Override public void run() {
            if (!visible || isFinishing() || setSession == null) return;
            Object current;
            boolean allowed;
            synchronized (Controller.class) {
                current = Controller.session;
                allowed = binding.allows(Controller.id, Controller.taskId);
            }
            if (current == null) { finishAndRemoveTask(); return; }
            // Same session re-tagged to another task, or a different session: never follow it.
            if (!allowed) { unavailable(); return; }
            if (current != attached || !binding.equals(attachedBinding)) {
                try {
                    setSession.invoke(mirror, current);
                    attached = current;
                    attachedBinding = binding;
                } catch (Throwable error) { fail("The agent view could not attach", error); return; }
                // The controller may have moved on while attaching; re-check before showing a frame.
                synchronized (Controller.class) {
                    allowed = Controller.session == current && binding.allows(Controller.id, Controller.taskId);
                }
                if (!allowed) { unavailable(); return; }
                android.util.Log.i("PixelAgentPip", "attached session=" + Controller.id);
            }
            handler.postDelayed(this, 500);
        }
    };

    private void detach() {
        if (setSession != null && attached != null) {
            try { setSession.invoke(mirror, new Object[]{null}); }
            catch (Throwable error) { android.util.Log.w("PixelAgentPip", "detach", Controller.root(error)); }
            attached = null;
        }
        attachedBinding = null;
    }
    private void fail(String message, Throwable error) {
        if (error != null) android.util.Log.e("PixelAgentPip", message, Controller.root(error));
        Toast.makeText(this, message, Toast.LENGTH_LONG).show();
        finishAndRemoveTask();
    }
    @Override protected void onStart() {
        super.onStart(); visible = true; handler.post(refresh);
    }
    // A visible PiP Activity is paused. Keep rendering until it actually stops.
    @Override protected void onStop() {
        visible = false; handler.removeCallbacks(refresh); detach(); super.onStop();
    }
    @Override protected void onDestroy() {
        visible = false; handler.removeCallbacksAndMessages(null); detach(); super.onDestroy();
    }
    @Override public void onConfigurationChanged(Configuration config) {
        super.onConfigurationChanged(config);
        if (!isFinishing()) setPictureInPictureParams(parameters());
    }
    @Override public void onPictureInPictureModeChanged(boolean inPip, Configuration config) {
        super.onPictureInPictureModeChanged(inPip, config);
        android.util.Log.i("PixelAgentPip", "pictureInPicture=" + inPip);
    }
}
