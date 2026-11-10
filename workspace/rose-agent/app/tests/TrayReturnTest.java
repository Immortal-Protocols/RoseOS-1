package dev.pixel.voice;

import java.util.ArrayList;
import java.util.List;

/** 1C: TrayReturn's protocol v1 logic, with a fake SystemUI client and clock. No Android. */
public final class TrayReturnTest {
 static int checks;
 static void expect(boolean ok, String what) { checks++; if (!ok) throw new AssertionError(what); }

 static final class Port implements TrayReturn.Port {
  final List<String> sent = new ArrayList<>();
  final List<Runnable> timers = new ArrayList<>();
  boolean fail;
  public boolean send(Object client, int what, int request, long token) {
   if (fail) return false;
   sent.add(client + ":" + what + ":" + request + ":" + token);
   return true;
  }
  public void schedule(Runnable task, long delayMs) { expect(delayMs == TrayReturn.TIMEOUT_MS, "4 s bound"); timers.add(task); }
  void fire() { List<Runnable> t = new ArrayList<>(timers); timers.clear(); for (Runnable r : t) r.run(); }
 }

 static final Object SYSTEMUI = "sysui", OTHER = "other";
 static final String OWN = "dev.pixel.voice";

 static TrayReturn fresh(Port port) {
  TrayReturn r = new TrayReturn();
  r.attach(port);
  return r;
 }

 static void target(TrayReturn r, long token) { r.target(SYSTEMUI, 1, token, "com.google.android.apps.messaging", 0, "Messages", 0, OWN); }

