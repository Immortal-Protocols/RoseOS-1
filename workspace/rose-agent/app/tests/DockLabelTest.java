package dev.pixel.voice;

import java.awt.Font;
import java.awt.font.FontRenderContext;
import java.io.File;

/** Dock label fitting (OS-HOME-2804-FIX-001 M008) measured with the app's bundled Nunito font through AWT.
 * AWT draws the variable font's default instance, so widths get +10% for the dock's weight 800; this
 * approximates Android's text measurement and is not native rendering. Geometry follows RoseHomeView: five
 * tiles across a tray with 8dp side padding, 4dp tile padding on each side, labels at 12sp. */
public final class DockLabelTest {
    static int checks;
    static Font font;
    static final FontRenderContext CONTEXT = new FontRenderContext(null, true, true);

    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static float width(String text, float sizePx) {
        float widest = 0;
        for (String line : text.split("\n"))
            widest = Math.max(widest, (float) font.deriveFont(sizePx).getStringBounds(line, CONTEXT).getWidth() * 1.10f);
        return widest;
    }
    /** Label width inside one dock tile for a screen widthPx wide at this density. */
    static float available(int widthPx, float density) {
        return (widthPx - 2 * 8 * density) / 5 - 2 * 4 * density;
    }
    static DockLabel fit(String label, int widthPx, float density, float fontScale) {
        float base = 12 * density * fontScale;
        return DockLabel.fit(label, base, base * 10 / 12, available(widthPx, density), DockLabelTest::width);
    }
    /** The 302/304 behaviour: one line at the dock size, cut with an ellipsis when it does not fit. */
    static boolean oldCuts(String label, int widthPx, float density, float fontScale) {
        return width(label, 12 * density * fontScale) > available(widthPx, density);
    }

    public static void main(String[] args) throws Exception {
        font = Font.createFont(Font.TRUETYPE_FONT, new File(args[0]));
        // The verifier's 720x1280 screenshot (xhdpi): the old dock cut both baseline labels.
        expect(oldCuts("Messaging", 720, 2f, 1f) && oldCuts("Camera (system)", 720, 2f, 1f),
                "Regression input: the old one-line dock cuts Messaging and Camera (system) at 720 px");

        DockLabel messaging = fit("Messaging", 720, 2f, 1f);
        expect(messaging.text.equals("Messaging") && messaging.lines == 1 && !messaging.ellipsize,
                "Messaging is whole on one line");
        expect(messaging.sizePx < 24 && messaging.sizePx >= 20, "...by shrinking a little, never below 10/12 of the dock size");
        DockLabel camera = fit("Camera (system)", 720, 2f, 1f);
        expect(camera.text.equals("Camera\n(system)") && camera.lines == 2 && camera.sizePx == 24 && !camera.ellipsize,
                "Camera (system) wraps once at its space, at full size");
        for (String label : new String[]{"Phone", "Browser", "Chrome", "All apps"}) {
            DockLabel same = fit(label, 720, 2f, 1f);
            expect(same.text.equals(label) && same.lines == 1 && same.sizePx == 24 && !same.ellipsize, label + " is unchanged");
        }

        // Narrower and denser screens at the default font size: realistic dock labels fit whole without the fallback.
        int[][] screens = {{720, 2}, {640, 2}, {1080, 3}, {1440, 4}};
        for (int[] screen : screens)
            for (String label : new String[]{"Messaging", "Camera (system)", "Messages", "Browser", "All apps"}) {
                DockLabel f = fit(label, screen[0], screen[1], 1f);
                expect(!f.ellipsize && f.text.replace('\n', ' ').equals(label), label + " whole at " + screen[0] + "px x" + screen[1]);
            }
        // Larger owner font sizes: where the fallback applies, the whole name still fits its two lines (an ellipsis
        // only shows past the second line), so nothing realistic is cut up to 1.3x.
        for (int[] screen : screens) for (float scale : new float[]{1.15f, 1.3f})
            for (String label : new String[]{"Messaging", "Camera (system)", "Messages", "Browser", "All apps"}) {
                DockLabel f = fit(label, screen[0], screen[1], scale);
                expect(!f.ellipsize || width(f.text, f.sizePx) <= 2 * available(screen[0], screen[1]),
                        label + " not cut at " + screen[0] + "px x" + screen[1] + " font " + scale);
            }
        // Every result fits its slot, or is the bounded two-line overflow; sizes never drop below 10/12.
        String[] labels = {"Messaging", "Camera (system)", "Open Camera", "Calendar (test)", "Weather (alternate)",
                "Supercalifragilisticexpialidocious", "A very long application name indeed"};
        for (int[] screen : screens) for (float scale : new float[]{1f, 1.15f, 1.3f, 2f}) for (String label : labels) {
            DockLabel f = fit(label, screen[0], screen[1], scale);
            float base = 12 * screen[1] * scale;
            expect(f.sizePx >= base * 10 / 12 - 0.001f && f.sizePx <= base, label + " size within bounds");
            expect(f.lines <= 2, label + " takes at most two lines");
            expect(f.ellipsize || width(f.text, f.sizePx) <= available(screen[0], screen[1]), label + " fits or is the bounded overflow");
            expect(f.text.replace('\n', ' ').equals(label), label + " keeps its full text (TextView ellipsizes only on screen)");
        }
        DockLabel huge = fit("Camera (system)", 720, 2f, 2f);
        expect(huge.lines == 2 && huge.ellipsize, "At the largest font size a long name is the bounded two-line overflow");
        System.out.println("DockLabelTest: " + checks + " checks passed");
    }
}
