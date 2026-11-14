package dev.pixel.voice;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * 1C (Nov 9 plan): the V3 return control's exact-task source, over the existing SystemUI tray link (AgentTrayService).
 * SystemUI's shell records the owner's previous live task when Rose comes forward and is the only side that can name
 * or front it; Rose holds only its opaque token. Protocol v1 as OS Core finalized it with Home/App and App Core:
 * <ul>
 * <li>{@link #TARGET} (SystemUI to Rose, replyTo set): {protocolVersion 1, token (0 = none), package, userId, label}.
 * The label is SystemUI's, for the task's user. The token stays the same while the recorded task does.</li>
 * <li>{@link #RESULT} (SystemUI to Rose): arg1 attempt, arg2 {@link #RETURNED}, {@link #UNAVAILABLE} or
 * {@link #REFUSED}; one per RETURN unless cancelled before its reorder started.</li>
 * <li>{@link #RETURN} (Rose to that replyTo): arg1 attempt, data {token}.</li>
 * <li>{@link #CANCEL} (Rose to that replyTo): arg1 attempt.</li>
 * </ul>
 * The shell answers RETURNED only once that exact task has focus, and runs V3's departure as its own transition. With
 * no answer within {@link #TIMEOUT_MS} Rose cancels and answers REFUSED; a late answer is ignored. Nothing here
 * launches, starts or reorders anything itself. Pure logic (no Android types) for the host tests; AgentTrayService
 * supplies the {@link Port}.
 */
final class TrayReturn implements ReturnSource {
 static final int TARGET = 7, RESULT = 8, RETURN = 101, CANCEL = 102;
 static final int RETURNED = 0, UNAVAILABLE = 1, REFUSED = 2;
 static final int PROTOCOL = 1;
 /** Home's bound on a pending request: longer than the shell's own 1.5 s answer deadline. */
 static final long TIMEOUT_MS = 4000;

 /** AgentTrayService's side effects; client is the Messenger that sent the latest TARGET. */
 interface Port {
  /** Sends what (RETURN or CANCEL) with arg1 = request and, for RETURN, data {token}; false if it could not. */
  boolean send(Object client, int what, int request, long token);
  void schedule(Runnable task, long delayMs);
 }

 static final TrayReturn INSTANCE = new TrayReturn();

 private static final class Pending {
  final Object client; final long token; final Consumer<ReturnModel.Answer> answer;
  Pending(Object client, long token, Consumer<ReturnModel.Answer> answer) { this.client = client; this.token = token; this.answer = answer; }
 }

 private Port port;
 private Object client;      // the SystemUI Messenger (its binder) that sent the current target
 private long token;         // 0: no target
 private ReturnModel.Target target;
 private int nextRequest = 1;
 private final Map<Integer, Pending> pending = new LinkedHashMap<>();

 synchronized void attach(Port port) { this.port = port; }

 /** A trusted TARGET from a registered client. Anything malformed, Rose itself, another user, no label, or token 0
  * is no target. */
 void target(Object from, int version, long newToken, String packageName, int userId, String label, int myUserId,
   String ownPackage) {
  synchronized (this) {
   if (from == null || version != PROTOCOL || newToken == 0 || packageName == null || packageName.isEmpty()
     || packageName.equals(ownPackage) || userId != myUserId || label == null || label.trim().isEmpty() || port == null) {
    if (from == null || from.equals(client)) clear();
    return;
   }
   String shown = label.trim();
   if (newToken == token && from.equals(client) && target != null && target.packageName.equals(packageName)
     && target.label.equals(shown)) return;  // the same live task: the same target object
   client = from; token = newToken;
   target = new ReturnModel.Target(packageName, shown, Long.toString(newToken));
  }
 }

 /** A trusted RESULT. Only a pending request's first answer counts; codes other than v1's are REFUSED. */
 void result(int request, int code) {
  Pending p;
  synchronized (this) { p = pending.remove(request); }
  if (p == null) return;
  p.answer.accept(code == RETURNED ? ReturnModel.Answer.RETURNED
    : code == UNAVAILABLE ? ReturnModel.Answer.UNAVAILABLE : ReturnModel.Answer.REFUSED);
 }

 /** The client's link is gone (unregistered, died, service destroyed): no target, and its requests are REFUSED. */
 void lost(Object gone) {
  List<Pending> refused = new ArrayList<>();
  synchronized (this) {
   if (gone == null || gone.equals(client)) clear();
   for (Iterator<Pending> it = pending.values().iterator(); it.hasNext(); ) {
    Pending p = it.next();
    if (gone == null || gone.equals(p.client)) { refused.add(p); it.remove(); }
   }
  }
  for (Pending p : refused) p.answer.accept(ReturnModel.Answer.REFUSED);
 }

 private void clear() { client = null; token = 0; target = null; }

 @Override public synchronized ReturnModel.Target current() { return target; }

 @Override public void returnTo(ReturnModel.Target wanted, Consumer<ReturnModel.Answer> answer) {
  final int request;
  final Object to;
  final long sent;
  synchronized (this) {
   if (port == null || wanted == null || target == null || client == null
     || !Long.toString(token).equals(wanted.token)) {
    request = 0; to = null; sent = 0;
   } else {
    request = nextRequest++;
    if (nextRequest <= 0) nextRequest = 1;
    to = client; sent = token;
    pending.put(request, new Pending(to, sent, answer));
   }
  }
  if (request == 0) { answer.accept(ReturnModel.Answer.REFUSED); return; }
  if (!port.send(to, RETURN, request, sent)) {
   synchronized (this) { pending.remove(request); }
   answer.accept(ReturnModel.Answer.REFUSED);
   return;
  }
  port.schedule(() -> expire(request), TIMEOUT_MS);
 }

 /** No answer in time: cancel at the shell, then REFUSED (a slow success then changes nothing in Rose). */
 private void expire(int request) {
  Pending p;
  synchronized (this) { p = pending.remove(request); }
  if (p == null) return;
  port.send(p.client, CANCEL, request, 0);
  p.answer.accept(ReturnModel.Answer.REFUSED);
 }

 /** The owner cancelled: the shell drops the request if it has not started; its answer is never delivered. */
 @Override public void cancel(ReturnModel.Target wanted) {
  List<int[]> cancelled = new ArrayList<>();
  List<Object> clients = new ArrayList<>();
  synchronized (this) {
   for (Iterator<Map.Entry<Integer, Pending>> it = pending.entrySet().iterator(); it.hasNext(); ) {
    Map.Entry<Integer, Pending> e = it.next();
    if (wanted == null || Long.toString(e.getValue().token).equals(wanted.token)) {
     cancelled.add(new int[]{e.getKey()}); clients.add(e.getValue().client); it.remove();
    }
   }
  }
  for (int i = 0; i < cancelled.size(); i++) port.send(clients.get(i), CANCEL, cancelled.get(i)[0], 0);
 }

 /** For tests: forget everything. */
 synchronized void reset() { clear(); pending.clear(); nextRequest = 1; port = null; }
}
