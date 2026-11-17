package dev.pixel.builder;

/**
 * The creator signing in to their own client-server app: when the builder offers it, and how its answers read. Pure,
 * host-tested. The invite itself is a credential: nothing here sees, words or keeps a token.
 *
 * <p>Offered only for the version the phone has installed when that version is the one whose server is set up in the
 * project's agent container (versions[].hosting), and only when the installed app carries the builder's pinned signer
 * for this project. An invite is a bearer credential: an app installed under the same package name by anyone else
 * (another signer) never gets one. An invite is never fetched for an app that isn't installed, or for an installed
 * version whose client wasn't built for that backend, so a token is never left unused.
 */
final class SignIn {
 private SignIn() {}

 enum Offer { NONE, INSTALL_FIRST, UPDATE_FIRST, NOT_THIS_APP, OFFER }

 /** The builder's pinned signer for the project and the installed app's signer are the same, known identity. */
 static boolean sameSigner(String pinned, String installed) {
  return pinned != null && !pinned.isEmpty() && installed != null && pinned.equalsIgnoreCase(installed);
 }

 static Offer offer(BuilderApi.Project p, InstallPlan.Installed installed) {
  if (p == null) return Offer.NONE;
  BuilderApi.Version hosted = null;
  for (BuilderApi.Version v : p.versions) if (v.hosting.setUp()) hosted = v;
  if (hosted == null) return Offer.NONE;
  if (installed == null) return Offer.INSTALL_FIRST;
  // Another app under this package name (or no pinned signer yet): never offered, whatever its version.
  if (!sameSigner(p.signerSha256, installed.signerSha256)) return Offer.NOT_THIS_APP;
  return installed.versionCode == hosted.versionCode ? Offer.OFFER : Offer.UPDATE_FIRST;
 }

 /**
  * Checked again just before the invite leaves Rose, against what is installed at that moment: exactly this project's
  * package (app.rose.gen.p<projectId>), the project's pinned signer, and the version the invite was made for. Anything
  * else and the invite is dropped, never handed over.
  */
 static boolean mayHandOver(String packageName, String pinnedSigner, InstallPlan.Installed now, BuilderApi.Invite invite) {
  return invite != null && packageName != null && packageName.equals("app.rose.gen.p" + invite.projectId)
    && now != null && packageName.equals(now.packageName) && sameSigner(pinnedSigner, now.signerSha256)
    && now.versionCode == invite.versionCode;
 }

 static String offerLine(Offer o, String app) {
  switch (o) {
   case INSTALL_FIRST: return "Install " + app + " to sign in to it.";
   case UPDATE_FIRST: return "Install the version of " + app + " whose backend is set up to sign in to it.";
   case NOT_THIS_APP: return "The " + app + " on this phone wasn't signed by your builder, so Rose won't sign it in.";
   default: return null;
  }
 }

 static String button(String app) { return "Sign in to " + app; }

 /** What the owner reads when the builder issued no invite. Unknown words read as the builder's own, plainly. */
 static String refused(BuilderApi.InviteRefused r, String app) {
  String word = r == null ? null : r.word;
  String again = r != null && r.retryable ? " Try again in a little while." : "";
  switch (word == null ? "" : word) {
   case "invalid_account": return "That account name can't be used: up to 128 bytes, with no ':' or control characters.";
   case "not_hosted": return app + "'s backend isn't set up yet." + again;
   case "open_admission": return app + " lets anyone sign up, so it needs no invite. Open " + app + " and create your account there.";
   case "already_enrolled": return "That account is already set up in " + app + ". Open " + app + " and sign in.";
   case "server_unavailable": return app + "'s backend can't make an invite right now." + (again.isEmpty() ? " Try again later." : again);
   case "not_found": return "Your builder doesn't know this app any more.";
   default: return "Your builder didn't make an invite (" + ShareMessage.clean(word, "refused") + ")." + again;
  }
 }

 /** The installed app can't be opened by Rose (it has no launcher activity). */
 static String cantOpen(String app) { return "Rose can't open " + app + ". Open it yourself to set up your account."; }

 /** The invite was made but not handed over: the installed app changed, or couldn't be opened. It is dropped. */
 static String notHandedOver(String app) {
  return "Rose didn't hand the invite to " + app + ": the app on this phone isn't the one your builder made for it, or couldn't be opened. Nothing was signed in.";
 }

 static String handedOver(String app) { return "Opening " + app + " to finish setting up your account."; }
}
