package dev.pixel.backgroundcontrol;

public final class ReplacementTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }

    public static void main(String[] args) {
        Replacement r = new Replacement();
        expect(r.clear() && !r.releasing(), "nothing earlier: a first start requests at once");

        // Calculator (start token 1) is requested and created; nothing is pending.
        r.requested(1); expect(!r.clear(), "an unanswered request blocks the next one");
        r.answered(1); expect(r.clear(), "a created session's request is answered");

        // Clock (token 2) replaces it: the controller closes Calculator; Android has not reported it closed.
        r.closing(1);
        expect(!r.clear() && r.releasing(), "the reported collision: no request while the closed session may still be listed");
        r.closed(1);
        expect(r.clear() && !r.releasing(), "Android's onClosed for that session, dispatched after its release, clears the way");
        r.closed(1); expect(r.clear(), "the second close callback (onSessionClosed and onClosed) changes nothing");

        // Clock is requested; the agent starts a third app before Android answers: wait for the answer first.
        r.requested(2); expect(!r.clear(), "Clock's request is unanswered");
        r.answered(2); expect(r.clear(), "creation failed or consent pending answers it");

        // A stale created session (an older start answered after a newer start) is closed and waited on the same way.
        r.requested(3); r.answered(3); r.closing(3);
        expect(!r.clear(), "a stale created session is closed and waited on");
        r.closed(3); expect(r.clear(), "and released");

        // Several closes: every one must be reported.
        r.closing(4); r.closing(5); r.closed(4);
        expect(!r.clear() && r.releasing(), "one closed of two is not enough");
        r.closed(5); expect(r.clear(), "both reported");

        // Android closed the session itself and reported it before the controller's own close() returned.
        r.closed(6); r.closing(6);
        expect(r.clear(), "a close already reported is not waited on again");

        // Answers and closes of unknown tokens never block or unblock anything else.
        r.closing(7); r.answered(7); r.closed(8);
        expect(!r.clear(), "unrelated answers do not clear a pending close");
        r.closed(7); expect(r.clear(), "own close clears");

        // Android's refusal is the answer when its release itself failed; never retried, named exactly.
        expect(Replacement.PREVIOUS_SESSION_UNRELEASED.equals(Replacement.reason("java.lang.IllegalArgumentException: Session name must be unique")),
               "name refusal: this controller's own earlier session is still listed");
        expect(Replacement.SESSION_LIMIT.equals(Replacement.reason("Android session error 1")),
               "limit refusal: a session is listed (possibly another owner's)");
        for (String other : new String[] {"", "Android session error 2", "Android session error 3", "Android session error 10",
                "java.lang.SecurityException: Caller x is not allowed to create a session", "Android close reason 1"}) {
            expect(Replacement.reason(other).isEmpty() && !Replacement.listed(other), "not a listing refusal: " + other);
        }
        expect(Replacement.reason(null).isEmpty() && !Replacement.listed(null), "no error");
        expect(Replacement.listed("java.lang.IllegalArgumentException: Session name must be unique")
               && Replacement.listed("Android session error 1"), "both refusals are reported as rejected");
        System.out.println("ReplacementTest: " + checks + " checks passed");
    }
}
