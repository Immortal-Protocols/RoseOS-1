package dev.pixel.backgroundcontrol;

public final class StopLedgerTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static void rejects(String value) {
        try { StopLedger.taskId(value); } catch (IllegalArgumentException expected) { checks++; return; }
        throw new AssertionError("Accepted invalid taskId " + value);
    }
    public static void main(String[] args) {
        StopLedger l = new StopLedger();
        expect(l.reason().isEmpty() && !l.refuses("t1"), "Fresh ledger refuses nothing");
        expect(!l.refuses(null), "Legacy start without taskId is never refused");
        l.stopped(StopLedger.AGENT, "s1", "t1");
        expect(StopLedger.AGENT.equals(l.reason()) && !l.refuses("t1"), "Agent stop does not block its task");
        l.stopped(StopLedger.DEADLINE, "s1", "t1");
        expect(!l.refuses("t1"), "Deadline does not block its task");
        l.stopped(StopLedger.USER, "s2", "t2");
        expect(StopLedger.USER.equals(l.reason()) && "s2".equals(l.sessionId()) && "t2".equals(l.taskId()),
                "Owner stop is reported with its session and task");
        expect(l.refuses("t2") && !l.refuses("t3"), "Only the owner-stopped task is refused");
        l.stopped(StopLedger.AGENT, "s2", "t2");
        expect(StopLedger.USER.equals(l.reason()), "Agent cleanup of the same session keeps the owner report");
        l.started();
        expect(l.reason().isEmpty() && l.sessionId() == null && l.taskId() == null, "New session clears report");
        expect(l.refuses("t2"), "A new session does not undo the owner's stop of t2");
        java.util.Map<String, Object> refused = l.refusal("t2");
        expect(refused != null && "user_stopped".equals(refused.get("status"))
                && "t2".equals(refused.get("rejectedTaskId")) && StopLedger.USER.equals(refused.get("stopReason"))
                && Boolean.FALSE.equals(refused.get("actionExecuted")), "Refusal names the rejected task");
        expect(!refused.containsKey("taskId") && !refused.containsKey("sessionId")
                && !refused.containsKey("stoppedTaskId"), "Refusal carries no current-workspace identity");
        expect(l.refusal("t9") == null && l.refusal(null) == null, "A different or absent task is not refused");
        expect(l.reason().isEmpty() && l.sessionId() == null, "Building a refusal changes nothing");
        l.stopped(StopLedger.USER, "s3", null);
        expect(StopLedger.USER.equals(l.reason()) && !l.refuses(null), "Owner stop without taskId is reported only");
        for (int i = 0; i < StopLedger.REMEMBERED_TASKS; i++) l.stopped(StopLedger.USER, "s", "bulk" + i);
        expect(!l.refuses("t2") && l.refuses("bulk0") && l.refuses("bulk31"), "Memory stays bounded, oldest first");
        l.stopped(StopLedger.USER, "s", "bulk0");
        l.stopped(StopLedger.USER, "s", "bulk32");
        expect(l.refuses("bulk0") && !l.refuses("bulk1"), "Re-stopping a task refreshes it");
        expect("abc-1._:Z".equals(StopLedger.taskId("abc-1._:Z")) && StopLedger.taskId(null) == null,
                "Opaque identifiers accepted");
        rejects(""); rejects("has space"); rejects("x/y"); rejects(new String(new char[129]).replace('\0', 'a'));
        try { l.stopped("owner", "s", "t"); throw new AssertionError("Unknown reason accepted"); }
        catch (IllegalArgumentException expected) { checks++; }
        System.out.println("StopLedgerTest: " + checks + " checks passed");
    }
}
