package dev.pixel.backgroundcontrol;

public final class ReplacementTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }

    public static void main(String[] args) {
        Replacement r = new Replacement();
        Object calculator = new Object(), clock = new Object(), stale = new Object();
        expect(r.clear() && !r.releasing(), "nothing earlier: a first start requests at once");

        // Calculator (start token 1) is requested, admitted and created.
        r.requested(1); expect(!r.clear(), "an unanswered request blocks the next one");
        r.admitted(1); r.answered(1); expect(r.clear() && !r.releasing(), "admitted with nothing closed before it");

        // The reported collision: Clock (token 2) replaces Calculator; the controller closed it; Android has not reported it.
        r.closing(1, calculator);
        expect(!r.clear() && r.releasing() && r.handle(1) == calculator, "no request before Calculator's onClosed; its handle is kept");
        r.closed(1);
        expect(r.clear(), "onClosed is the earliest point a request can be admitted");
        expect(r.releasing() && r.handle(1) == calculator, "but onClosed is no proof of release: still unconfirmed, handle kept");
        r.closed(1); expect(r.clear() && r.releasing(), "the second close callback changes nothing");
        r.requested(2); expect(!r.clear(), "Clock's one request is in flight");
        r.admitted(2);
        expect(!r.releasing() && r.handle(1) == null, "Android's admission (its same-name check) attests Calculator unlisted");
        r.answered(2); expect(r.clear(), "Clock answered");

        // OS Core's exception path: Android's release threw, so onClosed arrived but Clock (token 2) is still listed.
        r.closing(2, clock); r.closed(2);
        expect(r.clear() && r.releasing(), "one request may be sent; Clock is still unconfirmed");
        r.requested(3); r.answered(3);   // requestSession threw "Session name must be unique": not admitted.
        expect(r.releasing() && r.handle(2) == clock, "a name refusal clears nothing: the handle stays, never a false empty");
        expect(r.clear(), "no automatic retry is queued; a later start may send its own one request");
        r.requested(4); expect(!r.clear(), "that later start's request is in flight");
        r.answered(4);   // refused again
        expect(r.releasing() && r.handle(2) == clock, "still unconfirmed after a second refusal");
        r.requested(5); r.admitted(5); r.answered(5);
        expect(!r.releasing(), "only Android's admission clears it");

        // A close recorded while a request is in flight is not attested by that request's admission.
        r.requested(6); r.closing(7, stale); r.admitted(6); r.answered(6);
        expect(r.releasing() && r.handle(7) == stale, "admission covers only sessions closed before the request was sent");
        expect(!r.clear(), "and that session's onClosed is still awaited");
        r.closed(7); r.requested(8); r.admitted(8); r.answered(8);
        expect(!r.releasing() && r.clear(), "the next admission covers it");

        // A stale created session (an older start answered after a newer one) is closed and waited on the same way.
        r.requested(9); r.admitted(9); r.answered(9); r.closing(9, stale);
        expect(!r.clear() && r.handle(9) == stale, "a stale created session is closed, kept and waited on");
        r.closed(9); expect(r.clear() && r.releasing(), "its onClosed allows the next request, which decides");
        r.requested(10); r.admitted(10); r.answered(10); expect(!r.releasing(), "admitted");

        // Two closes: both must report before a request; Android closed one itself before close() returned.
        r.closing(11, calculator); r.closing(12, clock); r.closed(11);
        expect(!r.clear(), "one onClosed of two is not enough");
        r.closed(12); expect(r.clear(), "both reported");
        r.closed(13); r.closing(13, stale);
        expect(r.clear() && r.handle(13) == stale, "a close Android already reported is not awaited again, but stays unconfirmed");
        r.requested(14); r.admitted(14); r.answered(14); expect(!r.releasing(), "all attested");

        // Answers and closes of other tokens never block or unblock anything else; an unknown admission attests nothing.
        r.closing(15, calculator); r.answered(15); r.closed(16); r.admitted(17);
        expect(!r.clear() && r.handle(15) == calculator, "unrelated answers and admissions change nothing");
        r.closed(15); r.requested(18); r.admitted(18); r.answered(18); expect(r.clear() && !r.releasing(), "own close, then admission");

        // Android's refusals, named exactly; the limit comes after a passed name check, so it is never this controller's.
        expect(Replacement.PREVIOUS_SESSION_UNRELEASED.equals(Replacement.reason("java.lang.IllegalArgumentException: Session name must be unique")),
               "name refusal: this controller's own earlier session is still listed");
        expect(Replacement.SESSION_LIMIT.equals(Replacement.reason("Android session error 1")), "limit refusal: another session is listed");
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
