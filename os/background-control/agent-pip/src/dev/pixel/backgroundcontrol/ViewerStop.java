package dev.pixel.backgroundcontrol;

/** What the agent viewer's Stop (#410, Nathan-approved) says and does after the owner confirms it. Only the confirmation's
 * positive button reaches Controller.viewerStop; closing, Back, Home, picture-in-picture and every lifecycle path still
 * never stop work. The controller's reply decides: a confirmed owner Stop closes the viewer; cleanup_failed and locked keep
 * it, with Stop available again; every refusal stops nothing and closes the viewer, which no longer shows that work.
 * Wording agreed with Agent Product/Home (#410, 00:02 UTC Nov 3). Pure logic, no Android types. */
final class ViewerStop {
 static final String BUTTON = "Stop", KEEP = "Keep working";
 static final String MESSAGE = "This stops only this session. Rose’s other work keeps running. "
  + "Closing this view doesn’t stop anything.";
 static final String STOPPED = "Stopped. Rose’s other work keeps running.",
  CLEANUP_FAILED = "Android couldn’t close this session yet. It may still be running.",
  UNCONFIRMED = "Rose couldn’t confirm the stop. It may still be running.",
  ENDED = "This work already ended. Nothing was stopped.",
  REPLACED = "This view no longer matches Rose’s work. Nothing was stopped.",
  RETAGGED = "Rose moved this session to another task. Nothing was stopped.",
  LOCKED = "Unlock to stop it.";

 private ViewerStop() {}

 static String title(String app) { return "Stop Rose’s work in " + app + "?"; }
 static String description(String app) { return "Stop Rose’s work in " + app; }

 /** The viewer's response to one confirmed Stop. */
 static final class Outcome {
  final String text; final boolean closeViewer, stopAgain;
  private Outcome(String text, boolean closeViewer, boolean stopAgain) {
   this.text = text; this.closeViewer = closeViewer; this.stopAgain = stopAgain;
  }
 }

 /** status and confirmed from Controller.viewerStop. A refusal stops nothing; an ended, re-tagged or replaced pair (or an
  * unbound one, which a bound viewer never sends) also closes the viewer. A stop that ran but is not confirmed, and
  * anything unrecognised (an error), is never "Stopped" or "Nothing was stopped": it may still be running, and Stop stays. */
 static Outcome of(String status, boolean confirmed) {
  if ("stopped".equals(status)) return confirmed ? new Outcome(STOPPED, true, false) : new Outcome(UNCONFIRMED, false, true);
  if ("cleanup_failed".equals(status)) return new Outcome(CLEANUP_FAILED, false, true);
  if (MultiSession.VIEWER_LOCKED.equals(status)) return new Outcome(LOCKED, false, true);
  if (MultiSession.VIEWER_ENDED.equals(status)) return new Outcome(ENDED, true, false);
  if (MultiSession.VIEWER_RETAGGED.equals(status)) return new Outcome(RETAGGED, true, false);
  if (MultiSession.VIEWER_REPLACED.equals(status) || MultiSession.VIEWER_UNBOUND.equals(status))
   return new Outcome(REPLACED, true, false);
  return new Outcome(UNCONFIRMED, false, true);
 }
}
