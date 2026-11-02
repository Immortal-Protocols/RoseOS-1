package dev.pixel.voice;

/** Home's Rose control opening the conversation (#410; the committed Oxblood reference, app.js:183-201): the sheet
 * grows from the Rose control to the full screen over 620ms. Natively that is the platform's clip-reveal window
 * animation from the control's rectangle (ActivityOptions.makeClipRevealAnimation), run by the system and scaled by the
 * owner's animation settings. This class decides the two things the activity can: whether a tap opens Rose now, and
 * whether it animates. One launch at a time: a repeated or interrupted tap while a launch is under way (the
 * reference's 620ms, or until Home is left) opens nothing extra, so a quick second tap can never stack a second Rose
 * or land on what lies under the growing sheet. With animations off (animator scale 0) the launch is plain and instant.
 * Pure logic, no Android types; times are uptime milliseconds. */
final class HomeRoseLaunch {
 static final long OPEN_MS = 620;
 private boolean inFlight;
 private long startedAt;

 /** A tap on Home's Rose control at now: true to launch (and the launch is now in flight), false to ignore it. */
 boolean tap(long now) {
  if (inFlight && now - startedAt >= 0 && now - startedAt < OPEN_MS) return false;
  inFlight = true;
  startedAt = now;
  return true;
 }

 /** Home was left (paused) or came back (resumed): the launch has resolved either way, so the next tap is fresh. */
 void reset() { inFlight = false; }

 boolean inFlight(long now) { return inFlight && now - startedAt >= 0 && now - startedAt < OPEN_MS; }

 /** Whether the launch animates: only with animations on (Settings.Global.ANIMATOR_DURATION_SCALE above 0). */
 static boolean animate(float animatorScale) { return animatorScale > 0f; }
}
