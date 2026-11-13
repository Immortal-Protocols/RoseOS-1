package dev.pixel.builder;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageInstaller;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.os.Build;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.security.MessageDigest;

/**
 * Project 1: installs or updates a generated app through Android's own package installer. Android always shows its
 * confirmation and the owner decides; this app holds only REQUEST_INSTALL_PACKAGES (the ordinary "install unknown
 * apps" grant the owner gives in Settings), never INSTALL_PACKAGES, root, ADB or developer mode. A file is handed to
 * Android only after {@link InstallPlan#verify} accepts its hash, package, version and signer.
 */
final class Installer {
 static final String ACTION_STATUS = "dev.pixel.builder.INSTALL_STATUS", EXTRA_PROJECT = "dev.pixel.builder.PROJECT";
 private Installer() {}

 /** What the phone has installed for a package, or null. */
 static InstallPlan.Installed installed(Context c, String packageName) {
  try {
   PackageInfo info = c.getPackageManager().getPackageInfo(packageName, signingFlag());
   return new InstallPlan.Installed(packageName, signer(info), versionCode(info));
  } catch (PackageManager.NameNotFoundException e) { return null; }
 }

 /** What a downloaded file actually is, or null when Android can't read it as an app. */
 static InstallPlan.Apk inspect(Context c, File apk) throws Exception {
  PackageInfo info = c.getPackageManager().getPackageArchiveInfo(apk.getPath(), signingFlag());
  if (info == null) return null;
  return new InstallPlan.Apk(info.packageName, signer(info), sha256(apk), versionCode(info));
 }

 /** Whether the owner has allowed this app to ask Android to install apps (Settings › Install unknown apps). */
 static boolean allowed(Context c) { return c.getPackageManager().canRequestPackageInstalls(); }

 static Intent allowIntent(Context c) {
  return new Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, android.net.Uri.parse("package:" + c.getPackageName()));
 }

 /** Opens an install session for a verified file; Android reports back to the activity, which shows its prompt. */
 static void install(Context c, File apk, String packageName, String projectId) throws Exception {
  PackageInstaller pi = c.getPackageManager().getPackageInstaller();
  PackageInstaller.SessionParams params = new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
  params.setAppPackageName(packageName);
  int id = pi.createSession(params);
  PackageInstaller.Session session = pi.openSession(id);
  try {
   try (InputStream in = new FileInputStream(apk); OutputStream out = session.openWrite("app.apk", 0, apk.length())) {
    byte[] buf = new byte[65536];
    for (int n; (n = in.read(buf)) > 0; ) out.write(buf, 0, n);
    session.fsync(out);
   }
   // The project rides on the report, so its outcome reaches the builder even if this activity was recreated.
   Intent status = new Intent(ACTION_STATUS).setPackage(c.getPackageName()).setClass(c, MainActivity.class)
     .putExtra(EXTRA_PROJECT, projectId).putExtra(PackageInstaller.EXTRA_PACKAGE_NAME, packageName);
   int flags = PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0);
   session.commit(PendingIntent.getActivity(c, id, status, flags).getIntentSender());
  } catch (Exception e) {
   session.abandon();
   throw e;
  } finally {
   session.close();
  }
 }

 @SuppressWarnings("deprecation")
 static int signingFlag() { return Build.VERSION.SDK_INT >= 28 ? PackageManager.GET_SIGNING_CERTIFICATES : PackageManager.GET_SIGNATURES; }

 /** The SHA-256 of the app's one current signing certificate, or null for none or several signers (unsupported:
  * App Core signs each generated app with exactly one key, and key rotation is not part of Level 1). */
 @SuppressWarnings("deprecation")
 static String signer(PackageInfo info) {
  Signature[] sigs;
  if (Build.VERSION.SDK_INT >= 28) {
   if (info.signingInfo == null || info.signingInfo.hasMultipleSigners()) return null;
   Signature[] history = info.signingInfo.getSigningCertificateHistory();
   // The current signer is the last in the lineage.
   sigs = history == null || history.length == 0 ? null : new Signature[]{history[history.length - 1]};
  } else sigs = info.signatures;
  if (sigs == null || sigs.length != 1) return null;
  try { return hex(MessageDigest.getInstance("SHA-256").digest(sigs[0].toByteArray())); }
  catch (java.security.NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
 }

 @SuppressWarnings("deprecation")
 static long versionCode(PackageInfo info) { return Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode; }

 static String sha256(File f) throws Exception {
  MessageDigest d = MessageDigest.getInstance("SHA-256");
  try (InputStream in = new FileInputStream(f)) {
   byte[] buf = new byte[65536];
   for (int n; (n = in.read(buf)) > 0; ) d.update(buf, 0, n);
  }
  return hex(d.digest());
 }

 static String hex(byte[] b) {
  StringBuilder s = new StringBuilder();
  for (byte x : b) s.append(String.format("%02x", x & 0xff));
  return s.toString();
 }
}
