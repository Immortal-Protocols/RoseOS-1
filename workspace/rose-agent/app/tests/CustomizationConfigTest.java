package dev.pixel.voice;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/** Nov 7 plan 1O: the shared customization catalog (assets/customization-agent-tools.json) matches the model's
 * operations and value sets, and CustomizationConfig validates, stores and reports the four controls as documented.
 * Also checks RoseFonts' role families and that a chosen Home column count keeps every app, in order, once. */
public final class CustomizationConfigTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static Map<String, Object> args(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        return m;
    }
    static void rejects(CustomizationConfig c, String op, Map<String, Object> p, String message) {
        try { c.apply(op, p); } catch (IllegalArgumentException expected) { checks++; return; }
        throw new AssertionError("accepted: " + message);
    }
    static List<Object> longs(int... values) {
        List<Object> out = new ArrayList<>();
        for (int v : values) out.add((long) v);
        return out;
    }
    @SuppressWarnings("unchecked")
    static Map<String, Object> prop(Map<String, Object> tool, String name) {
        Map<String, Object> schema = (Map<String, Object>) tool.get("inputSchema");
        return (Map<String, Object>) ((Map<String, Object>) schema.get("properties")).get(name);
    }

    @SuppressWarnings("unchecked")
    static void catalog(String path) throws Exception {
        Object root = MiniJson.parse(new String(Files.readAllBytes(Paths.get(path)), StandardCharsets.UTF_8));
        expect(root instanceof List, "catalog is an array");
        List<Object> tools = (List<Object>) root;
        Pattern name = Pattern.compile("^[a-z][a-z0-9_]{0,63}$");
        List<String> names = new ArrayList<>(), operations = new ArrayList<>();
        Map<String, Map<String, Object>> byName = new HashMap<>();
        for (Object o : tools) {
            Map<String, Object> t = (Map<String, Object>) o;
            String n = (String) t.get("name"), op = (String) t.get("operation");
            expect(n != null && name.matcher(n).matches(), "tool name pattern: " + n);
            expect(op != null && name.matcher(op).matches(), "operation pattern: " + op);
            expect(n.equals(op), "tool name equals operation: " + n);
            expect(Boolean.FALSE.equals(t.get("foregroundRequired")), "foregroundRequired false: " + n);
            expect(t.get("description") instanceof String && !((String) t.get("description")).isEmpty(), "description: " + n);
            Map<String, Object> schema = (Map<String, Object>) t.get("inputSchema");
            expect(schema != null && "object".equals(schema.get("type")), "object schema: " + n);
            expect(Boolean.FALSE.equals(schema.get("additionalProperties")), "additionalProperties false: " + n);
            expect(schema.get("properties") instanceof Map && schema.get("required") instanceof List, "properties and required: " + n);
            for (Object r : (List<Object>) schema.get("required"))
                expect(((Map<String, Object>) schema.get("properties")).containsKey(r), "required names a property: " + n + "." + r);
            names.add(n); operations.add(op); byName.put(n, t);
        }
        expect(names.equals(CustomizationConfig.OPERATIONS), "catalog tools are the model's operations, in order: " + names);
        expect(operations.equals(CustomizationConfig.OPERATIONS), "catalog operations in order");
        expect(((Map<String, Object>) ((Map<String, Object>) byName.get(CustomizationConfig.STATE).get("inputSchema")).get("properties")).isEmpty(),
                "state takes no arguments");

        Map<String, Object> columns = prop(byName.get(CustomizationConfig.HOME_COLUMNS), "columns");
        expect("integer".equals(columns.get("type")), "columns is an integer");
        List<Object> wantColumns = longs(CustomizationConfig.AUTO_COLUMNS);
        for (int c = CustomizationConfig.MIN_COLUMNS; c <= CustomizationConfig.MAX_COLUMNS; c++) wantColumns.add((long) c);
        expect(wantColumns.equals(columns.get("enum")), "columns enum is 0 and MIN..MAX: " + columns.get("enum"));
        Map<String, Object> variant = prop(byName.get(CustomizationConfig.WALLPAPER), "variant");
        expect("string".equals(variant.get("type")) && OxbloodVariants.IDS.equals(variant.get("enum")), "variant enum is OxbloodVariants.IDS");
        Map<String, Object> font = prop(byName.get(CustomizationConfig.FONT), "font");
        expect("string".equals(font.get("type")) && RoseFonts.IDS.equals(font.get("enum")), "font enum is RoseFonts.IDS");
        Map<String, Object> hook = byName.get(CustomizationConfig.GLASSES_HOOK);
        expect("boolean".equals(prop(hook, "enabled").get("type")), "enabled is boolean");
        expect("string".equals(prop(hook, "action").get("type")) && CustomizationConfig.HOOK_ACTIONS.equals(prop(hook, "action").get("enum")),
                "hook action enum is HOOK_ACTIONS");
        expect("string".equals(prop(hook, "packageName").get("type")), "packageName is a string");
        expect(((Map<String, Object>) ((Map<String, Object>) hook.get("inputSchema")).get("properties")).size() == 3, "hook has three arguments");
        expect(((List<Object>) ((Map<String, Object>) hook.get("inputSchema")).get("required")).isEmpty(), "hook requires none (at least one is checked by the model)");
    }

    static void model() {
        CustomizationConfig d = CustomizationConfig.DEFAULTS;
        expect(d.homeColumns == 0 && "classic".equals(d.wallpaper) && "default".equals(d.font), "visual defaults are shipped behaviour");
        expect(!d.hookEnabled && "tray".equals(d.hookAction) && "".equals(d.hookPackage) && d.revision == 0, "hook defaults off, tray, revision 0");
        expect(d.apply(CustomizationConfig.STATE, null) == d && d.apply(CustomizationConfig.STATE, args()) == d, "state is unchanged");
        rejects(d, CustomizationConfig.STATE, args("x", 1L), "state with an argument");
        rejects(d, "customization_theme", args(), "unknown operation");
        rejects(d, null, args(), "null operation");

        // Home columns.
        String hc = CustomizationConfig.HOME_COLUMNS;
        CustomizationConfig five = d.apply(hc, args("columns", 5L));
        expect(five.homeColumns == 5 && five.revision == 1, "columns 5 accepted, revision 1");
        expect(d.homeColumns == 0 && d.revision == 0, "defaults are immutable");
        expect(d.apply(hc, args("columns", 5)).homeColumns == 5, "Integer accepted");
        expect(d.apply(hc, args("columns", 5.0)).homeColumns == 5, "Double 5.0 accepted");
        expect(five.apply(hc, args("columns", 5L)) == five, "same columns is idempotent");
        expect(five.apply(hc, args("columns", 0L)).homeColumns == 0 && five.apply(hc, args("columns", 0L)).revision == 2, "back to automatic");
        for (int c : new int[]{3, 4, 6, 7, 8}) expect(d.apply(hc, args("columns", (long) c)).homeColumns == c, "columns " + c);
        rejects(d, hc, args("columns", 5.5), "fractional columns");
        rejects(d, hc, args("columns", 1L), "columns 1");
        rejects(d, hc, args("columns", 2L), "columns 2");
        rejects(d, hc, args("columns", 9L), "columns 9");
        rejects(d, hc, args("columns", -1L), "negative columns");
        rejects(d, hc, args("columns", 1L << 40), "columns beyond int");
        rejects(d, hc, args("columns", Double.POSITIVE_INFINITY), "infinite columns");
        rejects(d, hc, args("columns", Double.NaN), "NaN columns");
        rejects(d, hc, args("columns", "5"), "string columns");
        rejects(d, hc, args("columns", true), "boolean columns");
        rejects(d, hc, args(), "missing columns");
        rejects(d, hc, args("columns", 5L, "rows", 4L), "unknown argument beside columns");

        // Wallpaper.
        String wp = CustomizationConfig.WALLPAPER;
        for (String v : OxbloodVariants.IDS) expect(d.apply(wp, args("variant", v)).wallpaper.equals(v), "variant " + v);
        expect(d.apply(wp, args("variant", "classic")) == d, "classic on classic is idempotent");
        CustomizationConfig garnet = d.apply(wp, args("variant", "garnet"));
        expect(garnet.revision == 1 && garnet.apply(wp, args("variant", "garnet")) == garnet, "garnet once");
        rejects(d, wp, args("variant", "ocean"), "non-Oxblood theme");
        rejects(d, wp, args("variant", "Garnet"), "case-changed variant");
        rejects(d, wp, args("variant", "/sdcard/x.png"), "arbitrary image");
        rejects(d, wp, args("variant", 1L), "numeric variant");
        rejects(d, wp, args(), "missing variant");
        rejects(d, wp, args("variant", "garnet", "image", "x"), "unknown argument beside variant");

        // Font.
        String ft = CustomizationConfig.FONT;
        for (String f : RoseFonts.IDS) expect(d.apply(ft, args("font", f)).font.equals(f), "font " + f);
        expect(d.apply(ft, args("font", "default")) == d, "default on default is idempotent");
        rejects(d, ft, args("font", "comic"), "unbundled font");
        rejects(d, ft, args("font", "fonts/nunito.ttf"), "asset path as font");
        rejects(d, ft, args("font", null), "null font");
        rejects(d, ft, args("font", "serif", "size", 14L), "unknown argument beside font");

        // Glasses hook.
        String gh = CustomizationConfig.GLASSES_HOOK;
        rejects(d, gh, args(), "empty hook request");
        rejects(d, gh, args("enabled", "true"), "string enabled");
        rejects(d, gh, args("enabled", 1L), "numeric enabled");
        rejects(d, gh, args("action", "launch"), "unsupported action");
        rejects(d, gh, args("action", "app"), "app without a package");
        rejects(d, gh, args("action", "app", "packageName", ""), "app with an empty package");
        rejects(d, gh, args("action", "app", "packageName", "camera"), "single-segment package");
        rejects(d, gh, args("action", "app", "packageName", "1dev.pixel"), "package starting with a digit");
        rejects(d, gh, args("action", "app", "packageName", "dev.pixel;rm"), "package with punctuation");
        rejects(d, gh, args("action", "app", "packageName", "dev." + new String(new char[300]).replace('\0', 'a')), "overlong package");
        rejects(d, gh, args("action", "app", "packageName", 5L), "numeric package");
        rejects(d, gh, args("packageName", "dev.pixel.glassescamera"), "package with tray (default action)");
        rejects(d, gh, args("action", "tray", "packageName", "dev.pixel.glassescamera"), "package with explicit tray");
        rejects(d, gh, args("enabled", true, "target", "x"), "unknown hook argument");
        CustomizationConfig on = d.apply(gh, args("enabled", true));
        expect(on.hookEnabled && "tray".equals(on.hookAction) && on.revision == 1, "enable keeps tray");
        expect(on.apply(gh, args("enabled", true)) == on, "enable twice is idempotent");
        CustomizationConfig app = on.apply(gh, args("action", "app", "packageName", " dev.pixel.glassescamera "));
        expect("app".equals(app.hookAction) && "dev.pixel.glassescamera".equals(app.hookPackage) && app.hookEnabled && app.revision == 2,
                "app with a trimmed package");
        expect(app.apply(gh, args("action", "app")) == app, "app keeps its package when only action is sent");
        expect(app.apply(gh, args("packageName", "dev.pixel.voice")).hookPackage.equals("dev.pixel.voice"), "package alone changes the app");
        rejects(app, gh, args("packageName", ""), "clearing the package while action is app");
        CustomizationConfig off = app.apply(gh, args("enabled", false));
        expect(!off.hookEnabled && "app".equals(off.hookAction) && "dev.pixel.glassescamera".equals(off.hookPackage), "disable keeps action and package");
        CustomizationConfig tray = app.apply(gh, args("action", "tray"));
        expect("tray".equals(tray.hookAction) && "".equals(tray.hookPackage) && tray.hookEnabled, "tray clears the package");
        expect(app.apply(gh, args("action", "tray", "packageName", "")).hookPackage.isEmpty(), "tray with an empty package is accepted");
        expect(d.apply(gh, args("enabled", true, "action", "app", "packageName", "dev.pixel.glassescamera")).revision == 1, "one change, one revision");

        // Different controls are independent.
        CustomizationConfig all = d.apply(hc, args("columns", 6L)).apply(wp, args("variant", "dusk")).apply(ft, args("font", "mono"))
                .apply(gh, args("enabled", true, "action", "app", "packageName", "dev.pixel.glassescamera"));
        expect(all.homeColumns == 6 && "dusk".equals(all.wallpaper) && "mono".equals(all.font) && all.hookEnabled && all.revision == 4, "four changes");
        rejects(all, hc, args("columns", 2L), "invalid change on a changed value");
        expect(all.homeColumns == 6 && all.revision == 4, "rejected change leaves the value");

        // Stored form.
        String json = all.toJson();
        expect(json.equals("{\"homeColumns\":6,\"wallpaper\":\"dusk\",\"font\":\"mono\",\"glassesHook\":{\"enabled\":true,\"action\":\"app\","
                + "\"packageName\":\"dev.pixel.glassescamera\"},\"revision\":4}"), "stable stored JSON: " + json);
        CustomizationConfig back = CustomizationConfig.parse(json);
        expect(back.sameValues(all) && back.revision == all.revision && back.toJson().equals(json), "round trip");
        expect(MiniJson.parse(json) instanceof Map, "stored JSON is valid JSON");
        expect(CustomizationConfig.parse(d.toJson()).sameValues(d), "defaults round trip");
        for (String broken : new String[]{null, "", "{", "[]", "\"x\"", "null", "{\"homeColumns\":5}x", "{\"a\":1,\"a\":2}"}) {
            CustomizationConfig p = CustomizationConfig.parse(broken);
            expect(p.sameValues(d) && p.revision == 0, "damaged stored value gives defaults: " + broken);
        }
        CustomizationConfig partial = CustomizationConfig.parse("{\"homeColumns\":9,\"wallpaper\":\"garnet\",\"font\":\"comic\","
                + "\"glassesHook\":{\"enabled\":\"yes\",\"action\":\"app\",\"packageName\":\"dev.pixel.glassescamera\"},\"revision\":-3}");
        expect(partial.homeColumns == 0 && "garnet".equals(partial.wallpaper) && "default".equals(partial.font), "invalid fields fall back alone");
        expect(!partial.hookEnabled && "app".equals(partial.hookAction) && "dev.pixel.glassescamera".equals(partial.hookPackage) && partial.revision == 0,
                "hook fields fall back alone");
        CustomizationConfig badPackage = CustomizationConfig.parse("{\"glassesHook\":{\"enabled\":true,\"action\":\"app\",\"packageName\":\"no way\"}}");
        expect(badPackage.hookEnabled && "tray".equals(badPackage.hookAction) && "".equals(badPackage.hookPackage), "app without a valid package falls back to tray");
        CustomizationConfig trayPkg = CustomizationConfig.parse("{\"glassesHook\":{\"action\":\"tray\",\"packageName\":\"dev.pixel.glassescamera\"}}");
        expect("tray".equals(trayPkg.hookAction) && "".equals(trayPkg.hookPackage), "stored tray never keeps a package");
        expect(CustomizationConfig.parse("{\"homeColumns\":5.0,\"revision\":7}").homeColumns == 0, "stored fractional-form columns fall back");
        expect(CustomizationConfig.parse("{\"homeColumns\":3,\"revision\":7}").revision == 7 && CustomizationConfig.parse("{\"homeColumns\":3}").homeColumns == 3,
                "stored columns and revision");
        expect(CustomizationConfig.parse("{\"glassesHook\":[]}").sameValues(d), "non-object hook falls back");
    }

    static void columns() {
        for (float w : new float[]{200, 290, 300, 336, 364, 400, 520, 552, 600, 784, 1000}) {
            expect(CustomizationConfig.effectiveHomeColumns(0, w) == HomeAppLayout.homeColumns(w), "automatic equals homeColumns at " + w);
            for (int bad : new int[]{1, 2, 9, -1}) expect(CustomizationConfig.effectiveHomeColumns(bad, w) == HomeAppLayout.homeColumns(w), "invalid preference is automatic at " + w);
            for (int p = 3; p <= 8; p++) {
                int e = CustomizationConfig.effectiveHomeColumns(p, w);
                int fit = (int) Math.floor((w + HomeAppLayout.HOME_COL_GAP_DP) / (HomeAppLayout.HOME_CELL_DP + HomeAppLayout.HOME_COL_GAP_DP));
                expect(e >= 3 && e <= p, "effective in [3, preferred] at " + w + " for " + p);
                expect(e == Math.max(3, Math.min(p, fit)), "honoured down to what fits at " + w + " for " + p);
                if (fit >= 3) expect(e * HomeAppLayout.HOME_CELL_DP + (e - 1) * HomeAppLayout.HOME_COL_GAP_DP <= w + 1e-3f, "cells fit at " + w + " for " + p);
            }
        }
        expect(HomeAppLayout.homeColumns(336) == 4 && HomeAppLayout.homeColumns(364) == 4, "phone automatic is four");
        expect(CustomizationConfig.effectiveHomeColumns(5, 336) == 4, "five on 336dp clamps to four");
        expect(CustomizationConfig.effectiveHomeColumns(3, 336) == 3 && CustomizationConfig.effectiveHomeColumns(3, 784) == 3, "three honoured");
        expect(CustomizationConfig.effectiveHomeColumns(8, 784) == 8, "eight on 784dp");
        expect(CustomizationConfig.effectiveHomeColumns(8, 552) == 7, "eight on 552dp is seven");
        expect(CustomizationConfig.effectiveHomeColumns(4, 200) == 3 && CustomizationConfig.effectiveHomeColumns(8, 100) == 3, "never below three");
    }

    /** MainActivity's Home split (HomeAppLayout.homeShare, pages, slice) with each effective column count: the curated
     * apps and the inventory are each shown in order and exactly once across Home and the app pages. */
    static void reflow() {
        List<String> curated = new ArrayList<>(), inventory = new ArrayList<>();
        for (int i = 0; i < 6; i++) curated.add("curated" + i);
        for (int i = 0; i < 37; i++) inventory.add("app" + i);
        for (int curatedCount : new int[]{0, 3, 6}) for (boolean folder : new boolean[]{false, true}) for (int cols = 3; cols <= 8; cols++) for (int rows = 1; rows <= 6; rows++) for (int perPage : new int[]{1, 7, 20, 24}) {
            List<String> cur = curated.subList(0, curatedCount);
            int capacity = cols * rows;
            int[] share = HomeAppLayout.homeShare(cur.size(), folder, inventory.size(), capacity);
            expect(share[0] + share[1] + (folder ? 1 : 0) <= Math.max(1, capacity), "Home share fits its capacity");
            List<String> home = new ArrayList<>(cur.subList(0, share[0]));
            home.addAll(inventory.subList(0, share[1]));
            List<String> appEntries = new ArrayList<>(cur.subList(share[0], cur.size()));
            appEntries.addAll(inventory.subList(share[1], inventory.size()));
            List<String> seen = new ArrayList<>(home);
            int pages = HomeAppLayout.pages(appEntries.size(), perPage);
            for (int p = 0; p < pages; p++) {
                int[] r = HomeAppLayout.slice(appEntries.size(), perPage, p);
                expect(r[1] - r[0] <= perPage, "page holds at most perPage");
                seen.addAll(appEntries.subList(r[0], r[1]));
            }
            List<String> want = new ArrayList<>(cur); want.addAll(inventory);
            List<String> got = new ArrayList<>();
            for (String s : seen) if (s.startsWith("curated")) got.add(s);
            for (String s : seen) if (s.startsWith("app")) got.add(s);
            expect(seen.size() == want.size() && got.equals(want), "every app exactly once in order: cols " + cols + " rows " + rows + " curated " + curatedCount);
            List<String> inv = new ArrayList<>();
            for (String s : seen) if (s.startsWith("app")) inv.add(s);
            expect(inv.equals(inventory), "inventory order preserved");
            expect(HomeAppLayout.usedRows(home.size() + (folder ? 1 : 0), cols) <= rows, "Home uses no more rows than it has");
        }
    }

    static void fonts() {
        expect(RoseFonts.IDS.get(0).equals(RoseFonts.DEFAULT) && RoseFonts.IDS.size() == 6, "font ids, default first");
        expect(RoseFonts.NUNITO_ASSET.equals(RoseFonts.conversationFamily(RoseFonts.DEFAULT)), "default conversation is the Nunito asset");
        expect("sans-serif".equals(RoseFonts.labelFamily(RoseFonts.DEFAULT)), "default labels are sans-serif");
        expect(RoseFonts.NUNITO_ASSET.equals(RoseFonts.conversationFamily("nunito")) && RoseFonts.NUNITO_ASSET.equals(RoseFonts.labelFamily("nunito")), "nunito throughout");
        for (String id : RoseFonts.IDS) {
            String c = RoseFonts.conversationFamily(id), l = RoseFonts.labelFamily(id);
            expect(c != null && !c.isEmpty() && l != null && !l.isEmpty(), "both roles resolve: " + id);
            expect(!RoseFonts.label(id).isEmpty(), "label: " + id);
            if (!id.equals(RoseFonts.DEFAULT)) expect(c.equals(l), "a chosen font applies throughout: " + id);
            expect(c.equals(RoseFonts.NUNITO_ASSET) || Arrays.asList("sans-serif", "serif", "sans-serif-condensed", "monospace").contains(c), "platform alias: " + c);
        }
        try { RoseFonts.conversationFamily("comic"); throw new AssertionError("unknown font resolved"); } catch (IllegalArgumentException expected) { checks++; }
        try { RoseFonts.labelFamily(null); throw new AssertionError("null font resolved"); } catch (IllegalArgumentException expected) { checks++; }
    }

    public static void main(String[] a) throws Exception {
        if (a.length < 1) throw new IllegalArgumentException("usage: CustomizationConfigTest <customization-agent-tools.json>");
        catalog(a[0]);
        model();
        columns();
        reflow();
        fonts();
        System.out.println("CustomizationConfigTest: " + checks + " checks passed");
    }
}
