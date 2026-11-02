package dev.pixel.backgroundcontrol;

public final class ReplacementTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    /** Android's first callback for a request (pending, created or creation failed): admission, then the answer. */
    static void callback(Replacement r, long token) { r.admitted(token); r.answered(token); }

    public static void main(String[] args) {
        Replacement r = new Replacement();
        Object calculator = new Object(), clock = new Object(), a = new Object(), x = new Object(), y = new Object(), z = new Object();
        expect(r.clear() && !r.releasing() && r.blocker().isEmpty(), "nothing earlier: a first start requests at once");

        // Calculator (start token 1): requested, then Android's first callback.
        r.requested(1);
        expect(!r.clear() && r.unanswered(1) && Replacement.REQUEST_UNANSWERED.equals(r.blocker()), "an unanswered request blocks the next one");
        expect(r.releasing(), "an unanswered request may yet become a session: never shown empty");
        callback(r, 1); expect(r.clear() && !r.releasing() && !r.unanswered(1), "answered, nothing closed before it");

        // The reported collision: Clock (token 2) replaces Calculator; the controller closed it; Android has not reported it.
        r.closing(1, calculator);
        expect(!r.clear() && r.releasing() && r.handle(1) == calculator && Replacement.REPLACING.equals(r.blocker()),
               "no request before Calculator's onClosed; its handle is kept");
        r.closed(1);
        expect(r.clear() && r.blocker().isEmpty(), "onClosed is the earliest point a request can be admitted");
        expect(r.releasing() && r.handle(1) == calculator, "but onClosed is no proof of release: still unconfirmed, handle kept");
        r.closed(1); expect(r.clear() && r.releasing(), "the second close callback changes nothing");
        r.requested(2);
        expect(r.handle(1) == calculator, "a normal return of requestSession alone attests nothing");
        callback(r, 2);
        expect(!r.releasing() && r.handle(1) == null, "Android's first callback (after its same-name check) attests Calculator unlisted");

        // Missing virtual-device service: requestSession returns silently and no callback ever comes.
        r.closing(2, clock); r.closed(2); r.requested(3);
        expect(r.releasing() && r.handle(2) == clock, "a silent return never clears the closed session: unknown, not released");
        expect(!r.clear() && r.unanswered(3) && Replacement.REQUEST_UNANSWERED.equals(r.blocker()),
               "the silent request stays unanswered: later starts wait with request_unanswered; no retry, no timeout");
        r.closed(99); r.answered(98); r.admitted(97);
        expect(!r.clear() && r.handle(2) == clock && r.unanswered(3), "other tokens' callbacks change nothing");
        // Only Android's own callback for that request (if one ever comes) admits what was closed before it.
        callback(r, 3);
        expect(r.clear() && !r.releasing() && r.handle(2) == null, "a real callback for request 3 attests Clock unlisted");

        // A session closed after a request was sent is not attested by that request's callback.
        r.requested(5); r.closing(6, x); callback(r, 5);
        expect(r.handle(6) == x && r.releasing() && Replacement.REPLACING.equals(r.blocker()),
               "admission covers only sessions closed before the request was sent");
        r.closed(6); r.requested(7); callback(r, 7);
        expect(!r.releasing() && r.clear(), "the next request's callback covers it");

        // Consent-pending A becomes live just before B is created: B's limit refusal is this controller's own A.
        r.requested(8); callback(r, 8);                      // A: onSessionPending (consent); the start moves on
        expect(r.clear() && !r.releasing(), "a pending request is answered: it is not a listed session");
        r.requested(9);                                      // B is sent; its name check passes (A is not yet listed)
        r.admitted(8); r.answered(8); r.closing(8, a);       // A's late stale onSessionCreated: closed and kept
        callback(r, 9);                                      // B: onSessionCreationFailed(ERROR_SESSION_LIMIT_REACHED)
        expect(r.releasing() && r.handle(8) == a, "B's callback does not clear A, closed after B was sent: A is kept, never empty");
        expect(Replacement.SESSION_LIMIT.equals(Replacement.reason("Android session error 1")), "B is rejected with session_limit");
        expect(!r.clear() && Replacement.REPLACING.equals(r.blocker()), "the next start waits for A's onClosed");
        r.closed(8); expect(r.clear() && r.releasing(), "then one request; A is still unconfirmed");
        r.requested(10); callback(r, 10); expect(!r.releasing(), "only a later admission clears A");

        // A thrown name refusal attests nothing; no retry is queued.
        r.closing(11, y); r.closed(11); r.requested(12); r.answered(12);
        expect(r.releasing() && r.handle(11) == y, "a name refusal clears nothing: the handle stays");
        expect(r.clear(), "no automatic retry; a later start may send its own one request");
        r.requested(13); callback(r, 13); expect(!r.releasing(), "only admission clears it");

        // A late duplicate callback for an already answered request (created after pending) admits nothing new.
        r.requested(14); callback(r, 14); r.closing(15, z); r.admitted(14); r.answered(14);
        expect(r.handle(15) == z && r.releasing(), "a late duplicate callback never clears a later close");
        r.closed(15); r.requested(16); callback(r, 16); expect(!r.releasing(), "cleared by its own later admission");

        // Two closes: both must report before a request; Android closed one itself before close() returned.
        r.closing(17, calculator); r.closing(18, clock); r.closed(17);
        expect(!r.clear(), "one onClosed of two is not enough");
        r.closed(18); expect(r.clear(), "both reported");
        r.closed(19); r.closing(19, x);
        expect(r.clear() && r.handle(19) == x, "a close Android already reported is not awaited again, but stays unconfirmed");
        r.requested(20); callback(r, 20); expect(!r.releasing() && r.clear(), "all attested");

        // Android's refusals, named exactly.
        expect(Replacement.PREVIOUS_SESSION_UNRELEASED.equals(Replacement.reason("java.lang.IllegalArgumentException: Session name must be unique")),
               "name refusal: this controller's own earlier session is still listed");
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
