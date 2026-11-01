package dev.pixel.voice;

import dev.pixel.voice.WorkspaceOverview.*;
import dev.pixel.voice.WorkspaceOverviewPreview.*;
import java.util.Arrays;
import java.util.List;

/** #225: the overview's consumer of the controller's read-only preview (PR340), on synthetic answers shaped exactly like
 * Controller.preview's (status, sessionId, taskId, targetPackage, capturedAt, width, height, mimeType, image). Host JVM
 * only: no provider, controller, frame or phone. */
public final class WorkspaceOverviewPreviewTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static final Scope S = new Scope("srv-9", "c2-def");
    static final String WS = "rose-ws-n-1-com.android.calculator2", SESSION = "s-1", PKG = "com.android.calculator2";
    static final Binding B = new Binding(WS, SESSION, PKG, S);
    static final Object IMG = new Object();
    static Response ok(Request r, long capturedAt) { return new Response("ok", r.sessionId(), r.taskId(), PKG, capturedAt, 320, 693, "image/jpeg", 30000, IMG); }
    static Response refusal(String word) { return new Response(word, null, null, null, 0, 0, 0, null, 0, null); }

    public static void main(String[] args) {
        long t = 1_761_960_000_000L;

        // Target only from a verified binding: the model's watchRow.
        List<Task> bound = Arrays.asList(new Task("srv-9#4", S, Status.RUNNING, "Open Calculator", "Opening", "", 30, 0, B));
        Workspace live = Workspace.read("ready", SESSION, WS, PKG, "Calculator", S);
        Model linked = WorkspaceOverview.of(true, S, live, bound, null, null, false);
        expect(linked.watchRow != null && WorkspaceOverviewPreview.sameTarget(linked.watchRow.binding, B), "a verified binding is the preview target");
        Model unbound = WorkspaceOverview.of(true, Scope.UNKNOWN, live, Arrays.asList(new Task("@30#4", Scope.UNKNOWN, Status.RUNNING, "Open Calculator", "Opening", "", 30, 0, null)), null, null, false);
        expect(unbound.watchRow == null, "PR318 today (no producer binding): no target, even with a workspace open");
        Model other = WorkspaceOverview.of(true, S, Workspace.read("ready", "s-2", "rose-ws-n-2-com.android.chrome", "com.android.chrome", "Chrome", S), bound, null, null, false);
        expect(other.watchRow == null, "the open workspace is another session: no target");

        // No binding / hidden: no request.
        WorkspaceOverviewPreview p = new WorkspaceOverviewPreview();
        expect(p.next(true, null, t) == null, "no binding, no request");
        expect(p.next(false, B, t) == null, "hidden overview, no request");
        expect(p.next(true, new Binding(WS, "", PKG, S), t) == null, "incomplete binding, no request");

        // One in flight, at most once a second.
        Request r1 = p.next(true, B, t);
        expect(r1 != null && r1.sessionId().equals(SESSION) && r1.taskId().equals(WS), "request names the live session and the controller task");
        expect(p.next(true, B, t + 2000) == null, "one request in flight");
        expect(p.answer(r1, ok(r1, t + 40), t + 60), "a matching fresh answer is shown");
        expect(p.frameFor(B, t + 100) != null && p.frameFor(B, t + 100).image == IMG, "frame for its binding");
        expect(p.next(true, B, t + 500) == null, "not again within the interval");
        Request r2 = p.next(true, B, t + 1000);
        expect(r2 != null, "again after the interval");

        // Wrong identity: dropped and the old frame cleared.
        for (String[] f : new String[][]{{"s-2", WS, PKG}, {SESSION, "rose-ws-x", PKG}, {SESSION, WS, "com.android.chrome"}}) {
            WorkspaceOverviewPreview q = new WorkspaceOverviewPreview();
            Request r = q.next(true, B, t);
            expect(q.answer(r, ok(r, t + 10), t + 20), "setup");
            Request rr = q.next(true, B, t + 1000);
            expect(!q.answer(rr, new Response("ok", f[0], f[1], f[2], t + 1010, 320, 693, "image/jpeg", 30000, IMG), t + 1020), "wrong " + Arrays.toString(f) + " is not shown");
            expect(q.frameFor(B, t + 1030) == null, "...and the earlier frame is cleared");
        }

        // Stale and out-of-order answers.
        WorkspaceOverviewPreview s = new WorkspaceOverviewPreview();
        Request a = s.next(true, B, t);
        expect(!s.answer(a, ok(a, t - 5000), t + 10), "captured before the request: stale");
        Request a2 = s.next(true, B, t + 1000);
        expect(!s.answer(a2, ok(a2, t + 1000 - 1500), t + 1010), "captured before the request was sent (within MAX_AGE): stale");
        Request b = s.next(true, B, t + 2000);
        expect(s.answer(b, ok(b, t + 2500), t + 2600), "fresh");
        Request c = s.next(true, B, t + 3000);
        expect(!s.answer(c, ok(c, t + 2400), t + 3100), "older than the frame shown: out of order");
        expect(s.frameFor(B, t + 3100).capturedAt == t + 2500, "the newer frame stays");
        expect(s.frameFor(B, t + 2500 + WorkspaceOverviewPreview.MAX_AGE_MS + 1) == null, "a frame past MAX_AGE is not shown");
        Request d = s.next(true, B, t + 9000);
        expect(!s.answer(d, ok(d, t + 9000 + 5000), t + 9010), "captured in the future: rejected");
        expect(!s.answer(d, ok(d, t + 9005), t + 9010), "an answer is taken once");

        // A late answer after cancel or a replaced request.
        WorkspaceOverviewPreview l = new WorkspaceOverviewPreview();
        Request old = l.next(true, B, t);
        l.cancel();
        expect(!l.answer(old, ok(old, t + 10), t + 20), "after cancel (Back/pause/teardown) a late answer is ignored");
        expect(l.frameFor(B, t + 20) == null, "...and nothing is shown");
        Request fresh = l.next(true, B, t + 1000);
        expect(!l.answer(old, ok(old, t + 1010), t + 1020) && l.inFlight(), "an old request's answer never satisfies the new one");
        expect(l.answer(fresh, ok(fresh, t + 1010), t + 1020), "the current request's answer is shown");

        // Changed or replaced binding: cancel and refuse what belonged to the old one.
        Binding replaced = new Binding("rose-ws-n-2-com.android.chrome", "s-2", "com.android.chrome", S);
        Request onOld = l.next(true, B, t + 2100);
        expect(l.next(true, replaced, t + 2200) == null && !l.inFlight(), "target changed: the old request is cancelled (the next waits for the interval)");
        expect(l.frameFor(B, t + 2200) == null && l.frameFor(replaced, t + 2200) == null, "a changed binding drops the old frame");
        expect(!l.answer(onOld, ok(onOld, t + 2150), t + 2210), "an answer for the old binding is ignored");
        Binding rescoped = new Binding(WS, SESSION, PKG, new Scope("srv-10", "c3"));
        expect(!WorkspaceOverviewPreview.sameTarget(B, rescoped), "the same ids after a restart/re-pair are another binding");

        // Refusals (PreviewPolicy words) clear the frame.
        for (String word : new String[]{"unbound", "locked", "not_ready", "stale_session", "stale_task", "blocked", "too_soon", "no_frame", "error", "weird"}) {
            WorkspaceOverviewPreview f = new WorkspaceOverviewPreview();
            Request r = f.next(true, B, t);
            f.answer(r, ok(r, t + 10), t + 20);
            Request rr = f.next(true, B, t + 1000);
            expect(!f.answer(rr, refusal(word), t + 1010) && f.frameFor(B, t + 1010) == null, word + ": nothing shown");
            expect(f.lastDrop.equals(word) && !f.inFlight(), word + ": recorded as the fixed word, request finished");
        }
        WorkspaceOverviewPreview n = new WorkspaceOverviewPreview();
        Request nr = n.next(true, B, t);
        expect(!n.answer(nr, null, t + 10) && !n.inFlight(), "no answer (provider gone): nothing shown, request finished");

        // Bounds: never larger than PR340 promises, JPEG only, decoded image required.
        Object[][] bad = {{"image/png", 320, 693, 30000, IMG}, {"image/jpeg", 321, 693, 30000, IMG}, {"image/jpeg", 320, 721, 30000, IMG},
            {"image/jpeg", 0, 693, 30000, IMG}, {"image/jpeg", 320, 693, WorkspaceOverviewPreview.MAX_BYTES + 1, IMG}, {"image/jpeg", 320, 693, 30000, null}};
        for (Object[] x : bad) {
            WorkspaceOverviewPreview g = new WorkspaceOverviewPreview();
            Request r = g.next(true, B, t);
            expect(!g.answer(r, new Response("ok", SESSION, WS, PKG, t + 5, (Integer) x[1], (Integer) x[2], (String) x[0], (Integer) x[3], x[4]), t + 10), "out of bounds: " + Arrays.toString(x));
        }
        expect(WorkspaceOverviewPreview.MAX_BASE64 == 65536, "base64 cap for 48 KiB");

        // Hidden after showing: cancels and stops requesting.
        WorkspaceOverviewPreview h = new WorkspaceOverviewPreview();
        Request hr = h.next(true, B, t);
        h.answer(hr, ok(hr, t + 5), t + 10);
        expect(h.next(false, B, t + 2000) == null && h.frameFor(B, t + 2000) == null, "hidden: no request, frame dropped");
        for (int i = 0; i < 5; i++) expect(h.next(false, B, t + 3000 + i * 1000) == null, "no requests while hidden " + i);

        System.out.println("WorkspaceOverviewPreviewTest: " + checks + " checks passed");
    }
}
