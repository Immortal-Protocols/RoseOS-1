package dev.pixel.backgroundcontrol;

/** The result of the owner's last button press in MainActivity (#225), kept apart from the session status that
 * the 500 ms refresh rewrites, so an error or "nothing to do" stays readable. It reports what actually happened;
 * it never claims success for an action that did not run. Survives recreation through its saved text.
 * Pure logic, no Android types. */
final class ActionNotice {
 static final String SAVED_KEY = "dev.pixel.backgroundcontrol.notice";

 private String text = "";

 String text() { return text; }

 /** Restore the notice saved by a previous instance; null or empty restores nothing. */
 void restore(String saved) { text = saved == null ? "" : saved; }

 /** Continue Android consent: launched is Controller.requestConsent's result. */
 void consent(boolean launched) {
  text = launched ? "Android consent opened." : "No consent is pending.";
 }

 /** Any button whose action threw. */
 void failed(String action, Throwable error) {
  String detail = error == null ? "unknown error" : error.toString();
  text = action + " failed: " + (detail.length() > 300 ? detail.substring(0, 300) : detail);
 }

 /** Stop background session, from Controller's status and error after the stop returned. */
 void stopped(String status, String error) {
  if ("cleanup_failed".equals(status)) {
   text = "Stop did not finish: the session could not be closed" + (error == null || error.isEmpty() ? "." : ": " + error);
  } else if ("stopped".equals(status)) {
   text = "Background session stopped.";
  } else {
   text = "Stop requested; session status is " + status + ".";
  }
 }
}
