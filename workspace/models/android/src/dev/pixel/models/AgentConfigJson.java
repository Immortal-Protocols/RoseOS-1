package dev.pixel.models;

/** Builds the local-agent-v1 config from the user's settings and locally resolved allowlist. */
final class AgentConfigJson {
    private AgentConfigJson() {
    }

    /** The selected service model: its exact id and its own operator line (Muse: `Reasoning strength: low`). */
    static String build(ModelsService.Model model, AppAllowlist allowlist) {
        return build(model.id, model.systemPrompt, allowlist);
    }

    static String build(String model, String systemPrompt, AppAllowlist allowlist) {
        StringBuilder apps = new StringBuilder();
        for (AppAllowlist.Entry e : allowlist.entries()) {
            apps.append(apps.length() == 0 ? "" : ",")
                    .append("{\"app_id\":").append(Json.quote(e.appId))
                    .append(",\"label\":").append(Json.quote(label(e.label))).append('}');
        }
        return "{\"contract\":\"local-agent-v1\",\"model\":" + Json.quote(model)
                + (systemPrompt == null ? "" : ",\"system_prompt\":" + Json.quote(systemPrompt))
                + ",\"allowlist\":[" + apps + "]}";
    }

    /** Core accepts 1-64 printable characters. */
    static String label(String raw) {
        StringBuilder out = new StringBuilder();
        int count = 0;
        for (int k = 0; k < raw.length() && count < 64; count++) {
            int cp = raw.codePointAt(k);
            out.appendCodePoint(Character.isISOControl(cp) ? ' ' : cp);
            k += Character.charCount(cp);
        }
        String s = out.toString().trim();
        return s.isEmpty() ? "App" : s;
    }
}
