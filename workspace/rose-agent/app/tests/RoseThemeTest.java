package dev.pixel.voice;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.List;

/** Plan 1E: the generated RoseTheme against the approved prototype's own colour map (golden file) and its rules. */
public final class RoseThemeTest {
    private static int checks, failures;

    private static void check(boolean ok, String what) {
        checks++;
        if (!ok) {
            failures++;
            System.out.println("FAIL " + what);
        }
    }

    public static void main(String[] args) throws Exception {
        List<String> golden = Files.readAllLines(Paths.get(args[0]), StandardCharsets.UTF_8);
        int compared = 0;
        for (String line : golden) {
            if (line.startsWith("#") || line.isEmpty()) continue;
            String[] f = line.split(" ");
            int in = (int) Long.parseLong(f[0], 16), map = (int) Long.parseLong(f[1], 16), warm = (int) Long.parseLong(f[2], 16);
            compared++;
            check(RoseTheme.map(RoseTheme.GLACIER, in) == map, String.format("glacier map %08X = %08X, got %08X", in, map, RoseTheme.map(RoseTheme.GLACIER, in)));
            check(RoseTheme.warm(RoseTheme.GLACIER, in) == warm, String.format("glacier warm %08X = %08X, got %08X", in, warm, RoseTheme.warm(RoseTheme.GLACIER, in)));
            check(RoseTheme.map(RoseTheme.OXBLOOD, in) == in && RoseTheme.warm(RoseTheme.OXBLOOD, in) == in, String.format("oxblood is identity for %08X", in));
        }
        check(compared > 4000, "golden corpus covers the native literals and the lattice (" + compared + ")");
        // Alpha is kept, so translucent plates stay translucent.
        int plate = 0x47140407;
        check((RoseTheme.map(RoseTheme.GLACIER, plate) >>> 24) == 0x47, "alpha kept by map");
        check((RoseTheme.warm(RoseTheme.GLACIER, 0xbfff5a6e) >>> 24) == 0xbf, "alpha kept by warm");
        // Warm meaning: Oxblood's mic-muted ink becomes Glacier's #FFABAC; never blue.
        check(RoseTheme.warm(RoseTheme.GLACIER, 0xffff8c9a) == 0xffffabac, "muted ink is Glacier destructive");
        check(RoseTheme.map(RoseTheme.GLACIER, 0xff8a1428) != 0xff8a1428, "ruby is remapped in Glacier");
        // Ids, unknown ids and spoken names.
        check(RoseTheme.IDS.size() == 3 && RoseTheme.IDS.get(0).equals("oxblood") && RoseTheme.IDS.get(1).equals("glacier")
                && RoseTheme.IDS.get(2).equals("winds"), "ids");
        // Winds (WINDS-SELECTED-1111) is light: a reviewed table, its own wallpaper, warm meanings in its error red.
        check(RoseTheme.light("winds") && !RoseTheme.light("glacier") && !RoseTheme.light("oxblood"), "only Winds is light");
        check("winds".equals(RoseTheme.fromSpoken("Zephyr")) && "winds".equals(RoseTheme.fromSpoken("winds")), "Winds spoken names");
        check(RoseTheme.wallpapers("winds").size() == 1 && "winds".equals(RoseTheme.wallpaperTheme("winds"))
                && "rose_winds_field".equals(RoseTheme.wallpaperDrawable("winds")), "Winds has its own wallpaper");
        check(RoseTheme.warm("winds", 0xffff8c9a) == 0xffa52c3b && (RoseTheme.warm("winds", 0xbfff5a6e) >>> 24) == 0xbf,
                "Winds muted ink and ring are its error red, alpha kept");
        check(RoseTheme.warm("winds", 0xffe6a23a) == 0xff775100 && RoseTheme.warm("winds", 0xffd9534a) == 0xffa52c3b,
                "Winds attention amber is its warning colour, not its destructive red");
        check(RoseTheme.map("winds", 0xfff1cfc8) == 0xff577a70 && RoseTheme.map("winds", 0xffc44a5e) == 0xff245f54,
                "Winds running edge is a visible teal on light cards");
        check(RoseTheme.map("winds", 0x12345678) == 0x12345678, "a colour outside the Winds table is never guessed");
        // Every reviewed table entry is what map() returns (args[1]: table-themes-golden.txt), signed sort included.
        if (args.length > 1) {
            int entries = 0;
            for (String line : Files.readAllLines(Paths.get(args[1]), StandardCharsets.UTF_8)) {
                if (line.startsWith("#") || line.isEmpty()) continue;
                String[] f = line.split(" ");
                int from = (int) Long.parseLong(f[1], 16), to = (int) Long.parseLong(f[2], 16);
                entries++;
                check(RoseTheme.map(f[0], from) == to, String.format("%s table %08X -> %08X, got %08X", f[0], from, to, RoseTheme.map(f[0], from)));
            }
            check(entries > 300, "the Winds table is checked entry by entry (" + entries + ")");
        }
        check(RoseTheme.Keyboard.color("winds", RoseTheme.Keyboard.TEXT) == 0xFF102D2D
                && RoseTheme.Shade.color("winds", RoseTheme.Shade.ERROR) == 0xFFA52C3B, "Winds surface tokens");
        // The text table is today's Rose ink (OxbloodRose.INK, INK_2) in Oxblood and Glacier.
        for (String t : new String[] {"oxblood", "glacier"}) {
            check(RoseTheme.Text.color(t, RoseTheme.Text.PRIMARY) == 0xffffffff
                    && RoseTheme.Text.color(t, RoseTheme.Text.SECONDARY) == 0xc7ffffff, t + " text table is today's ink");
        }
        check("rose_glacier_field".equals(RoseTheme.wallpaperDrawable("glacier")) && RoseTheme.wallpaperDrawable("oxblood") == null,
                "raster wallpapers are named drawables");
        check(RoseTheme.DEFAULT.equals(RoseTheme.OXBLOOD), "default is Oxblood");
        check(RoseTheme.normalize(null).equals("oxblood") && RoseTheme.normalize("aurora").equals("oxblood"), "unknown ids fall back to the default");
        check(RoseTheme.normalize("glacier").equals("glacier") && RoseTheme.known("glacier") && !RoseTheme.known("Glacier"), "known ids are exact");
        check("glacier".equals(RoseTheme.fromSpoken(" Glacier ")) && "oxblood".equals(RoseTheme.fromSpoken("Ox blood")), "spoken names");
        check(RoseTheme.fromSpoken("ocean") == null, "an unknown spoken name is not a theme");
        check(RoseTheme.label("glacier").equals("Glacier") && RoseTheme.label("oxblood").equals("Oxblood"), "labels");
        // Wallpapers belong to their theme.
        check(RoseTheme.wallpapers("oxblood").equals(OxbloodVariants.IDS), "Oxblood's wallpapers are its curated variants");
        check(RoseTheme.defaultWallpaper("oxblood").equals(OxbloodVariants.CLASSIC), "Oxblood default wallpaper is classic");
        check(RoseTheme.wallpapers("glacier").size() == 1 && RoseTheme.defaultWallpaper("glacier").equals("glacier"), "Glacier has its own wallpaper");
        check("oxblood".equals(RoseTheme.wallpaperTheme("velvet")) && "glacier".equals(RoseTheme.wallpaperTheme("glacier")) && RoseTheme.wallpaperTheme("teal") == null, "wallpaper owner");
        check(RoseTheme.rasterWallpaper("glacier") && !RoseTheme.rasterWallpaper("oxblood"), "wallpaper kinds");
        // Surface colours: Oxblood is exactly today's drawing; Glacier exactly the approved tokens.
        check(RoseTheme.Keyboard.color("oxblood", RoseTheme.Keyboard.BOARD_TOP) == 0xFF1D1114, "Oxblood keyboard board");
        check(RoseTheme.Keyboard.color("glacier", RoseTheme.Keyboard.ACTION_INK) == 0xFF08131D, "Glacier action ink is text-on-accent");
        check(RoseTheme.Shade.color("glacier", RoseTheme.Shade.ERROR) == 0xFFFFABAC, "Glacier shade error is warm");
        check(RoseTheme.Shade.color("nope", RoseTheme.Shade.PANEL) == 0xF2141012, "unknown theme draws Oxblood");
        System.out.println("RoseThemeTest: " + (checks - failures) + "/" + checks + " checks passed");
        if (failures > 0) System.exit(1);
    }
}
