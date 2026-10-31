package dev.pixel.backgroundcontrol;

/** How the agent viewer presents itself for one entry. An explicit Watch (a task/session pair bound by this
 * intent, from Rose's apps or an overview selection) opens Rose's workspace full screen and does not drop into
 * picture-in-picture. The automatic show and an unbound reopen (the session notification) keep today's
 * picture-in-picture fallback; an unbound reopen never changes a viewer the user already has full screen.
 * Binding checks stay in WatchBinding: this decides presentation only. Pure logic, no Android types. */
final class ViewerPresentation {
 enum Mode { PIP, EXPANDED }

 private ViewerPresentation() {}

 /** boundEntry: this intent carried a valid Watch or selection. current: the viewer's presentation so far,
  * null for a new viewer. */
 static Mode forEntry(boolean boundEntry, Mode current) {
  if (boundEntry) return Mode.EXPANDED;
  return current == null ? Mode.PIP : current;
 }

 /** Only the picture-in-picture presentation asks Android for the floating window. */
 static boolean entersPip(Mode mode) {
  return mode == Mode.PIP;
 }

 /** A saved presentation, or picture-in-picture for anything missing or unrecognised. */
 static Mode restore(String saved) {
  return Mode.EXPANDED.name().equals(saved) ? Mode.EXPANDED : Mode.PIP;
 }
}
