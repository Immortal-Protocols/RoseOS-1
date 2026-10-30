package dev.pixel.voice;

import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageInstaller;
import java.io.File;

/** The Android side of UpdateRecovery for "Update Rose Agent": the persisted channel, timestamped attempt records in
 * the existing "self-update" preferences (target, state, plus at), recovery of an interrupted attempt, and the
 * dialog's text. The update files call these one-line entry points; all decisions are UpdateRecovery's. Nothing
 * here installs, downgrades, uninstalls, clears data or touches another package's session. */
final class UpdateAttempts {
 private UpdateAttempts() {}

 static final String PREFS = "self-update", CHANNEL = "update-channel";

 /** The owner's channel (fresh install Beta), repairing a corrupted stored value. */
 static DirectUpdate.Channel channel(Context c) {
  SharedPreferences p = c.getSharedPreferences(PREFS, 0);
  String stored = p.getString(CHANNEL, null);
  if (UpdateRecovery.repair(stored)) p.edit().putString(CHANNEL, UpdateRecovery.store(DirectUpdate.Channel.BETA)).apply();
  return UpdateRecovery.channel(stored);
 }
 static void setChannel(Context c, DirectUpdate.Channel channel) {
  c.getSharedPreferences(PREFS, 0).edit().putString(CHANNEL, UpdateRecovery.store(channel)).apply();
 }

 /** The existing record (target, state) with the time it was written. */
 static void record(Context c, long target, String state) {
  c.getSharedPreferences(PREFS, 0).edit().putLong("target", target).putString("state", state)
    .putLong("at", System.currentTimeMillis()).apply();
 }

 /** A PackageInstaller session of ours for this app, and whether one was committed. */
 private static boolean[] sessions(Context c) {
  boolean live = false, committed = false;
  for (PackageInstaller.SessionInfo s : c.getPackageManager().getPackageInstaller().getMySessions()) {
   if (!c.getPackageName().equals(s.getAppPackageName())) continue;
   live = true;
   committed |= android.os.Build.VERSION.SDK_INT >= 29 && s.isCommitted();
  }
  return new boolean[]{live, committed};
 }

 static UpdateRecovery.Attempt classify(Context c, long installed) {
  SharedPreferences p = c.getSharedPreferences(PREFS, 0);
  return UpdateRecovery.classify(p.getLong("target", -1), p.getString("state", ""), p.getLong("at", -1),
    System.currentTimeMillis(), installed, sessions(c)[0]);
 }

 /** Before a check: an interrupted attempt's own uncommitted sessions are abandoned and its download deleted,
  * and the record says so. Returns the attempt as it now stands. */
 static UpdateRecovery.Attempt recover(Context c, long installed) {
  UpdateRecovery.Attempt a = classify(c, installed);
  PackageInstaller installer = c.getPackageManager().getPackageInstaller();
  for (PackageInstaller.SessionInfo s : installer.getMySessions()) {
   boolean committed = android.os.Build.VERSION.SDK_INT >= 29 && s.isCommitted();
   if (UpdateRecovery.abandonSession(s.getAppPackageName(), c.getPackageName(), committed, a)) {
    try { installer.abandonSession(s.getSessionId()); } catch (SecurityException ignored) {}
   }
  }
  if (UpdateRecovery.deleteDownload(a)) {
   new File(c.getCacheDir(), UpdateRecovery.DOWNLOAD).delete();
   new File(c.getCacheDir(), UpdateRecovery.DOWNLOAD_SIGNATURE).delete();
  }
  return a;
 }

 /** The dialog line for the last attempt at `offered` (empty when that version was never attempted). */
 static String text(Context c, long offered, long installed) {
  SharedPreferences p = c.getSharedPreferences(PREFS, 0);
  if (p.getLong("target", -1) != offered) return "";
  UpdateRecovery.Attempt a = classify(c, installed);
  return UpdateRecovery.text(a, offered, installed, p.getString("state", ""));
 }
}
