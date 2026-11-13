package dev.pixel.builder;

/**
 * Project 1 (Nov 13 plan, Level 1): one build of a generated app, as the phone shows it. App Core's build service owns
 * the job; this is the client's reading of its state. Pure rules, no Android types, tested on the host.
 *
 * <p>Honest progress: a job is only "ready" once the server reports a signed artifact for it; a failure always names
 * its category and whether a retry can help; nothing is inferred from elapsed time.
 */
final class BuildJob {
 enum Stage { QUEUED, PREPARING, BUILDING, TESTING, SIGNING, READY, FAILED, CANCELLED }
 enum Category { TOOLCHAIN, DEPENDENCY, COMPILE, TEST, SIGNING, TIMEOUT, CANCELLED, INTERNAL, UNREACHABLE }

 static final class Failure {
  final Category category; final String summary; final boolean retryable;
  Failure(Category category, String summary, boolean retryable) { this.category = category; this.summary = summary; this.retryable = retryable; }
 }

 /** attempt: App Core's retry count for this job, from 1, so a retry is visible rather than silent. */
 final String jobId; final Stage stage; final String message; final Failure failure; final int attempt;
 BuildJob(String jobId, Stage stage, String message, Failure failure) { this(jobId, stage, message, failure, 1); }
 BuildJob(String jobId, Stage stage, String message, Failure failure, int attempt) {
  this.jobId = jobId; this.stage = stage; this.message = message; this.failure = failure; this.attempt = Math.max(1, attempt);
 }

 boolean terminal() { return stage == Stage.READY || stage == Stage.FAILED || stage == Stage.CANCELLED; }
 boolean cancellable() { return !terminal(); }
 /** builder API v1 retry: only a failed job whose failure says a retry can help; it runs again as attempt + 1. */
 boolean retryable() { return stage == Stage.FAILED && failure != null && failure.retryable; }

 /** The one-line status the phone shows; a live retry says which attempt it is. */
 static String status(BuildJob j) { return status(j, false); }

 /** stopRequested: the owner pressed Cancel. App Core's cancel only means "stopping"; the build reads as cancelled
  * only once the job itself reports CANCELLED, and a job that finished first keeps its real result. */
 static String status(BuildJob j, boolean stopRequested) {
  if (stopRequested && !j.terminal()) return "Stopping the build…";
  String s = stageText(j);
  return j.attempt > 1 && !j.terminal() ? s + " (attempt " + j.attempt + ")" : s;
 }

 private static String stageText(BuildJob j) {
  switch (j.stage) {
   case QUEUED: return "Waiting for the builder";
   case PREPARING: return "Setting up the project";
   case BUILDING: return "Building the app";
   case TESTING: return "Running its tests";
   case SIGNING: return "Signing the app";
   case READY: return "Ready to install";
   case CANCELLED: return "Build cancelled";
   default: return failureTitle(j.failure);
  }
 }

 static String failureTitle(Failure f) {
  if (f == null) return "The build failed";
  switch (f.category) {
   case TOOLCHAIN: return "The builder's Android tools aren't working";
   // builder API v1: generated apps use framework APIs only; AndroidX, Kotlin, Gradle or other libraries fail here.
   case DEPENDENCY: return "The app needs a library or tool the builder doesn't support";
   case COMPILE: return "The app's code didn't compile";
   case TEST: return "The app's tests failed";
   case SIGNING: return "The app couldn't be signed";
   case TIMEOUT: return "The build took too long and was stopped";
   case CANCELLED: return "Build cancelled";
   case UNREACHABLE: return "Can't reach your Rose builder";
   default: return "The builder hit an internal error";
  }
 }

 /** What the owner can do next after a failure: never "retry" when the server says it would not help. */
 static String failureAdvice(Failure f) {
  if (f == null) return "Try again, or describe the change differently.";
  if (f.category == Category.UNREACHABLE) return "Check that your Mac or builder is on and connected, then reconnect.";
  if (f.category == Category.CANCELLED) return "Start the build again when you're ready.";
  return f.retryable ? "Try again. If it fails the same way, describe the change differently." : "Describe the change differently, or ask Rose what went wrong.";
 }

 /** Stage order, so a stale or out-of-order update never moves progress backwards. */
 static boolean advances(Stage from, Stage to) {
  if (from == null) return true;
  if (isTerminal(from)) return false;
  if (isTerminal(to)) return true;
  return to.ordinal() >= from.ordinal();
 }

 static boolean isTerminal(Stage s) { return s == Stage.READY || s == Stage.FAILED || s == Stage.CANCELLED; }
}
