package dev.pixel.voice;

/** APP-VISUAL-153-P1 (#159): the conversation screen's layout and tray glyph decisions, exercised through the
 * production ConversationPresentation used by RoseConversationActivity. Real ConversationState; no Android. */
public final class ConversationPresentationTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static int available(int heightDp) { return heightDp - ConversationPresentation.reservedTop() - ConversationPresentation.reservedBottom(); }

    public static void main(String[] args) {
        // Reference dimensions (HiFi 384×832 screen): the HiFi hierarchy unchanged.
        ConversationPresentation empty = ConversationPresentation.of(832, false, 1f);
        expect(empty.stage == 220 && !empty.compact, "Empty: Rose large (220) and centred");
        expect(empty.titleSp == 44 && empty.bodySp == 21, "HiFi result type 44/21");
        ConversationPresentation result = ConversationPresentation.of(832, true, 1f);
        expect(result.stage == 88 && result.compact, "Result: Rose compact (88) beside its caption");
        expect(ConversationPresentation.HEAD_SP == 30 && ConversationPresentation.CAPTION_SP == 15, "Header 30, caption 15");

        // The tray is never overlapped: content always ends above tray, failure line and gap.
        expect(ConversationPresentation.reservedBottom() >= ConversationPresentation.TRAY_BOTTOM + ConversationPresentation.TRAY_HEIGHT + ConversationPresentation.FAILURE_LINE,
                "Content stops above the tray and failure line");

        // Constrained height and larger text: Rose and caption fit or shrink to a floor; type steps down.
        for (int h : new int[] {832, 720, 640, 560, 480, 420}) {
            for (float scale : new float[] {1f, 1.15f, 1.3f, 1.5f, 2f}) {
                ConversationPresentation e = ConversationPresentation.of(h, false, scale);
                expect(e.stage >= ConversationPresentation.MIN_STAGE && e.stage <= ConversationPresentation.STAGE, "Stage within bounds at " + h + "/" + scale);
                int caption = Math.round(24 * Math.max(1f, scale));
                if (available(h) - caption - 32 >= ConversationPresentation.MIN_STAGE)
                    expect(e.stage + caption + 32 <= available(h), "Rose and caption fit between header and tray at " + h + "/" + scale);
                ConversationPresentation r = ConversationPresentation.of(h, true, scale);
                expect(r.compact && r.stage == 88, "Result stays compact at " + h + "/" + scale);
                boolean tight = h < 640 || scale > 1.3f;
                expect(tight ? (e.titleSp == 34 && e.bodySp == 19) : (e.titleSp == 44 && e.bodySp == 21), "Type steps down only when tight at " + h + "/" + scale);
            }
        }
        expect(ConversationPresentation.of(480, false, 1f).stage < 220, "A short screen shrinks the empty Rose instead of pushing into the tray");
        expect(ConversationPresentation.of(0, false, 1f).stage == ConversationPresentation.MIN_STAGE, "Degenerate height uses the floor");

        // Tray glyphs: distinct per meaning, from the session state only.
        ConversationState idle = ConversationState.of(true, false, false, false, false, "", "", true);
        ConversationState connecting = ConversationState.of(true, false, false, false, true, "", "", true);
        ConversationState active = ConversationState.of(true, true, true, false, true, "", "", true);
        ConversationState muted = ConversationState.of(true, true, true, true, true, "", "", true);
        ConversationState none = ConversationState.of(false, false, false, false, false, null, null, null);
        expect(ConversationPresentation.primary(idle) == ConversationPresentation.Glyph.MIC, "Idle: microphone (Speak)");
        expect(ConversationPresentation.primary(none) == ConversationPresentation.Glyph.MIC, "No service: microphone (Speak)");
        expect(ConversationPresentation.primary(connecting) == ConversationPresentation.Glyph.CANCEL, "Connecting: cancel");
        expect(ConversationPresentation.primary(active) == ConversationPresentation.Glyph.MIC, "In call, mic on: microphone (Mute)");
        expect(ConversationPresentation.primary(muted) == ConversationPresentation.Glyph.MIC_OFF, "In call, muted: crossed microphone (Unmute)");
        for (ConversationState s : new ConversationState[] {idle, connecting, active, muted, none}) {
            ConversationPresentation.Glyph g = ConversationPresentation.primary(s);
            expect(g != ConversationPresentation.Glyph.END && g != ConversationPresentation.Glyph.APPS, "The microphone well never looks like End or Rose's apps");
        }

        System.out.println("ConversationPresentationTest: " + checks + " checks passed");
    }
}
