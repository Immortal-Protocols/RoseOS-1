package dev.pixel.builder;

/**
 * Project 1, Level 3: a version's share state (proposed versions[].share, schemaVersion 1). The phone shares only a
 * link the builder recorded for exactly that version; anything missing, malformed or not plain https is never a link.
 * The links come from hosting through the builder; the phone never invents or derives one. With args[0], also checked
 * against App Core's shared fixture (share-summary-v1.json). Host JVM.
 */
public final class ShareStateTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    static final String V = "https://share.example.invalid/p1/v2", L = "https://share.example.invalid/p1/latest";

    static ShareState of(String json) throws Exception { return ShareState.parse(Json.parse(json)); }
    static String pub(String extra) { return "{\"schemaVersion\":1,\"status\":\"published\",\"versionUrl\":\"" + V + "\",\"latestUrl\":\"" + L + "\"" + extra + "}"; }

    static void notALink(ShareState s, String what) {
        expect(!s.published() && s.versionUrl == null && s.latestUrl == null, what + ": no link");
    }

    /** App Core's shared fixture (share-summary-v1.json, args[0]): every served share reads exactly as served, links
     * verbatim; every clientUnreadable value reads as unreadable and never as a link. */
    @SuppressWarnings("unchecked")
    static void fixture(String path) throws Exception {
        java.util.Map<String, Object> fx = (java.util.Map<String, Object>) Json.parse(new String(
            java.nio.file.Files.readAllBytes(java.nio.file.Paths.get(path)), java.nio.charset.StandardCharsets.UTF_8));
        expect(String.valueOf(fx.get("contract")).contains("versions[].share"), "the fixture is the share contract");
        int served = 0, unreadable = 0;
        for (Object o : (java.util.List<Object>) fx.get("cases")) {
            java.util.Map<String, Object> c = (java.util.Map<String, Object>) o;
            String name = (String) c.get("case");
            java.util.Map<String, Object> share = (java.util.Map<String, Object>) c.get("share");
            ShareState s = ShareState.parse(share);
            expect(!s.unreadable && s.status.name().toLowerCase(java.util.Locale.ROOT).equals(share.get("status")), name + ": reads as " + share.get("status"));
            if (s.published()) {
                expect(share.get("versionUrl").equals(s.versionUrl) && share.get("latestUrl").equals(s.latestUrl), name + ": links verbatim");
                expect(ShareState.line(s) == null, name + ": published shows the share buttons, no status line");
            } else {
                notALink(s, name);
                String line = ShareState.line(s);
                expect(line != null, name + ": says why it can't be shared");
                if (share.get("reason") != null) expect(line.contains((String) share.get("reason")), name + ": shows the builder's reason");
            }
            served++;
        }
        java.util.Map<String, Object> bad = (java.util.Map<String, Object>) fx.get("clientUnreadable");
        for (Object o : (java.util.List<Object>) bad.get("cases")) {
            java.util.Map<String, Object> c = (java.util.Map<String, Object>) o;
            ShareState s = ShareState.parse(c.get("share"));
            notALink(s, (String) c.get("case"));
            expect(s.unreadable && ShareState.line(s).contains("couldn't be read"), c.get("case") + ": unreadable, never a link");
            unreadable++;
        }
        expect(served > 0 && unreadable > 0, "the fixture has served and unreadable cases");
    }

    public static void main(String[] args) throws Exception {
        if (args.length > 0) fixture(args[0]);
        // Absent (an older server, or no hosting yet): not shared yet, never a link.
        ShareState absent = ShareState.parse(null);
        notALink(absent, "absent");
        expect(absent.status == ShareState.Status.UNPUBLISHED && !absent.unreadable, "absent reads as unpublished");
        expect(ShareState.line(absent).contains("Not shared yet"), "absent: says not shared yet");

        // Published: exactly the recorded links.
        ShareState p = of(pub(",\"future\":1"));
        expect(p.published() && V.equals(p.versionUrl) && L.equals(p.latestUrl), "published: both recorded links, unknown fields ignored");
        expect(ShareState.line(p) == null, "published: no status line, the share buttons show instead");

        // Unpublished and unavailable.
        ShareState u = of("{\"schemaVersion\":1,\"status\":\"unpublished\"}");
        notALink(u, "unpublished");
        expect(u.status == ShareState.Status.UNPUBLISHED && ShareState.line(u).contains("Not shared yet"), "unpublished");
        ShareState n = of("{\"schemaVersion\":1,\"status\":\"unavailable\",\"reason\":\"your Chariot is asleep\\n\"}");
        notALink(n, "unavailable");
        expect(n.status == ShareState.Status.UNAVAILABLE && !n.unreadable && ShareState.line(n).contains("your Chariot is asleep"), "unavailable: its reason, plain");

        // Off-spec values: unavailable ("couldn't be read"), never a link.
        String[] bad = {
            "\"published\"", "[]", "{}",
            "{\"schemaVersion\":2,\"status\":\"published\",\"versionUrl\":\"" + V + "\",\"latestUrl\":\"" + L + "\"}",
            "{\"schemaVersion\":1,\"status\":\"live\"}",
            "{\"schemaVersion\":1,\"status\":\"published\",\"versionUrl\":\"" + V + "\"}",
            "{\"schemaVersion\":1,\"status\":\"published\",\"latestUrl\":\"" + L + "\"}",
            "{\"schemaVersion\":1,\"status\":\"unavailable\"}",
            pub("").replace(V, "http://share.example.invalid/p1/v2"),
            pub("").replace(L, "javascript:alert(1)"),
            pub("").replace(V, "https://user:pw@share.example.invalid/p1/v2"),
            pub("").replace(V, "https:///nohost"),
            pub("").replace(V, "https://share.example.invalid/p1/v2 x"),
            pub("").replace(V, "https://share.example.invalid/p\\u00e91"),
            pub("").replace(V, "https://share.example.invalid/p1#frag"),
            "{\"schemaVersion\":1,\"status\":\"published\",\"versionUrl\":7,\"latestUrl\":\"" + L + "\"}",
        };
        for (String b : bad) {
            ShareState s = of(b);
            notALink(s, b);
            expect(s.status == ShareState.Status.UNAVAILABLE && s.unreadable && ShareState.line(s).contains("couldn't be read"), "off-spec reads as unreadable: " + b);
        }
        StringBuilder longUrl = new StringBuilder("https://share.example.invalid/");
        for (int i = 0; i < 2100; i++) longUrl.append('a');
        notALink(of(pub("").replace(V, longUrl.toString())), "an over-long link");

        // Share text names the exact version for its immutable link, and not for the latest link.
        expect(ShareMessage.text("Tip Calc", "1.2", p.versionUrl, false).startsWith("Tip Calc 1.2 for Android: " + V), "version link text");
        expect(ShareMessage.text("Tip Calc", "1.2", p.latestUrl, true).startsWith("Tip Calc for Android: " + L), "latest link text");

        System.out.println("ShareStateTest: " + checks + " checks passed");
    }
}
