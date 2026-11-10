package dev.pixel.voice;

/**
 * 1C: where the return control learns the app the owner was using before Rose came forward, and how it brings that
 * exact live task back. App Core supplies the implementation (task restoration); until it does, {@link #NONE} offers
 * nothing, so the control stays hidden rather than guessing an app.
 */
interface ReturnSource {
 /** The owner's last live foreground app before Rose (never Rose, Home, the background workspace or system UI), or
  * null when there is none or it is not known. Called on the main thread; must not block. */
 ReturnModel.Target current();

 /** Bring exactly that task to the front. answer runs once, on any thread, with RETURNED only after it is in front,
  * UNAVAILABLE when the task no longer exists, or REFUSED otherwise. */
 void returnTo(ReturnModel.Target target, java.util.function.Consumer<ReturnModel.Answer> answer);

 ReturnSource NONE = new ReturnSource() {
  public ReturnModel.Target current() { return null; }
  public void returnTo(ReturnModel.Target target, java.util.function.Consumer<ReturnModel.Answer> answer) { answer.accept(ReturnModel.Answer.REFUSED); }
 };
}
