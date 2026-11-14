package rose.models.composer;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * One request in, one validated layout or typed failure out. Mirrors {@code composer/compose.py}. No network, no
 * files, no model text in failures. Callable from the Android app (Java 8, no Android or org.json types); the
 * caller supplies the request as JSON-shaped maps (see {@link Json}) and a {@link LocalInference}.
 */
public final class Composer {
    public static final int TOKEN_CHARS = 2, MAX_TOKENS_CAP = 512;

    /** Monotonic time source in nanoseconds; injectable for tests. */
    public interface Clock {
        long nanos();

        Clock SYSTEM = new Clock() {
            @Override public long nanos() { return System.nanoTime(); }
        };
    }

    private final Catalog catalog;
    private final LocalInference inference;
    private final Clock clock;
    private final int budgetChars;

    public Composer(Catalog catalog, LocalInference inference) {
        this(catalog, inference, Clock.SYSTEM, ContextSelector.DEFAULT_BUDGET_CHARS);
    }

    public Composer(Catalog catalog, LocalInference inference, Clock clock, int budgetChars) {
        this.catalog = catalog;
        this.inference = inference;
        this.clock = clock;
        this.budgetChars = budgetChars;
    }

    private static double ms(long a, long b) { return Math.round((b - a) / 1000.0) / 1000.0; }

    public ComposeResult compose(Object request) { return compose(request, LocalInference.Cancellation.NEVER); }

    public ComposeResult compose(Object request, LocalInference.Cancellation cancellation) {
        long t0 = clock.nanos();
        Map<String, Double> timing = new LinkedHashMap<String, Double>();
        Map<String, Object> req = Json.asMap(request);
        String rid = req != null && req.get("requestId") instanceof String ? (String) req.get("requestId") : null;
        try {
            Catalog.Limits limits = Contract.checkRequest(request, catalog);
            Map<String, Object> snap = Json.asMap(req.get("snapshot"));
            String prompt = req.get("prompt") instanceof String ? (String) req.get("prompt") : null;
            ContextSelector.Context ctx = ContextSelector.select(snap, prompt, budgetChars,
                    ContextSelector.DEFAULT_PER_APP, Json.asList(req.get("avoid")));
            Grammar.Node tree = Grammar.build(ctx, catalog, limits);
            if (tree == null) throw new Refused("empty_context");
            String gbnf = Grammar.toGbnf(tree);
            String user = Prompt.user(ctx, catalog, prompt, snap.get("timeOfDay"));
            int maxTokens = Math.min(MAX_TOKENS_CAP, Grammar.maxChars(tree) / TOKEN_CHARS + 8);
            long t1 = clock.nanos();
            timing.put("contextMs", ms(t0, t1));
            long deadlineLeft = limits.deadlineMs - (t1 - t0) / 1000000L;
            if (cancellation.isCancelled()) throw new Refused("cancelled");
            LocalInference.Generation gen;
            try {
                gen = inference.generate(new LocalInference.Request(Prompt.SYSTEM, user, gbnf, maxTokens,
                        deadlineLeft, cancellation));
            } catch (Refused r) {
                throw r;
            } catch (RuntimeException e) { // a runtime failure is a typed failure, never a crash of the page
                throw new Refused("model_unavailable");
            }
            long t2 = clock.nanos();
            timing.put("generationMs", ms(t1, t2));
            if (cancellation.isCancelled()) throw new Refused("cancelled");
            if ((t2 - t0) / 1e6 > limits.deadlineMs) throw new Refused("timeout");
            if (gen == null) throw new Refused("invalid_output");
            Map<String, Object> layout = Validator.validate(gen.text, ctx, catalog, limits,
                    (String) snap.get("snapshotVersion"));
            long t3 = clock.nanos();
            timing.put("validationMs", ms(t2, t3));
            timing.put("composeMs", ms(t0, t3));
            return ComposeResult.ok(rid, layout, inference.info(), timing, ctx.aliases.size(), ctx.chars,
                    maxTokens, gen.stats);
        } catch (Refused r) {
            timing.put("composeMs", ms(t0, clock.nanos()));
            return ComposeResult.failed(rid, r.category, timing);
        }
    }
}
