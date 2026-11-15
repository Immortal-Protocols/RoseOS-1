package dev.pixel.voice;

/** Home page order and where an entry lands (OS-HIFI-HOME-001, #223; Project 2). Pages run the workspace, Home, then
 * the app pages, and nothing else. The workspace is Project 2's one extra page to the left of Home (the approved HiFi
 * r2): it never replaces or rearranges Home, its dock or the chin, and the dots mark it with a star before Home's
 * round dots. The legacy Widgets and voice ("Tap to talk") pages are no longer pages, so normal swipes stay in
 * the current Home; the legacy voice controls that opened over it are removed with their setting (1H). A HOME press that
 * returns from an app or the Rose conversation keeps the page the owner left, like a phone launcher; HOME while
 * Home is already showing, or the app icon, goes to Home. Pure logic, no Android types. */
final class HomePages {
 private HomePages() {}

 static final int WORKSPACE = 0, HOME = 1, FIRST_APPS = 2;

 /** The workspace page: its dot is the star, and the page is live (not inert) only while it is shown. */
 static boolean workspace(int page) { return page == WORKSPACE; }

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
  if (previous < 0 || previous >= count) return HOME;
  return previous;
 }

 /** Who owns a single-finger drag so far (LauncherPager): the pager once it is clearly horizontal, the child once
  * it is clearly vertical, nobody yet inside the slop. A drag the pager takes cancels the child's tap. */
 static final int UNDECIDED = 0, PAGER = 1, CHILD = 2;
 static int owner(float dx, float dy, float slop) {
  dx = Math.abs(dx); dy = Math.abs(dy);
  if (dy > slop && dy > dx) return CHILD;
  if (dx > slop && dx > dy * 1.35f) return PAGER;
  return UNDECIDED;
 }

 /**
  * Where a released drag settles, never more than one page from where it started and always a real page.
  * A fling decides first, by its direction at release, so a drag that reverses at the end goes where the
  * finger last went; otherwise a drag past a fifth of the width turns the page; otherwise the nearest page.
  * @param dx       the finger's travel since down (negative: toward the next page)
  * @param vx       the release velocity in px/s
  * @param nearest  the page nearest the current scroll position
  */
 static int settle(int downPage, float dx, float vx, int width, float flingThreshold, int nearest, int count) {
  int target;
  if (Math.abs(vx) > flingThreshold) target = downPage + (vx < 0 ? 1 : -1);
  else if (Math.abs(dx) > width * .2f) target = downPage + (dx < 0 ? 1 : -1);
  else target = Math.max(downPage - 1, Math.min(downPage + 1, nearest));
  return Math.max(0, Math.min(count - 1, target));
 }

 /** A page saved before recreation, if the rebuilt pager still has it. A page that no longer exists (one the app
  * inventory lost, or a Widgets or voice page saved before #223) returns to Home rather than the nearest app page. */
 static int restore(int saved, int count) {
  return saved < 0 || saved >= count ? HOME : saved;
 }
}
