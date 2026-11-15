package dev.pixel.builder;

/**
 * Project 1, Level 3: a version's share state (proposed versions[].share, schemaVersion 1). The phone shares only a
 * link the builder recorded for exactly that version; anything missing, malformed or not plain https is never a link.
 * The links come from hosting through the builder; the phone never invents or derives one. Host JVM.
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

    public static void main(String[] args) throws Exception {
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
