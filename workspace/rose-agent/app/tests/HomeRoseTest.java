package dev.pixel.voice;

import dev.pixel.voice.RoseVisualState.Connection;
import dev.pixel.voice.RoseVisualState.Mic;
import dev.pixel.voice.RoseVisualState.Surround;

/** OS-HIFI-HOME-001 M004: the Home tray's Rose state from what VoiceService publishes, with Home's own
 * visibility and reduced motion. Uses the production ConversationState and RoseVisualState classes. */
public final class HomeRoseTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static final ConversationState ACTIVE = ConversationState.of(true, true, true, false, true, "", "", true),
            MUTED = ConversationState.of(true, true, true, true, true, "", "", true),
            CONNECTING = ConversationState.of(true, false, false, false, true, "", "", true),
            IDLE = ConversationState.of(true, false, false, false, false, "", "", true),
            NO_SERVICE = ConversationState.of(false, false, false, false, false, null, null, null);
    static final long NOW = 100_000;

    static RoseVisualState home(ConversationState c, float in, float out, long observed, boolean reduced, boolean shown) {
        return HomeRose.state(c, in, out, observed, NOW, reduced, shown);
    }
    static void axes(RoseVisualState r, Connection c, Mic m, Surround s, String message) {
        expect(r.connection == c && r.mic == m && r.surround == s, message + ": " + r.connection + "/" + r.mic + "/" + r.surround);
    }

    public static void main(String[] args) {
        // Fresh measured levels on a shown Home animate from the real axes.
        RoseVisualState speaking = home(ACTIVE, .02f, .3f, NOW - 200, false, true);
        axes(speaking, Connection.JOINED, Mic.ROSE, Surround.SPEAKING, "Connected, mic on, Rose speaking");
        expect(speaking.animate && speaking.levelsKnown && speaking.amplitude == .3f, "Animates with Rose's real output level");
        axes(home(ACTIVE, .2f, 0f, NOW - 200, false, true), Connection.JOINED, Mic.ROSE, Surround.LISTENING, "Connected, quiet: listening");

        // Muted while Rose speaks: connection geometry kept, silver microphone, real output surround.
        RoseVisualState mutedSpeaking = home(MUTED, .2f, .3f, NOW - 200, false, true);
        axes(mutedSpeaking, Connection.JOINED, Mic.SILVER, Surround.SPEAKING, "Muted speaking keeps JOINED, shows SILVER and SPEAKING");
        // Negative controls for crossed axes: muting must not change the connection, connecting is never joined.
        expect(mutedSpeaking.connection == speaking.connection, "Mute never moves the connection axis");
        axes(home(CONNECTING, .5f, .5f, NOW - 200, false, true), Connection.NARROW_GAP, Mic.SILVER, Surround.QUIET, "Connecting is the narrow gap, never joined");
        axes(home(IDLE, .5f, .5f, NOW - 200, false, true), Connection.WIDE_GAP, Mic.SILVER, Surround.QUIET, "Idle is the wide gap, quiet");
        axes(home(NO_SERVICE, Float.NaN, Float.NaN, 0, false, true), Connection.WIDE_GAP, Mic.SILVER, Surround.QUIET, "No service: nothing guessed");

        // Stale, never observed or future levels stay unknown: no speaking inferred, no amplitude.
        for (long observed : new long[]{NOW - RoseVisualState.STALE_MS - 1, 0, NOW + 50}) {
            RoseVisualState r = home(ACTIVE, .4f, .4f, observed, false, true);
            expect(!r.levelsKnown && r.amplitude == 0f && r.surround == Surround.LISTENING, "Levels observed at " + observed + " are unknown");
        }

        // Hidden Home or reduced motion stops animation only; the meaning stays.
        for (boolean[] mode : new boolean[][]{{false, false}, {true, true}, {true, false}}) {
            RoseVisualState r = home(MUTED, .2f, .3f, NOW - 200, mode[0], mode[1]);
            axes(r, Connection.JOINED, Mic.SILVER, Surround.SPEAKING, "Reduced " + mode[0] + " shown " + mode[1] + " keeps the axes");
            expect(!r.animate && r.amplitude == 0f, "Reduced " + mode[0] + " shown " + mode[1] + " does not animate");
        }

        // Home's inputs.
        expect(HomeRose.reducedMotion(0f) && !HomeRose.reducedMotion(1f) && !HomeRose.reducedMotion(.5f), "Animator scale 0 is reduced motion");
        expect(HomeRose.shown(true, true) && !HomeRose.shown(false, true) && !HomeRose.shown(true, false),
                "Shown only while resumed with the Home frame as content");
        System.out.println("HomeRoseTest: " + checks + " checks passed");
    }
}
