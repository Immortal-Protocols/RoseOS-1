package dev.pixel.voice;

public final class ConversationStateTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static ConversationState s(boolean live, boolean connected, boolean muted, boolean communicating, String status) {
        return ConversationState.of(true, live, connected, muted, communicating, status);
    }
    static final ConversationState IDLE = s(false, false, false, false, "Ready");
    static final ConversationState OPENING = s(false, false, false, true, "Opening microphone…");
    static final ConversationState ACTIVE = s(true, true, false, true, "Listening");
    static final ConversationState MUTED = s(true, true, true, true, "Muted");
    static final ConversationState FAILED = s(false, false, false, false, "Voice connection failed");

    public static void main(String[] args) {
        ConversationState none = ConversationState.of(false, true, true, true, true, "Listening");
        expect(none.phase == ConversationState.Phase.INACTIVE && !none.muted, "No service is inactive, never guessed active");
        expect(s(true, false, false, false, "x").phase == ConversationState.Phase.INACTIVE, "live alone is not active");
        expect(s(false, true, false, false, "x").phase == ConversationState.Phase.INACTIVE, "connected alone is not active");
        expect(OPENING.phase == ConversationState.Phase.CONNECTING && !OPENING.muted, "Open attempt is connecting");
        expect(FAILED.phase == ConversationState.Phase.ERROR, "Failure status is an error");
        expect(s(false, false, true, false, "Ready").muted == false, "Muted is only reported for an active session");

        expect("talk".equals(IDLE.primaryCommand()) && "talk".equals(FAILED.primaryCommand()), "Idle or failed starts voice");
        expect(OPENING.primaryCommand() == null, "No second start while connecting");
        expect("tray_mute".equals(ACTIVE.primaryCommand()) && "tray_unmute".equals(MUTED.primaryCommand()),
                "Active uses the idempotent set-mute commands");
        expect(!IDLE.canEnd() && OPENING.canEnd() && ACTIVE.canEnd() && !FAILED.canEnd(), "End only for open sessions");

        ConversationState.Notices n = new ConversationState.Notices();
        expect(n.observe(ACTIVE) == null, "First observation seeds; reopening never replays Connected");
        expect(n.observe(MUTED).equals("Muted") && n.observe(ACTIVE).equals("Unmuted"), "Mute and unmute notices");
        expect(n.observe(ACTIVE) == null, "Steady state emits nothing");
        expect(n.observe(IDLE).equals("Call ended"), "Ending an active session");
        expect(n.observe(IDLE) == null, "Call ended is emitted once");

        n.reset(); n.observe(IDLE);
        expect(n.observe(OPENING) == null, "No Connecting notice");
        expect(n.observe(ACTIVE).equals("Connected"), "Connected only when the session is actually active");
        expect(n.observe(OPENING) == null, "A drop into reconnecting is not a call end");
        expect(n.observe(ACTIVE).equals("Connected"), "Recovered session is connected again");
        n.observe(OPENING);
        expect(n.observe(FAILED).equals("Call ended"), "Reconnect that fails ends the call");

        n.reset(); n.observe(IDLE); n.observe(OPENING);
        expect(n.observe(FAILED) == null, "A start that never connected is not a call end");

        n.reset(); n.observe(IDLE);
        expect(n.observe(MUTED).equals("Connected"), "Connecting while muted announces Connected only");

        System.out.println("ConversationStateTest: " + checks + " checks passed");
    }
}
