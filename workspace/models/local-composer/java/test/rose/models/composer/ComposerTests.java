package rose.models.composer;

import java.io.ByteArrayOutputStream;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.math.BigInteger;
import java.nio.charset.Charset;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Offline tests for the Java core: every shared vector in contract/vectors/v1.json (generated from the Python
 * reference) must give the same context, grammar and result, plus Java-only checks of cancellation, deadline,
 * runtime failure, the goal/ask boundary and the JSON reader. Plain Java 8, no test framework.
 *
 * <pre>java -cp out/classes rose.models.composer.ComposerTests ../contract/vectors/v1.json</pre>
 */
public final class ComposerTests {
    private static int passed, failed;
    /** The result keys OS Product's WorkspaceLayout.check allows (draft PR787 at d71fd72a). */
    private static final java.util.Set<String> RESULT_KEYS = new java.util.HashSet<String>(java.util.Arrays.asList(
            "contract", "requestId", "status", "layout", "failure", "model", "timing", "context", "runtimeStats"));
    private static final List<String> failures = new ArrayList<String>();

    private static void check(boolean ok, String what) {
        if (ok) {
            passed++;
        } else {
            failed++;
            failures.add(what);
        }
    }

    private static void eq(Object want, Object got, String what) {
        boolean same = want == null ? got == null : want.equals(got);
        check(same, what + "\n      want: " + want + "\n      got:  " + got);
    }

    static String read(String path) throws IOException {
        InputStream in = new FileInputStream(path);
        try {
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            byte[] buf = new byte[65536];
            int n;
            while ((n = in.read(buf)) > 0) b.write(buf, 0, n);
            return new String(b.toByteArray(), Charset.forName("UTF-8"));
        } finally {
            in.close();
        }
    }

    static String sha256(String s) throws Exception {
        byte[] d = MessageDigest.getInstance("SHA-256").digest(s.getBytes(Charset.forName("UTF-8")));
        StringBuilder b = new StringBuilder();
        for (byte x : d) b.append(String.format("%02x", x & 0xff));
        return b.toString();
    }

    private static LocalInference scripted(Object output) throws Refused {
        Map<String, Object> err = Json.asMap(output);
        if (err == null) return new ScriptedInference((String) output);
        String cat = (String) err.get("error");
        if ("crash".equals(cat)) return new ScriptedInference(new IllegalStateException("runtime crashed: secret"));
        return new ScriptedInference(new Refused(cat));
    }

    private static Map<String, Object> resolve(Map<String, Object> request, Map<String, Object> snapshots) {
        Map<String, Object> r = new LinkedHashMap<String, Object>(request);
        Map<String, Object> s = Json.asMap(r.get("snapshot"));
        if (s != null && s.containsKey("$ref")) r.put("snapshot", snapshots.get(s.get("$ref")));
        return r;
    }

