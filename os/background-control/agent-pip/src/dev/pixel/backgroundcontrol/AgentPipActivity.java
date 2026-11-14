package dev.pixel.backgroundcontrol;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.PictureInPictureParams;
import android.content.pm.PackageManager;
import android.content.Intent;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Rect;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Rational;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.SurfaceView;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.Toast;
import java.lang.reflect.Method;

/** The viewer of the existing session, full screen by default (automatic picture-in-picture is off; see
 * ViewerPresentation). Closing it never closes the agent. A Watch from Rose Agent binds it to one task and session
 * (WatchBinding); it then closes rather than show any other. A bound full-screen viewer also offers the owner's Stop of
 * exactly that pair (#410, Nathan-approved): only its confirmation's positive button stops, after the controller rechecks
 * the pair (Controller.viewerStop); Back, Home, picture-in-picture, closing and every lifecycle path still stop nothing. */
public final class AgentPipActivity extends Activity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private View mirror;
    private Method setSession;
    private Object attached;
    private FrameLayout frame;
    private boolean visible;
    private WatchBinding binding = WatchBinding.UNBOUND;
    private WatchBinding attachedBinding;
    // Full screen for every entry while automatic picture-in-picture is off (ViewerPresentation.AUTOMATIC_PIP).
    private ViewerPresentation.Mode presentation;
    // The bound pair's Stop (#410): the button, its confirmation, and one confirmed Stop at a time.
    private Button stopButton;
    private AlertDialog confirm;
    private boolean stopping;
    private String appLabel = "this app";

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        // A recreated viewer keeps the pair it was bound to; its original intent is not re-read.
        boolean recreated = saved != null && saved.containsKey(KEY_MODE);
        binding = recreated ? restore(saved) : fromIntent(getIntent());
        if (binding.mode == WatchBinding.Mode.INVALID) { unavailable(); return; }
        presentation = recreated ? ViewerPresentation.restore(saved.getString(KEY_PRESENTATION))
            : ViewerPresentation.forEntry(binding.mode == WatchBinding.Mode.BOUND, null);
        // Only the floating presentation needs picture-in-picture; the full-screen viewer works without it.
        if (ViewerPresentation.entersPip(presentation)
                && !getPackageManager().hasSystemFeature(PackageManager.FEATURE_PICTURE_IN_PICTURE)) {
            fail("Picture-in-picture is unavailable on this phone", null);
            return;
        }
        synchronized (Controller.class) {
            // The bound session's own slot, or slot 0 for an unbound entry while no other session is live (never a guess).
            Controller.Slot viewed = Controller.viewed(binding);
            if (viewed == null) { unavailable(); return; }
            if (viewed.session == null) {
                fail("Start an agent task first, then tap its notification to watch", null);
                return;
            }
            if (!binding.allows(viewed.id, viewed.taskId)) { unavailable(); return; }
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
            if (binding.mode == WatchBinding.Mode.BOUND) addStopButton();
            setContentView(frame);
            if (ViewerPresentation.entersPip(presentation)) frame.post(() -> enterFloatingView());
        } catch (Throwable error) {
            fail("The agent view could not open", error);
        }
    }

    private PictureInPictureParams parameters() {
        int width, height;
        synchronized (Controller.class) { Controller.Slot viewed = Controller.viewed(binding); width = viewed == null ? 0 : viewed.width; height = viewed == null ? 0 : viewed.height; }
        if (width <= 0 || height <= 0) { width = 9; height = 20; }
        // Android's standard PiP window permits ratios from 1:2.39 to 2.39:1.
        double ratio = Math.max(100.0 / 239, Math.min(239.0 / 100, (double) width / height));
        PictureInPictureParams.Builder builder = new PictureInPictureParams.Builder()
            .setAspectRatio(new Rational((int) Math.round(ratio * 10000), 10000))
            // Leaving a full-screen viewer (Home, another app) never floats it on its own.
            .setAutoEnterEnabled(ViewerPresentation.AUTOMATIC_PIP)
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
        // A question asked about one pair never answers for another.
        if (!WatchBinding.next(binding, incoming).equals(binding)) dismissConfirm();
        binding = WatchBinding.next(binding, incoming);
        if (binding.mode == WatchBinding.Mode.INVALID) { unavailable(); return; }
        // Every entry is shown full screen and is never pushed back into the floating view here; launching the viewer's
        // task brings it out of picture-in-picture.
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
        dismissConfirm();
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
            String target;
            boolean allowed;
            boolean ambiguous;
            synchronized (Controller.class) {
                Controller.Slot viewed = Controller.viewed(binding);
                ambiguous = viewed == null;
                current = viewed == null ? null : viewed.session;
                allowed = viewed != null && binding.allows(viewed.id, viewed.taskId);
                target = viewed == null ? null : viewed.target;
            }
            if (ambiguous) { unavailable(); return; }
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
                    Controller.Slot viewed = Controller.viewed(binding);
                    allowed = viewed != null && viewed.session == current && binding.allows(viewed.id, viewed.taskId);
                }
                if (!allowed) { unavailable(); return; }
                android.util.Log.i("PixelAgentPip", "attached " + (binding.mode == WatchBinding.Mode.BOUND ? "bound" : "unbound") + " session");
                appLabel = label(target);
            }
            updateStop();
            handler.postDelayed(this, 500);
        }
    };

    /** The bound pair's Stop button: shown only full screen, while attached to that pair, and not during a Stop. */
    private void addStopButton() {
        stopButton = new Button(this);
        stopButton.setText(ViewerStop.BUTTON);
        stopButton.setFilterTouchesWhenObscured(true);
        stopButton.setVisibility(View.GONE);
        stopButton.setOnClickListener(v -> confirmStop());
        int margin = (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, 16, getResources().getDisplayMetrics());
        FrameLayout.LayoutParams place = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.END);
        place.setMargins(margin, margin, margin, margin);
        frame.addView(stopButton, place);
        // Edge to edge: keep the button clear of the system bars.
        frame.setOnApplyWindowInsetsListener((view, insets) -> {
            Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
            FrameLayout.LayoutParams at = (FrameLayout.LayoutParams) stopButton.getLayoutParams();
            at.setMargins(margin + bars.left, margin + bars.top, margin + bars.right, margin + bars.bottom);
            stopButton.setLayoutParams(at);
            return insets;
        });
    }
    private void updateStop() {
        if (stopButton == null) return;
        boolean show = attached != null && !stopping && !isInPictureInPictureMode() && !isFinishing();
        stopButton.setVisibility(show ? View.VISIBLE : View.GONE);
        if (show) stopButton.setContentDescription(ViewerStop.description(appLabel));
    }
    private String label(String packageName) {
        if (packageName == null) return "this app";
        try { return String.valueOf(getPackageManager().getApplicationLabel(getPackageManager().getApplicationInfo(packageName, 0))); }
        catch (Throwable error) { return "this app"; }
    }
    /** The owner's explicit Stop asks first; Keep working, Back, a tap outside or the viewer stopping dismiss it and stop nothing. */
    private void confirmStop() {
        if (stopping || attached == null || (confirm != null && confirm.isShowing())) return;
        Object seen = attached;
        WatchBinding asked = binding;
        confirm = new AlertDialog.Builder(this).setTitle(ViewerStop.title(appLabel)).setMessage(ViewerStop.MESSAGE)
            .setNegativeButton(ViewerStop.KEEP, null)
            .setPositiveButton(ViewerStop.BUTTON, (dialog, which) -> stopConfirmed(seen, asked))
            .create();
        confirm.setOnShowListener(dialog -> {
            confirm.getButton(AlertDialog.BUTTON_POSITIVE).setFilterTouchesWhenObscured(true);
            confirm.getButton(AlertDialog.BUTTON_NEGATIVE).setFilterTouchesWhenObscured(true);
        });
        confirm.show();
    }
    private void dismissConfirm() {
        if (confirm != null && confirm.isShowing()) confirm.dismiss();
        confirm = null;
    }
    /** The only path to a Stop: once per confirmation, for the pair and session object it asked about, still bound and
     * attached (a new Watch or another attached session since then passes none, so the controller refuses). */
    private void stopConfirmed(Object seen, WatchBinding asked) {
        if (stopping || isFinishing()) return;
        stopping = true;
        updateStop();
        String status;
        boolean confirmed;
        try {
            org.json.JSONObject reply = Controller.viewerStop(this, asked, seen == attached && asked.equals(binding) ? seen : null);
            status = reply.optString("status");
            confirmed = reply.optBoolean("confirmed");
        } catch (Throwable error) {
            android.util.Log.e("PixelAgentPip", "viewer stop", Controller.root(error));
            status = "error";
            confirmed = false;
        }
        ViewerStop.Outcome outcome = ViewerStop.of(status, confirmed);
        Toast.makeText(this, outcome.text, Toast.LENGTH_LONG).show();
        if (outcome.closeViewer) { detach(); finishAndRemoveTask(); return; }
        stopping = false;
        updateStop();
    }

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
        visible = false; handler.removeCallbacks(refresh); dismissConfirm(); detach(); super.onStop();
    }
    @Override protected void onDestroy() {
        visible = false; handler.removeCallbacksAndMessages(null); dismissConfirm(); detach(); super.onDestroy();
    }
    @Override public void onConfigurationChanged(Configuration config) {
        super.onConfigurationChanged(config);
        if (!isFinishing()) setPictureInPictureParams(parameters());
    }
    @Override public void onPictureInPictureModeChanged(boolean inPip, Configuration config) {
        super.onPictureInPictureModeChanged(inPip, config);
        android.util.Log.i("PixelAgentPip", "pictureInPicture=" + inPip);
        if (inPip) dismissConfirm();
        updateStop();
    }
}
