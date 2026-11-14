package rose.models.composer;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** The model selects a short list of useful names and native display styles; IDs remain behind the registry. */
final class PlacementPlan {
    static final int MAX_CHOICES = 24;
    static final String SYSTEM = "Choose the most useful available items for the user's phone page. "
            + "Select at most 8 items directly relevant to the request. Omit unrelated items. "
            + "Use exact item names in the listed order. Give the page a short title. "
            + "When no request is typed, prefer earlier listed items: they reflect time and recency. "
            + "appsLayout is grid or carousel. contentLayout is card, list or carousel. "
            + "Reply in this shape: {\"title\":\"short title\",\"items\":[\"exact item name\"],"
            + "\"appsLayout\":\"grid\",\"contentLayout\":\"carousel\"}.";
    final ContextSelector.Context context;
    final Map<String, String> aliases = new LinkedHashMap<String, String>();
    final String user, grammar;
    final String ask;
    final int maxTokens = 320;

    PlacementPlan(ContextSelector.Context context, String originalUser) {
        this.context = context;
        StringBuilder prompt = new StringBuilder("Available items:\n");
        int i = 0;
        for (Map.Entry<String, String> entry : context.kinds.entrySet()) {
            if (i == MAX_CHOICES) break;
            String line = context.lines.get(i++);
            String label = line.substring(entry.getKey().length() + 1);
            int targetAt = label.lastIndexOf(" -> "); if (targetAt >= 0) label = label.substring(0, targetAt);
            int categoryAt = label.lastIndexOf(" [");
            String category = categoryAt >= 0 ? label.substring(categoryAt) : "";
            if (categoryAt >= 0) label = label.substring(0, categoryAt);
            String name = label;
            if (aliases.containsKey(name)) name += " (" + entry.getKey() + ")";
            aliases.put(name, entry.getKey());
            prompt.append(Json.write(name)).append(" : ").append(entry.getValue()).append(category).append('\n');
        }
        List<String> names = new ArrayList<String>(aliases.keySet());
        StringBuilder rules = new StringBuilder(); listGrammar(rules, names, 8);
        grammar = "root ::= " + literal("{") + " ws " + literal("\"title\"") + " ws "
                + literal(":") + " ws " + literal("\"") + " " + Grammar.TITLE_CHAR + "{1,40} " + literal("\"")
                + " ws " + literal(",") + " ws " + literal("\"items\"") + " ws " + literal(":") + " ws items"
                + " ws " + literal(",") + " ws " + literal("\"appsLayout\"") + " ws " + literal(":")
                + " ws (" + literal("\"grid\"") + " | " + literal("\"carousel\"") + ")"
                + " ws " + literal(",") + " ws " + literal("\"contentLayout\"") + " ws " + literal(":")
                + " ws (" + literal("\"card\"") + " | " + literal("\"list\"") + " | " + literal("\"carousel\"") + ")"
                + " ws " + literal("}") + "\n" + rules + "ws ::= [ \\t\\n\\r]{0,4}\n";
        int requestAt = originalUser.lastIndexOf("\n\nRequest: ");
        ask = requestAt >= 0 ? originalUser.substring(requestAt + "\n\nRequest: ".length()) : originalUser;
        prompt.append("\nRequest: ").append(ask);
        user = prompt.toString();
    }

    private static String literal(String s) { return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\""; }

    // A list can move only forward through names: duplicate IDs are impossible while decoding.
    private static void listGrammar(StringBuilder out, List<String> names, int cap) {
        out.append("items ::= ").append(literal("[")).append(" ws ").append(literal("]"));
        if (!names.isEmpty()) out.append(" | ").append(literal("[")).append(" ws i-0-").append(cap).append(" ws ").append(literal("]"));
        out.append('\n');
        for (int index = 0; index < names.size(); index++) for (int left = 1; left <= cap; left++) {
            out.append("i-").append(index).append('-').append(left).append(" ::= ").append(literal(Json.write(names.get(index))));
            if (index + 1 < names.size() && left > 1) out.append(" (ws ").append(literal(",")).append(" ws i-").append(index + 1).append('-').append(left - 1).append(")?");
            if (index + 1 < names.size()) out.append(" | i-").append(index + 1).append('-').append(left);
            out.append('\n');
        }
    }

