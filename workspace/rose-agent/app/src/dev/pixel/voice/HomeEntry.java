package dev.pixel.voice;

/** Where an intent that reaches MainActivity lands (OS-HOME-2804-FIX-001). HOME shows Rose Home; the app icon
 * (MAIN/LAUNCHER) shows Rose Home with the conversation opened over it, so Back returns to Home, never the legacy
 * voice page. Other callers (notification, entry activities) keep their page. Only navigation: nothing here starts
 * or ends voice, and the owner's HOME choice is never changed. Pure logic, no Android types. */
final class HomeEntry {
 final boolean showHome, openConversation;

 private HomeEntry(boolean showHome, boolean openConversation) {
  this.showHome = showHome; this.openConversation = openConversation;
 }

 /** fresh: a new intent (onNewIntent, or onCreate without saved state). Recreation and a relaunch from Recents
  * keep Home but never reopen the conversation; before pairing, the pairing screen comes first. */
 static HomeEntry of(boolean main, boolean home, boolean launcher, boolean fresh, boolean fromHistory, boolean paired) {
  if (home) return new HomeEntry(true, false);
  if (main && launcher) return new HomeEntry(true, fresh && !fromHistory && paired);
  return new HomeEntry(false, false);
 }
}
