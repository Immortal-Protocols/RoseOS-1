package dev.pixel.voice;

import dev.pixel.voice.RoseVisualState.Connection;
import dev.pixel.voice.RoseVisualState.Mic;
import dev.pixel.voice.RoseVisualState.Surround;

public final class RoseVisualStateTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static ConversationState s(boolean live, boolean connected, boolean muted, boolean communicating, String endReason) {
        return ConversationState.of(true, live, connected, muted, communicating, endReason, "", true);
    }
    static final ConversationState NO_SERVICE = ConversationState.of(false, false, false, false, false, null, null, null);
    static final ConversationState IDLE = s(false, false, false, false, "");
    static final ConversationState OPENING = s(false, false, false, true, "");
    static final ConversationState ACTIVE = s(true, true, false, true, "");
    static final ConversationState MUTED = s(true, true, true, true, "");
    static final ConversationState TIMED_OUT = s(false, false, false, false, "start_timeout");
    static final ConversationState DROPPED = s(false, false, false, false, "provider_disconnect");
    static final ConversationState BUSY = ConversationState.of(true, false, false, false, false, "start_error", "409", true);
    static final ConversationState UNREACHABLE = ConversationState.of(true, false, false, false, false, "", "", false);
    static final float QUIET = 0f, SPEECH = .4f, VOICE_IN = .3f;

    static RoseVisualState v(ConversationState state, float input, float output) {
        return RoseVisualState.from(state, input, output, false, true);
    }
    static void axes(RoseVisualState r, Connection c, Mic m, Surround s, String message) {
        expect(r.connection == c && r.mic == m && r.surround == s,
                message + ": " + r.connection + "/" + r.mic + "/" + r.surround);
    }

    public static void main(String[] args) {
        // The packet's headline case: muted while Rose speaks is joined, silver and speaking together.
        RoseVisualState mutedSpeaking = v(MUTED, QUIET, SPEECH);
        axes(mutedSpeaking, Connection.JOINED, Mic.SILVER, Surround.SPEAKING, "Muted + speaking keeps all three axes");
        expect(mutedSpeaking.amplitude == SPEECH && !mutedSpeaking.failed, "Speaking while muted animates the output");

        // Muting changes only the microphone color and the listening surround, never the connection.
        for (float output : new float[] {QUIET, SPEECH}) {
            RoseVisualState on = v(ACTIVE, VOICE_IN, output), off = v(MUTED, VOICE_IN, output);
            expect(on.connection == Connection.JOINED && off.connection == Connection.JOINED, "Mute keeps the connection joined");
            expect(on.mic == Mic.ROSE && off.mic == Mic.SILVER, "Mute changes the color");
        }
        axes(v(ACTIVE, VOICE_IN, QUIET), Connection.JOINED, Mic.ROSE, Surround.LISTENING, "Live microphone listens");
        expect(v(ACTIVE, VOICE_IN, QUIET).amplitude == VOICE_IN, "Listening follows the measured input");
        axes(v(MUTED, VOICE_IN, QUIET), Connection.JOINED, Mic.SILVER, Surround.QUIET, "Muted and silent is quiet");
        expect(v(MUTED, VOICE_IN, QUIET).amplitude == 0f, "A muted microphone's input never animates");
        axes(v(ACTIVE, VOICE_IN, SPEECH), Connection.JOINED, Mic.ROSE, Surround.SPEAKING, "Rose's speech wins over listening");

        // The speaking threshold is the voice page's assistantSpeaking threshold.
        expect(v(ACTIVE, QUIET, RoseVisualState.SPEAKING_LEVEL).surround == Surround.LISTENING, "At the threshold is not speaking");
        expect(v(ACTIVE, QUIET, .009f).surround == Surround.SPEAKING, "Above the threshold is speaking");

        // Connecting, ended and the failure paths stay distinct and keep ConversationState's words.
        axes(v(OPENING, VOICE_IN, SPEECH), Connection.NARROW_GAP, Mic.SILVER, Surround.QUIET, "Connecting is a narrow gap with no voice");
        expect(!v(OPENING, VOICE_IN, SPEECH).failed && v(OPENING, VOICE_IN, SPEECH).amplitude == 0f, "Connecting is not a failure and does not animate");
        for (ConversationState ended : new ConversationState[] {NO_SERVICE, IDLE}) {
            RoseVisualState r = v(ended, VOICE_IN, SPEECH);
            axes(r, Connection.WIDE_GAP, Mic.SILVER, Surround.QUIET, "Ended is a wide gap");
            expect(!r.failed && r.amplitude == 0f, "A deliberate end is not a failure");
        }
        for (ConversationState failure : new ConversationState[] {TIMED_OUT, DROPPED, BUSY, UNREACHABLE}) {
            RoseVisualState r = v(failure, VOICE_IN, SPEECH);
            axes(r, Connection.WIDE_GAP, Mic.SILVER, Surround.QUIET, "Failure " + failure.failure + " is a wide gap");
            expect(r.failed && !failure.failureText().isEmpty(), "Failure " + failure.failure + " is flagged with its existing words");
        }

        // Levels are bounded and finite; missing, stale or non-finite levels are never treated as speech.
        expect(v(ACTIVE, QUIET, 7f).amplitude == 1f && v(ACTIVE, -2f, QUIET).amplitude == 0f, "Levels are bounded to 0..1");
        for (float bad : new float[] {Float.NaN, Float.POSITIVE_INFINITY, Float.NEGATIVE_INFINITY}) {
            RoseVisualState r = v(MUTED, QUIET, bad);
            axes(r, Connection.JOINED, Mic.SILVER, Surround.QUIET, "Unknown output is not speech (" + bad + ")");
            expect(!r.levelsKnown && r.amplitude == 0f && Float.isFinite(r.amplitude), "Unknown output gives a finite zero");
            RoseVisualState live = v(ACTIVE, bad, SPEECH);
            expect(live.surround == Surround.LISTENING && !live.levelsKnown && live.amplitude == 0f,
                    "Unknown input still listens on the mic state but is not animated or used as speech");
        }
        long now = 50_000;
        expect(Float.isNaN(RoseVisualState.fresh(SPEECH, 0, now)), "A level never observed is missing");
        expect(RoseVisualState.fresh(SPEECH, now - RoseVisualState.STALE_MS, now) == SPEECH, "A level within the window is used");
        expect(Float.isNaN(RoseVisualState.fresh(SPEECH, now - RoseVisualState.STALE_MS - 1, now)), "A stale level is missing");
        expect(Float.isNaN(RoseVisualState.fresh(SPEECH, now + 5, now)), "A level from the future is missing");
        RoseVisualState stale = v(MUTED, RoseVisualState.fresh(QUIET, 1, now), RoseVisualState.fresh(SPEECH, 1, now));
        expect(stale.surround == Surround.QUIET && !stale.levelsKnown, "Old speech does not keep the surround speaking");
        expect(v(ACTIVE, QUIET, SPEECH).levelsKnown, "Fresh finite levels are known");

        // Reduced motion and hidden presentation stop motion without changing the semantic axes.
        for (boolean reduced : new boolean[] {false, true}) for (boolean visible : new boolean[] {false, true}) {
            RoseVisualState r = RoseVisualState.from(MUTED, VOICE_IN, SPEECH, reduced, visible);
            axes(r, Connection.JOINED, Mic.SILVER, Surround.SPEAKING, "Presentation keeps state (reduced " + reduced + ", visible " + visible + ")");
            expect(r.animate == (!reduced && visible), "Animate only when motion is allowed and the surface is shown");
            expect(r.amplitude == (r.animate ? SPEECH : 0f), "No amplitude work while still or hidden");
        }

        // The adapter is read-only: ConversationState's notices, controls and tray fields are unchanged by it.
        ConversationState.Notices notices = new ConversationState.Notices();
        expect(notices.observe(ACTIVE) == null, "Seed");
        v(MUTED, QUIET, SPEECH);
        expect(MUTED.primaryCommand().equals("tray_unmute") && MUTED.canEnd() && MUTED.trayConnected() && MUTED.trayMuted()
                && !MUTED.trayListening() && MUTED.trayStatus(true).equals("Muted"), "Mute keeps the existing control and tray contract");
        expect(ConversationState.MUTED.equals(notices.observe(MUTED)), "The mute notice still comes only from Notices");
        expect(notices.observe(MUTED) == null, "Building visual state emits no further notice");
        System.out.println("RoseVisualStateTest: " + checks + " checks passed");
    }
}
