package dev.pixel.voice;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Nov 7 plan 1O: every curated wallpaper variant is still Oxblood. The palette is read from OxbloodField's own home()
 * source (the shipped Oxblood Home field), so a palette change there is checked here too. */
public final class OxbloodVariantsTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static String hex(int c) { return String.format("0x%08x", c); }

    /** Opaque RGB of every colour literal and argb(alpha, rgb) in home(): the alpha is irrelevant to the family. */
    static List<Integer> palette(String source) {
        int from = source.indexOf("private void home("), to = source.indexOf("// ---- oxblood-rose.svg");
        expect(from >= 0 && to > from, "home() located in OxbloodField");
        String home = source.substring(from, to);
        Set<Integer> out = new LinkedHashSet<>();
        Matcher argb = Pattern.compile("argb\\(\\s*[0-9.]+f?\\s*,\\s*0x([0-9a-fA-F]{6})\\s*\\)").matcher(home);
        while (argb.find()) out.add(0xff000000 | Integer.parseInt(argb.group(1), 16));
        Matcher lit = Pattern.compile("0x([0-9a-fA-F]{8}|[0-9a-fA-F]{6})\\b").matcher(home);
        while (lit.find()) out.add(0xff000000 | (int) (Long.parseLong(lit.group(1), 16) & 0xffffff));
        return new ArrayList<>(out);
    }

    public static void main(String[] a) throws Exception {
        if (a.length < 1) throw new IllegalArgumentException("usage: OxbloodVariantsTest <OxbloodField.java>");
        List<Integer> palette = palette(new String(Files.readAllBytes(Paths.get(a[0])), StandardCharsets.UTF_8));
        expect(palette.size() >= 15, "palette read: " + palette.size());
        expect(palette.contains(0xff170104) && palette.contains(0xff5e0a1a) && palette.contains(0xffd65a69), "known palette colours read");
        for (int c : palette) expect(OxbloodVariants.oxbloodFamily(c), "classic palette is Oxblood: " + hex(c));

        expect(OxbloodVariants.IDS.size() == 5 && OxbloodVariants.IDS.get(0).equals(OxbloodVariants.CLASSIC), "five variants, classic first");
        expect(new LinkedHashSet<>(OxbloodVariants.IDS).size() == 5, "variant ids unique");
        expect(OxbloodVariants.matrix(OxbloodVariants.CLASSIC) == null, "classic has no transform");
        for (int c : palette) expect(OxbloodVariants.apply(null, c) == c, "null matrix is identity");

        float[] identity = OxbloodVariants.multiply(OxbloodVariants.saturate(1), OxbloodVariants.hueRotate(0));
        for (int row = 0; row < 4; row++) for (int col = 0; col < 5; col++)
            expect(Math.abs(identity[row * 5 + col] - (row == col ? 1 : 0)) < 1e-4, "parameters {0,1,1} are the identity at " + row + "," + col);
        float[] classic = OxbloodVariants.parameters(OxbloodVariants.CLASSIC);
        expect(classic[0] == 0 && classic[1] == 1 && classic[2] == 1, "classic parameters are the identity");

        for (String id : OxbloodVariants.IDS) {
            float[] p = OxbloodVariants.parameters(id);
            expect(p.length == 3 && Math.abs(p[0]) <= OxbloodVariants.MAX_HUE_TURN_DEG, "hue turn bounded: " + id);
            expect(p[1] > 0 && p[2] > 0, "positive factors: " + id);
            expect(!OxbloodVariants.label(id).isEmpty(), "label: " + id);
            if (id.equals(OxbloodVariants.CLASSIC)) continue;
            float[] m = OxbloodVariants.matrix(id);
            expect(m != null && m.length == 20, "matrix shape: " + id);
            expect(m[15] == 0 && m[16] == 0 && m[17] == 0 && m[18] == 1 && m[19] == 0, "alpha row preserved: " + id);
            for (int c : palette) {
                int t = OxbloodVariants.apply(m, c);
                expect((t >>> 24) == 0xff, "opaque stays opaque: " + id);
                expect(OxbloodVariants.oxbloodFamily(t), id + " keeps " + hex(c) + " Oxblood, got " + hex(t));
                int translucent = (0x40 << 24) | (c & 0xffffff);
                expect((OxbloodVariants.apply(m, translucent) >>> 24) == 0x40, "alpha kept: " + id);
            }
            expect(OxbloodVariants.apply(m, 0xff170104) != 0xff170104 || OxbloodVariants.apply(m, 0xff5e0a1a) != 0xff5e0a1a,
                    "variant differs from classic: " + id);
        }
        // Distinct from one another too, on the mid red.
        Set<Integer> mids = new LinkedHashSet<>();
        for (String id : OxbloodVariants.IDS) mids.add(OxbloodVariants.apply(OxbloodVariants.matrix(id), 0xff5e0a1a));
        expect(mids.size() == 5, "every variant looks different on the mid red");

        expect(OxbloodVariants.oxbloodFamily(0xff8a1426) && !OxbloodVariants.oxbloodFamily(0xff1e6fd0) && !OxbloodVariants.oxbloodFamily(0xff2a9d3a)
                && !OxbloodVariants.oxbloodFamily(0xffe0a020), "family check rejects blue, green and amber");
        for (String bad : new String[]{"ocean", "", null}) {
            try { OxbloodVariants.matrix(bad); throw new AssertionError("unsupported variant accepted: " + bad); } catch (IllegalArgumentException expected) { checks++; }
        }
        System.out.println("OxbloodVariantsTest: " + checks + " checks passed (" + palette.size() + " palette colours)");
    }
}
