package dev.pixel.voice;

/** #410 wave item 6: Rose Agent's one microphone-input mute (RoseMicInput): what every surface shows, and the only
 * commands it uses. The backend's start/reconnect/acknowledgement rules are App Core's and tested there. */
public final class RoseMicInputTest {
    static int checks;
    static void expect(boolean c, String m) { checks++; if (!c) throw new AssertionError(m); }

    public static void main(String[] args) {
        // What every surface shows: the live call's real input mute while active; otherwise the shared state.
        expect(RoseMicInput.shown(false, false, true) && !RoseMicInput.shown(false, true, false), "idle: the shared state, whatever a stale call said");
        expect(RoseMicInput.shown(true, true, false) && !RoseMicInput.shown(true, false, true), "live: the call's own input mute (truth), never the stored wish");
        // Only the two existing input-mute commands: no output, call, task or session command.
        expect(RoseMicInput.command(true).equals("tray_mute") && RoseMicInput.command(false).equals("tray_unmute"), "the existing input-mute commands");
        System.out.println("RoseMicInputTest: " + checks + " checks passed");
    }
}
