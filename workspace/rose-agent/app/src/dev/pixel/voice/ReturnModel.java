package dev.pixel.voice;

/**
 * 1C (Nathan's Nov 9–10 plan; V3, bottom-left of Rose's content above the chin): the compact named control that
 * returns to the app the owner was using before Rose came forward. Pure rules, no Android types, tested on the host.
 *
 * <p>One tap starts the return and shows it as pending for a short, cancellable moment ({@link #COMMIT_MS}); the
 * return is then asked of the {@link ReturnSource}, which brings that exact live task back to the front (its own
 * draft, selection, scroll and keyboard are the app's and stay as they were). Rose's conversation and work keep going;
 * nothing here ends a call or stops a task. Only the source's answer for this attempt counts (a correlated attempt
 * number): success hides the control (the app is in front), "unavailable" says honestly that the app is no longer
 * open and offers nothing to return to, and a refusal keeps the control so the owner can try again.
 */
final class ReturnModel {
 private ReturnModel() {}

 /** The cancellable moment between the tap and the return, and the longest the source may take to answer. */
 static final long COMMIT_MS = 450, ANSWER_MS = 4000, UNAVAILABLE_MS = 4000;
 static final int MAX_LABEL = 18;

 enum Phase { HIDDEN, READY, PENDING, RETURNING, UNAVAILABLE, FAILED }
 enum Answer { RETURNED, UNAVAILABLE, REFUSED }

 /** The app to return to: its package, its own label and the source's identity for that exact live task. */
 static final class Target {
  final String packageName, label, token;
  Target(String packageName, String label, String token) { this.packageName = packageName; this.label = label; this.token = token; }
  boolean same(Target o) { return o != null && packageName.equals(o.packageName) && token.equals(o.token); }
 }

 /** One immutable state; every transition returns a new one. attempt correlates a return with its answer. */
 static final class State {
  final Phase phase; final Target target; final int attempt; final long since;
  State(Phase phase, Target target, int attempt, long since) { this.phase = phase; this.target = target; this.attempt = attempt; this.since = since; }

  /** The source now reports this target (null when there is no app to return to). A pending or running return keeps
   * its own target; a different app resets the control. */
  State observed(Target t, long now) {
   // A return already started keeps going to its own answer (an app that is gone is answered UNAVAILABLE).
   if (t == null) return phase == Phase.UNAVAILABLE || phase == Phase.PENDING || phase == Phase.RETURNING ? this : new State(Phase.HIDDEN, null, attempt, now);
   if (target != null && target.same(t)) return phase == Phase.HIDDEN || phase == Phase.UNAVAILABLE ? new State(Phase.READY, t, attempt, now) : this;
   if (phase == Phase.PENDING || phase == Phase.RETURNING) return this;
   return new State(Phase.READY, t, attempt, now);
  }
  /** A tap on the control: READY or FAILED starts a new attempt; a tap while pending cancels it. */
  State tapped(long now) {
   if (phase == Phase.READY || phase == Phase.FAILED) return new State(Phase.PENDING, target, attempt + 1, now);
   if (phase == Phase.PENDING) return cancelled(now);
   return this;
  }
  /** Cancel (a second tap, Back, or leaving the screen) before the return was asked for. */
  State cancelled(long now) { return phase == Phase.PENDING ? new State(Phase.READY, target, attempt, now) : this; }
  /** The cancellable moment ended: ask the source (the caller does so exactly when this returns RETURNING). */
  State committed(long now) { return phase == Phase.PENDING && now - since >= COMMIT_MS ? new State(Phase.RETURNING, target, attempt, now) : this; }
  /** The source's answer; one for another attempt, or arriving after the state moved on, is ignored. */
  State answered(int forAttempt, Answer a, long now) {
   if (phase != Phase.RETURNING || forAttempt != attempt) return this;
   switch (a) {
    case RETURNED: return new State(Phase.HIDDEN, null, attempt, now);
    case UNAVAILABLE: return new State(Phase.UNAVAILABLE, target, attempt, now);
    default: return new State(Phase.FAILED, target, attempt, now);
   }
  }
  /** No answer in time is not a success: it is shown as a failure the owner can retry. */
  State timedOut(long now) { return phase == Phase.RETURNING && now - since >= ANSWER_MS ? new State(Phase.FAILED, target, attempt, now) : this; }

  /** The "isn't open now" line stays long enough to read, then the control leaves. */
  State expired(long now) { return phase == Phase.UNAVAILABLE && now - since >= UNAVAILABLE_MS ? new State(Phase.HIDDEN, null, attempt, now) : this; }

  boolean visible() { return phase != Phase.HIDDEN; }
  /** The control acts (as a button) unless the app is no longer open. */
  boolean actionable() { return phase == Phase.READY || phase == Phase.PENDING || phase == Phase.FAILED; }
 }

 static State initial() { return new State(Phase.HIDDEN, null, 0, 0); }

 /** The app's name as shown: control characters removed, trimmed, at most MAX_LABEL characters. */
 static String name(String label) {
  String clean = label == null ? "" : label.replaceAll("\\p{Cntrl}", "").trim();
  if (clean.isEmpty()) return "previous app";
  return clean.length() > MAX_LABEL ? clean.substring(0, MAX_LABEL - 1) + "…" : clean;
 }

 /** The visible text: compact and named (V3). */
 static String text(State s) {
  if (s.target == null) return "";
  String n = name(s.target.label);
  switch (s.phase) {
   case PENDING: case RETURNING: return "Returning to " + n;
   case UNAVAILABLE: return n + " isn’t open now";
   case FAILED: return "Couldn’t return · Try again";
   default: return n;
  }
 }

 /** What the control says to a screen reader. */
 static String spoken(State s) {
  if (s.target == null) return "";
  String n = name(s.target.label);
  switch (s.phase) {
   case PENDING: return "Returning to " + n + ". Tap to cancel";
   case RETURNING: return "Returning to " + n;
   case UNAVAILABLE: return n + " is no longer open, so there is nothing to return to";
   case FAILED: return "Couldn’t return to " + n + ". Tap to try again";
   default: return "Return to " + n;
  }
 }
}