 public static void main(String[] args) {
  // Protocol v1 as OS Core finalized it (no other numbering exists): 7/8 SystemUI to Rose, 101/102 Rose to SystemUI.
  expect(TrayReturn.TARGET == 7 && TrayReturn.RESULT == 8 && TrayReturn.RETURN == 101 && TrayReturn.CANCEL == 102,
    "wire codes 7/8/101/102");
  expect(TrayReturn.RETURNED == 0 && TrayReturn.UNAVAILABLE == 1 && TrayReturn.REFUSED == 2 && TrayReturn.PROTOCOL == 1,
    "results 0/1/2, protocol 1");

  // Nothing known: hidden, and a return is REFUSED without any message.
  Port port = new Port();
  TrayReturn r = fresh(port);
  expect(r.current() == null, "no target before SystemUI sends one");
  List<ReturnModel.Answer> answers = new ArrayList<>();
  r.returnTo(new ReturnModel.Target("x", "X", "5"), answers::add);
  expect(answers.size() == 1 && answers.get(0) == ReturnModel.Answer.REFUSED && port.sent.isEmpty(), "no target: REFUSED, nothing sent");

  // A target: package and local label; the token stays opaque.
  target(r, 77);
  ReturnModel.Target t = r.current();
  expect(t != null && t.packageName.equals("com.google.android.apps.messaging") && t.label.equals("Messages") && t.token.equals("77"), "target");
  ReturnModel.Target same = r.current();
  target(r, 77);
  expect(r.current() == same, "the same live task keeps the same target");

  // RETURN goes to that client, RETURNED arrives: answered once; a duplicate or late answer changes nothing.
  answers.clear();
  r.returnTo(t, answers::add);
  expect(port.sent.equals(java.util.Collections.singletonList("sysui:101:1:77")), "RETURN_TO to the target's client: " + port.sent);
  expect(answers.isEmpty(), "pending until the shell answers");
  r.result(1, TrayReturn.RETURNED);
  r.result(1, TrayReturn.REFUSED);
  port.fire();
  expect(answers.size() == 1 && answers.get(0) == ReturnModel.Answer.RETURNED, "RETURNED once: " + answers);
  expect(port.sent.size() == 1, "an answered request is not cancelled");

  // UNAVAILABLE and REFUSED map; an unknown code is REFUSED.
  for (int[] c : new int[][]{{TrayReturn.UNAVAILABLE, 1}, {TrayReturn.REFUSED, 2}, {9, 2}, {-1, 2}}) {
   List<ReturnModel.Answer> a = new ArrayList<>();
   r.returnTo(t, a::add);
   int request = Integer.parseInt(port.sent.get(port.sent.size() - 1).split(":")[2]);
   r.result(request, c[0]);
   expect(a.size() == 1 && a.get(0) == ReturnModel.Answer.values()[c[1]], "code " + c[0] + " -> " + a);
  }

  // No answer within 4 s: CANCEL at the shell, then REFUSED; the late answer is ignored.
  answers.clear();
  port.sent.clear();
  r.returnTo(t, answers::add);
  int request = Integer.parseInt(port.sent.get(0).split(":")[2]);
  port.fire();
  expect(port.sent.size() == 2 && port.sent.get(1).equals("sysui:102:" + request + ":0"), "timeout cancels: " + port.sent);
  expect(answers.size() == 1 && answers.get(0) == ReturnModel.Answer.REFUSED, "timeout REFUSED");
  r.result(request, TrayReturn.RETURNED);
  expect(answers.size() == 1, "late answer ignored");

  // The owner cancels: CANCEL sent, and no answer is ever delivered for that request.
  answers.clear();
  port.sent.clear();
  r.returnTo(t, answers::add);
  request = Integer.parseInt(port.sent.get(0).split(":")[2]);
  r.cancel(t);
  r.result(request, TrayReturn.RETURNED);
  port.fire();
  expect(port.sent.size() == 2 && port.sent.get(1).equals("sysui:102:" + request + ":0") && answers.isEmpty(), "cancel: " + port.sent + answers);

  // A stale token is REFUSED locally; a send failure is REFUSED.
  answers.clear();
  port.sent.clear();
  r.returnTo(new ReturnModel.Target("com.google.android.apps.messaging", "Messages", "76"), answers::add);
  expect(answers.get(0) == ReturnModel.Answer.REFUSED && port.sent.isEmpty(), "stale token");
  port.fail = true;
  answers.clear();
  r.returnTo(t, answers::add);
  expect(answers.size() == 1 && answers.get(0) == ReturnModel.Answer.REFUSED, "send failure");
  port.fail = false;

  // Link loss: the target clears and pending requests are REFUSED; another client's loss changes nothing.
  answers.clear();
  r.returnTo(t, answers::add);
  r.lost(OTHER);
  expect(r.current() != null && answers.isEmpty(), "another client's loss");
  r.lost(SYSTEMUI);
  expect(r.current() == null && answers.size() == 1 && answers.get(0) == ReturnModel.Answer.REFUSED, "link loss");

  // Malformed, Rose itself, another user, token 0, no label: no target (and an existing one clears).
  target(r, 5);
  r.target(SYSTEMUI, 1, 0, "com.x", 0, "X", 0, OWN);
  expect(r.current() == null, "token 0 is none");
  for (Object[] bad : new Object[][]{{2, 5L, "com.x", 0, "X"}, {0, 5L, "com.x", 0, "X"}, {1, 5L, OWN, 0, "Rose"}, {1, 5L, "com.x", 10, "X"},
    {1, 5L, "", 0, "X"}, {1, 5L, null, 0, "X"}, {1, 5L, "com.x", 0, " "}, {1, 5L, "com.x", 0, null}}) {
   target(r, 9);
   r.target(SYSTEMUI, (Integer) bad[0], (Long) bad[1], (String) bad[2], (Integer) bad[3], (String) bad[4], 0, OWN);
   expect(r.current() == null, "rejected: " + java.util.Arrays.toString(bad));
  }
  // Another client's malformed target does not clear this client's.
  target(r, 12);
  r.target(OTHER, 2, 5, "com.x", 0, "X", 0, OWN);
  expect(r.current() != null, "another client's bad target leaves ours");
  // A new task (new token) replaces the target.
  target(r, 13);
  expect(r.current().token.equals("13"), "new token replaces");

  // Unattached (service not created): nothing.
  TrayReturn bare = new TrayReturn();
  bare.target(SYSTEMUI, 1, 5, "com.x", 0, "X", 0, OWN);
  expect(bare.current() == null, "no port, no target");

  System.out.println("TrayReturnTest: " + checks + " checks passed");
 }
}
