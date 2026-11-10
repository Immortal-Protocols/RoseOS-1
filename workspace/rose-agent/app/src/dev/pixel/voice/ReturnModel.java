package dev.pixel.voice;

/**
 * 1C (Nathan's Nov 9–10 plan): the approved V3 return control, "Return to your app" (Designer's F11/RETURN-1N study,
 * Library version 3, Rose-F11-Return.html SHA-256 798c9a30…955ae5). Pure rules, no Android types, tested on the host.
 *
 * <p>V3's contract: a tap requests the return directly, with no confirmation and no delay. While it is pending the
 * control reads "Returning…", duplicate requests are ignored, Rose stays usable and a separate Cancel stays
 * available. Cancelling invalidates the request: a late, duplicate or mismatched answer changes nothing. Only an
 * acknowledged return to the exact live source task leaves Rose (the source app keeps its own draft, selection, scroll
 * and keyboard). A missing or replaced source, or a failed transfer, stays in Rose and offers Stay in Rose, Open
 * switcher or Go Home, with no relaunch. Rose's work, voice session and microphone are independent of all of this.
 */
final class ReturnModel {
 private ReturnModel() {}

 enum Phase { HIDDEN, READY, RETURNING, UNAVAILABLE, FAILED }
 /** The platform's definitive answer for one request. UNAVAILABLE covers a closed or replaced source task. */
 enum Answer { RETURNED, UNAVAILABLE, REFUSED }

 /** The app to return to: its package, its own label and the platform's identity for that exact live task. */
 static final class Target {
  final String packageName, label, token;
  Target(String packageName, String label, String token) { this.packageName = packageName; this.label = label; this.token = token; }
  boolean same(Target o) { return o != null && packageName.equals(o.packageName) && token.equals(o.token); }
 }

 /** One immutable state; every transition returns a new one. request correlates a return with its answer. */
 static final class State {
  final Phase phase; final Target target; final int request;
  State(Phase phase, Target target, int request) { this.phase = phase; this.target = target; this.request = request; }

  /** The platform now reports this source (null when there is none). A pending request or an open sheet keeps its
   * own source; a different task while idle replaces it (a replaced task is never the same source). */
  State observed(Target t) {
   if (phase == Phase.RETURNING || phase == Phase.UNAVAILABLE || phase == Phase.FAILED) return this;
   if (t == null) return phase == Phase.HIDDEN ? this : new State(Phase.HIDDEN, null, request);
   if (phase == Phase.READY && target.same(t)) return this;
   return new State(Phase.READY, t, request);
  }
  /** A tap requests the return directly (a new request id); taps while pending are ignored. */
  State tapped() { return phase == Phase.READY ? new State(Phase.RETURNING, target, request + 1) : this; }
  /** Cancel (its button, Back or leaving Rose) invalidates the pending request; Rose stays. */
  State cancelled() { return phase == Phase.RETURNING ? new State(Phase.READY, target, request) : this; }
  /** The platform's answer; one for another request, or after a cancel, is ignored. */
  State answered(int forRequest, Answer a) {
   if (phase != Phase.RETURNING || forRequest != request) return this;
   switch (a) {
    case RETURNED: return new State(Phase.HIDDEN, null, request);
    case UNAVAILABLE: return new State(Phase.UNAVAILABLE, target, request);
    default: return new State(Phase.FAILED, target, request);
   }
  }
  /** Stay in Rose (or Back) closes the sheet: a source that is gone is not offered again; a failed transfer may be. */
  State stayed() {
   if (phase == Phase.UNAVAILABLE) return new State(Phase.HIDDEN, null, request);
   if (phase == Phase.FAILED) return new State(Phase.READY, target, request);
   return this;
  }

  boolean controlShown() { return phase == Phase.READY || phase == Phase.RETURNING; }
  boolean pending() { return phase == Phase.RETURNING; }
  boolean sheetShown() { return phase == Phase.UNAVAILABLE || phase == Phase.FAILED; }
 }

 static State initial() { return new State(Phase.HIDDEN, null, 0); }

 /** The source app's name: control characters removed and trimmed, or null when it has none. The view truncates. */
 static String name(String label) {
  String clean = label == null ? "" : label.replaceAll("\\p{Cntrl}", "").trim();
  return clean.isEmpty() ? null : clean;
 }

 /** The visible label (V3 sourceLabel). */
 static String text(State s) {
  if (s.pending()) return "Returning…";
  String n = s.target == null ? null : name(s.target.label);
  return n == null ? "Previous app" : n;
 }

 /** The accessible name: always the full source name (V3 aria-label). */
 static String spoken(State s) {
  String n = s.target == null ? null : name(s.target.label);
  return n == null ? "Return to previous app" : "Return to " + n;
 }

 /** V3's status lines, announced to assistive technology, and its sheet. */
 static final String REQUESTED = "Return requested. Rose remains available until confirmation.",
   CANCELLED = "Return request cancelled. Late confirmation will be ignored.",
   HELP = "Activate to return to your source app. Moving off before release cancels.",
   CANCEL = "Cancel",
   SHEET_TITLE = "Your source app is unavailable.",
   SHEET_BODY = "You are still in Rose. Choose where to go; Rose keeps working.",
   STAY = "Stay in Rose", SWITCHER = "Open switcher", HOME = "Go Home";

 /** V3 motion, for the platform's transition on the exact-task reorder (OS Core's shell transition; a WMShell
  * reorder carries no ActivityOptions): only on acknowledged success, Rose fades and moves down 8dp over 200 ms with
  * cubic-bezier(.2,.7,.3,1) while the source stays still; reduced motion is immediate. Rose never departs before the
  * acknowledgement: an unacknowledged return stays in Rose. */
 static final int DEPART_MS = 200, DEPART_DP = 8;
 static final float[] DEPART_EASE = {0.2f, 0.7f, 0.3f, 1f};
}
