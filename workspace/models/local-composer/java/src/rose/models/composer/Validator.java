package rose.models.composer;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Strict validation of one model output against one request, then alias to real-ID mapping. Mirrors
 * {@code composer/validate.py}, check for check and in the same order, so both return the same failure category.
 */
public final class Validator {
    private Validator() {}

    public static final int MAX_OUTPUT_CHARS = 8192;
    private static final Set<String> SECTION_KEYS = new HashSet<String>(Arrays.asList("component", "title", "items", "actions"));
    private static final Set<String> LAYOUT_KEYS = new HashSet<String>(Arrays.asList("title", "sections"));

    static Map<String, Object> parse(String text) throws Refused {
        if (text == null || Text.len(text) > MAX_OUTPUT_CHARS) throw new Refused("invalid_output");
        Object v;
        try {
            v = Json.parse(text);
        } catch (Json.ParseError e) {
            throw new Refused("invalid_output");
        }
        Map<String, Object> m = Json.asMap(v);
        if (m == null) throw new Refused("invalid_output");
        return m;
    }

    private static String title(Object value, int limit) throws Refused {
        if (!(value instanceof String) || Text.strip((String) value).isEmpty() || Text.hasControl((String) value)) {
            throw new Refused("invalid_output");
        }
        if (Text.len((String) value) > limit) throw new Refused("over_limit");
        return (String) value;
    }

    private static List<String> aliasList(Object value) throws Refused {
        List<Object> l = Json.asList(value);
        if (l == null) throw new Refused("invalid_output");
        List<String> out = new ArrayList<String>();
        for (Object o : l) {
            if (!(o instanceof String)) throw new Refused("invalid_output");
            out.add((String) o);
        }
        return out;
    }

    /** The layout with real IDs for {@code text}, or Refused. Keys: snapshotVersion, catalogVersion, title, sections. */
    public static Map<String, Object> validate(String text, ContextSelector.Context ctx, Catalog catalog,
                                               Catalog.Limits limits, String snapshotVersion) throws Refused {
        Map<String, Object> raw = parse(text);
        if (!raw.keySet().equals(LAYOUT_KEYS)) throw new Refused("invalid_output");
        String layoutTitle = title(raw.get("title"), Contract.LAYOUT_TITLE_CHARS);
        List<Object> sections = Json.asList(raw.get("sections"));
        if (sections == null || sections.isEmpty()) throw new Refused("invalid_output");
        if (sections.size() > limits.maxSections) throw new Refused("over_limit");
        Set<String> seen = new HashSet<String>();
        int total = 0;
        List<Object> out = new ArrayList<Object>();
        for (Object so : sections) {
            Map<String, Object> s = Json.asMap(so);
            if (s == null || !s.keySet().containsAll(Arrays.asList("component", "title", "items"))
                    || !SECTION_KEYS.containsAll(s.keySet())) {
                throw new Refused("invalid_output");
            }
            Catalog.Component rule = s.get("component") instanceof String ? catalog.components.get(s.get("component")) : null;
            if (rule == null) throw new Refused("invalid_output");
            String sectionTitle = title(s.get("title"), Contract.SECTION_TITLE_CHARS);
            List<String> items = aliasList(s.get("items"));
            List<String> actions = s.containsKey("actions") ? aliasList(s.get("actions")) : new ArrayList<String>();
            if (items.size() < rule.min) throw new Refused("invalid_output");
            if (!actions.isEmpty() && rule.actions == 0) throw new Refused("disallowed_ref");
            if (items.size() > Math.min(rule.max, limits.maxItemsPerSection) || actions.size() > rule.actions) {
                throw new Refused("over_limit");
            }
            List<String> all = new ArrayList<String>(items);
            all.addAll(actions);
            for (String a : all) {
                if (!ctx.aliases.containsKey(a)) throw new Refused("unknown_ref");
                if (!seen.add(a)) throw new Refused("duplicate_ref");
            }
            for (String a : items) {
                if (!rule.items.contains(ctx.kinds.get(a)) || rule.excludeActionKinds.contains(ctx.actionKinds.get(a))) {
                    throw new Refused("disallowed_ref");
                }
            }
            for (String a : actions) { // a card's actions must be actions that target one of the section's own items
                if (!"actions".equals(ctx.kinds.get(a)) || !items.contains(ctx.targets.get(a))) {
                    throw new Refused("disallowed_ref");
                }
            }
            total += items.size();
            if (total > limits.maxTotalItems) throw new Refused("over_limit");
            Map<String, Object> sec = new LinkedHashMap<String, Object>();
            sec.put("component", rule.name);
            sec.put("title", sectionTitle);
            sec.put("items", real(items, ctx));
            sec.put("actions", real(actions, ctx));
            out.add(sec);
        }
        Map<String, Object> layout = new LinkedHashMap<String, Object>();
        layout.put("snapshotVersion", snapshotVersion);
        layout.put("catalogVersion", catalog.version);
        layout.put("title", layoutTitle);
        layout.put("sections", out);
        return layout;
    }

    private static List<Object> real(List<String> aliases, ContextSelector.Context ctx) {
        List<Object> out = new ArrayList<Object>();
        for (String a : aliases) out.add(ctx.aliases.get(a));
        return out;
    }

    /** Just before rendering: a result composed against an older snapshot becomes {@code stale_snapshot}. */
    public static ComposeResult checkFresh(ComposeResult result, String currentSnapshotVersion) {
        if (result.ok() && !currentSnapshotVersion.equals(result.layout.get("snapshotVersion"))) {
            return ComposeResult.failed(result.requestId, "stale_snapshot", result.timing);
        }
        return result;
    }
}
