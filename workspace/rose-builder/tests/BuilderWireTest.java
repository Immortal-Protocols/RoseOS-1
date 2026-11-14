package dev.pixel.builder;

import java.util.Arrays;
import java.util.List;
import java.util.Map;

/** Project 1: the small JSON reader/writer and builder API v1's wire mapping (BuilderWire), against App Core's shapes. */
public final class BuilderWireTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }
    static void malformed(String body, String message) {
        try { BuilderWire.library(body); } catch (BuilderWire.Malformed e) { checks++; return; }
        throw new AssertionError(message);
    }

    static String deep(int n) { StringBuilder b = new StringBuilder(); for (int i = 0; i < n; i++) b.append('['); for (int i = 0; i < n; i++) b.append(']'); return b.toString(); }

    static final String SHA = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

    public static void main(String[] args) throws Exception {
        // JSON: strict, bounded, and exact for what the builder sends.
        Object v = Json.parse("{\"a\":[1,2.5,-3e2,true,false,null,\"x\\n\\u00e9\"],\"b\":{}}");
        @SuppressWarnings("unchecked") Map<String, Object> m = (Map<String, Object>) v;
        expect(((List<?>) m.get("a")).equals(Arrays.asList(1L, 2.5, -300.0, true, false, null, "x\né")), "values parse");
        for (String bad : new String[]{"", "{", "[1,]", "{\"a\":1,}", "{\"a\":1}x", "01", "{\"a\":1,\"a\":2}", "\"\u0001\"", "nul", "1.", deep(41)})
            { try { Json.parse(bad); throw new AssertionError("accepted " + bad); } catch (Json.ParseError e) { checks++; } }
        expect("{\"schemaVersion\":1,\"clientRequestId\":\"r1\",\"description\":\"A \\\"tip\\\" calc\\n\"}".equals(BuilderWire.createBody("r1", "A \"tip\" calc\n")), "create body");
        expect(BuilderWire.reviseBody("r2", "Bigger").equals("{\"schemaVersion\":1,\"clientRequestId\":\"r2\",\"request\":\"Bigger\"}"), "revise body");
        expect(BuilderWire.emptyBody().equals("{\"schemaVersion\":1}"), "cancel and retry body");
        expect(BuilderWire.text("ok\uD800x😀").equals("ok�x😀"), "a lone surrogate becomes U+FFFD; a pair stays");

        // Library: {schemaVersion, projects}; versions ordered; activeJob; seconds to milliseconds; unknown fields ignored.
        String lib = "{\"schemaVersion\":1,\"projects\":[{\"projectId\":\"p1\",\"name\":\"Tip Calc\",\"packageName\":\"app.rose.gen.pp1\","
            + "\"signerSha256\":null,\"activeJob\":{\"jobId\":\"j2\",\"state\":\"building\"},\"future\":{\"x\":1},\"versions\":["
            + "{\"versionCode\":2,\"versionName\":\"1.1\",\"createdAt\":1794608705.11,\"jobId\":\"j1\",\"sourceRevision\":\"r2\",\"apk\":{\"sha256\":\"" + SHA.toUpperCase() + "\",\"size\":4096},\"notes\":\"Adds tips\"},"
            + "{\"versionCode\":1,\"versionName\":\"1.0\",\"createdAt\":1794608600,\"jobId\":\"j0\",\"sourceRevision\":\"r1\",\"apk\":{\"sha256\":\"" + SHA + "\",\"size\":4000},\"notes\":null}]}]}";
        List<BuilderWire.LibraryProject> ps = BuilderWire.library(lib);
        BuilderApi.Project p = ps.get(0).project;
        expect(ps.size() == 1 && "p1".equals(p.projectId) && "app.rose.gen.pp1".equals(p.packageName), "project");
        expect(p.signerSha256 == null, "a null signer pin stays null (re-read, never cached)");
        expect(p.versions.get(0).versionCode == 1 && p.latest().versionCode == 2, "versions in versionCode order");
        expect(p.latest().createdAt == 1794608705110L && p.latest().apkSize == 4096 && SHA.equals(p.latest().apkSha256), "times in ms, exact size, lowercase sha");
        expect("j2".equals(ps.get(0).activeJobId) && ps.get(0).activeState == BuildJob.Stage.BUILDING && p.job != null && !p.job.terminal(), "active job");
        expect(BuilderWire.library("{\"schemaVersion\":1,\"projects\":[{\"projectId\":\"p\",\"packageName\":\"app.rose.gen.pp\",\"activeJob\":null,\"versions\":[]}]}").get(0).project.job == null, "no active job");
        malformed("{\"projects\":[]}", "no schemaVersion");
        malformed("{\"schemaVersion\":2,\"projects\":[]}", "another schemaVersion");
        malformed("{\"schemaVersion\":1}", "no projects");
        malformed("{\"schemaVersion\":1,\"projects\":[{\"projectId\":\"p\",\"packageName\":\"x\",\"versions\":[{\"versionCode\":1,\"apk\":{\"sha256\":\"zz\",\"size\":1}}]}]}", "a bad sha");
        malformed("{\"schemaVersion\":1,\"projects\":[{\"projectId\":\"p\",\"packageName\":\"x\",\"versions\":[{\"versionCode\":1.5,\"apk\":{\"sha256\":\"" + SHA + "\",\"size\":1}}]}]}", "a fractional versionCode");
        malformed("{\"schemaVersion\":1,\"projects\":[{\"projectId\":\"p\",\"packageName\":\"x\",\"activeJob\":{\"jobId\":\"j\",\"state\":\"dreaming\"},\"versions\":[]}]}", "an unknown state");

        // Job (§3.2), with a failure.
        BuildJob j = BuilderWire.job("{\"schemaVersion\":1,\"jobId\":\"j3\",\"projectId\":\"p1\",\"attempt\":2,\"state\":\"failed\",\"stage\":\"compile\",\"message\":\"m\",\"logTail\":\"...\","
            + "\"startedAt\":1794608705.5,\"updatedAt\":1794608710.0,\"failure\":{\"category\":\"dependency\",\"summary\":\"Kotlin\",\"retryable\":false}}");
        expect(j.stage == BuildJob.Stage.FAILED && j.attempt == 2 && j.startedAt == 1794608705500L, "job fields");
        expect(j.failure.category == BuildJob.Category.DEPENDENCY && !j.failure.retryable && "Kotlin".equals(j.failure.summary), "failure");
        expect(BuilderWire.category("brand-new") == BuildJob.Category.INTERNAL, "an unknown category reads as internal");

        // create / revise / cancel / retry answers.
        BuilderWire.Accepted c = BuilderWire.accepted("{\"schemaVersion\":1,\"projectId\":\"p9\",\"jobId\":\"j9\",\"notes\":[\"No SMS.\"]}", true);
        expect("p9".equals(c.projectId) && "j9".equals(c.jobId) && c.notes.equals(Arrays.asList("No SMS.")), "create accepted with notes");
        expect(BuilderWire.accepted("{\"schemaVersion\":1,\"jobId\":\"j10\",\"notes\":[]}", false).notes.isEmpty(), "revise accepted");
        expect(BuilderWire.cancel("{\"schemaVersion\":1,\"status\":\"not_running\"}") == BuilderApi.CancelStatus.NOT_RUNNING, "cancel status");
        expect(BuilderWire.retryAttempt("{\"schemaVersion\":1,\"jobId\":\"j3\",\"attempt\":3}") == 3, "retry attempt");

        // Refusals.
        BuilderWire.Refusal r = BuilderWire.refusal(400, "{\"schemaVersion\":1,\"error\":\"Rose won't build that. Nothing was started.\",\"code\":\"out_of_scope\",\"category\":\"harm\"}");
        expect(r.kind == BuilderWire.Kind.OUT_OF_SCOPE && "harm".equals(r.category) && r.message.endsWith("Nothing was started."), "out of scope");
        BuilderWire.Refusal review = BuilderWire.refusal(400, "{\"schemaVersion\":1,\"error\":\"This builder isn't accepting new apps yet. Nothing was started.\",\"code\":\"review_required\",\"category\":\"unavailable\"}");
        expect(review.kind == BuilderWire.Kind.OUT_OF_SCOPE && "unavailable".equals(review.category) && review.message.endsWith("Nothing was started."), "review_required: the builder's own sentence, never success");
        expect(BuilderWire.refusal(400, "{\"schemaVersion\":1,\"error\":\"unknown project\"}").kind == BuilderWire.Kind.REFUSED, "other 400");
        expect(BuilderWire.refusal(404, "{\"schemaVersion\":1,\"error\":\"not found\"}").kind == BuilderWire.Kind.REFUSED, "404");
        expect(BuilderWire.refusal(401, "{\"schemaVersion\":1,\"error\":\"unknown device\",\"final\":true}").kind == BuilderWire.Kind.PAIR_AGAIN, "401 final: pair again");
        expect(BuilderWire.refusal(401, "{\"schemaVersion\":1,\"error\":\"stale time\",\"final\":false}").kind == BuilderWire.Kind.RETRY_SIGNED, "401 not final: sign afresh");
        expect(BuilderWire.refusal(401, "garbage").kind == BuilderWire.Kind.RETRY_SIGNED, "an unreadable 401 is never taken as final");
        expect(BuilderWire.refusal(503, "").kind == BuilderWire.Kind.UNREACHABLE && BuilderWire.refusal(405, "{}").kind == BuilderWire.Kind.UNREACHABLE, "others: unavailable");

        // Receipt body (§3.5): signerSha256 always present, null when nothing is installed.
        String rb = BuilderWire.receiptBody(new BuilderApi.InstallReceipt("p1", "app.rose.gen.pp1", 2, null, InstallPlan.Receipt.CANCELLED_BY_USER, null));
        expect(rb.equals("{\"schemaVersion\":1,\"projectId\":\"p1\",\"packageName\":\"app.rose.gen.pp1\",\"versionCode\":2,\"signerSha256\":null,\"status\":\"cancelled_by_user\",\"detail\":\"\"}"), "receipt body");

        // Receipt limits: detail at most 500 characters, never splitting a pair; no receipt without a version.
        StringBuilder longMsg = new StringBuilder(); for (int i = 0; i < 700; i++) longMsg.append('x');
        String clipped = BuilderWire.receiptBody(new BuilderApi.InstallReceipt("p1", "app.rose.gen.pp1", 2, null, InstallPlan.Receipt.FAILED, longMsg.toString()));
        expect(clipped.contains("\"detail\":\"" + longMsg.substring(0, 500) + "\"}"), "detail clipped to 500");
        expect(BuilderWire.clip("ab\uD83D\uDE00cd", 3).equals("ab\uD83D\uDE00"), "clip counts code points");
        expect(!BuilderWire.sendable(new BuilderApi.InstallReceipt("p1", "x", -1, null, InstallPlan.Receipt.FAILED, null)), "no receipt without a version");
        expect(!BuilderWire.sendable(new BuilderApi.InstallReceipt("p1", "x", 1L << 31, null, InstallPlan.Receipt.FAILED, null)), "versionCode beyond 2^31-1 isn't sent");
        expect(BuilderWire.sendable(new BuilderApi.InstallReceipt("p1", "x", 2, null, InstallPlan.Receipt.UPDATED, null)), "a normal receipt is sent");

        // Path ids.
        expect("p1".equals(BuilderWire.segment("p1")), "id segment");
        for (String bad : new String[]{"", "../x", "a/b", "a b", null}) { try { BuilderWire.segment(bad); throw new AssertionError("segment " + bad); } catch (BuilderWire.Malformed e) { checks++; } }

        System.out.println("BuilderWireTest: " + checks + " checks passed");
    }
}