    String layout(String text) throws Refused {
        Map<String, Object> plan;
        try { plan = Json.asMap(Json.parse(text)); } catch (Json.ParseError | RuntimeException e) { throw new Refused("invalid_output"); }
        if (plan == null || plan.size() != 4 || !(plan.get("title") instanceof String)) throw new Refused("invalid_output");
        if (!Arrays.asList("grid", "carousel").contains(plan.get("appsLayout"))
                || !Arrays.asList("card", "list", "carousel").contains(plan.get("contentLayout"))) throw new Refused("invalid_output");
        List<Object> raw = Json.asList(plan.get("items"));
        if (raw == null || raw.size() > 8) throw new Refused("invalid_output");
        Map<String,List<String>> groups = new LinkedHashMap<String,List<String>>();
        for (String kind : Arrays.asList("apps", "notifications", "content", "actions")) groups.put(kind,new ArrayList<String>());
        Set<String> seen = new HashSet<String>();
        List<String> selected = new ArrayList<String>();
        for (Object name : raw) {
            String alias = aliases.get(name);
            if (alias == null || !seen.add(alias)) throw new Refused("invalid_output");
            selected.add(alias);
        }
        // Preserve explicit item requests even when the small model omits them. Every anchor is an offered
        // alias, never an invented capability. The model still chooses semantic matches and display styles.
        List<String> ordered = new ArrayList<String>();
        if (!ask.startsWith("No request was typed.")) {
            Set<String> words = normalized(ask);
            for (Map.Entry<String,String> entry : aliases.entrySet()) {
                String kind = context.kinds.get(entry.getValue());
                Set<String> label = normalized(entry.getKey()); label.retainAll(words);
                if (!label.isEmpty() || ("notifications".equals(kind) && words.contains("notification"))) ordered.add(entry.getValue());
            }
        }
        for (String alias : selected) if (!ordered.contains(alias)) ordered.add(alias);
        int total = 0;
        for (String alias : ordered) {
            if (total++ == 8) break;
            groups.get(context.kinds.get(alias)).add(alias);
        }
        List<Map<String,Object>> sections = new ArrayList<Map<String,Object>>();
        List<String> apps = groups.get("apps"), actions = groups.get("actions");
        if (apps.size() == 1) {
            // cat-1 allows direct actions in a grid; preserve a single selected app together with one.
            for (String action : new ArrayList<String>(actions)) if (!"agent_task".equals(context.actionKinds.get(action))) {
                apps.add(action); actions.remove(action); break;
            }
        }
        if (apps.size() >= 2) sections.add(section(apps.get(1).startsWith("x") ? "grid" : (String)plan.get("appsLayout"), "Apps", apps));
        List<String> notes = groups.get("notifications");
        if (!notes.isEmpty()) sections.add(section("list", "Notifications", notes));
        List<String> content = groups.get("content");
        if (!content.isEmpty()) {
            String display = (String)plan.get("contentLayout");
            if (content.size() == 1) display = "card";
            if ("card".equals(display) && sections.size() + content.size() + (actions.isEmpty()?0:1) > 6) display = "list";
            if ("card".equals(display)) for (String id : content) sections.add(section(display,"For you",Arrays.asList(id)));
            else sections.add(section(display,"For you",content));
        }
        if (!actions.isEmpty()) sections.add(section("button","Quick actions",actions.subList(0,Math.min(4,actions.size()))));
        if (sections.isEmpty()) throw new Refused("empty_context");
        Map<String,Object> result = new LinkedHashMap<String,Object>();
        result.put("title",plan.get("title"));result.put("sections",sections);return Json.write(result);
    }

    private static Set<String> normalized(String text) {
        Set<String> result = new HashSet<String>();
        Set<String> stop = new HashSet<String>(Arrays.asList("a","an","the","i","me","my","for","to","and","or","in","on","with","of","is","it","be","do","show","use","want","help","please","button","card","list","carousel","grid","app","content"));
        for (String word : ContextSelector.words(text)) {
            if (word.length() > 3 && word.endsWith("s")) word = word.substring(0,word.length()-1);
            if (word.length() > 1 && !stop.contains(word)) result.add(word);
        }
        return result;
    }

    private static Map<String,Object> section(String component,String title,List<String> ids) {
        Map<String,Object> s=new LinkedHashMap<String,Object>();s.put("component",component);s.put("title",title);s.put("items",ids);return s;
    }
}
