package dev.pixel.models;

import android.app.Activity;
import android.app.ActivityOptions;
import android.app.KeyguardManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.view.Display;
import java.util.ArrayList;
import java.util.List;

/**
 * The Android half of open_app: list launcher activities, read the keyguard, start one explicit
 * component. Adapted from the narrow launch path in Rose Agent's PhoneControl (9664372b); none of
 * its other actions are carried over. Called only from the foreground activity's UI thread.
 */
final class AndroidAppLauncher implements AllowlistLauncher.Platform {
    private final Activity activity;

    AndroidAppLauncher(Activity activity) {
        this.activity = activity;
    }

    @Override
    public List<AppAllowlist.Launchable> launcherActivities() {
        PackageManager pm = activity.getPackageManager();
        Intent query = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
        List<AppAllowlist.Launchable> out = new ArrayList<>();
        for (ResolveInfo info : pm.queryIntentActivities(query, 0)) {
            if (info.activityInfo == null || activity.getPackageName().equals(info.activityInfo.packageName)) {
                continue;
            }
            out.add(new AppAllowlist.Launchable(info.activityInfo.packageName, info.activityInfo.name,
                    String.valueOf(info.loadLabel(pm))));
        }
        return out;
    }

    @Override
    public boolean isLocked() {
        KeyguardManager keyguard = (KeyguardManager) activity.getSystemService(Context.KEYGUARD_SERVICE);
        return keyguard == null || keyguard.isKeyguardLocked();
    }

    @Override
    public void startExplicit(AppAllowlist.Launchable target) {
        Intent launch = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
                .setComponent(new ComponentName(target.packageName, target.activityName))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        activity.startActivity(launch,
                ActivityOptions.makeBasic().setLaunchDisplayId(Display.DEFAULT_DISPLAY).toBundle());
    }
}
