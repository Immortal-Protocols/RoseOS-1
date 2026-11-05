package dev.pixel.backgroundcontrol;

/**
 * F06: the apps Rose never takes into her workspace, because their task is the owner's Home or recents. Rose Agent's
 * MainActivity is a singleTask HOME + LAUNCHER activity, and a takeover launch (the launcher intent, activity type
 * undefined) may reuse that HOME task and move the owner's Home screen onto the session display. The controller refuses
 * these before any slot is chosen or any session is stopped; the platform's "target_not_standard: <pkg>" (#551) is the
 * backstop for any other HOME or recents task. Pure rules, no Android types.
 */
final class HomeTarget {
 private HomeTarget() {}

 static final String ROSE_AGENT = "dev.pixel.voice", REASON = "target_not_standard";
 /** The package Android resolves HOME to while no default is chosen (its chooser), never a real Home app. */
 static final String CHOOSER = "android";

 /** Whether a start of pkg is refused. defaultHome: the package of the current default HOME activity, or null. */
 static boolean refuses(String pkg, String defaultHome) {
  if (pkg == null) return false;
  return ROSE_AGENT.equals(pkg) || (defaultHome != null && !CHOOSER.equals(defaultHome) && defaultHome.equals(pkg));
 }

 /** Whether a session error is the platform's refusal of a Home or recents task. */
 static boolean platformRefused(String error) { return error != null && error.contains(REASON + ":"); }
}
