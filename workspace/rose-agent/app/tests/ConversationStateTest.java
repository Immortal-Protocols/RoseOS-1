package dev.pixel.voice;

public final class ConversationStateTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static ConversationState s(boolean live, boolean connected, boolean muted, boolean communicating, String endReason) {
        return ConversationState.of(true, live, connected, muted, communicating, endReason, "", true);
    }
    static final ConversationState IDLE = s(false, false, false, false, "");
    static final ConversationState ENDED_BY_USER = s(false, false, false, false, "cleanup");
    static final ConversationState OPENING = s(false, false, false, true, "");
    static final ConversationState ACTIVE = s(true, true, false, true, "");
    static final ConversationState MUTED = s(true, true, true, true, "");
    static final ConversationState TIMED_OUT = s(false, false, false, false, "start_timeout");
    static final ConversationState DROPPED = s(false, false, false, false, "provider_disconnect");

    public static void main(String[] args) {
        ConversationState none = ConversationState.of(false, true, true, true, true, "", "", true);
        expect(none.phase == ConversationState.Phase.INACTIVE && !none.muted, "No service is inactive, never guessed active");
        expect(s(true, false, false, false, "").phase == ConversationState.Phase.INACTIVE, "live alone is not active");
        expect(s(false, true, false, false, "").phase == ConversationState.Phase.INACTIVE, "connected alone is not active");
        expect(OPENING.phase == ConversationState.Phase.CONNECTING && !OPENING.muted, "Open attempt is connecting");
        expect(s(false, false, true, false, "").muted == false, "Muted is only reported for an active session");

        // The five current failure paths (review of aeef650): each maps to a visible failure, not idle.
        // Start timeout: voiceLost('Voice did not connect…','start_timeout').
        expect(TIMED_OUT.phase == ConversationState.Phase.ERROR
                && TIMED_OUT.failure == ConversationState.Failure.NOT_CONNECTED, "Start timeout is a failure");
        // Provider disconnect: voiceLost('Voice disconnected…','provider_disconnect').
        expect(DROPPED.failure == ConversationState.Failure.DISCONNECTED, "Provider disconnect is a failure");
        // Backend restart: voiceLost('Mac restarted…') uses the default reason 'other'.
        expect(s(false, false, false, false, "other").failure == ConversationState.Failure.DISCONNECTED,
                "Backend restart is a failure");
        // Backend unreachable while idle: check() reports /health failure; no call ended.
        ConversationState unreachable = ConversationState.of(true, false, false, false, false, "", "", false);
        expect(unreachable.phase == ConversationState.Phase.ERROR
                && unreachable.failure == ConversationState.Failure.UNREACHABLE, "Unreachable backend is shown");
        // Occupied session: POST /voice/session returns 409 → start_error with status 409.
        ConversationState busy = ConversationState.of(true, false, false, false, false, "start_error", "409", true);
        expect(busy.failure == ConversationState.Failure.BUSY, "Occupied session (409) is its own failure");
        expect(ConversationState.of(true, false, false, false, false, "start_error", "TypeError", true).failure
                == ConversationState.Failure.NOT_CONNECTED, "Other start errors did not connect");

        expect(ENDED_BY_USER.phase == ConversationState.Phase.INACTIVE && ENDED_BY_USER.failureText().isEmpty(),
                "A deliberate end is not a failure");
        expect(s(false, false, false, false, "handoff").phase == ConversationState.Phase.INACTIVE
                && s(false, false, false, false, "routing_change").phase == ConversationState.Phase.INACTIVE,
                "Handoff and routing changes are not failures");
        expect(ConversationState.of(true, false, false, false, false, "cleanup", "", null).phase
                == ConversationState.Phase.INACTIVE, "Unknown reachability is not a failure");
        expect(ConversationState.of(true, false, false, false, false, "start_timeout", "", false).failure
                == ConversationState.Failure.NOT_CONNECTED, "The call failure outranks reachability");
        expect(OPENING.failure == ConversationState.Failure.NONE && ACTIVE.failure == ConversationState.Failure.NONE,
                "A new attempt or live session hides an old failure");
        expect(!TIMED_OUT.failureText().isEmpty() && !busy.failureText().equals(TIMED_OUT.failureText()),
                "Failures explain themselves distinctly");

        expect("talk".equals(IDLE.primaryCommand()) && "talk".equals(TIMED_OUT.primaryCommand()), "Idle or failed: Speak");
        expect("end".equals(OPENING.primaryCommand()), "Connecting: Cancel ends the attempt");
        expect("tray_mute".equals(ACTIVE.primaryCommand()) && "tray_unmute".equals(MUTED.primaryCommand()),
                "Active uses the idempotent set-mute commands");
        expect(!IDLE.canEnd() && !OPENING.canEnd() && ACTIVE.canEnd() && !TIMED_OUT.canEnd(), "End call only when active");

        ConversationState.Notices n = new ConversationState.Notices();
        expect(n.observe(ACTIVE) == null, "First observation seeds; reopening never replays Connected");
        expect(n.observe(MUTED).equals("Muted") && n.observe(ACTIVE).equals("Unmuted"), "Mute and unmute notices");
        expect(n.observe(ACTIVE) == null, "Steady state emits nothing");
        expect(n.observe(ENDED_BY_USER).equals("Call ended"), "Ending an active session");
        expect(n.observe(ENDED_BY_USER) == null, "Call ended is emitted once");

        // Rapid end then start: the previous Call ended notice is withdrawn as soon as the attempt opens.
        expect(ConversationState.CLEAR.equals(n.observe(OPENING)), "New attempt clears the old Call ended notice");
        expect(n.observe(ACTIVE).equals("Connected"), "Then Connected once actually active");

        n.reset(); n.observe(IDLE);
        expect(ConversationState.CLEAR.equals(n.observe(OPENING)), "No Connecting notice; only a clear");
        expect(n.observe(ACTIVE).equals("Connected"), "Connected only when the session is actually active");
        expect(n.observe(OPENING) == null, "A drop into reconnecting is not a call end");
        expect(n.observe(ACTIVE).equals("Connected"), "Recovered session is connected again");
        n.observe(OPENING);
        expect(n.observe(DROPPED).equals("Call ended"), "Reconnect that fails ends the call");

        // Failed start, then retry: no notice for the failure, the retry clears, and a success connects.
        n.reset(); n.observe(IDLE); n.observe(OPENING);
        expect(n.observe(TIMED_OUT) == null, "A start that never connected is not a call end");
        expect(ConversationState.CLEAR.equals(n.observe(OPENING)), "Retry clears");
        expect(n.observe(ACTIVE).equals("Connected"), "Retry that connects announces Connected");

        n.reset(); n.observe(IDLE);
        expect(n.observe(MUTED).equals("Connected"), "Connecting while muted announces Connected only");

        // Tray protocol v1 fields come from the same state (OS-CORE-STATE-R001).
        expect(OPENING.trayStatus(true).equals("Connecting") && !OPENING.trayConnected() && !OPENING.trayListening(),
                "Any open attempt is Connecting on the tray, whatever the display text says");
        ConversationState liveNotConnected = s(true, false, false, true, "");
        expect(!liveNotConnected.trayListening() && !liveNotConnected.trayConnected()
                && liveNotConnected.trayStatus(true).equals("Connecting"), "live without connected never lights listening");
        expect(ACTIVE.trayConnected() && ACTIVE.trayListening() && !ACTIVE.trayMuted()
                && ACTIVE.trayStatus(true).equals("Listening"), "Active session: connected and listening");
        expect(MUTED.trayConnected() && !MUTED.trayListening() && MUTED.trayMuted()
                && MUTED.trayStatus(true).equals("Muted"), "Muted session: connected, not listening");
        expect(!IDLE.trayConnected() && !IDLE.trayListening() && !IDLE.trayMuted()
                && IDLE.trayStatus(true).equals("Disconnected"), "Idle service is disconnected");
        expect(!TIMED_OUT.trayConnected() && TIMED_OUT.trayStatus(true).equals("Disconnected"), "Failure is disconnected, not connecting");
        expect(s(false, false, true, false, "").trayMuted() == false, "Muted flag without a session is not reported");
        expect(none.trayStatus(false).equals("Ready") && !none.trayConnected(), "No service keeps the existing Ready word");
        for (ConversationState x : new ConversationState[] {IDLE, OPENING, ACTIVE, MUTED, TIMED_OUT, DROPPED})
            expect(x.trayConnected() == x.active() && (x.trayListening() == (x.active() && !x.muted)),
                    "Tray and conversation agree on active and listening");

        System.out.println("ConversationStateTest: " + checks + " checks passed");
    }
}
