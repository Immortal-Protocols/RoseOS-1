package dev.pixel.voice;

/** Home page order and where an entry lands (OS-HIFI-HOME-001). Pages run Home, the app pages, Widgets,
 * then the legacy voice page last, so a swipe from Home reaches the apps under the fixed tray. A HOME
 * press that returns from an app or the Rose conversation keeps the page the owner left, like a phone
 * launcher; HOME while Home is already showing, or the app icon, goes to Home. Pure logic, no Android types. */
final class HomePages {
 private HomePages() {}

 static final int HOME = 0, FIRST_APPS = 1;
 static int widgets(int count) { return count - 2; }
 static int voice(int count) { return count - 1; }

 /**
  * @param showHome   the entry is a Home entry (HomeEntry.showHome)
  * @param launcher   the entry is the app icon (MAIN/LAUNCHER without HOME)
  * @param homeShown  Home was in the foreground when HOME arrived (only paused, never stopped)
  * @param previous   the page shown before the entry, or -1 if none
  * @param fallback   the page the activity would show otherwise (non-Home entries keep it)
  */
 static int target(boolean showHome, boolean launcher, boolean homeShown, int previous, int fallback, int count) {
  if (!showHome) return fallback;
  if (launcher || homeShown) return HOME;
  if (previous < 0 || previous >= count || previous == voice(count)) return HOME;
  return previous;
 }

 /** A page saved before recreation, kept within the rebuilt pager. */
 static int restore(int saved, int count) {
  return saved < 0 || count <= 0 ? HOME : Math.min(saved, count - 1);
 }
}
