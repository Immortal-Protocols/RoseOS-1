package dev.pixel.voice;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.ResolveInfo;
import android.net.Uri;
import android.os.Bundle;
import android.widget.Toast;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONObject;

/** Opening the agent's app reveals its existing display instead of taking that app away. */
final class WorkspaceLauncher {
    private static final ExecutorService worker = Executors.newSingleThreadExecutor();

    static void open(Activity activity, ResolveInfo app, String label) {
        final String pkg = app.activityInfo.packageName;
        worker.execute(() -> {
            boolean inWorkspace = false;
            try {
                Bundle reply = activity.getContentResolver().call(
                    Uri.parse("content://dev.pixel.backgroundcontrol.agent"),
                    "get_state", null, null);
                JSONObject state = new JSONObject(reply.getString("result", "{}"));
                String phase = state.optString("status");
                inWorkspace = (phase.equals("ready") || phase.equals("blocked") || phase.equals("launching"))
                    && !state.isNull("sessionId") && !state.optString("sessionId").isEmpty()
                    && (pkg.equals(state.optString("targetPackage"))
                        || pkg.equals(state.optString("observedPackage")));
            } catch (Exception ignored) { /* Ordinary launch when no controller is installed. */ }
            final boolean reveal = inWorkspace;
            activity.runOnUiThread(() -> {
                if (activity.isFinishing() || activity.isDestroyed()) return;
                try {
                    if (reveal) {
                        if (activity instanceof WorkspaceAppsActivity && activity.isInMultiWindowMode()) return;
                        activity.startActivity(new Intent()
                            .setComponent(new ComponentName("dev.pixel.backgroundcontrol",
                                "dev.pixel.backgroundcontrol.AgentWorkspaceActivity"))
                            .putExtra("showAppPicker", true)
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                    } else {
                        Intent launch = new Intent(Intent.ACTION_MAIN)
                            .addCategory(Intent.CATEGORY_LAUNCHER)
                            .setComponent(new ComponentName(pkg, app.activityInfo.name));
                        // The right-hand picker is a normal Activity task. Keeping its task
                        // lets the selected app replace just that pane; NEW_TASK exits split.
                        if (!(activity instanceof WorkspaceAppsActivity))
                            launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                        activity.startActivity(launch);
                    }
                } catch (Exception e) {
                    Toast.makeText(activity, "Could not open " + label, Toast.LENGTH_SHORT).show();
                }
            });
        });
    }
}
