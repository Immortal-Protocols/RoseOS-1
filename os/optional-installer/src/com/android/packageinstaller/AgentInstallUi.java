package com.android.packageinstaller;

import android.app.Activity;
import android.app.Application;
import android.content.pm.PackageInstaller;
import android.os.Bundle;
import java.lang.ref.WeakReference;
import java.util.ArrayList;

/** Retires only the confirmation window for a session already verified installed. */
public final class AgentInstallUi implements Application.ActivityLifecycleCallbacks {
    private static final ArrayList<WeakReference<Activity>> activities = new ArrayList<>();
    public static void register(Application app) { app.registerActivityLifecycleCallbacks(new AgentInstallUi()); }
    public static void finishConfirmedSession(int sessionId) {
        for (WeakReference<Activity> ref : new ArrayList<>(activities)) {
            Activity activity = ref.get();
            if (activity == null || activity.isFinishing() || activity.isDestroyed()) continue;
            if (!(activity instanceof com.android.packageinstaller.v2.ui.InstallLaunch)
                    && !(activity instanceof PackageInstallerActivity)) continue;
            if (PackageInstaller.ACTION_CONFIRM_INSTALL.equals(activity.getIntent().getAction())
                    && activity.getIntent().getIntExtra(PackageInstaller.EXTRA_SESSION_ID, -1) == sessionId) {
                activity.setResult(Activity.RESULT_OK);
                activity.finish();
            }
        }
    }
    @Override public void onActivityCreated(Activity a, Bundle b) { activities.add(new WeakReference<>(a)); }
    @Override public void onActivityDestroyed(Activity a) { activities.removeIf(ref -> ref.get() == null || ref.get() == a); }
    @Override public void onActivityStarted(Activity a) {}
    @Override public void onActivityResumed(Activity a) {}
    @Override public void onActivityPaused(Activity a) {}
    @Override public void onActivityStopped(Activity a) {}
    @Override public void onActivitySaveInstanceState(Activity a, Bundle b) {}
}
