package rose.models.composer;

import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** rose-layout-v1 (PROPOSAL) request and snapshot checks. Mirrors {@code composer/contract.py} exactly. */
public final class Contract {
    private Contract() {}

    public static final String CONTRACT = "rose-layout-v1";
    public static final List<String> TRIGGERS = Arrays.asList("button", "direct_text", "conversational");
    public static final List<String> TIMES_OF_DAY = Arrays.asList("morning", "afternoon", "evening", "night");
    /** Snapshot list name to alias prefix, in selection order. */
    public static final List<String> KINDS = Arrays.asList("apps", "notifications", "content", "actions");
    public static final List<String> ACTION_KINDS = Arrays.asList(
            "launch", "shortcut", "deeplink", "notification_action", "app_function", "agent_task");
    public static final int MAX_PROMPT_CHARS = 500, MAX_LABEL_CHARS = 60, MAX_REQUEST_ID_CHARS = 64;
    public static final long MAX_RECENT_RANK = 1000000L;
    public static final int LAYOUT_TITLE_CHARS = 40, SECTION_TITLE_CHARS = 30;

    static String prefix(String kind) {
        if ("apps".equals(kind)) return "a";
        if ("notifications".equals(kind)) return "n";
        if ("content".equals(kind)) return "c";
        return "x";
    }

    /** The request's effective limits, or Refused(bad_request | catalog_mismatch). */
    public static Catalog.Limits checkRequest(Object request, Catalog catalog) throws Refused {
        Map<String, Object> req = Json.asMap(request);
        if (req == null || !CONTRACT.equals(req.get("contract")) || !TRIGGERS.contains(req.get("trigger"))) {
            throw new Refused("bad_request");
        }
        Object rid = req.get("requestId");
        if (!Json.isString(rid) || Text.len((String) rid) == 0 || Text.len((String) rid) > MAX_REQUEST_ID_CHARS) {
            throw new Refused("bad_request");
        }
        Object prompt = req.get("prompt");
        if ("button".equals(req.get("trigger"))) {
            if (!(Json.isNull(prompt) || "".equals(prompt))) throw new Refused("bad_request");
        } else if (!Json.isString(prompt) || Text.strip((String) prompt).isEmpty()
                || Text.len((String) prompt) > MAX_PROMPT_CHARS) {
            throw new Refused("bad_request");
        }
        Map<String, Object> snap = Json.asMap(req.get("snapshot"));
        if (snap == null || !Json.isString(snap.get("snapshotVersion"))) throw new Refused("bad_request");
        if (!catalog.version.equals(req.get("catalogVersion")) || !catalog.version.equals(snap.get("catalogVersion"))) {
            throw new Refused("catalog_mismatch");
        }
        Catalog.Limits limits = catalog.limits;
        Object asked = req.get("limits");
        if (!Json.isNull(asked) && Json.asMap(asked) == null) throw new Refused("bad_request");
        if (Json.asMap(asked) != null) {
            for (Map.Entry<String, Object> e : Json.asMap(asked).entrySet()) {
                Object v = e.getValue();
                if (!Catalog.Limits.has(e.getKey()) || !(v instanceof Long)) throw new Refused("bad_request");
                long n = (Long) v;
                if (n <= 0 || n > catalog.limits.get(e.getKey())) throw new Refused("bad_request");
                limits = limits.with(e.getKey(), (int) n);
            }
        }
        checkSnapshot(snap);
        return limits;
    }

    /** Unique string ids, bounded labels, string-typed optional fields, known action kinds and targets. */
    public static void checkSnapshot(Map<String, Object> snap) throws Refused {
        Object tod = snap.get("timeOfDay");
        if (!Json.isNull(tod) && !TIMES_OF_DAY.contains(tod)) throw new Refused("bad_request");
        Object pinnedObj = snap.containsKey("pinned") ? snap.get("pinned") : null;
        if (pinnedObj != null && Json.asList(pinnedObj) == null) throw new Refused("bad_request");
        Set<String> seen = new HashSet<String>();
        for (String kind : KINDS) {
            List<Object> entries = entries(snap, kind);
            for (Object o : entries) {
                Map<String, Object> e = Json.asMap(o);
                if (e == null || !Json.isString(e.get("id")) || ((String) e.get("id")).isEmpty()
                        || seen.contains(e.get("id"))) {
                    throw new Refused("bad_request");
                }
                Object label = e.containsKey("label") ? e.get("label") : e.get("title");
                if (!Json.isString(label) || ((String) label).isEmpty() || Text.len((String) label) > MAX_LABEL_CHARS) {
                    throw new Refused("bad_request");
                }
                for (String f : new String[] {"category", "kind", "appId"}) {
                    if (!Json.isNull(e.get(f)) && !Json.isString(e.get(f))) throw new Refused("bad_request");
                }
                Object rank = e.get("recentRank");
                if (!Json.isNull(rank) && !(rank instanceof Long && (Long) rank >= 0 && (Long) rank <= MAX_RECENT_RANK)) {
                    throw new Refused("bad_request");
                }
                seen.add((String) e.get("id"));
            }
        }
        for (Object o : entries(snap, "actions")) {
            Map<String, Object> a = Json.asMap(o);
            if (!Json.isString(a.get("targetId")) || !seen.contains(a.get("targetId"))
                    || !ACTION_KINDS.contains(a.get("kind"))) {
                throw new Refused("bad_request");
            }
            if ("agent_task".equals(a.get("kind")) && !Boolean.TRUE.equals(a.get("requiresConfirmation"))) {
                throw new Refused("bad_request"); // a Rose task always asks first
            }
        }
        List<Object> pinned = pinnedObj == null ? null : Json.asList(pinnedObj);
        if (pinned != null) {
            for (Object p : pinned) if (!Json.isString(p) || !seen.contains(p)) throw new Refused("bad_request");
        }
    }

    /** The snapshot's list for {@code kind}; Refused(bad_request) if present and not a list. */
    static List<Object> entries(Map<String, Object> snap, String kind) throws Refused {
        if (!snap.containsKey(kind)) return java.util.Collections.emptyList();
        List<Object> l = Json.asList(snap.get(kind));
        if (l == null) throw new Refused("bad_request");
        return l;
    }
}
