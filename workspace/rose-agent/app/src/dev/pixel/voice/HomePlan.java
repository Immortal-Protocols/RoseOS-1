package dev.pixel.voice;

/**
 * When Home re-plans its pages from a measured viewport (F01, #410 phone feedback: Home jittered between one and two
 * pages, and a swipe right sometimes showed an extra page, with the app set unchanged).
 *
 * Demonstrated mechanism (HomePlanTest, from the rule this replaces): every new page reports each inner size it is
 * measured at, and the old rule dropped a report whose capacity matched the current plan without recording its size.
 * A build that reports two sizes, a transient A and then B, therefore re-planned forever: with B's plan, A was stored
 * and rebuilt while B was dropped; with A's plan, B was stored and rebuilt; the page count alternated each rebuild.
 * Which transient produces two sizes per build on the phone is not reproduced here.
 *
 * The rule now: every report is recorded, so the plan always follows the latest real viewport; a re-plan waits until
 * the reports have been quiet for SETTLE_MS and is skipped when the latest viewport still fits the current plan; and
 * if measurement keeps flipping between two plans, the flip back is refused (Breaker), so Home stays put. A real change
 * (rotation, resize, font scale) still re-plans once. Pure logic, no Android types.
 */
final class HomePlan {
 private HomePlan() {}

 /** How long the measurements must be quiet before Home re-plans from them. */
 static final long SETTLE_MS = 250;
 /** A flip back to the plan Home just left, this soon after leaving it, is oscillation, not a new viewport. */
 static final long FLIP_MS = 3000;

 /** One grid's latest measured viewport (inner px and cell height px); unknown until a page reports. */
 static final class Viewport {
  int width = -1, height = -1;
  float cell = -1;

  boolean known() { return width > 0 && height > 0 && cell > 0; }

  /** Records a report, whatever plan it fits. */
  void record(int width, int height, float cell) { this.width = width; this.height = height; this.cell = cell; }
 }

 /** Whether Home's plan (columns, capacity) no longer fits its latest viewport. */
 static boolean homeStale(int columns, int capacity, Viewport home, float density) {
  return home.known() && (columns != HomeAppLayout.homeColumns(home.width / density)
    || capacity != HomeAppLayout.homeCapacity(home.width, home.height, home.cell, density));
 }

 /** Whether the app pages' plan (columns, apps per page) no longer fits their latest viewport. */
 static boolean appsStale(int columns, int perPage, Viewport apps, float density) {
  return apps.known() && HomeAppLayout.replan(columns, perPage, apps.width, apps.height, apps.cell, density);
 }

 /** A plan's identity: Home's columns and capacity, the app pages' columns and apps per page. */
 static String key(int homeColumns, int homeCapacity, int appColumns, int appPerPage) {
  return homeColumns + "x" + homeCapacity + "/" + appColumns + "x" + appPerPage;
 }

 /** Refuses a measured re-plan that returns to the plan Home left less than FLIP_MS ago. */
 static final class Breaker {
  String previous;
  long leftAt = Long.MIN_VALUE;

  /** Whether Home may move from plan current to plan next, measured at now (ms, monotonic). */
  boolean allow(String current, String next, long now) {
   if (next.equals(current)) return false;
   if (next.equals(previous) && now - leftAt < FLIP_MS) return false;
   previous = current;
   leftAt = now;
   return true;
  }
 }
}
