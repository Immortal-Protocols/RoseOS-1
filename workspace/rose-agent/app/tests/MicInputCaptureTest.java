package dev.pixel.voice;

/** #410 item 6: the capture-side rules of Rose's microphone-input mute (MicInputCapture). Host test, no Android. */
public final class MicInputCaptureTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    public static void main(String[] args) {
        expect("rose-mic-input".equals(MicInputCapture.PREFS) && "muted".equals(MicInputCapture.KEY), "Home's RoseMicState store");
        expect(Boolean.TRUE.equals(MicInputCapture.requested("tray_mute")) && Boolean.FALSE.equals(MicInputCapture.requested("tray_unmute")),
            "the two requests");
        for (String other : new String[] {"mute", "talk", "end", "glasses", null, ""})
            expect(MicInputCapture.requested(other) == null, "never a request: " + other);
        expect(MicInputCapture.settled(3, 3) && MicInputCapture.settled(4, 3) && !MicInputCapture.settled(2, 3), "settled by sequence");
        // A live, connected, settled change the page made itself is stored.
        expect(MicInputCapture.storeReported(true, true, true, true, false), "the page's own mute is stored");
        expect(MicInputCapture.storeReported(true, true, true, false, true), "the page's own unmute is stored");
        expect(!MicInputCapture.storeReported(true, true, true, true, true), "no change, nothing stored");
        // A reset is never stored: a call ending, cleanup, a lost renderer, a report while connecting.
        expect(!MicInputCapture.storeReported(false, false, true, false, true), "call end or renderer loss keeps the mute");
        expect(!MicInputCapture.storeReported(false, true, true, false, true), "connecting keeps the mute");
        expect(!MicInputCapture.storeReported(true, false, true, false, true), "not connected keeps the mute");
        // A report from before a request was applied is stale.
        expect(!MicInputCapture.storeReported(true, true, false, false, true), "a stale report never undoes a request");
        // A newly live call: nothing is stored from its first report; a stored mute it came up without is applied.
        expect(!MicInputCapture.storable(false, true) && MicInputCapture.storable(true, true) && !MicInputCapture.storable(true, false),
            "never the first report of a newly live call");
        expect(MicInputCapture.muteOnConnect(false, true, false, true), "stored muted, came up listening: mute it");
        expect(!MicInputCapture.muteOnConnect(false, true, true, false), "came up muted: never unmuted to match");
        expect(!MicInputCapture.muteOnConnect(false, true, true, true) && !MicInputCapture.muteOnConnect(false, true, false, false), "already matching");
        expect(!MicInputCapture.muteOnConnect(true, true, false, true), "only on the transition; later it is the page's own change");
        expect(!MicInputCapture.muteOnConnect(false, false, false, true), "not live: nothing to apply");
        System.out.println("MicInputCaptureTest: " + checks + " checks passed");
    }
}
