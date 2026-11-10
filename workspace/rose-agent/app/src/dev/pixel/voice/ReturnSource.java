package dev.pixel.voice;

/**
 * 1C: where the V3 return control learns the owner's source app and how it returns to that exact live task. App Core
 * supplies the implementation (task restoration, within existing permissions); until it does, {@link #NONE} offers
 * nothing, so the control stays hidden rather than guessing an app. A package launch is never a substitute: only the
 * captured live task counts, and a closed or replaced task is answered UNAVAILABLE.
 *
 * <p>Agreed seam, final v1 (OS Core 00:58 UTC 2026-11-10; ACKed by App Core and Home/App), on the existing always-on
 * status-bar tray link (unchanged trust, no new permission or exported surface):
 * <ul>
 * <li>SystemUI to Rose: 7 RETURN_TARGET {protocolVersion 1, token (0 = none), package, userId, label resolved by
 * SystemUI for the task's user}; the token is stable while the recorded task is unchanged. 8 RETURN_RESULT, arg1 the
 * attempt, arg2 0 RETURNED (exactly that task focused, identity rechecked), 1 UNAVAILABLE (gone, replaced, moved or
 * user changed), 2 REFUSED (stale token, keyguard, Rose not in focus, timeout).</li>
 * <li>Rose to SystemUI: 101 RETURN_TO, arg1 the attempt, {token}; 102 RETURN_CANCEL, arg1 the attempt.</li>
 * <li>The shell reorders exactly that task (no launch of any kind) and answers within 1.5 s; Rose waits 4 s, then
 * sends 102 and reports REFUSED. The shell plays V3's departure (ReturnModel.DEPART_*) on the applied reorder only.</li>
 * </ul>
 */
interface ReturnSource {
 /** The owner's live source task from before Rose came forward (never Rose, Home, the background workspace or system
  * UI), with the platform's token for exactly that task, or null when there is none or it is not known. Called on the
  * main thread about every 150 ms while Rose is visible; must not block. */
 ReturnModel.Target current();

 /** Bring exactly that task to the front. answer runs once, on any thread: RETURNED only after the platform
  * acknowledges that this task is in front, UNAVAILABLE when it is closed or replaced, REFUSED otherwise. The caller
  * applies the answer only to its own request, so a late answer after a cancel changes nothing in Rose. Rose's V3
  * departure (ReturnModel.DEPART_*) is the platform's transition on that reorder, after acknowledgement. */
 void returnTo(ReturnModel.Target target, java.util.function.Consumer<ReturnModel.Answer> answer);

 /** The owner cancelled the pending request. The platform stops it if it can; a transfer that already completed is
  * not assumed undone (its answer is simply ignored). */
 default void cancel(ReturnModel.Target target) {}

 ReturnSource NONE = new ReturnSource() {
  public ReturnModel.Target current() { return null; }
  public void returnTo(ReturnModel.Target target, java.util.function.Consumer<ReturnModel.Answer> answer) { answer.accept(ReturnModel.Answer.REFUSED); }
 };
}
