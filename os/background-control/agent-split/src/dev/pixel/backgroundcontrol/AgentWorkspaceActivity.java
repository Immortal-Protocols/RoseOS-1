package dev.pixel.backgroundcontrol;

import android.content.ComponentName;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;

/** Launch the live mirror first, then the human app picker adjacent on the right. */
public final class AgentWorkspaceActivity extends AgentPaneActivity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private boolean showPicker;

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        showPicker = saved == null && getIntent().getBooleanExtra("showAppPicker", false);
    }
    @Override public void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        showPicker = intent.getBooleanExtra("showAppPicker", false);
        showPicker();
    }
    @Override protected void onResume() { super.onResume(); showPicker(); }
    private void showPicker() {
        if (!showPicker) return;
        showPicker = false;
        // Launch from this Activity, not the application context: Android needs the
        // source task to create a split with this mirror on the left.
        handler.postDelayed(() -> {
            if (isFinishing() || isDestroyed() || !PaneCoordinator.expanded(this)) return;
            startActivity(new Intent().setComponent(new ComponentName("dev.pixel.voice",
                "dev.pixel.voice.WorkspaceAppsActivity"))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_LAUNCH_ADJACENT));
        }, 250);
    }
    @Override public void onDestroy() { handler.removeCallbacksAndMessages(null); super.onDestroy(); }
}
