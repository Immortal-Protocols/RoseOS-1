package rose.models.composer;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The component catalog, read from data; nothing else here hard-codes a component. {@link #cat1()} is OS
 * Product's catalog {@code cat-1} as specified in {@code workspace/rose-agent/docs/workspace-contract.md} (draft
 * PR787 at d71fd72a), the same data as the Python reference's {@code DEFAULT_CATALOG}. OS Product owns it: in the
 * app, prefer {@link #fromJson} with OS Product's own copy.
 */
public final class Catalog {
    public static final class Component {
        public final String name;
        public final List<String> items;              // snapshot kinds it may reference
        public final int min, max, actions;           // item bounds, max card actions
        public final List<String> excludeActionKinds; // action kinds it may not hold as items

        Component(String name, List<String> items, int min, int max, int actions, List<String> exclude) {
            this.name = name;
            this.items = Collections.unmodifiableList(items);
            this.min = min;
            this.max = max;
            this.actions = actions;
            this.excludeActionKinds = Collections.unmodifiableList(exclude);
        }
    }

    public final String version;
    public final Map<String, Component> components; // in catalog order
    public final Limits limits;

    private Catalog(String version, Map<String, Component> components, Limits limits) {
        this.version = version;
        this.components = Collections.unmodifiableMap(components);
        this.limits = limits;
    }

    /** Reads a catalog in the shape of {@code DEFAULT_CATALOG} / {@code contract/vectors/v1.json#catalog}. */
    public static Catalog fromJson(Map<String, Object> m) {
        String version = (String) m.get("version");
        Map<String, Component> comps = new LinkedHashMap<String, Component>();
        for (Map.Entry<String, Object> e : Json.asMap(m.get("components")).entrySet()) {
            Map<String, Object> r = Json.asMap(e.getValue());
            comps.put(e.getKey(), new Component(e.getKey(), strings(r.get("items")), intOf(r.get("min")),
                    intOf(r.get("max")), intOf(r.get("actions")), strings(r.get("excludeActionKinds"))));
        }
        Map<String, Object> l = Json.asMap(m.get("limits"));
        Limits limits = new Limits(intOf(l.get("maxSections")), intOf(l.get("maxItemsPerSection")),
                intOf(l.get("maxTotalItems")), intOf(l.get("deadlineMs")));
        return new Catalog(version, comps, limits);
    }

    private static List<String> strings(Object o) {
        List<String> out = new ArrayList<String>();
        List<Object> l = Json.asList(o);
        if (l != null) for (Object x : l) out.add((String) x);
        return out;
    }

    private static int intOf(Object o) { return ((Number) o).intValue(); }

    /** OS Product's catalog {@code cat-1} (mirrors the Python reference's DEFAULT_CATALOG). */
    public static Catalog cat1() {
        Map<String, Component> c = new LinkedHashMap<String, Component>();
        c.put("grid", new Component("grid", list("apps", "actions"), 2, 8, 0, list("agent_task")));
        c.put("carousel", new Component("carousel", list("content", "notifications", "apps"), 2, 8, 0, list()));
        c.put("list", new Component("list", list("notifications", "content"), 1, 8, 0, list()));
        c.put("card", new Component("card", list("notifications", "content"), 1, 1, 2, list()));
        c.put("button", new Component("button", list("actions"), 1, 4, 0, list()));
        return new Catalog("cat-1", c, new Limits(6, 8, 20, 4000));
    }

    private static List<String> list(String... xs) {
        List<String> l = new ArrayList<String>();
        Collections.addAll(l, xs);
        return l;
    }

    /** Output bounds for one request (the catalog's, possibly tightened by the request). */
    public static final class Limits {
        public final int maxSections, maxItemsPerSection, maxTotalItems, deadlineMs;

        public Limits(int maxSections, int maxItemsPerSection, int maxTotalItems, int deadlineMs) {
            this.maxSections = maxSections;
            this.maxItemsPerSection = maxItemsPerSection;
            this.maxTotalItems = maxTotalItems;
            this.deadlineMs = deadlineMs;
        }

        int get(String k) {
            if ("maxSections".equals(k)) return maxSections;
            if ("maxItemsPerSection".equals(k)) return maxItemsPerSection;
            if ("maxTotalItems".equals(k)) return maxTotalItems;
            if ("deadlineMs".equals(k)) return deadlineMs;
            throw new IllegalArgumentException();
        }

        static boolean has(String k) {
            return "maxSections".equals(k) || "maxItemsPerSection".equals(k) || "maxTotalItems".equals(k)
                    || "deadlineMs".equals(k);
        }

        Limits with(String k, int v) {
            return new Limits("maxSections".equals(k) ? v : maxSections,
                    "maxItemsPerSection".equals(k) ? v : maxItemsPerSection,
                    "maxTotalItems".equals(k) ? v : maxTotalItems, "deadlineMs".equals(k) ? v : deadlineMs);
        }
    }
}
