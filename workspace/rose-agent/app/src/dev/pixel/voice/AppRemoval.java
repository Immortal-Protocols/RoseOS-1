package dev.pixel.voice;

/**
 * F03 (PM-PHONE-FEEDBACK-1104-001): what a long press on an installed app offers for removing it, and through which
 * supported Android route. Rose never removes an app itself: every removal is the owner's choice in Android's own UI.
 *
 * Routes, from the pinned platform (GrapheneOS frameworks/base 84183af):
 * - CONFIRM: Android's uninstall confirmation (Intent.ACTION_DELETE for the package). PackageInstaller's
 *   UninstallerActivity silently finishes for a caller targeting P or later that holds neither REQUEST_DELETE_PACKAGES
 *   (protection level normal) nor DELETE_PACKAGES (signature|privileged|role), so this route is used only while
 *   Rose Agent actually holds REQUEST_DELETE_PACKAGES. Cancel leaves the app installed; the system removes it only
 *   after the owner confirms.
 * - APP_INFO: the app's system App info page (Settings.ACTION_APPLICATION_DETAILS_SETTINGS), which needs no
 *   permission and where the owner uninstalls, or disables a built-in app, with Android's own confirmation.
 *
 * Built-in apps cannot be uninstalled: they are offered App info only, named honestly. An updated built-in app offers
 * "Uninstall updates", which is what Android's confirmation then does (the factory version stays). Android's own
 * screens decide everything else (a device administrator, a policy-blocked app) and its refusal is reported as such.
 *
 * 1J: Rose Agent declares REQUEST_DELETE_PACKAGES (protection level normal: granted at install or update, no prompt),
 * so the long press opens Android's uninstall confirmation directly, asking for its result (EXTRA_RETURN_RESULT).
 * The result is checked against the package manager (outcome): Home refreshes once the app is gone, a cancelled
 * confirmation leaves it installed and says nothing, and an app Android kept is named as still installed. Home's
 * package receiver also rebuilds the pages on any removal. Pure logic, no Android types.
 */
final class AppRemoval {
 private AppRemoval() {}

 enum Route { CONFIRM, APP_INFO }

 /** What the long-press menu offers for one app. remove is null when the app cannot be removed by the owner. */
 static final class Offer {
  final String remove, note;
  final Route route;
  Offer(String remove, String note, Route route) { this.remove = remove; this.note = note; this.route = route; }
 }

 static final String UNINSTALL = "Uninstall", UNINSTALL_UPDATES = "Uninstall updates", APP_INFO = "App info",
   BUILT_IN = "Built into the system", IN_APP_INFO = "Uninstall in App info";

 /**
  * @param self              the app is Rose Agent itself (never offered here)
  * @param system            ApplicationInfo.FLAG_SYSTEM
  * @param updatedSystem     ApplicationInfo.FLAG_UPDATED_SYSTEM_APP
  * @param mayRequestDelete  Rose Agent holds REQUEST_DELETE_PACKAGES at this moment
  * @return the offer, or null for no removal menu at all
  */
 static Offer offer(boolean self, boolean system, boolean updatedSystem, boolean mayRequestDelete) {
  if (self) return null;
  Route route = mayRequestDelete ? Route.CONFIRM : Route.APP_INFO;
  if (updatedSystem) return new Offer(mayRequestDelete ? UNINSTALL_UPDATES : IN_APP_INFO, null, route);
  if (system) return new Offer(null, BUILT_IN, Route.APP_INFO);
  return new Offer(mayRequestDelete ? UNINSTALL : IN_APP_INFO, null, route);
 }
 /** What the owner's answer in Android's confirmation came to. */
 enum Outcome { REMOVED, UPDATES_REMOVED, KEPT, NOT_REMOVED }

 static final String UPDATES = " updates were uninstalled", GONE = " was uninstalled", STILL = " is still installed. Android didn't uninstall it.";

 /**
  * @param confirmed      Android returned RESULT_OK
  * @param cancelled      Android returned RESULT_CANCELED (the owner cancelled, or backed out)
  * @param stillInstalled the package manager still has the package after the confirmation closed
  * @param updatesOnly    the offer was Uninstall updates (an updated built-in app keeps its factory version)
  */
 static Outcome outcome(boolean confirmed, boolean cancelled, boolean stillInstalled, boolean updatesOnly) {
  if (!stillInstalled) return Outcome.REMOVED; // the package manager is the truth, whatever the result code
  if (updatesOnly && confirmed) return Outcome.UPDATES_REMOVED;
  if (cancelled) return Outcome.KEPT;
  return Outcome.NOT_REMOVED;
 }

 /** Home rebuilds its pages for these; a kept app leaves Home as it is. */
 static boolean refreshes(Outcome outcome) { return outcome == Outcome.REMOVED || outcome == Outcome.UPDATES_REMOVED; }

 /** The short confirmation shown afterwards, or null when nothing changed because the owner cancelled. */
 static String message(Outcome outcome, String label) {
  switch (outcome) {
   case REMOVED: return label + GONE;
   case UPDATES_REMOVED: return label + UPDATES;
   case NOT_REMOVED: return label + STILL;
   default: return null;
  }
 }
 /**
  * 1I (owner decision, 2026-11-07): Rose Models (dev.pixel.models) is retired with no successor. Nothing ships it any
  * more and Rose Agent never installs, updates or calls it. A copy already on a phone stays until its owner removes
  * it: Home offers that once (RetiredAppNotice) and on the app's long press, always through Android's own
  * confirmation, never silently. Only the genuine app counts: the exact package signed by the Models release key
  * (the signer service-auth-v1 enrolls). Another app reusing the package name is an ordinary app.
  */
 static final String MODELS = "dev.pixel.models";
 static final String MODELS_SIGNER = "066c66d62518d1db6c8a7d40795fcca580412049d2c5a5cce89b7673540108da";
 static final String MODELS_NAME = "Rose Models", RETIRED_NOTE = "Retired · no longer updated";
 static final String RETIRED_TITLE = "Rose Models is retired", RETIRED_UNINSTALL = "Uninstall", RETIRED_LATER = "Not now",
   RETIRED_BODY = "It no longer gets updates, and Rose Agent doesn't use it. Uninstalling it removes the app and its "
     + "saved sign-in key from this phone. If you keep it for now, it stays in the System folder, and you can "
     + "uninstall it there with a long press.";
 /** Where Home records that the owner has answered the notice, so it is offered once. */
 static final String RETIRED_PREFS = "retired-apps", MODELS_NOTICE = "models-notice-answered";

 /** @param signerSha256 the SHA-256 (hex) of each of the installed package's signing certificates */
 static boolean retired(String packageName, Iterable<String> signerSha256) {
  if (!MODELS.equals(packageName) || signerSha256 == null) return false;
  for (String signer : signerSha256) if (MODELS_SIGNER.equalsIgnoreCase(signer)) return true;
  return false;
 }
}
