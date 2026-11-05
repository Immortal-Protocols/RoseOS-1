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
 * "Uninstall updates", which is what Android's confirmation then does (the factory version stays). A successful
 * removal reaches Home through its existing package receiver, which rebuilds the pages. Pure logic, no Android types.
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
}
