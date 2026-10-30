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
 *
 * #156: an accepted request that leaves Home showing used to say nothing. Each accepted request now has a token;
 * SETTLE_MS later, if that same request is still the one in flight and Home never left, the owner gets one truthful
 * notice that the overview was not confirmed. Home leaving (pause), the activity going away (cancel) or a newer
 * request make the old token stale, so it says nothing. Not leaving Home is not proof of why: the notice claims
 * only what this app observed, and nothing is retried, toggled, enabled or opened instead.
 * Pure logic, no Android types. */
final class HomeOverview {
 static final long SETTLE_MS = 1500;

 /** The overview request: null when the route is unavailable, else whether the system accepted it. */
 interface Route {
  Boolean show();
 }

 enum Result { REQUESTED, IN_FLIGHT, UNAVAILABLE, FAILED }

 private long requestedAt = Long.MIN_VALUE, token;
 private boolean inFlight;

 Result request(long nowMs, Route route) {
  if (inFlight && nowMs - requestedAt < SETTLE_MS) return Result.IN_FLIGHT;
  inFlight = false;
  Boolean accepted = route.show();
  if (accepted == null) return Result.UNAVAILABLE;
  if (!accepted) return Result.FAILED;
  inFlight = true;
  requestedAt = nowMs;
  token++;
  return Result.REQUESTED;
 }

 /** The token of the request just made (valid after REQUESTED): hand it to the SETTLE_MS check. */
 long token() {
  return token;
 }

 /** SETTLE_MS after the request with this token: the one notice if it is still in flight and Home never left,
  * else null. It is said at most once per request, and it ends that request so the next tap is a new one. */
 String unconfirmed(long requestToken, long nowMs) {
  if (!inFlight || requestToken != token || nowMs - requestedAt < SETTLE_MS) return null;
  inFlight = false;
  return UNCONFIRMED;
 }

 /** Home left the foreground; the next tap after returning is a new request, and a pending check says nothing. */
 void homeLeft() {
  inFlight = false;
 }

 /** The activity is going away: nothing pending may speak. */
 void cancel() {
  inFlight = false;
  token++;
 }

 static final String UNCONFIRMED = "The overview didn’t open here. Home is still showing.";

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
