package dev.pixel.builder;

import android.app.Activity;
import android.content.Intent;

/**
 * Hands the creator's one-time invite to their installed generated app (app.rose.gen.p<projectId>) and nowhere else:
 * that package's own launch intent (explicit to its launcher activity), with the extras App Core's generated
 * RoseInvite.take reads once and removes. Just before it leaves, the installed app is re-read and must be exactly the
 * invite's project package, with the project's pinned signer and the invite's version (SignIn.mayHandOver). It starts
 * a fresh task (CLEAR_TASK), so an already-open app gets the extras in onCreate instead of only being brought forward.
 * The token lives only in this call's intent: it is never logged, stored, shown or put in a PendingIntent.
 */
final class EnrollHandoff {
 private EnrollHandoff() {}

 /** App Core's codegen: <pkg>.rose.RoseInvite.take reads exactly these. */
 static final String EXTRA_ACCOUNT = "rose.signin.account";
 static final String EXTRA_INVITE = "rose.signin.invite";

 /** The installed app's launch intent, explicit to its own package; null when there is none to open. */
 static Intent launch(Activity a, String packageName) {
  if (packageName == null || !packageName.startsWith("app.rose.gen.")) return null;
  Intent i = a.getPackageManager().getLaunchIntentForPackage(packageName);
  if (i == null || i.getComponent() == null || !packageName.equals(i.getComponent().getPackageName())) return null;
  return i;
 }

 /** Asked before any invite is made: the app can be opened. */
 static boolean canOpen(Activity a, String packageName) { return launch(a, packageName) != null; }

 /** Opens the installed app with the invite. False when it isn't exactly this project's app (package, pinned signer,
  *  invite version) or can't be opened; the invite is then dropped. Never with the token in a message. */
 static boolean start(Activity a, BuilderApi.Project p, BuilderApi.Invite invite) {
  String packageName = p.packageName;
  if (!SignIn.mayHandOver(packageName, p.signerSha256, Installer.installed(a, packageName), invite)) return false;
  Intent i = launch(a, packageName);
  if (i == null) return false;
  i.setPackage(packageName).putExtra(EXTRA_ACCOUNT, invite.account).putExtra(EXTRA_INVITE, invite.token)
    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK);
  try { a.startActivity(i); return true; }
  catch (RuntimeException e) { return false; } // ActivityNotFound or Security: the token never reaches a message
 }
}
