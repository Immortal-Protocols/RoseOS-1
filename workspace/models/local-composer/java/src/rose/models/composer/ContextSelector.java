package rose.models.composer;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Bounded, deterministic context selection. Mirrors {@code composer/context.py}. Pinned ids are left out: the
 * phone keeps pinned sections at their own index and lists their ids so the composer does not repeat them (OS
 * Product's workspace contract). Ids in {@code avoid} (regenerate's soft hint) rank after all others. The rest rank
 * by overlap with the prompt (or time-of-day category hints for a button press), then recency, with a per-app cap,
 * within a character budget counted in code points. Only {@code label}/{@code title}, {@code category} and
 * {@code kind} reach the model; an action's {@code ask} or {@code goal}, and every other field, never do.
 */
public final class ContextSelector {
    private ContextSelector() {}

    public static final int DEFAULT_BUDGET_CHARS = 1600, DEFAULT_PER_APP = 3;
    private static final Pattern WORD = Pattern.compile("[a-z0-9]+");
    private static final Map<String, Set<String>> TIME_HINTS = new HashMap<String, Set<String>>();
    static {
        TIME_HINTS.put("morning", set("news", "weather", "calendar", "commute", "music"));
        TIME_HINTS.put("afternoon", set("work", "tools", "calendar", "messages"));
        TIME_HINTS.put("evening", set("cooking", "food", "media", "music", "messages"));
        TIME_HINTS.put("night", set("media", "music", "reading", "alarm"));
    }

    /** The selected entries. The model sees {@link #lines} only; aliases map back to real IDs. */
    public static final class Context {
        /** alias to real id, in selection order */
        public final Map<String, String> aliases = new LinkedHashMap<String, String>();
        /** alias to snapshot kind ({@code apps}, ...) */
        public final Map<String, String> kinds = new LinkedHashMap<String, String>();
        /** action alias to the alias of its target, when the target was selected */
        public final Map<String, String> targets = new LinkedHashMap<String, String>();
        /** action alias to action kind ({@code agent_task}, ...) */
        public final Map<String, String> actionKinds = new LinkedHashMap<String, String>();
        public final List<String> lines = new ArrayList<String>();
        public int chars;
    }

    static Set<String> words(Object text) {
        Set<String> out = new HashSet<String>();
        if (!(text instanceof String) || ((String) text).isEmpty()) return out;
        Matcher m = WORD.matcher(((String) text).toLowerCase(Locale.ROOT));
        while (m.find()) out.add(m.group());
        return out;
    }

    private static String label(Map<String, Object> e) {
        Object l = e.get("label");
        if (l instanceof String && !((String) l).isEmpty()) return (String) l;
        Object t = e.get("title");
        if (t instanceof String && !((String) t).isEmpty()) return (String) t;
        return "";
    }

    private static String str(Map<String, Object> e, String k) {
        Object v = e.get(k);
        return v instanceof String ? (String) v : null;
    }

    private static final class Candidate {
        final int[] score; // not avoided, overlap, hinted, recency, isAction
        final String kind;
        final Map<String, Object> e;

        Candidate(int[] score, String kind, Map<String, Object> e) {
            this.score = score;
            this.kind = kind;
            this.e = e;
        }
    }

    private static int[] score(String kind, Map<String, Object> e, Set<String> promptWords, Set<String> hints,
                               Set<String> avoid) {
        Set<String> text = words(label(e));
        text.addAll(words(e.get("category")));
        text.addAll(words(e.get("kind")));
        int overlap = 0;
        for (String w : text) if (promptWords.contains(w)) overlap++;
        int hinted = 0;
        for (String w : words(e.get("category"))) if (hints.contains(w)) { hinted = 1; break; }
        Object rank = e.get("recentRank");
        int recency = rank instanceof Long ? (int) -(Long) rank : -1000000; // Contract bounds it to 0..1e6
        return new int[] {avoid.contains(e.get("id")) ? 0 : 1, overlap, hinted, recency,
                "actions".equals(kind) ? 1 : 0};
    }

    public static Context select(Map<String, Object> snapshot, String prompt) throws Refused {
        return select(snapshot, prompt, DEFAULT_BUDGET_CHARS, DEFAULT_PER_APP, null);
    }

