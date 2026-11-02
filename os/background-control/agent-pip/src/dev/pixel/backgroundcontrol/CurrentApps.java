package dev.pixel.backgroundcontrol;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** The apps currently running under agent control (#156/#225 membership rule): the packages on the controller's live
 * ComputerControl session display, never task history or results. A session that is not ready, blocked or launching
 * has none, so close, stop, release, deadline or failure empties the set; each read is the whole set, so re-entry or
 * reconnect never brings an old one back. Members are the application windows the session display shows now (front)
 * then the session's target app, without the controller itself. While the phone is locked nothing is listed. The
 * generation rises on every change of status, session or members, so a consumer can replace its set exactly when it
 * changed. Completeness is explicit: an empty set is complete only when no session is live. A live set is never
 * claimed complete, because the controller cannot see an app hidden behind the front on its own session display
 * (incompleteReason behind_front_unlisted), and it says so too when the windows could not be read (windows_unreadable)
 * or the session is blocked (blocked); while locked nothing is known (locked).
 *
 * When the platform answers (ComputerControlSession.getAutomatedApps, #156 parity: the same automated-package
 * repository Launcher3's listener reads), its packages are the members instead: every app with a non-finishing activity
 * of the owner's user on the session's own device, front or hidden. Its completeness is the platform's (false only when
 * another user's activity was omitted: other_user_omitted); a closed session lists nothing. Front windows still mark
 * which members are visible now. A missing, failed or malformed platform answer falls back to the session windows
 * above, marked incomplete. A session the controller still holds but could not release (cleanup_failed) is never a
 * genuine empty (session_unreleased), whatever its retained binder answers.
 *
 * Preferred source, independent of any session (ComputerControlExtensions.getOwnedAutomatedDevices, PR374): the platform's
 * snapshot of every session device the controller owns in its user, the same repository and generation Launcher3's
 * listener reads. One rule on that snapshot, shared with Launcher3: no device is genuine empty; exactly one device lists
 * its apps (complete only when the device is: other_user_omitted otherwise); more than one is multiple_devices, never
 * complete and with no members. A failed-close or replacement device stays listed until the platform removes it, so
 * attested apps are shown whatever the session's state; the session status is only informational then.
 *
 * Rendering contract shared with Launcher3 and Home (#156/#225 parity, qa/parity/current-apps-parity.json):
 * complete with apps shows exactly those apps; complete and empty is genuine empty; anything not complete shows no app
 * cards and an explicit cannot-confirm status. Pure logic, no Android types. */
final class CurrentApps {
 private CurrentApps() {}

 static final String LIVE = "live", NONE = "none", LOCKED = "locked";

 /** Whether a controller status is a live session whose apps are running under agent control. */
 static boolean live(String status) {
  return "ready".equals(status) || "blocked".equals(status) || "launching".equals(status);
 }

 static final class App {
  final String packageName;final boolean front,target;
  App(String packageName, boolean front, boolean target) {this.packageName = packageName;this.front = front;this.target = target;}
 }

 static final class Result {
  final String status,incompleteReason,source;final List<App> apps;final boolean complete;
  Result(String status, List<App> apps, boolean complete, String incompleteReason) {this(status, apps, complete, incompleteReason, SESSION_WINDOWS);}
  Result(String status, List<App> apps, boolean complete, String incompleteReason, String source) {
   this.status = status;this.apps = Collections.unmodifiableList(apps);this.complete = complete;this.incompleteReason = incompleteReason;this.source = source;
  }
 }

 static final String BEHIND_FRONT_UNLISTED = "behind_front_unlisted", WINDOWS_UNREADABLE = "windows_unreadable",
  BLOCKED = "blocked", OTHER_USER_OMITTED = "other_user_omitted",
  /** The controller still holds a session it could not release (cleanup_failed): its device may still run apps, so
   * nothing can be confirmed, and certainly not a genuine empty. */
  SESSION_UNRELEASED = "session_unreleased";
 /** Where the members came from. */
 static final String PLATFORM = "platform", SESSION_WINDOWS = "session_windows", OWNED_DEVICES = "owned_devices";
 static final String MULTIPLE_DEVICES = "multiple_devices";

 /** The platform's owned-device snapshot (the agreed PR374 Bundle keys), validated. */
 static final class Owned {
  final long generation;final int[] deviceIds;final boolean[] deviceComplete;final List<List<String>> apps;
  Owned(long generation, int[] deviceIds, boolean[] deviceComplete, List<List<String>> apps) {
   this.generation = generation;this.deviceIds = deviceIds;this.deviceComplete = deviceComplete;this.apps = apps;
  }
 }

 /** The Bundle's values as read ("generation" long, "deviceIds" int[], "deviceComplete" boolean[], "appDeviceIds"
  * int[], "appPackageNames" String[]), or null when any is missing or of another type, the parallel arrays differ in
  * length, a device id repeats, an app names an unlisted device, or a package name is empty. */
 static Owned owned(Object generation, Object deviceIds, Object deviceComplete, Object appDeviceIds, Object appPackageNames) {
  if (!(generation instanceof Long) || !(deviceIds instanceof int[]) || !(deviceComplete instanceof boolean[])
    || !(appDeviceIds instanceof int[]) || !(appPackageNames instanceof String[])) return null;
  int[] ids = (int[]) deviceIds, appIds = (int[]) appDeviceIds;boolean[] complete = (boolean[]) deviceComplete;String[] names = (String[]) appPackageNames;
  if (ids.length != complete.length || appIds.length != names.length) return null;
  List<List<String>> apps = new ArrayList<>();
  for (int i = 0; i < ids.length; i++) {
   for (int j = 0; j < i; j++) if (ids[j] == ids[i]) return null;
   apps.add(new ArrayList<String>());
  }
  for (int k = 0; k < appIds.length; k++) {
   if (names[k] == null || names[k].isEmpty()) return null;
   int at = -1;for (int i = 0; i < ids.length; i++) if (ids[i] == appIds[k]) at = i;
   if (at < 0) return null;
   if (!apps.get(at).contains(names[k])) apps.get(at).add(names[k]);
  }
  return new Owned((Long) generation, ids.clone(), complete.clone(), apps);
 }

 /** Membership from the owned-device snapshot, whatever the session's state (see the class comment). front: the live
  * session display's application windows, null when not read; target: the live session's target, for the flag. */
 static Result of(boolean locked, String target, Owned owned, List<String> front, String controller) {
  if (locked) return new Result(LOCKED, new ArrayList<App>(), false, LOCKED, OWNED_DEVICES);
  if (owned.deviceIds.length == 0) return new Result(NONE, new ArrayList<App>(), true, "", OWNED_DEVICES);
  if (owned.deviceIds.length > 1) return new Result(LIVE, new ArrayList<App>(), false, MULTIPLE_DEVICES, OWNED_DEVICES);
  List<String> members = owned.apps.get(0);List<App> apps = new ArrayList<>();
  if (front != null) for (String p : front)
   if (p != null && members.contains(p) && !p.equals(controller) && !contains(apps, p)) apps.add(new App(p, true, p.equals(target)));
  List<String> rest = new ArrayList<>(members);Collections.sort(rest);
  for (String p : rest) if (!p.equals(controller) && !contains(apps, p)) apps.add(new App(p, false, p.equals(target)));
  boolean complete = owned.deviceComplete[0];
  return new Result(LIVE, apps, complete, complete ? "" : OTHER_USER_OMITTED, OWNED_DEVICES);
 }

 /** The platform's answer for the session's own device (the agreed Bundle keys), validated. */
 static final class Platform {
  final long generation;final List<String> packages;final boolean complete,closed;
  Platform(long generation, List<String> packages, boolean complete, boolean closed) {
   this.generation = generation;this.packages = Collections.unmodifiableList(packages);this.complete = complete;this.closed = closed;
  }
 }

 /** The Bundle's values as read ("generation" long, "packageNames" String[], "userId" int, "complete" boolean,
  * "closed" boolean), or null when any is missing or of another type, a package name is empty, the user is not the
  * caller's own, or a closed answer still lists apps. */
 static Platform platform(Object generation, Object packageNames, Object userId, Object complete, Object closed, int ownUser) {
  if (!(generation instanceof Long) || !(packageNames instanceof String[]) || !(userId instanceof Integer)
    || !(complete instanceof Boolean) || !(closed instanceof Boolean)) return null;
  if ((Integer) userId != ownUser) return null;
  List<String> packages = new ArrayList<>();
  for (String p : (String[]) packageNames) {
   if (p == null || p.isEmpty()) return null;
   if (!packages.contains(p)) packages.add(p);
  }
  if ((Boolean) closed && !packages.isEmpty()) return null;
  return new Platform((Long) generation, packages, (Boolean) complete, (Boolean) closed);
 }

 /** Membership from the platform's answer: its packages, front ones first in front order, then the rest in its
  * (sorted) order; the controller never; completeness the platform's; closed lists nothing. */
 static Result of(boolean locked, boolean hasSession, String status, String target, Platform platform, List<String> front, String controller) {
  if (locked) return new Result(LOCKED, new ArrayList<App>(), false, LOCKED, PLATFORM);
  // An unreleased session stays unconfirmed even when its retained binder answers closed: the platform marks a session
  // closed when its close begins (PR361 ComputerControlSessionImpl.close), before the device is torn down, so after a
  // failed close the device may still run apps that Launcher3 lists.
  if (hasSession && !live(status)) return new Result(NONE, new ArrayList<App>(), false, SESSION_UNRELEASED, PLATFORM);
  if (platform.closed) return new Result(NONE, new ArrayList<App>(), true, "", PLATFORM);
  if (!hasSession || !live(status)) return new Result(NONE, new ArrayList<App>(), true, "", PLATFORM);
  List<App> apps = new ArrayList<>();
  if (front != null) for (String p : front)
   if (p != null && platform.packages.contains(p) && !p.equals(controller) && !contains(apps, p)) apps.add(new App(p, true, p.equals(target)));
  for (String p : platform.packages)
   if (!p.equals(controller) && !contains(apps, p)) apps.add(new App(p, false, p.equals(target)));
  return new Result(LIVE, apps, platform.complete, platform.complete ? "" : OTHER_USER_OMITTED, PLATFORM);
 }

 private static boolean contains(List<App> apps, String p) {
  for (App a : apps) if (a.packageName.equals(p)) return true;
  return false;
 }

 /** front: the packages of the session display's application windows now, top first; null when they were not read. */
 static Result of(boolean locked, boolean hasSession, String status, boolean blocked, String target, List<String> front, String controller) {
  if (locked) return new Result(LOCKED, new ArrayList<App>(), false, LOCKED);
  if (hasSession && !live(status)) return new Result(NONE, new ArrayList<App>(), false, SESSION_UNRELEASED);
  if (!hasSession || !live(status)) return new Result(NONE, new ArrayList<App>(), true, "");
  List<App> apps = new ArrayList<>();List<String> seen = new ArrayList<>();
  if (front != null) for (String p : front) {
   if (p == null || p.isEmpty() || p.equals(controller) || seen.contains(p)) continue;
   seen.add(p);apps.add(new App(p, true, p.equals(target)));
  }
  if (target != null && !target.isEmpty() && !target.equals(controller) && !seen.contains(target)) apps.add(new App(target, false, true));
  String reason = blocked || "blocked".equals(status) ? BLOCKED : front == null ? WINDOWS_UNREADABLE : BEHIND_FRONT_UNLISTED;
  return new Result(LIVE, apps, false, reason);
 }

 /** What a consumer sees, for the generation: status, session, its controller status, completeness, source and members
  * in order. The platform's own per-device generation is not part of it: it restarts with each session device (and is
  * Long.MAX_VALUE once closed), so the consumer's generation is the controller's counter, which rises across sessions. */
 static String signature(Result r, String sessionId) {
  return signature(r, sessionId, null);
 }
 static String signature(Result r, String sessionId, String sessionStatus) {
  boolean live = LIVE.equals(r.status);
  StringBuilder b = new StringBuilder(r.status).append('|').append(live && sessionId != null ? sessionId : "")
   .append('|').append(live && sessionStatus != null ? sessionStatus : "")
   .append('|').append(r.complete).append('|').append(r.incompleteReason).append('|').append(r.source);
  for (App a : r.apps) b.append('|').append(a.packageName).append(a.front ? ":f" : ":-").append(a.target ? "t" : "-");
  return b.toString();
 }
}
