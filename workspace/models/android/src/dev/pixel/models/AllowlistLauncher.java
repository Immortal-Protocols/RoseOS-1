package dev.pixel.models;

import java.util.List;

/**
 * open_app policy: allowlist, installed-component resolution and lock check, then one explicit
 * launch through the platform. The platform part is the only Android-specific piece.
 */
public final class AllowlistLauncher implements AppLauncher {
    /** Narrow platform surface, implemented by AndroidAppLauncher and by tests. */
    public interface Platform {
        List<AppAllowlist.Launchable> launcherActivities();

        boolean isLocked();

        /** Starts exactly this explicit component; throws on platform failure. */
        void startExplicit(AppAllowlist.Launchable target);
    }

    private final AppAllowlist allowlist;
    private final Platform platform;

    public AllowlistLauncher(AppAllowlist allowlist, Platform platform) {
        this.allowlist = allowlist;
        this.platform = platform;
    }

    @Override
    public LaunchResult launch(String appId) {
        AppAllowlist.Resolution r = allowlist.resolve(appId, platform.launcherActivities());
        if (r.refusal != null) {
            return new LaunchResult(appId, r.refusal, r.detail);
        }
        if (platform.isLocked()) {
            return new LaunchResult(appId, LaunchResult.Status.LOCKED, "unlock the phone first");
        }
        try {
            platform.startExplicit(r.target);
        } catch (RuntimeException e) {
            return new LaunchResult(appId, LaunchResult.Status.FAILED, e.getClass().getSimpleName());
        }
        return new LaunchResult(appId, LaunchResult.Status.LAUNCH_REQUESTED, r.target.label);
    }
}
