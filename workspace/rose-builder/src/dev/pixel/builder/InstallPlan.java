package dev.pixel.builder;

/**
 * Project 1: whether a generated app's version may be installed, and how it is described. Pure rules, host-tested.
 *
 * <p>Installs and updates go only through Android's own package installer, which always asks the owner. An update
 * must keep the app's package and signing identity and raise its version, so Android keeps the app's data; a changed
 * signer or a downgrade is refused here with its reason, never turned into an uninstall-and-reinstall. A download is
 * used only when its SHA-256, package and signer match what the builder recorded for that version.
 */
final class InstallPlan {
 enum Action { INSTALL, UPDATE, OPEN, REFUSE }

 /** What is on the phone now for the project's package: null package means not installed. */
 static final class Installed {
  final String packageName, signerSha256; final long versionCode;
  Installed(String packageName, String signerSha256, long versionCode) { this.packageName = packageName; this.signerSha256 = signerSha256; this.versionCode = versionCode; }
 }

 /** The builder's record for one version (expected) or what the downloaded file actually is (found). */
 static final class Apk {
  final String packageName, signerSha256, sha256; final long versionCode;
  Apk(String packageName, String signerSha256, String sha256, long versionCode) { this.packageName = packageName; this.signerSha256 = signerSha256; this.sha256 = sha256; this.versionCode = versionCode; }
 }

 final Action action; final String reason;
 private InstallPlan(Action action, String reason) { this.action = action; this.reason = reason; }

 /** Before download: what the version's button offers. */
 static InstallPlan offer(Apk expected, Installed installed) {
  if (installed == null) return new InstallPlan(Action.INSTALL, null);
  if (!eq(installed.packageName, expected.packageName)) return new InstallPlan(Action.INSTALL, null);
  if (!eqIgnoreCase(installed.signerSha256, expected.signerSha256))
   return new InstallPlan(Action.REFUSE, "Another app with this name is already installed and was signed differently, so Android can't update it. Your app's data is left alone.");
  if (expected.versionCode == installed.versionCode) return new InstallPlan(Action.OPEN, null);
  if (expected.versionCode < installed.versionCode)
   return new InstallPlan(Action.REFUSE, "A newer version is already installed. Android can't go back to an older version without removing the app's data.");
  return new InstallPlan(Action.UPDATE, null);
 }

 /** After download, before Android's installer: the file must be exactly the version the builder recorded. */
 static InstallPlan verify(Apk expected, Apk found, Installed installed) {
  if (found == null || !eqIgnoreCase(found.sha256, expected.sha256))
   return new InstallPlan(Action.REFUSE, "The download doesn't match what your builder made, so it wasn't installed. Try downloading it again.");
  if (!eq(found.packageName, expected.packageName) || found.versionCode != expected.versionCode)
   return new InstallPlan(Action.REFUSE, "The downloaded file is a different app or version than expected, so it wasn't installed.");
  if (!eqIgnoreCase(found.signerSha256, expected.signerSha256))
   return new InstallPlan(Action.REFUSE, "The downloaded file isn't signed with this app's identity, so it wasn't installed.");
  return offer(expected, installed);
 }

 /** The button label for an offer. */
 static String label(InstallPlan p) {
  switch (p.action) {
   case INSTALL: return "Install";
   case UPDATE: return "Update";
   case OPEN: return "Open";
   default: return "Can't install";
  }
 }

 private static boolean eq(String a, String b) { return a != null && a.equals(b); }
 private static boolean eqIgnoreCase(String a, String b) { return a != null && b != null && a.equalsIgnoreCase(b); }
}
