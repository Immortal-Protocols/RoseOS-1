package dev.pixel.builder;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Project 1: the advisory API level summary (docs/APP-BUILDER.md §3.3, versions[].apiReport), parsed and worded by
 * ApiReport. Checked against App Core's shared fixture file (args[0]): every served case reads as its status, and every
 * clientUnreadable case reads as unavailable, never clean. No status is ever worded as a pass. Host JVM.
 */
public final class ApiReportTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    static final String[] NEVER = {"compatible", "safe", "works on this phone", "no problems", "passed", "all clear"};

    /** Every worded line set says advisory/not proof, and none reads as a pass. */
    static void honest(List<String> lines, String what) {
        String all = String.join("\n", lines).toLowerCase(Locale.ROOT);
        expect(all.contains("advisory") && all.contains("not proof"), what + ": says advisory, not proof");
        for (String w : NEVER) expect(!all.contains(w), what + ": never says \"" + w + "\"");
    }

    @SuppressWarnings("unchecked")
    public static void main(String[] args) throws Exception {
        Map<String, Object> fx = (Map<String, Object>) Json.parse(new String(Files.readAllBytes(Paths.get(args[0])), StandardCharsets.UTF_8));
        expect(String.valueOf(fx.get("contract")).contains("apiReport"), "the fixture is the apiReport contract");

        // Served cases: each reads as the status the server sent, with its fields.
        for (Object o : (List<Object>) fx.get("cases")) {
            Map<String, Object> c = (Map<String, Object>) o;
            Map<String, Object> served = (Map<String, Object>) c.get("apiReport");
            ApiReport r = ApiReport.parse(served);
            String name = (String) c.get("case");
            expect(r.status.wire().equals(served.get("status")) && !r.unreadable, name + ": reads as " + served.get("status"));
            if (r.hasCounts()) {
                Map<String, Object> counts = (Map<String, Object>) served.get("counts");
                expect(r.notGuardedHere == (Long) counts.get("notGuardedHere") && r.unverified == (Long) counts.get("unverified")
                    && r.guarded == (Long) counts.get("guarded"), name + ": counts");
                expect(r.truncated == (Long) served.get("truncated") && r.unlistedCount == (Long) served.get("unlistedCount"), name + ": truncated and unlisted");
                expect(served.get("highestLevel") == null ? r.highestLevel == null : r.highestLevel == (long) (Long) served.get("highestLevel"), name + ": highestLevel");
            }
            for (int sdk : new int[]{26, 30, 34, 35}) honest(ApiReport.lines(r, sdk), name + " on API " + sdk);
        }

        // Client-unreadable values: unavailable ("couldn't read"), never clean, never a library failure.
        Map<String, Object> bad = (Map<String, Object>) fx.get("clientUnreadable");
        for (Object o : (List<Object>) bad.get("cases")) {
            Map<String, Object> c = (Map<String, Object>) o;
            ApiReport r = ApiReport.parse(c.get("apiReport"));
            expect(r.status == ApiReport.Status.UNAVAILABLE && r.unreadable && !r.hasCounts(), c.get("case") + ": read as unavailable");
            List<String> lines = ApiReport.lines(r, 35);
            honest(lines, (String) c.get("case"));
            expect(String.join(" ", lines).contains("couldn't be read"), c.get("case") + ": says it couldn't be read");
        }

        // Legacy: no apiReport at all (an older server) reads as none.
        ApiReport legacy = ApiReport.parse(null);
        expect(legacy.status == ApiReport.Status.NONE && !legacy.hasCounts(), "absent: none");
        honest(ApiReport.lines(legacy, 35), "absent");
        expect(String.join(" ", ApiReport.lines(legacy, 35)).contains("No API level report"), "absent: says there is no report");

        // Wording of the uncertainty, from the served cases.
        ApiReport incompleteZero = served(fx, "incomplete-zero-counts");
        String iz = String.join(" ", ApiReport.lines(incompleteZero, 35));
        expect(iz.contains("Not every part of the app was checked") && iz.contains("budget ran out"), "incomplete: says not everything was checked, and why");
        expect(iz.contains("0 uses"), "zero counts are stated as counts, not as a result");
        ApiReport truncated = served(fx, "truncated");
        String tr = String.join(" ", ApiReport.lines(truncated, 35));
        expect(tr.contains("3 more findings weren't kept") && tr.contains("at least API 34"), "truncated: says findings were dropped and the level is a lower bound");
        ApiReport unlisted = served(fx, "complete-unlisted-assumptions");
        expect(String.join(" ", ApiReport.lines(unlisted, 35)).contains("60 uses aren't in the API database"), "unlisted: says some uses were assumed");
        ApiReport findings = served(fx, "complete-with-findings");
        String below = String.join(" ", ApiReport.lines(findings, 30)), above = String.join(" ", ApiReport.lines(findings, 35));
        expect(below.contains("this phone has API 30") && below.contains("may fail on this phone"), "a flagged level above the phone's: may fail here");
        expect(above.contains("this phone has API 35") && !above.contains("may fail on this phone") && above.contains("at least API 34"), "at or below the phone's: stated as a lower bound, not a pass");
        expect(above.contains("1 use") && above.contains("2 it couldn't verify") && above.contains("1 with a version check"), "counts worded");
        ApiReport unavailable = served(fx, "unavailable");
        expect(String.join(" ", ApiReport.lines(unavailable, 35)).contains("dexdump is missing"), "unavailable: says why, as plain text");

        // A served reason is plain, single-line display text.
        ApiReport ctl = ApiReport.parse(Json.parse("{\"schemaVersion\":1,\"advisory\":true,\"deviceProof\":false,\"status\":\"unavailable\",\"reason\":\"a\\nb<script>\",\"note\":\"n\"}"));
        expect(String.join(" ", ApiReport.lines(ctl, 35)).contains("a b<script>"), "reason: control characters removed, shown as text");

        System.out.println("ApiReportTest: " + checks + " checks passed");
    }

    @SuppressWarnings("unchecked")
    static ApiReport served(Map<String, Object> fx, String name) {
        for (Object o : (List<Object>) fx.get("cases")) {
            Map<String, Object> c = (Map<String, Object>) o;
            if (name.equals(c.get("case"))) return ApiReport.parse(c.get("apiReport"));
        }
        throw new AssertionError("no fixture case " + name);
    }
}
