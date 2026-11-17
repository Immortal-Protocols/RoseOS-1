package dev.pixel.builder;

/**
 * The creator signing in to their own client-server app: when the builder offers it, and how its answers read. Pure,
 * host-tested. The invite itself is a credential: nothing here sees, words or keeps a token.
 *
 * <p>Offered only for the version the phone has installed when that version is the one whose server is set up in the
 * project's agent container (versions[].hosting). An invite is never fetched for an app that isn't installed, or for an
 * installed version whose client wasn't built for that backend, so a token is never left unused.
 */
final class SignIn {
 private SignIn() {}

 enum Offer { NONE, INSTALL_FIRST, UPDATE_FIRST, OFFER }

 static Offer offer(BuilderApi.Project p, InstallPlan.Installed installed) {
  if (p == null) return Offer.NONE;
  BuilderApi.Version hosted = null;
  for (BuilderApi.Version v : p.versions) if (v.hosting.setUp()) hosted = v;
  if (hosted == null) return Offer.NONE;
  if (installed == null) return Offer.INSTALL_FIRST;
  return installed.versionCode == hosted.versionCode ? Offer.OFFER : Offer.UPDATE_FIRST;
 }

 static String offerLine(Offer o, String app) {
  switch (o) {
   case INSTALL_FIRST: return "Install " + app + " to sign in to it.";
   case UPDATE_FIRST: return "Install the version of " + app + " whose backend is set up to sign in to it.";
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

 static String handedOver(String app) { return "Opening " + app + " to finish setting up your account."; }
}
