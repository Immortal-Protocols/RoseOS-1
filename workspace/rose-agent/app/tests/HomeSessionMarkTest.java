package dev.pixel.voice;

public final class HomeSessionMarkTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static HomeSessionMark.Mark mark(boolean service, boolean live, boolean connected, boolean muted, boolean communicating, String endReason) {
        return HomeSessionMark.of(ConversationState.of(service, live, connected, muted, communicating, endReason, "", true));
    }
    public static void main(String[] args) {
        expect(HomeSessionMark.of(null) == HomeSessionMark.Mark.NONE, "No state shows nothing");
        expect(mark(false, true, true, false, true, "") == HomeSessionMark.Mark.NONE,
                "Missing service never shows a guessed active microphone");
        expect(mark(true, false, false, false, false, "") == HomeSessionMark.Mark.NONE, "Idle shows nothing");
        expect(mark(true, false, false, false, true, "") == HomeSessionMark.Mark.CONNECTING, "Connecting is shown as connecting");
        expect(mark(true, true, true, false, true, "") == HomeSessionMark.Mark.LISTENING, "Active and unmuted shows the microphone on");
        expect(mark(true, true, true, true, true, "") == HomeSessionMark.Mark.MUTED, "Active and muted shows muted");
        expect(mark(true, true, false, false, false, "") == HomeSessionMark.Mark.NONE, "live alone is not an active microphone");
        expect(mark(true, false, true, false, false, "") == HomeSessionMark.Mark.NONE, "connected alone is not an active microphone");
        expect(mark(true, false, false, true, false, "") == HomeSessionMark.Mark.NONE, "A stale muted flag outside a session shows nothing");
        expect(mark(true, false, false, false, false, "cleanup") == HomeSessionMark.Mark.NONE, "Ended by the owner shows nothing");
        expect(mark(true, false, false, false, false, "provider_disconnect") == HomeSessionMark.Mark.NONE,
                "A failed or dropped session shows nothing on Home; the conversation explains it");
        expect(mark(true, false, true, false, true, "provider_disconnect") == HomeSessionMark.Mark.CONNECTING,
                "Reconnecting after a drop is connecting, not active");
        expect("Microphone on".equals(HomeSessionMark.description(HomeSessionMark.Mark.LISTENING))
                && "Microphone muted".equals(HomeSessionMark.description(HomeSessionMark.Mark.MUTED))
                && "Connecting".equals(HomeSessionMark.description(HomeSessionMark.Mark.CONNECTING))
                && HomeSessionMark.description(HomeSessionMark.Mark.NONE).isEmpty(), "Spoken descriptions");
        System.out.println("HomeSessionMarkTest: " + checks + " checks passed");
    }
}
