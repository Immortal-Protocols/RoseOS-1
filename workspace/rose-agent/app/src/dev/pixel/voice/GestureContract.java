package dev.pixel.voice;

/**
 * Home's side of Launcher3's gesture_nav_contract_v1 (#410; keys and behaviour confirmed by OS Product from pinned
 * Launcher3 47accce, FallbackSwipeHandler addGestureContract/accept/update, #410 comment 6593674343; security review
 * comment 6593742638). When a swipe goes Home from an app, Launcher3 puts a Bundle on the HOME intent naming the closing
 * task's root activity and a reply Message; if Home answers with the on-screen rect of that app's tile, the closing
 * window springs into it, otherwise Launcher3 springs to its own default rect (no wait, no hang).
 *
 * The reply is a RectF only: never a SurfaceControl (Launcher3 would stretch it over the whole closing window each
 * frame), never a finish callback, never any label, icon, page or user data. Any app can put this Bundle on a HOME
 * intent, and the rect would tell it whether an app is installed and where it sits on Home, so Home answers only a
 * caller the platform names as the device's recents component (ComponentCaller, which needs Launcher3 to share its
 * identity), and fails closed everywhere else: no caller, wrong caller, wrong class or version, malformed or partial
 * Bundle, another user, no fully visible tile on the page Home lands on. Pure rules, no Android types.
 */
final class GestureContract {
 private GestureContract() {}

 static final String EXTRA = "gesture_nav_contract_v1", COMPONENT = "android.intent.extra.COMPONENT_NAME",
   USER = "android.intent.extra.USER", CALLBACK = "android.intent.extra.REMOTE_CALLBACK",
   ENABLE = "gesture_nav_contract_enable", ICON_POSITION = "gesture_nav_contract_icon_position";
 /** How Home's cells name themselves (View tags), so the target is found by exact package and activity. */
 static final String TILE_TAG = "home-app:", FOLDER_TAG = "home-folder";
 /** The first SDK with ComponentCaller (Activity.getCurrentCaller). */
 static final int MIN_SDK = 35;

 static String tag(String packageName, String activity) { return TILE_TAG + packageName + "/" + activity; }

 /** The package of a flattened component ("pkg/cls", as config_recentsComponentName holds it), or null. */
 static String recentsPackage(String flattened) {
  if (flattened == null) return null;
  int slash = flattened.indexOf('/');
  String pkg = (slash < 0 ? flattened : flattened.substring(0, slash)).trim();
  return pkg.isEmpty() ? null : pkg;
 }

 /** The entry may carry a contract: MainActivity itself (never a subclass such as WorkspaceAppsActivity), on an SDK
  * with ComponentCaller, a HOME intent, and Home was not already showing (a swipe from an app, not a tap on Home). */
 static boolean entry(boolean mainActivityItself, int sdk, boolean homeIntent, boolean homeAlreadyShown) {
  return mainActivityItself && sdk >= MIN_SDK && homeIntent && !homeAlreadyShown;
 }

 /** The sender is the device's recents component, as the platform reports it (a shared identity: a real uid and the
  * package of config_recentsComponentName). Anything unknown is not it. */
 static boolean caller(String callerPackage, int callerUid, String recentsPackage) {
  return recentsPackage != null && callerUid >= 0 && recentsPackage.equals(callerPackage);
 }

 /** A rect Home may report: finite, non-empty, and wholly inside its window. */
 static boolean rect(float left, float top, float right, float bottom, int windowWidth, int windowHeight) {
  if (!(isFinite(left) && isFinite(top) && isFinite(right) && isFinite(bottom))) return false;
  return windowWidth > 0 && windowHeight > 0 && right > left && bottom > top
    && left >= 0 && top >= 0 && right <= windowWidth && bottom <= windowHeight;
 }

 private static boolean isFinite(float v) { return !Float.isNaN(v) && !Float.isInfinite(v); }
}
