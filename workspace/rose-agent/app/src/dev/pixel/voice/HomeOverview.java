package dev.pixel.voice;

/** The Home tray's overview entry (OS-OVERVIEW-156-M001). A non-system Home has no permitted API of its own
 * for the system task overview: SystemUI's toggleRecentApps needs STATUS_BAR (signature|privileged), and
 * Launcher3 Quickstep's RecentsActivity is not exported. The one existing permitted route in this app is the
 * owner-enabled Rose phone-controls accessibility service (PhoneAccessibility), whose
 * performGlobalAction(GLOBAL_ACTION_RECENTS) asks SystemUI to show the real overview. Nothing here enables
 * that service, opens Settings or shows any other picker: with it off, the entry is unavailable and says so.
 *
 * GLOBAL_ACTION_RECENTS toggles, so a second tap while the first request is still taking Home away would
 * close the overview it just opened. One request is in flight until Home leaves the foreground (onPause:
 * the overview covered Home) or, if the system accepted it but nothing happened, until SETTLE_MS passes.
 * One instance lives as long as its MainActivity; tray rebuilds keep it, a recreated activity starts clear.
 * Pure logic, no Android types. */
final class HomeOverview {
 static final long SETTLE_MS = 1500;

 /** The overview request: null when the route is unavailable, else whether the system accepted it. */
 interface Route {
  Boolean show();
 }

 enum Result { REQUESTED, IN_FLIGHT, UNAVAILABLE, FAILED }

 private long requestedAt = Long.MIN_VALUE;
 private boolean inFlight;

 Result request(long nowMs, Route route) {
  if (inFlight && nowMs - requestedAt < SETTLE_MS) return Result.IN_FLIGHT;
  inFlight = false;
  Boolean accepted = route.show();
  if (accepted == null) return Result.UNAVAILABLE;
  if (!accepted) return Result.FAILED;
  inFlight = true;
  requestedAt = nowMs;
  return Result.REQUESTED;
 }

 /** Home left the foreground; the next tap after returning is a new request. */
 void homeLeft() {
  inFlight = false;
 }

 /** What the overview well says: the action when the route is available, the reason when it is not. */
 static String describe(boolean available) {
  return available ? "Overview" : "Overview, unavailable: Rose phone controls are off";
 }

 /** The one line shown for a tap that opened nothing; null when the request went to the system. */
 static String notice(Result result) {
  switch (result) {
   case UNAVAILABLE: return "Overview needs Rose phone controls (Accessibility). Nothing was opened.";
   case FAILED: return "The system did not open the overview.";
   default: return null;
  }
 }
}
