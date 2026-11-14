package rose.models.composer;

import java.util.HashMap;
import java.util.Map;

/** The text the model sees. Mirrors {@code composer/prompt.py} character for character. */
public final class Prompt {
    private Prompt() {}

    public static final String SYSTEM = "You arrange a phone page from the items listed below. Reply with one JSON object only: "
            + "{\"title\": short page title, \"sections\": [{\"component\", \"title\", \"items\": [ids], \"actions\": [ids]}]}. "
            + "Use only the listed ids. Prefer few, relevant sections. Put the most useful section first.";

    private static final Map<String, String> COMPONENT_HELP = new HashMap<String, String>();
    static {
        COMPONENT_HELP.put("grid", "grid: 2-8 apps or actions");
        COMPONENT_HELP.put("carousel", "carousel: 2-8 content, notifications or apps");
        COMPONENT_HELP.put("list", "list: 1-8 notifications or content");
        COMPONENT_HELP.put("card", "card: 1 notification or content, plus up to 2 actions marked \"-> its id\"");
        COMPONENT_HELP.put("button", "button: 1 action");
    }

    /** The user message for one request (the system message is {@link #SYSTEM}). */
    public static String user(ContextSelector.Context ctx, Catalog catalog, String prompt, Object timeOfDay) {
        StringBuilder b = new StringBuilder("Components:\n");
        boolean first = true;
        for (String c : catalog.components.keySet()) {
            if (!first) b.append('\n');
            first = false;
            b.append(COMPONENT_HELP.containsKey(c) ? COMPONENT_HELP.get(c) : c);
        }
        b.append("\n\nItems:\n");
        for (int k = 0; k < ctx.lines.size(); k++) {
            if (k > 0) b.append('\n');
            b.append(ctx.lines.get(k));
        }
        String ask = prompt != null && !prompt.isEmpty() ? Text.strip(prompt)
                : "No request was typed. Make the best page for "
                + (timeOfDay instanceof String && !((String) timeOfDay).isEmpty() ? (String) timeOfDay : "now")
                + " from what is listed.";
        return b.append("\n\nRequest: ").append(ask).toString();
    }
}
