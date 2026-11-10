package dev.pixel.voice;

/**
 * 1C: where the V3 return control learns the owner's source app and how it returns to that exact live task. App Core
 * supplies the implementation (task restoration, within existing permissions); until it does, {@link #NONE} offers
 * nothing, so the control stays hidden rather than guessing an app. A package launch is never a substitute: only the
 * captured live task counts, and a closed or replaced task is answered UNAVAILABLE.
 */
interface ReturnSource {
 /** The owner's live source task from before Rose came forward (never Rose, Home, the background workspace or system
  * UI), with the platform's token for exactly that task, or null when there is none or it is not known. Called on the
  * main thread about every 150 ms while Rose is visible; must not block. */
 ReturnModel.Target current();

 /** Bring exactly that task to the front. answer runs once, on any thread: RETURNED only after the platform
  * acknowledges that this task is in front, UNAVAILABLE when it is closed or replaced, REFUSED otherwise. The caller
  * applies the answer only to its own request, so a late answer after a cancel changes nothing in Rose. The platform
  * should use {@link ReturnTransition} for Rose's departure. */
 void returnTo(ReturnModel.Target target, java.util.function.Consumer<ReturnModel.Answer> answer);

 /** The owner cancelled the pending request. The platform stops it if it can; a transfer that already completed is
  * not assumed undone (its answer is simply ignored). */
 default void cancel(ReturnModel.Target target) {}

 ReturnSource NONE = new ReturnSource() {
  public ReturnModel.Target current() { return null; }
  public void returnTo(ReturnModel.Target target, java.util.function.Consumer<ReturnModel.Answer> answer) { answer.accept(ReturnModel.Answer.REFUSED); }
 };
}
