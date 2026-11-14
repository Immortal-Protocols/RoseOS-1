package rose.models.composer;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/** One rose-layout-v1 result: {@code ok} with a validated layout of real IDs, or {@code failed} with a category. */
public final class ComposeResult {
    public final String requestId;      // null when the request had no string requestId
    public final String status;         // "ok" | "failed"
    public final String failure;        // a Refused.FAILURES category when failed
    public final Map<String, Object> layout;
    public final Map<String, String> model;
    /** composer-side milliseconds: contextMs, generationMs, validationMs, composeMs. Never request-to-render. */
    public final Map<String, Double> timing;
    public final int contextEntries, contextChars, maxTokens;
    public final Map<String, Object> runtimeStats;

    private ComposeResult(String requestId, String status, String failure, Map<String, Object> layout,
                          Map<String, String> model, Map<String, Double> timing, int entries, int chars,
                          int maxTokens, Map<String, Object> runtimeStats) {
        this.requestId = requestId;
        this.status = status;
        this.failure = failure;
        this.layout = layout;
        this.model = model;
        this.timing = timing == null ? Collections.<String, Double>emptyMap()
                : Collections.unmodifiableMap(new LinkedHashMap<String, Double>(timing));
        this.contextEntries = entries;
        this.contextChars = chars;
        this.maxTokens = maxTokens;
        this.runtimeStats = runtimeStats;
    }

    public boolean ok() { return "ok".equals(status); }

    /**
     * The rose-layout-v1 result text, in the same shape as the Python reference's result: what the composer
     * process hands the phone, whose own check ({@code WorkspaceLayout.check}) reads it again before rendering.
     */
    public String toJson() {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("contract", Contract.CONTRACT);
        m.put("requestId", requestId);
        m.put("status", status);
        if (ok()) {
            m.put("layout", layout);
            m.put("model", model);
            m.put("timing", timing);
            Map<String, Object> c = new LinkedHashMap<String, Object>();
            c.put("entries", Long.valueOf(contextEntries));
            c.put("chars", Long.valueOf(contextChars));
            c.put("maxTokens", Long.valueOf(maxTokens));
            m.put("context", c);
            m.put("runtimeStats", runtimeStats == null ? new LinkedHashMap<String, Object>() : runtimeStats);
        } else {
            m.put("failure", failure);
            if (!timing.isEmpty()) m.put("timing", timing);
        }
        return Json.write(m);
    }

    static ComposeResult failed(String requestId, String category, Map<String, Double> timing) {
        return new ComposeResult(requestId, "failed", category, null, null, timing, 0, 0, 0, null);
    }

    static ComposeResult ok(String requestId, Map<String, Object> layout, Map<String, String> model,
                            Map<String, Double> timing, int entries, int chars, int maxTokens,
                            Map<String, Object> runtimeStats) {
        return new ComposeResult(requestId, "ok", null, layout, model, timing, entries, chars, maxTokens, runtimeStats);
    }
}
