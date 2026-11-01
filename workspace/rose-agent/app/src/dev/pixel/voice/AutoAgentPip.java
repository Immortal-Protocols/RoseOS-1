package dev.pixel.voice;

import android.app.ActivityOptions;
import android.app.PictureInPictureParams;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.util.Rational;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import org.json.JSONObject;

/** Could reveal the existing phone viewer once, in picture-in-picture, when a background session becomes ready.
 * Off by default (AUTO-PIP-OFF-330): the work continues in the background and the viewer opens only when the owner asks
 * (a Watch from Rose's apps or the overview, or the session notification). The state is in memory only, so a fresh
 * install and an upgraded phone start the same way. */
final class AutoAgentPip {
    static final boolean ENABLED = false;
    private static final String CONTROLLER = "dev.pixel.backgroundcontrol";
    private static final ComponentName VIEWER = new ComponentName(CONTROLLER,
        CONTROLLER + ".AgentWorkspaceActivity");
    private static final Handler main = new Handler(Looper.getMainLooper());
    private static String requestedSession = "";
    private static String phase = "idle";
    private static String error = "";

    static synchronized void observe(Context context, String operation, JSONObject result) {
        if (!ENABLED) return;
        if (!(operation.equals("start") || operation.equals("get_state"))
                || !result.optString("status").equals("ready")) return;
        final String session = result.optString("sessionId");
        if (session.isEmpty() || session.equals("null") || session.equals(requestedSession)) return;
        // A viewer cannot appear over the keyguard. Leave this session unrequested so the
        // first observation after the owner unlocks reveals it.
        if (((android.app.KeyguardManager) context.getSystemService(Context.KEYGUARD_SERVICE))
                .isDeviceLocked()) return;
        try {
            ActivityInfo viewer = context.getPackageManager().getActivityInfo(VIEWER, 0);
            // The Fold alias uses its split-screen viewer. Leave that presentation unchanged.
            if (!(CONTROLLER + ".AgentPipActivity").equals(viewer.targetActivity)) return;
        } catch (Exception unavailable) { return; }
        requestedSession = session;
        phase = "scheduled";
        error = "";
        int width = result.optInt("width", 9), height = result.optInt("height", 20);
        final double ratio = Math.max(100.0 / 239, Math.min(239.0 / 100,
            height > 0 ? (double) width / height : 9.0 / 20));
        final Context app = context.getApplicationContext();
        main.post(() -> {
            try {
                // Recheck after the UI dispatch: a finished/replaced session must not pop up.
                android.os.Bundle reply = app.getContentResolver().call(
                    android.net.Uri.parse("content://" + CONTROLLER + ".agent"),
                    "get_state", null, null);
                JSONObject current = new JSONObject(reply.getString("result", "{}"));
                if (!session.equals(current.optString("sessionId"))
                        || !current.optString("status").equals("ready")) {
                    synchronized (AutoAgentPip.class) { phase = "session_ended"; }
                    return;
                }
                PictureInPictureParams params = new PictureInPictureParams.Builder()
                    .setAspectRatio(new Rational((int) Math.round(ratio * 10000), 10000))
                    .setSeamlessResizeEnabled(false).build();
                app.startActivity(new Intent().setComponent(VIEWER)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                    ActivityOptions.makeLaunchIntoPip(params).toBundle());
                synchronized (AutoAgentPip.class) { phase = "launch_requested"; }
            } catch (Exception failure) {
                synchronized (AutoAgentPip.class) {
                    phase = "failed";
                    error = failure.toString();
                }
                Log.w("PixelAgentPip", "Automatic viewer launch failed", failure);
            }
        });
    }

    static synchronized JSONObject status() throws Exception {
        boolean visible = false;
        PhoneAccessibility accessibility = PhoneAccessibility.active;
        if (accessibility != null) {
            for (AccessibilityWindowInfo window : accessibility.getWindows()) {
                if (window.isInPictureInPictureMode()) {
                    AccessibilityNodeInfo root = window.getRoot();
                    try {
                        visible |= root != null && CONTROLLER.contentEquals(root.getPackageName())
                            || "Rose Agent Agent".equals(String.valueOf(window.getTitle()));
                    } finally { if (root != null) root.recycle(); }
                }
            }
        }
        return new JSONObject().put("autoShow", ENABLED).put("phase", phase)
            .put("sessionId", requestedSession).put("visibleInAccessibility", visible)
            .put("visibilityNote", "Protected viewer windows may be absent from accessibility")
            .put("error", error);
    }
}
