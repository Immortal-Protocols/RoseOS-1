package dev.pixel.backgroundcontrol;

/** How the agent viewer presents itself for one entry. Automatic picture-in-picture is off by default (AUTO-PIP-OFF-330):
 * every entry, an explicit Watch (a task/session pair bound by this intent, from Rose's apps or an overview selection)
 * and an unbound reopen (the session notification) alike, shows Rose's workspace full screen, and leaving it never
 * auto-enters the floating window. A viewer recreated from an earlier build's saved state ("PIP", or no presentation
 * from before presentations were saved) comes back full screen too. Binding checks stay in WatchBinding: this decides
 * presentation only. Pure logic, no Android types. */
final class ViewerPresentation {
 enum Mode { PIP, EXPANDED }

 /** Whether the viewer floats on its own: the automatic show, an unbound reopen and auto-enter on leaving. Off. */
 static final boolean AUTOMATIC_PIP = false;

 private ViewerPresentation() {}

 /** boundEntry: this intent carried a valid Watch or selection. current: the viewer's presentation so far,
  * null for a new viewer. */
 static Mode forEntry(boolean boundEntry, Mode current) {
  if (boundEntry || !AUTOMATIC_PIP) return Mode.EXPANDED;
  return current == null ? Mode.PIP : current;
 }

 /** Only the picture-in-picture presentation asks Android for the floating window. */
 static boolean entersPip(Mode mode) {
  return mode == Mode.PIP;
 }

 /** A saved presentation. Anything but a saved full screen (an earlier build's "PIP", missing or unrecognised) is the
  * default: full screen while automatic picture-in-picture is off. */
 static Mode restore(String saved) {
  return Mode.EXPANDED.name().equals(saved) || !AUTOMATIC_PIP ? Mode.EXPANDED : Mode.PIP;
 }
}
