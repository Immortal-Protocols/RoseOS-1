package dev.pixel.voice;

/** #410 wave item 6: Rose Agent's one microphone-input mute (RoseMicInput), across Home, the raised edge (tray),
 * the conversation and the notification; calls that start, reconnect or are replaced; and stale page reports. */
public final class RoseMicInputTest {
    static int checks;
    static void expect(boolean c, String m) { checks++; if (!c) throw new AssertionError(m); }
    static final int NONE = RoseMicInput.Sync.NONE, APPLY_M = RoseMicInput.Sync.APPLY_MUTED, APPLY_U = RoseMicInput.Sync.APPLY_UNMUTED,
        PERSIST_M = RoseMicInput.Sync.PERSIST_MUTED, PERSIST_U = RoseMicInput.Sync.PERSIST_UNMUTED;

    public static void main(String[] args) {
        // What every surface shows: the live call's real input mute while active; otherwise the shared state.
        expect(RoseMicInput.shown(false, false, true) && !RoseMicInput.shown(false, true, false), "idle: the shared state, whatever a stale call said");
        expect(RoseMicInput.shown(true, true, false) && !RoseMicInput.shown(true, false, true), "live: the call's own input mute (truth), never the stored wish");
        // Only the two existing input-mute commands: no output, call, task or session command.
        expect(RoseMicInput.command(true).equals("tray_mute") && RoseMicInput.command(false).equals("tray_unmute"), "the existing input-mute commands");
        expect(RoseMicInput.requested("tray_mute") == Boolean.TRUE && RoseMicInput.requested("tray_unmute") == Boolean.FALSE, "commands carry the request");
        for (String other : new String[]{"talk", "end", "disconnect", "mute", "glasses", "auto_talk", null, ""})
            expect(RoseMicInput.requested(other) == null, "not a mic-input request: " + other);

        // A call becoming ready is brought to the shared state (reconnect, renderer replacement, a new call).
        RoseMicInput.Sync s = new RoseMicInput.Sync();
        expect(s.report(false, true, false, true) == APPLY_M && s.waiting(), "muted on Home while idle: the new call is muted");
        expect(s.report(true, true, false, true) == NONE, "a stale unmuted report while the apply travels changes nothing");
        expect(s.report(true, true, true, true) == NONE && !s.waiting(), "applied");
        expect(s.report(true, false, true, true) == NONE, "call ends: nothing persisted, nothing applied");
        expect(s.report(false, true, true, true) == NONE && !s.waiting(), "replacement already at the shared state: nothing to do");
        RoseMicInput.Sync r = new RoseMicInput.Sync();
        expect(r.report(false, true, true, false) == APPLY_U, "unmuted on Home: a call that starts muted is restored");
        expect(r.report(true, true, false, false) == NONE && !r.waiting(), "restored");

        // A change the page made itself (its own mute button) is the user's: it becomes the shared state.
        RoseMicInput.Sync p = new RoseMicInput.Sync();
        p.report(false, true, false, false);
        expect(p.report(true, true, true, false) == PERSIST_M, "in-page mute persists");
        expect(p.report(true, true, false, true) == PERSIST_U, "in-page unmute persists");
        expect(p.report(true, true, false, false) == NONE, "in step");

        // A surface's request while live (Home, the tray, the conversation's mic, the notification: all commands).
        RoseMicInput.Sync h = new RoseMicInput.Sync();
        h.report(false, true, false, false);
        h.expect(true);
        expect(h.report(true, true, false, true) == NONE, "Home's mute: the page's old report is stale, not a revert");
        expect(h.report(true, true, true, true) == NONE && !h.waiting(), "Home's mute applied");
        h.expect(false);
        expect(h.report(true, true, true, false) == NONE, "the edge's unmute: stale report ignored");
        expect(h.report(true, true, false, false) == NONE && !h.waiting(), "unmute applied (restores input)");
        // Rapid toggles: the last request wins; earlier echoes are stale.
        h.expect(true); h.expect(false);
        expect(h.report(true, true, true, false) == NONE, "the earlier mute's echo is stale after the unmute");
        expect(h.report(true, true, false, false) == NONE && !h.waiting(), "settled on the last request");

        // Lifecycle: losing the call (renderer gone, end) clears anything in flight; the next call re-applies.
        RoseMicInput.Sync l = new RoseMicInput.Sync();
        l.report(false, true, false, true);
        expect(l.waiting(), "apply in flight");
        expect(l.report(true, false, false, true) == NONE && !l.waiting(), "renderer gone: the pending apply is dropped, nothing persisted");
        expect(l.report(false, true, false, true) == APPLY_M, "the replacement call is brought to the shared state again");
        System.out.println("RoseMicInputTest: " + checks + " checks passed");
    }
}