    static void vectors(String path) throws Exception {
        Map<String, Object> data = Json.asMap(Json.parse(read(path)));
        Catalog fromData = Catalog.fromJson(Json.asMap(data.get("catalog")));
        Catalog catalog = Catalog.cat1();
        eq(fromData.version, catalog.version, "catalog version");
        eq(new ArrayList<String>(fromData.components.keySet()), new ArrayList<String>(catalog.components.keySet()),
                "catalog components");
        for (Catalog.Component c : catalog.components.values()) {
            Catalog.Component d = fromData.components.get(c.name);
            eq(c.items + " " + c.min + " " + c.max + " " + c.actions + " " + c.excludeActionKinds,
                    d.items + " " + d.min + " " + d.max + " " + d.actions + " " + d.excludeActionKinds, "rule " + c.name);
        }
        Map<String, Object> snapshots = Json.asMap(data.get("snapshots"));
        int n = 0;
        for (Object vo : Json.asList(data.get("vectors"))) {
            n++;
            Map<String, Object> v = Json.asMap(vo);
            String name = (String) v.get("name");
            Map<String, Object> request = resolve(Json.asMap(v.get("request")), snapshots);
            Map<String, Object> opts = Json.asMap(v.get("options"));
            Map<String, Object> expect = Json.asMap(v.get("expect"));
            int budget = opts.containsKey("budgetChars") ? ((Long) opts.get("budgetChars")).intValue()
                    : ContextSelector.DEFAULT_BUDGET_CHARS;

            Map<String, Object> wantCtx = Json.asMap(expect.get("context"));
            ContextSelector.Context ctx = null;
            Catalog.Limits limits = null;
            try {
                limits = Contract.checkRequest(request, catalog);
                Object p = request.get("prompt");
                ctx = ContextSelector.select(Json.asMap(request.get("snapshot")), p instanceof String ? (String) p : null,
                        budget, ContextSelector.DEFAULT_PER_APP, Json.asList(request.get("avoid")));
            } catch (Refused r) {
                ctx = null;
            }
            if (wantCtx == null) {
                check(ctx == null, name + ": expected no context");
            } else if (ctx == null) {
                check(false, name + ": context refused but Python selected one");
            } else {
                List<Object> aliases = new ArrayList<Object>();
                for (Map.Entry<String, String> e : ctx.aliases.entrySet()) {
                    List<Object> pair = new ArrayList<Object>();
                    pair.add(e.getKey());
                    pair.add(e.getValue());
                    aliases.add(pair);
                }
                eq(wantCtx.get("aliases"), aliases, name + ": aliases");
                eq(wantCtx.get("lines"), new ArrayList<Object>(ctx.lines), name + ": lines");
                eq(wantCtx.get("chars"), Long.valueOf(ctx.chars), name + ": chars");
                Map<String, Object> wantG = Json.asMap(expect.get("grammar"));
                Grammar.Node tree = Grammar.build(ctx, catalog, limits);
                if (wantG == null) {
                    check(tree == null, name + ": expected no grammar");
                } else if (tree == null) {
                    check(false, name + ": no grammar but Python built one");
                } else {
                    String g = Grammar.toGbnf(tree);
                    eq(wantG.get("sha256"), sha256(g), name + ": gbnf sha256");
                    eq(wantG.get("length"), Long.valueOf(Text.len(g)), name + ": gbnf length");
                    eq(wantG.get("maxChars"), Long.valueOf(Grammar.maxChars(tree)), name + ": maxChars");
                    for (String line : ctx.lines) check(!line.contains("GOAL-TEXT"), name + ": goal text reached the model");
                    check(!g.contains("GOAL-TEXT"), name + ": goal text in grammar");
                }
            }

            ComposeResult res = new Composer(catalog, scripted(v.get("output")), Composer.Clock.SYSTEM, budget)
                    .compose(request);
            Map<String, Object> want = Json.asMap(expect.get("result"));
            eq(want.get("status"), res.status, name + ": status");
            Map<String, Object> wire = Json.asMap(Json.parse(res.toJson()));
            eq(want.get("status"), wire.get("status"), name + ": wire status");
            check(RESULT_KEYS.containsAll(wire.keySet()), name + ": wire keys are the ones the phone's check accepts");
            if (res.ok()) eq(want.get("layout"), wire.get("layout"), name + ": wire layout");
            else eq(want.get("failure"), wire.get("failure"), name + ": wire failure");
            if (res.ok()) {
                eq(want.get("layout"), res.layout, name + ": layout");
                eq(want.get("maxTokens"), Long.valueOf(res.maxTokens), name + ": maxTokens");
            } else {
                eq(want.get("failure"), res.failure, name + ": failure");
                check(res.layout == null, name + ": failed result carries no layout");
            }
            if (expect.containsKey("afterFreshness")) {
                Map<String, Object> wf = Json.asMap(expect.get("afterFreshness"));
                ComposeResult fresh = Validator.checkFresh(res, (String) opts.get("currentSnapshotVersion"));
                eq(wf.get("status"), fresh.status, name + ": status after freshness");
                Object wantFailure = Json.isNull(wf.get("failure")) ? null : wf.get("failure");
                eq(wantFailure, fresh.failure, name + ": failure after freshness");
            }
        }
        check(n >= 40, "expected at least 40 vectors, got " + n);
        System.out.println("vectors: " + n);
    }

    /** Requests and helpers for the Java-only checks. */
    static Map<String, Object> firstRequest(String path) throws Exception {
        Map<String, Object> data = Json.asMap(Json.parse(read(path)));
        Map<String, Object> v = Json.asMap(Json.asList(data.get("vectors")).get(0));
        return resolve(Json.asMap(v.get("request")), Json.asMap(data.get("snapshots")));
    }

    static String exampleOutput(String path) throws Exception {
        Map<String, Object> data = Json.asMap(Json.parse(read(path)));
        return (String) Json.asMap(Json.asList(data.get("vectors")).get(0)).get("output");
    }

