package dev.pixel.backgroundcontrol;

import java.util.Arrays;

public final class PreviewPolicyTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static String r(String reqS, String reqT, boolean locked, boolean has, String status, boolean blocked, boolean expired,
                    String liveS, String liveT, long now, long last) {
        return PreviewPolicy.refusal(reqS, reqT, locked, has, status, blocked, expired, liveS, liveT, now, last);
    }

    public static void main(String[] args) {
        // The one accepted case: the caller names the live session and its task, ready, unblocked, unlocked.
        expect(r("s1", "t1", false, true, "ready", false, false, "s1", "t1", 10_000, 0) == null, "live bound session previews");
        expect(r("s1", "t1", false, true, "ready", false, false, "s1", "t1", 10_000, 9_000) == null, "once a second is allowed");

        // Missing binding fails closed before anything else is read.
        for (String[] bad : new String[][] {{null, "t1"}, {"s1", null}, {"", "t1"}, {"s1", ""}, {"null", "t1"}, {"s1", "null"}})
            expect(PreviewPolicy.UNBOUND.equals(r(bad[0], bad[1], false, true, "ready", false, false, "s1", "t1", 1, 0)), "unbound: " + Arrays.toString(bad));
        // Locked beats everything after the binding: nothing of the workspace while the phone is locked.
        expect(PreviewPolicy.LOCKED.equals(r("s1", "t1", true, true, "ready", false, false, "s1", "t1", 1, 0)), "locked");
        expect(PreviewPolicy.LOCKED.equals(r("s9", "t9", true, false, "idle", false, false, null, null, 1, 0)), "locked even without a session");
        // No session, or a session not ready.
        expect(PreviewPolicy.NOT_READY.equals(r("s1", "t1", false, false, "idle", false, false, null, null, 1, 0)), "no session");
        for (String status : new String[] {"starting", "launching", "scheduled", "consent_required", "closed", "stopped", "failed", "rejected", "cleanup_failed", "idle"})
            expect(PreviewPolicy.NOT_READY.equals(r("s1", "t1", false, true, status, false, false, "s1", "t1", 1, 0)), "not ready: " + status);
        expect(PreviewPolicy.NOT_READY.equals(r("s1", "t1", false, true, "ready", false, true, "s1", "t1", 1, 0)), "past the deadline");
        // Wrong or replaced session, wrong or missing task: never another session's or task's frame.
        expect(PreviewPolicy.STALE_SESSION.equals(r("s1", "t1", false, true, "ready", false, false, "s2", "t1", 1, 0)), "replaced session");
        expect(PreviewPolicy.STALE_TASK.equals(r("s1", "t1", false, true, "ready", false, false, "s1", "t2", 1, 0)), "another task in the same session");
        expect(PreviewPolicy.STALE_TASK.equals(r("s1", "t1", false, true, "ready", false, false, "s1", null, 1, 0)), "an untagged session is no task's");
        // Blocked by Android: no frame, even for the right session.
        expect(PreviewPolicy.BLOCKED.equals(r("s1", "t1", false, true, "blocked", true, false, "s1", "t1", 1, 0)), "blocked");
        expect(PreviewPolicy.BLOCKED.equals(r("s1", "t1", false, true, "ready", true, false, "s1", "t1", 1, 0)), "blocked flag wins over a ready status");
        // Rate: the agent's own screenshots keep priority.
        expect(PreviewPolicy.TOO_SOON.equals(r("s1", "t1", false, true, "ready", false, false, "s1", "t1", 10_000, 9_500)), "twice within 900 ms");

        // Output bounds: never larger than 320 x 720, never enlarged, aspect kept.
        expect(Arrays.equals(PreviewPolicy.size(1080, 2400), new int[] {320, 711}), "portrait phone frame");
        expect(Arrays.equals(PreviewPolicy.size(2400, 1080), new int[] {320, 144}), "landscape frame");
        expect(Arrays.equals(PreviewPolicy.size(300, 2000), new int[] {108, 720}), "very tall frame is height-bound");
        expect(Arrays.equals(PreviewPolicy.size(200, 100), new int[] {200, 100}), "small frames are not enlarged");
        expect(PreviewPolicy.size(0, 100) == null && PreviewPolicy.size(100, 0) == null && PreviewPolicy.size(-1, -1) == null, "no pixels, no preview");
        for (int[] wh : new int[][] {{1, 1}, {4000, 3}, {3, 4000}, {1440, 3120}}) {
            int[] s = PreviewPolicy.size(wh[0], wh[1]);
            expect(s[0] >= 1 && s[1] >= 1 && s[0] <= PreviewPolicy.MAX_WIDTH && s[1] <= PreviewPolicy.MAX_HEIGHT, "bounded: " + Arrays.toString(wh));
        }
        expect(PreviewPolicy.fits(1) && PreviewPolicy.fits(48 * 1024) && !PreviewPolicy.fits(48 * 1024 + 1) && !PreviewPolicy.fits(0), "48 KiB budget");
        expect(PreviewPolicy.QUALITIES[PreviewPolicy.QUALITIES.length - 1] >= 40, "never degraded below quality 40; too large is no_frame");
        System.out.println("PreviewPolicyTest: " + checks + " checks passed");
    }
}
