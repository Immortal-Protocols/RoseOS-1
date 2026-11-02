package dev.pixel.voice;

/** Launcher update channel and recovery decisions (OS Product, October 30). DirectUpdate finds and checks an
 * offer and SelfUpdate admits, verifies and reports one install; this adds what survives a process death or a
 * bad update: the owner's persisted channel, an attempt that can no longer be in progress, leftovers to clean,
 * and what recovery is truthfully available. Pure logic, no Android types.
 *
 * Recovery contract. Android replaces the installed app only when a committed session succeeds, so a rejected,
 * corrupt, cancelled or interrupted update leaves the working version installed; this never says otherwise.
 * After a bad update the supported path is forward: a newer same-signer release on the same channel, installed
 * through the same checks. Nothing here downgrades, changes a signer, uninstalls, clears data or resets pairing,
 * and no retry is called a rollback. */
final class UpdateRecovery {
 private UpdateRecovery() {}

 /** An attempt still marked downloading or committed this long after it was recorded, with no live installer
  * session of ours, was interrupted (the process died or Android dropped it). */
 static final long STALE_MS = 15 * 60 * 1000;
 /** The download file Delivery.installSelf writes in the cache; anything left after a kill is stale. */
 // The owned temp files. #407: the signature is never DOWNLOAD's ".idsig" sibling: the platform's v4 verifier
 // (ApkSignatureSchemeV4Verifier.extractSignature, pinned frameworks/base) reads <apk>.idsig beside an APK it parses and then
 // requires fs-verity on that APK, which a cache file never has, so getPackageArchiveInfo would fail for a valid update.
 // LEGACY_SIGNATURE is that old sibling name, kept only so it is removed (an interrupted earlier attempt can leave it).
 static final String DOWNLOAD = "self-update.apk", DOWNLOAD_SIGNATURE = "self-update.v4sig", LEGACY_SIGNATURE = "self-update.apk.idsig";

 /** The persisted channel: a fresh install (nothing stored) is Beta, the only published channel; a stored
  * alpha, beta or prod is kept exactly, even while unavailable; anything else was corrupted and reads as Beta. */
 static DirectUpdate.Channel channel(String stored) {
  if (stored == null) return DirectUpdate.Channel.BETA;
  try { return DirectUpdate.Channel.parse(stored); } catch (IllegalArgumentException corrupt) { return DirectUpdate.Channel.BETA; }
 }
 /** Whether the stored value should be rewritten (only a corrupted one; a fresh install stores nothing). */
 static boolean repair(String stored) {
  return stored != null && !store(channel(stored)).equals(stored);
 }
 static String store(DirectUpdate.Channel c) { return c.name().toLowerCase(java.util.Locale.ROOT); }

 enum Attempt { NONE, IN_PROGRESS, WAITING_FOR_USER, INSTALLED, INTERRUPTED, CANCELLED, REJECTED }

 /**
  * The last recorded attempt, judged from the installed version first.
  * @param state       SelfUpdate's recorded state: downloading, committed, an Outcome name, or a failure reason
  * @param recordedAt  when it was recorded (wall clock ms), or -1 for records written before this field
  * @param liveSession a PackageInstaller session of ours for this app still exists
  */
 static Attempt classify(long target, String state, long recordedAt, long now, long installed, boolean liveSession) {
  if (target <= 0 || state == null || state.isEmpty()) return Attempt.NONE;
  if (installed >= target) return Attempt.INSTALLED;
  switch (state) {
   case "downloading": case "committed":
    if (liveSession) return Attempt.IN_PROGRESS;
    return recordedAt > 0 && now >= recordedAt && now - recordedAt < STALE_MS ? Attempt.IN_PROGRESS : Attempt.INTERRUPTED;
   case "WAITING_FOR_USER": return liveSession ? Attempt.WAITING_FOR_USER : Attempt.INTERRUPTED;
   case "CANCELLED": return Attempt.CANCELLED;
   case "INSTALLED": return Attempt.INTERRUPTED; // reported, but the installed version says it did not happen
   default: return Attempt.REJECTED;
  }
 }

 /** Whether a new Install may start: never over a live session; always after an end state. */
 static boolean mayRetry(Attempt a) {
  return a != Attempt.IN_PROGRESS && a != Attempt.WAITING_FOR_USER;
 }

 /** Clean up after an interrupted attempt: abandon only our own sessions for this package that never
  * committed, and delete the stale download. A committed session is Android's to finish. */
 static boolean abandonSession(String sessionPackage, String self, boolean committed, Attempt a) {
  return a == Attempt.INTERRUPTED && self.equals(sessionPackage) && !committed;
 }
 static boolean deleteDownload(Attempt a) {
  return a != Attempt.IN_PROGRESS && a != Attempt.WAITING_FOR_USER;
 }

 /** What the update dialog says about the attempt; the working version is always named, and nothing is called
  * a rollback. reason is the recorded failure for REJECTED. */
 static String text(Attempt a, long target, long installed, String reason) {
  String working = installed > 0 ? "Rose Agent " + installed + " is still installed and working." : "The installed Rose Agent wasn’t changed.";
  switch (a) {
   case IN_PROGRESS: return "Installing version " + target + "…";
   case WAITING_FOR_USER: return "Confirm the installation of version " + target + " in Android.";
   case INSTALLED: return "Rose Agent " + installed + " is installed.";
   case INTERRUPTED: return "The update to version " + target + " was interrupted before Android installed it. " + working + " Install again to retry.";
   case CANCELLED: return "Installation of version " + target + " was cancelled. " + working;
   case REJECTED: return "Version " + target + " wasn’t installed" + (reason == null || reason.isEmpty() ? "." : ": " + reason) + " " + working;
   default: return "";
  }
 }

 enum Activation { UNCHANGED, FIRST_RUN_OF_UPDATE, FIRST_RUN }
 /** Recorded at each start: the version that last ran. A newer installed version than the last one that ran is
  * an update that has now started (activation evidence); nothing recorded is a first run. */
 static Activation activation(long lastRan, long installed) {
  if (lastRan <= 0) return Activation.FIRST_RUN;
  return installed > lastRan ? Activation.FIRST_RUN_OF_UPDATE : Activation.UNCHANGED;
 }

 /** After a bad update the only supported recovery is forward: a strictly newer release, same signer and
  * compatibility, through the normal check. This names it; it never offers an older version. */
 static String recovery(long installed) {
  return "If Rose Agent " + installed + " misbehaves, a fixed newer version on this channel installs the same way, "
    + "keeping your data and pairing. Older versions aren’t reinstalled.";
 }
}