    static void behaviour(String path) throws Exception {
        final Map<String, Object> req = firstRequest(path);
        final String output = exampleOutput(path);
        Catalog cat = Catalog.cat1();

        ScriptedInference before = new ScriptedInference(output);
        ComposeResult r = new Composer(cat, before).compose(req, new LocalInference.Cancellation() {
            @Override public boolean isCancelled() { return true; }
        });
        eq("cancelled", r.failure, "cancel before generation");
        eq(0, before.calls.size(), "cancel before generation does not call the runtime");

        final int[] polls = {0};
        r = new Composer(cat, new ScriptedInference(output)).compose(req, new LocalInference.Cancellation() {
            @Override public boolean isCancelled() { return polls[0]++ > 0; }
        });
        eq("cancelled", r.failure, "cancel during generation discards the output");

        final long[] t = {0};
        final long[] steps = {0, 1000000000L, 3500000000L, 0, 0};
        final int[] k = {0};
        Composer.Clock fake = new Composer.Clock() {
            @Override public long nanos() { t[0] += k[0] < steps.length ? steps[k[0]++] : 0; return t[0]; }
        };
        r = new Composer(cat, new ScriptedInference(output), fake, ContextSelector.DEFAULT_BUDGET_CHARS).compose(req);
        eq("timeout", r.failure, "deadline counts the whole compose");

        r = new Composer(cat, new ScriptedInference(new IllegalStateException("token=SECRET"))).compose(req);
        eq("model_unavailable", r.failure, "runtime exception is model_unavailable");
        check(!String.valueOf(r.timing).contains("SECRET") && r.layout == null, "no exception text in the result");

        ScriptedInference seen = new ScriptedInference(output);
        r = new Composer(cat, seen).compose(req);
        eq("ok", r.status, "scripted example composes");
        LocalInference.Request call = seen.calls.get(0);
        check(call.grammarGbnf.startsWith("root ::= "), "runtime receives GBNF");
        check(!call.user.contains("app.timer") && !call.grammarGbnf.contains("app.timer"), "no raw ids reach the runtime");
        check(call.user.endsWith("Request: something for cooking dinner"), "request text at the end of the user message");
        check(call.maxTokens <= Composer.MAX_TOKENS_CAP && call.deadlineMs <= 4000, "bounded call");
        eq("scripted", r.model.get("id"), "scripted provider is labelled");
        check(r.timing.containsKey("generationMs") && r.timing.containsKey("composeMs"), "timing fields");

        eq("bad_request", new Composer(cat, new ScriptedInference()).compose("not a map").failure, "non-map request");
        check(new Composer(cat, new ScriptedInference()).compose("not a map").requestId == null, "no requestId");

        ComposeResult stale = Validator.checkFresh(new Composer(cat, new ScriptedInference(output)).compose(req), "s-other");
        eq("stale_snapshot", stale.failure, "stale snapshot at render");
    }

    static void json() {
        String[] bad = {"", " ", "{", "{\"a\":1,\"a\":2}", "NaN", "[1,]", "{\"a\" 1}", "\"\t\"", "01", "1.", "-",
                "[1] x", "{\"a\":Infinity}", "\"\\x\"", "tru", "\uFEFF{}"};
        for (String s : bad) {
            boolean threw = false;
            try {
                Json.parse(s);
            } catch (Json.ParseError e) {
                threw = true;
            }
            check(threw, "json rejects " + s);
        }
        try {
            Map<String, Object> m = Json.asMap(Json.parse(" {\"a\":[1,-2.5e3,true,null,\"\\u00e9\\ud83c\\udf5d\"],\"b\":{}} "));
            List<Object> a = Json.asList(m.get("a"));
            eq(Long.valueOf(1), a.get(0), "json integer");
            check(a.get(1) instanceof java.math.BigDecimal, "json fraction");
            eq(Boolean.TRUE, a.get(2), "json true");
            check(a.get(3) == Json.NULL, "json null");
            eq("\u00e9\ud83c\udf5d", a.get(4), "json escapes and surrogate pair");
            check(Json.parse("123456789012345678901234567890") instanceof BigInteger, "json big integer");
            StringBuilder deep = new StringBuilder();
            for (int i = 0; i < 600; i++) deep.append('[');
            boolean threw = false;
            try {
                Json.parse(deep.toString());
            } catch (Json.ParseError e) {
                threw = true;
            }
            check(threw, "json depth bound");
            String tricky = "q\"b\\n\n\u0001\u00e9\ud83c\udf5d";
            Map<String, Object> rt = new LinkedHashMap<String, Object>();
            rt.put("s", tricky);
            rt.put("n", Long.valueOf(-3));
            eq(rt, Json.asMap(Json.parse(Json.write(rt))), "json write round-trips escapes");
        } catch (Json.ParseError e) {
            check(false, "json parses a valid document");
        }
    }

    public static void main(String[] args) throws Exception {
        String path = args.length > 0 ? args[0] : "../contract/vectors/v1.json";
        vectors(path);
        behaviour(path);
        json();
        for (String f : failures) System.out.println("FAIL " + f);
        System.out.println("passed " + passed + ", failed " + failed);
        System.exit(failed == 0 ? 0 : 1);
    }
}
