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

/** A PiP presentation of the existing session. Closing it never closes the agent. */
public final class AgentPipActivity extends Activity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private View mirror;
    private Method setSession;
    private Object attached;
    private FrameLayout frame;
    private boolean visible;

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        if (!getPackageManager().hasSystemFeature(PackageManager.FEATURE_PICTURE_IN_PICTURE)) {
            fail("Picture-in-picture is unavailable on this phone", null);
            return;
        }
        synchronized (Controller.class) {
            if (Controller.session == null) {
                fail("Start an agent task first, then tap its notification to watch", null);
                return;
            }
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
            frame.post(() -> enterFloatingView());
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
        if (frame != null) frame.post(() -> enterFloatingView());
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
            synchronized (Controller.class) { current = Controller.session; }
            if (current == null) { finishAndRemoveTask(); return; }
            if (current != attached) {
                try {
                    setSession.invoke(mirror, current);
                    attached = current;
                    android.util.Log.i("PixelAgentPip", "attached session=" + Controller.id);
                } catch (Throwable error) { fail("The agent view could not attach", error); return; }
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
