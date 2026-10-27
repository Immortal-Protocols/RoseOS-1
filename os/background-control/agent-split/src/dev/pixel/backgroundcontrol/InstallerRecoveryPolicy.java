package dev.pixel.backgroundcontrol;

/** Only a Play Store session interrupted by the system installer may retry. */
final class InstallerRecoveryPolicy {
    static boolean shouldRequest(boolean hasSession, String target, boolean blocked,
            int reason, String blocker, int installerReason) {
        return hasSession && blocked && reason == installerReason
            && "com.android.vending".equals(target)
            && "com.android.packageinstaller".equals(blocker);
    }
}
