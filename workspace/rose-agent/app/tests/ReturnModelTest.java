package dev.pixel.voice;

/** 1C: the return control's rules (ReturnModel), every phase and the correlated answer. Host JVM. */
public final class ReturnModelTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    public static void main(String[] args) {
        ReturnModel.Target messages = new ReturnModel.Target("com.bluebubbles.messaging", "Messages", "task-41");
        ReturnModel.Target maps = new ReturnModel.Target("app.organicmaps", "Maps", "task-52");

        // Nothing to return to: hidden, never a guessed app.
        ReturnModel.State s = ReturnModel.initial();
        expect(!s.visible() && s.observed(null, 0).phase == ReturnModel.Phase.HIDDEN, "no target: hidden");

        // The previous app appears: named, actionable.
        s = s.observed(messages, 10);
        expect(s.phase == ReturnModel.Phase.READY && s.actionable(), "a target: ready");
        expect("Messages".equals(ReturnModel.text(s)) && "Return to Messages".equals(ReturnModel.spoken(s)), "compact name, spoken as Return to");

        // One tap: pending for the cancellable moment; a second tap cancels and nothing is asked of the source.
        ReturnModel.State pending = s.tapped(100);
        expect(pending.phase == ReturnModel.Phase.PENDING && pending.attempt == s.attempt + 1, "tap starts an attempt");
        expect("Returning to Messages".equals(ReturnModel.text(pending)) && ReturnModel.spoken(pending).endsWith("Tap to cancel"), "pending is said, with cancel");
        expect(pending.committed(100 + ReturnModel.COMMIT_MS - 1).phase == ReturnModel.Phase.PENDING, "not before the moment ends");
        ReturnModel.State cancelled = pending.tapped(200);
        expect(cancelled.phase == ReturnModel.Phase.READY && cancelled.committed(10_000).phase == ReturnModel.Phase.READY, "second tap cancels; no return");
        expect(pending.cancelled(200).phase == ReturnModel.Phase.READY, "Back or leaving cancels too");

        // The moment ends: the return is asked for exactly once.
        ReturnModel.State returning = pending.committed(100 + ReturnModel.COMMIT_MS);
        expect(returning.phase == ReturnModel.Phase.RETURNING && returning.tapped(700) == returning, "asked; taps wait");
        expect(returning.cancelled(700) == returning, "a return already asked for is not undone");

        // Only this attempt's answer counts.
        expect(returning.answered(returning.attempt - 1, ReturnModel.Answer.RETURNED, 800) == returning, "a stale answer is ignored");
        expect(returning.answered(returning.attempt, ReturnModel.Answer.RETURNED, 800).phase == ReturnModel.Phase.HIDDEN, "returned: the app is in front, control leaves");
        expect(pending.answered(pending.attempt, ReturnModel.Answer.RETURNED, 800) == pending, "no answer before it was asked");

        // The app is gone: said honestly, not actionable, then the control leaves.
        ReturnModel.State gone = returning.answered(returning.attempt, ReturnModel.Answer.UNAVAILABLE, 900);
        expect(gone.phase == ReturnModel.Phase.UNAVAILABLE && !gone.actionable() && gone.tapped(950) == gone, "unavailable: nothing to tap");
        expect("Messages isn’t open now".equals(ReturnModel.text(gone)) && ReturnModel.spoken(gone).contains("no longer open"), "unavailable is named");
        expect(gone.observed(null, 1000).phase == ReturnModel.Phase.UNAVAILABLE, "the message stays while the source has nothing");
        expect(gone.expired(900 + ReturnModel.UNAVAILABLE_MS).phase == ReturnModel.Phase.HIDDEN, "then it leaves");

        // Refused, or no answer in time: a failure the owner can retry, never shown as success.
        ReturnModel.State refused = returning.answered(returning.attempt, ReturnModel.Answer.REFUSED, 900);
        expect(refused.phase == ReturnModel.Phase.FAILED && refused.actionable() && ReturnModel.spoken(refused).endsWith("Tap to try again"), "refused: retry");
        expect(refused.tapped(1000).phase == ReturnModel.Phase.PENDING && refused.tapped(1000).attempt == returning.attempt + 1, "retry is a new attempt");
        expect(returning.timedOut(returning.since + ReturnModel.ANSWER_MS).phase == ReturnModel.Phase.FAILED, "silence is a failure");
        expect(returning.timedOut(returning.since + ReturnModel.ANSWER_MS - 1) == returning, "not before the limit");

        // A different app while idle replaces the target; a running return keeps its own.
        expect(s.observed(maps, 20).target == maps, "a newer previous app replaces the target");
        expect(pending.observed(maps, 120).target == messages && returning.observed(maps, 700).target == messages, "an attempt keeps its target");
        expect(s.observed(new ReturnModel.Target("com.bluebubbles.messaging", "Messages", "task-77"), 30).target.token.equals("task-77"), "a new task of the same app is a new target");

        // The source losing its target mid-return does not swallow the answer; an idle control just leaves.
        expect(pending.observed(null, 150) == pending && returning.observed(null, 700) == returning, "an attempt waits for its own answer");
        expect(refused.observed(null, 1000).phase == ReturnModel.Phase.HIDDEN, "nothing to return to: hidden");

        // Names: control characters removed, trimmed, compact; unknown names are not invented.
        expect("Evil".equals(ReturnModel.name("Ev\nil")) && "previous app".equals(ReturnModel.name("  ")) && "previous app".equals(ReturnModel.name(null)), "names cleaned");
        String longName = ReturnModel.name("A very long application name indeed");
        expect(longName.length() == ReturnModel.MAX_LABEL && longName.endsWith("…"), "long names shortened");

        System.out.println("ReturnModelTest: " + checks + " checks passed");
    }
}