    /** Refused(empty_context) when nothing fits. The snapshot must already have passed {@link Contract}. */
    public static Context select(Map<String, Object> snapshot, String prompt, int budgetChars, int perApp,
                                 List<Object> avoidIds) throws Refused {
        Set<String> pinned = new HashSet<String>();
        List<Object> p = Json.asList(snapshot.get("pinned"));
        if (p != null) for (Object x : p) if (x instanceof String) pinned.add((String) x);
        Set<String> avoid = new HashSet<String>();
        if (avoidIds != null) for (Object x : avoidIds) if (x instanceof String) avoid.add((String) x);
        Set<String> promptWords = words(prompt);
        Set<String> hints = Collections.emptySet();
        if (promptWords.isEmpty() && TIME_HINTS.containsKey(snapshot.get("timeOfDay"))) {
            hints = TIME_HINTS.get(snapshot.get("timeOfDay"));
        }
        List<Candidate> candidates = new ArrayList<Candidate>();
        for (String kind : Contract.KINDS) {
            for (Object o : Contract.entries(snapshot, kind)) {
                Map<String, Object> e = Json.asMap(o);
                if (pinned.contains(e.get("id"))) continue;
                candidates.add(new Candidate(score(kind, e, promptWords, hints, avoid), kind, e));
            }
        }
        // Python sorts with reverse=True, which keeps equal elements in their original order: a stable sort on
        // the descending comparator does the same.
        Collections.sort(candidates, new Comparator<Candidate>() {
            @Override public int compare(Candidate a, Candidate b) {
                for (int k = 0; k < a.score.length; k++) {
                    if (a.score[k] != b.score[k]) return a.score[k] > b.score[k] ? -1 : 1;
                }
                return 0;
            }
        });
        Context ctx = new Context();
        Map<String, Integer> counts = new HashMap<String, Integer>();
        Map<String, Integer> perAppCount = new HashMap<String, Integer>();
        Map<String, String> chosenIds = new HashMap<String, String>();
        List<String> rawTargets = new ArrayList<String>();
        for (Candidate c : candidates) {
            Map<String, Object> e = c.e;
            String id = (String) e.get("id");
            String appId = str(e, "appId");
            String app = appId != null && !appId.isEmpty() ? appId : ("apps".equals(c.kind) ? id : null);
            if (app != null && count(perAppCount, app) >= perApp) continue;
            int n = count(counts, c.kind) + 1;
            String alias = Contract.prefix(c.kind) + n;
            String category = str(e, "category"), kind = str(e, "kind");
            String extra = category != null && !category.isEmpty() ? category : (kind != null ? kind : "");
            String line = alias + " " + label(e) + (extra.isEmpty() ? "" : " [" + extra + "]");
            String target = null;
            if ("actions".equals(c.kind)) {
                target = (String) e.get("targetId");
                line += " -> " + target; // rewritten to the target's alias below, or dropped
            }
            if (ctx.chars + Text.len(line) + 1 > budgetChars) continue;
            counts.put(c.kind, n);
            ctx.aliases.put(alias, id);
            ctx.kinds.put(alias, c.kind);
            if ("actions".equals(c.kind)) ctx.actionKinds.put(alias, kind);
            chosenIds.put(id, alias);
            ctx.lines.add(line);
            rawTargets.add(target);
            ctx.chars += Text.len(line) + 1;
            if (app != null) perAppCount.put(app, count(perAppCount, app) + 1);
        }
        List<String> fin = new ArrayList<String>();
        int chars = 0;
        for (int k = 0; k < ctx.lines.size(); k++) {
            String line = ctx.lines.get(k);
            String alias = line.substring(0, line.indexOf(' '));
            if ("actions".equals(ctx.kinds.get(alias))) {
                String target = rawTargets.get(k);
                String head = line.substring(0, line.length() - (" -> " + target).length());
                if (chosenIds.containsKey(target)) {
                    ctx.targets.put(alias, chosenIds.get(target));
                    line = head + " -> " + chosenIds.get(target);
                } else {
                    line = head;
                }
            }
            fin.add(line);
            chars += Text.len(line) + 1;
        }
        ctx.lines.clear();
        ctx.lines.addAll(fin);
        ctx.chars = chars;
        if (ctx.aliases.isEmpty()) throw new Refused("empty_context");
        return ctx;
    }

    private static int count(Map<String, Integer> m, String k) {
        Integer v = m.get(k);
        return v == null ? 0 : v;
    }

    private static Set<String> set(String... xs) { return new HashSet<String>(Arrays.asList(xs)); }
}
