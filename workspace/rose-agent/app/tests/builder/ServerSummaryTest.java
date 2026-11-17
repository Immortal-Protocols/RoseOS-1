package dev.pixel.builder;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Project 1, slice 9: a client-server version's server summary (docs/APP-BUILDER.md §3.3, versions[].server), parsed
 * and worded by ServerSummary. Checked against App Core's shared fixture file (args[0]): every served case reads as the
 * summary the server sent (absent for a plain version), every clientUnreadable value reads as unavailable, and every
 * clientReadable value reads as ok. No line says the server runs, treats unavailable as an error, or blocks anything.
 * Host JVM.
 */
public final class ServerSummaryTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    /** Words no line may use: the server isn't hosted, unavailable isn't an error, and nothing here is a gate. */
    static final String[] NEVER = {"compatible", "works", "running", "online", "hosted", "reachable", "error", "broken",
        "failed", "must", "required", "blocked", "can't install", "newer"};

    static void honest(List<String> lines, String what) {
        String all = String.join("\n", lines).toLowerCase(Locale.ROOT);
        for (String w : NEVER) expect(!all.contains(w), what + ": never says \"" + w + "\"");
    }

    @SuppressWarnings("unchecked")
    static ServerSummary read(Map<String, Object> c) {
        return ServerSummary.parse(c.get("server"), (Long) c.get("versionCode"));
    }

    @SuppressWarnings("unchecked")
    public static void main(String[] args) throws Exception {
        Map<String, Object> fx = (Map<String, Object>) Json.parse(new String(Files.readAllBytes(Paths.get(args[0])), StandardCharsets.UTF_8));
        expect(String.valueOf(fx.get("contract")).contains("versions[].server"), "the fixture is the server summary contract");

        // Served cases: each version reads as the summary the library sent for it; null is a plain version (absent).
        int served = 0;
        for (Object o : (List<Object>) fx.get("cases")) {
            Map<String, Object> c = (Map<String, Object>) o;
            Map<String, Object> byVersion = (Map<String, Object>) c.get("server");
            Map<Long, String> names = new HashMap<>();
            for (Object vo : (List<Object>) c.get("versions")) {
                long vc = (Long) ((Map<String, Object>) vo).get("versionCode");
                names.put(vc, String.valueOf(vc));
            }
            for (Object vo : (List<Object>) c.get("versions")) {
                long vc = (Long) ((Map<String, Object>) vo).get("versionCode");
                String what = c.get("case") + " v" + vc;
                expect(byVersion.containsKey(String.valueOf(vc)), what + ": the fixture lists its served value");
                Object sent = byVersion.get(String.valueOf(vc));
                ServerSummary s = ServerSummary.parse(sent, vc);
                expect(!s.unreadable, what + ": every value the server sends is readable");
                if (sent == null) {
                    expect(s.status == ServerSummary.Status.ABSENT, what + ": no field is absent");
                    expect(ServerSummary.lines(s, vc, names, null).isEmpty(), what + ": absent shows nothing");
                    continue;
                }
                Map<String, Object> m = (Map<String, Object>) sent;
                if ("ok".equals(m.get("status"))) {
                    expect(s.status == ServerSummary.Status.OK && s.api == (Long) m.get("api")
                        && s.minClientVersionCode == (Long) m.get("minClientVersionCode"), what + ": ok with its fields");
                } else {
                    expect(s.status == ServerSummary.Status.UNAVAILABLE, what + ": unavailable");
                }
                for (Long installed : new Long[]{null, 1L, vc, vc + 1}) honest(ServerSummary.lines(s, vc, names, installed), what);
                served++;
            }
        }
        expect(served > 0, "the fixture has served summaries");

        // Values the server never sends: each is read as unavailable, never as ok, and never fails anything.
        List<Object> unreadable = (List<Object>) fx.get("clientUnreadable");
        expect(!unreadable.isEmpty(), "the fixture lists client-unreadable values");
        for (Object o : unreadable) {
            Map<String, Object> c = (Map<String, Object>) o;
            ServerSummary s = read(c);
            expect(s.status == ServerSummary.Status.UNAVAILABLE && s.unreadable, "unreadable: " + c.get("why"));
            honest(ServerSummary.lines(s, (Long) c.get("versionCode"), null, null), "unreadable: " + c.get("why"));
        }

        // Edge values a client must accept.
        for (Object o : (List<Object>) fx.get("clientReadable")) {
            Map<String, Object> c = (Map<String, Object>) o;
            Map<String, Object> m = (Map<String, Object>) c.get("server");
            ServerSummary s = read(c);
            expect(s.status == ServerSummary.Status.OK && !s.unreadable && s.api == (Long) m.get("api")
                && s.minClientVersionCode == (Long) m.get("minClientVersionCode"), "readable: " + c.get("why"));
        }

        // A JSON null may read as absent (the server never sends one); a non-object is unavailable.
        expect(ServerSummary.parse(null, 3).status == ServerSummary.Status.ABSENT, "null: absent");
        expect(ServerSummary.parse(new ArrayList<Object>(), 3).unreadable, "a list: unavailable");

        // Wording.
        Map<Long, String> names = new HashMap<>();
        names.put(2L, "1.1");
        names.put(4L, "2.0");
        ServerSummary ok = ServerSummary.parse(Json.parse("{\"schemaVersion\":1,\"status\":\"ok\",\"api\":2,\"minClientVersionCode\":2}"), 4);
        List<String> lines = ServerSummary.lines(ok, 4, names, null);
        expect(lines.size() == 2 && lines.get(0).contains("contract API 2") && lines.get(1).contains("before version 1.1"),
            "ok: the api, and which app versions would need to update, by name: " + lines);
        expect(ServerSummary.lines(ok, 4, names, 1L).size() == 3 && ServerSummary.lines(ok, 4, names, 1L).get(2).contains("copy on this phone"),
            "an installed copy older than the minimum is told it would need an update");
        expect(ServerSummary.lines(ok, 4, names, 2L).size() == 2 && ServerSummary.lines(ok, 4, names, 5L).size() == 2,
            "an installed copy at or past the minimum, or newer than the version shown, gets no extra line");
        expect(ServerSummary.lines(ok, 4, names, 3L).size() == 2, "an installed copy between the minimum and the version shown gets no extra line");
        expect(ServerSummary.lines(ok, 4, null, null).get(1).contains("before version 2"), "a version with no name is shown by number");
        ServerSummary own = ServerSummary.parse(Json.parse("{\"schemaVersion\":1,\"status\":\"ok\",\"api\":1,\"minClientVersionCode\":4}"), 4);
        expect(ServerSummary.lines(own, 4, names, 1L).size() == 1,
            "a version that is its own minimum (the first with a server) says only its api: the versions before it never had one");
        List<String> none = ServerSummary.lines(ServerSummary.parse(Json.parse("{\"schemaVersion\":1,\"status\":\"unavailable\"}"), 4), 4, names, 1L);
        expect(none.size() == 1 && none.get(0).contains("isn't available"), "unavailable: one plain line, not an error: " + none);
        honest(lines, "ok");
        honest(none, "unavailable");

        System.out.println("ServerSummaryTest: " + checks + " checks passed");
    }
}
