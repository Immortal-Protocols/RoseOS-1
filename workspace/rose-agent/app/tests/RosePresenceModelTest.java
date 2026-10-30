package dev.pixel.voice;

/** APP-HIFI-CONVERSATION-1029-P: the conversation presence draws Core's RoseVisualState with the Rose
 * three-dimensions study's meaning. Real ConversationState and RoseVisualState, fixture levels; no Android. */
public final class RosePresenceModelTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static ConversationState s(boolean live, boolean connected, boolean muted, boolean communicating) {
        return ConversationState.of(true, live, connected, muted, communicating, "", "", true);
    }
    static final ConversationState IDLE = s(false, false, false, false), CONNECTING = s(false, false, false, true),
        ACTIVE = s(true, true, false, true), MUTED = s(true, true, true, true);
    static RosePresenceModel shown(ConversationState c, float in, float out, boolean animate) {
        RosePresenceModel m = new RosePresenceModel(); m.set(RoseVisualState.from(c, in, out, !animate, true)); return m;
    }
    static boolean near(float a, float b) { return Math.abs(a - b) < 1e-4f; }

    static int luma(int c) { return 299 * ((c >> 16) & 255) + 587 * ((c >> 8) & 255) + 114 * (c & 255); }
    static int chroma(int c) {
        int r = (c >> 16) & 255, g = (c >> 8) & 255, b = c & 255;
        return Math.max(r, Math.max(g, b)) - Math.min(r, Math.min(g, b));
    }
    static char hue(int c) { return ((c >> 16) & 255) > (c & 255) ? 'r' : 'b'; }
    static int shaderFace(double r, double g, double b) {
        int[] ch = new int[3]; double[] in = {r, g, b};
        for (int i = 0; i < 3; i++) { double x = in[i] * .16; x = x / (x + .55); ch[i] = (int) Math.round(Math.pow(x, 1 / 2.2) * 255); }
        return 0xff000000 | (ch[0] << 16) | (ch[1] << 8) | ch[2];
    }

    public static void main(String[] args) {
        // The three axes, exactly as the study defines them (static presentation: targets shown at once).
        RosePresenceModel idle = shown(IDLE, Float.NaN, Float.NaN, false);
        expect(near(idle.gap, RosePresenceModel.GAP_WIDE) && idle.mic == 0f && idle.ringAlpha() == 0f, "Disconnected: wide split, silver, no surround");
        RosePresenceModel connecting = shown(CONNECTING, .5f, .5f, false);
        expect(near(connecting.gap, RosePresenceModel.GAP_NARROW) && connecting.mic == 0f && connecting.ringAlpha() == 0f,
                "Connecting: narrow split, still silver, no surround even with levels present");
        RosePresenceModel listening = shown(ACTIVE, .2f, 0f, false);
        expect(near(listening.gap, RosePresenceModel.GAP_JOINED) && listening.metal() == RosePresenceModel.ROSE && listening.ringAlpha() == 1f
                && near(listening.ringRadius(0f), RosePresenceModel.RING_CLOSE), "Connected, mic on, quiet Rose: joined, rose, close surround");
        RosePresenceModel speaking = shown(ACTIVE, 0f, .5f, false);
        expect(near(speaking.ringRadius(0f), RosePresenceModel.RING_BROAD) && speaking.metal() == RosePresenceModel.ROSE, "Rose speaking: broad surround");
        RosePresenceModel mutedSpeaking = shown(MUTED, 0f, .5f, false);
        expect(near(mutedSpeaking.gap, RosePresenceModel.GAP_JOINED) && mutedSpeaking.metal() == RosePresenceModel.SILVER
                && near(mutedSpeaking.ringRadius(0f), RosePresenceModel.RING_BROAD), "Muted while Rose speaks: still joined, silver, broad surround");
        RosePresenceModel mutedQuiet = shown(MUTED, .3f, 0f, false);
        expect(mutedQuiet.ringAlpha() == 0f && mutedQuiet.metal() == RosePresenceModel.SILVER, "Muted and quiet: no surround, mic level ignored");
        expect(shown(ACTIVE, Float.NaN, Float.NaN, false).ringAlpha() == 1f && near(shown(ACTIVE, Float.NaN, Float.NaN, false).ringRadius(0f), RosePresenceModel.RING_CLOSE),
                "Unknown levels never claim Rose is speaking");

        // Static presentation (reduced motion or hidden): snapped, no frames, no speech envelope.
        expect(!speaking.step(.016f) && speaking.energy == 0f && near(speaking.ringRadius(0f), speaking.ringRadius(12.3f)), "No animation, no envelope");
        RosePresenceModel hidden = new RosePresenceModel(); hidden.set(RoseVisualState.from(ACTIVE, 0f, .5f, false, false));
        expect(!hidden.step(.016f) && near(hidden.ringRadius(0f), RosePresenceModel.RING_BROAD), "Hidden is static too");

        // Animated transitions ease toward the target and settle; the speech envelope moves only the ring.
        RosePresenceModel m = shown(IDLE, Float.NaN, Float.NaN, false);
        m.set(RoseVisualState.from(ACTIVE, .1f, 0f, false, true));
        float previous = m.gap; boolean converging = true;
        for (int i = 0; i < 20; i++) { m.step(.016f); converging &= m.gap <= previous; previous = m.gap; }
        expect(converging && m.gap < RosePresenceModel.GAP_WIDE && m.gap > RosePresenceModel.GAP_JOINED, "Gap eases from wide toward joined");
        for (int i = 0; i < 400; i++) m.step(.016f);
        expect(near(m.gap, RosePresenceModel.GAP_JOINED) && near(m.mic, 1f) && near(m.voice, 1f), "Then settles exactly on the target");
        m.set(RoseVisualState.from(ACTIVE, 0f, .9f, false, true));
        for (int i = 0; i < 400; i++) m.step(.016f);
        float lo = Float.MAX_VALUE, hi = 0f;
        for (int i = 0; i < 200; i++) { float r = m.ringRadius(i * .05f); lo = Math.min(lo, r); hi = Math.max(hi, r); }
        expect(hi - lo > .01f && lo >= RosePresenceModel.RING_CLOSE && hi <= RosePresenceModel.RING_BROAD + RosePresenceModel.RING_ENERGY * 1.1f,
                "Speech energy moves the broad ring within its bounds");
        m.set(RoseVisualState.from(IDLE, Float.NaN, Float.NaN, false, true));
        for (int i = 0; i < 600; i++) m.step(.016f);
        expect(!m.step(.016f) && m.ringAlpha() == 0f && near(m.gap, RosePresenceModel.GAP_WIDE), "Ending the call settles quiet and stops frames");
        expect(m.step(.016f) == false, "Frames stay stopped");
        expect(new RosePresenceModel().step(10f) == false, "Large frame gaps are bounded and an unset model is static");

        // Accessibility words describe state; no notice, no Interrupt.
        expect(RosePresenceModel.describe(RoseVisualState.from(MUTED, 0f, .5f, true, true)).equals("Rose: connected, microphone off, Rose speaking"), "Muted while speaking described");
        expect(RosePresenceModel.describe(RoseVisualState.from(CONNECTING, 0f, 0f, true, true)).equals("Rose: connecting, microphone off"), "Connecting described");
        expect(RosePresenceModel.mix(RosePresenceModel.SILVER, RosePresenceModel.ROSE, 0f) == RosePresenceModel.SILVER
                && RosePresenceModel.mix(RosePresenceModel.SILVER, RosePresenceModel.ROSE, 1f) == RosePresenceModel.ROSE, "Metal mix endpoints");

        // The quiet caption (HiFi .rose-caption), from the same state; never a notice.
        expect(RosePresenceModel.caption(RoseVisualState.from(IDLE, Float.NaN, Float.NaN, true, true)).isEmpty(), "No caption when not in a call");
        expect(RosePresenceModel.caption(RoseVisualState.from(CONNECTING, 0f, 0f, true, true)).equals("Connecting…"), "Connecting caption");
        expect(RosePresenceModel.caption(RoseVisualState.from(ACTIVE, .2f, 0f, true, true)).equals("Listening"), "Listening caption");
        expect(RosePresenceModel.caption(RoseVisualState.from(MUTED, 0f, 0f, true, true)).equals("Microphone muted"), "Muted caption");
        expect(RosePresenceModel.caption(RoseVisualState.from(MUTED, 0f, .5f, true, true)).equals("Rose speaking"), "Muted while Rose speaks says Rose is speaking");
        expect(RosePresenceModel.caption(RoseVisualState.from(ACTIVE, Float.NaN, Float.NaN, true, true)).equals("Listening"), "Unknown levels never claim speaking");
        // HiFi frame policy: a settled listening ring holds still; only speaking keeps frames.
        RosePresenceModel still = shown(ACTIVE, .2f, 0f, true);
        for (int i = 0; i < 400; i++) still.step(.016f);
        expect(!still.step(.016f), "Settled listening stops frames");
        RosePresenceModel talking = shown(ACTIVE, 0f, .5f, true);
        for (int i = 0; i < 400; i++) talking.step(.016f);
        expect(talking.step(.016f), "Speaking keeps the surround moving");

        // Tray size (APP-OS-VISUAL-1030): the view is sized so the drawn sphere matches the HiFi tray's 42dp, and the
        // broadest speaking ring still fits inside it. The old fixed 64dp view drew a sphere about 15% small.
        int tray = RosePresenceModel.viewSizeFor(RosePresenceModel.TRAY_SPHERE_DP);
        float sphere = 2f * tray / (2f * RosePresenceModel.VIEW_RADII);
        expect(Math.abs(sphere - 42f) < 0.6f, "The tray view draws a 42dp sphere: " + sphere);
        expect(tray <= 66 + 2 * 5, "and fits the well with its pocket (76dp): " + tray);
        float ringRadius = (RosePresenceModel.RING_BROAD + RosePresenceModel.RING_ENERGY) * sphere / 2f;
        expect(ringRadius < tray / 2f, "The broadest ring stays inside the view");
        expect(2f * 64 / (2f * RosePresenceModel.VIEW_RADII) < 42f * .87f, "The previous 64dp view drew a smaller sphere");

        // Material (APP-OS-VISUAL-1030): rendered tones measured from the HiFi renderer's own output, mixed by the
        // eased microphone value; camera tilt and cut-face colour recomputed here from the shader's numbers.
        RosePresenceModel tone = new RosePresenceModel();
        tone.mic = 1f;
        expect(java.util.Arrays.equals(tone.tones(), RosePresenceModel.ROSE_TONES), "Mic on: the rose tones");
        tone.mic = 0f;
        expect(java.util.Arrays.equals(tone.tones(), RosePresenceModel.SILVER_TONES), "Muted: the silver tones");
        tone.mic = .5f;
        int mid = tone.tones()[3];
        expect(mid != RosePresenceModel.ROSE_TONES[3] && mid != RosePresenceModel.SILVER_TONES[3], "Mid-ease tones are in between");
        expect(RosePresenceModel.TONE_STOPS.length == RosePresenceModel.ROSE_TONES.length
            && RosePresenceModel.TONE_STOPS.length == RosePresenceModel.SILVER_TONES.length, "One tone per stop");
        for (int i = 1; i < RosePresenceModel.TONE_STOPS.length; i++) {
            expect(RosePresenceModel.TONE_STOPS[i] > RosePresenceModel.TONE_STOPS[i - 1], "Stops ascend");
            expect(luma(RosePresenceModel.ROSE_TONES[i]) <= luma(RosePresenceModel.ROSE_TONES[i - 1])
                && luma(RosePresenceModel.SILVER_TONES[i]) <= luma(RosePresenceModel.SILVER_TONES[i - 1]), "Lit from above: darker downwards");
        }
        expect(chroma(RosePresenceModel.ROSE_TONES[3]) < chroma(RosePresenceModel.ROSE) / 2,
            "The rendered rose is far less saturated than the raw #f27a87 base the view used to paint");
        expect(hue(RosePresenceModel.ROSE_TONES[1]) == 'r' && hue(RosePresenceModel.SILVER_TONES[1]) == 'b', "Rose stays warm, silver stays cool");
        double tilt = Math.sin(Math.atan((1.8 - .15) / 7.4));
        expect(Math.abs(RosePresenceModel.TILT - tilt) < 1e-6 && RosePresenceModel.TILT > .21f && RosePresenceModel.TILT < .22f,
            "Rims flatten by the camera's ~12.6 degree elevation: " + RosePresenceModel.TILT);
        expect(RosePresenceModel.ROSE_FACE == shaderFace(.95, .48, .53) && RosePresenceModel.SILVER_FACE == shaderFace(.65, .73, .81),
            "Cut face: base*.16, tone-mapped and gamma-corrected as the shader does");
        expect(luma(RosePresenceModel.ROSE_FACE) < luma(RosePresenceModel.ROSE_TONES[6]), "The cut face is darker than the shell");

        System.out.println("RosePresenceModelTest: " + checks + " checks passed");
    }
}
