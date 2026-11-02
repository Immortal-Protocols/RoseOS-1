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
 * changed. Pure logic, no Android types. */
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
  final String status;final List<App> apps;
  Result(String status, List<App> apps) {this.status = status;this.apps = Collections.unmodifiableList(apps);}
 }

 /** front: the packages of the session display's application windows now, top first (empty when not read). */
 static Result of(boolean locked, boolean hasSession, String status, String target, List<String> front, String controller) {
  if (locked) return new Result(LOCKED, new ArrayList<App>());
  if (!hasSession || !live(status)) return new Result(NONE, new ArrayList<App>());
  List<App> apps = new ArrayList<>();List<String> seen = new ArrayList<>();
  if (front != null) for (String p : front) {
   if (p == null || p.isEmpty() || p.equals(controller) || seen.contains(p)) continue;
   seen.add(p);apps.add(new App(p, true, p.equals(target)));
  }
  if (target != null && !target.isEmpty() && !target.equals(controller) && !seen.contains(target)) apps.add(new App(target, false, true));
  return new Result(LIVE, apps);
 }

 /** What a consumer sees, for the generation: status, session and members in order. */
 static String signature(Result r, String sessionId) {
  StringBuilder b = new StringBuilder(r.status).append('|').append(LIVE.equals(r.status) && sessionId != null ? sessionId : "");
  for (App a : r.apps) b.append('|').append(a.packageName).append(a.front ? ":f" : ":-").append(a.target ? "t" : "-");
  return b.toString();
 }
}
