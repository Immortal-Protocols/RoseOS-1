package dev.pixel.models;

/** Public service defaults (service-auth-v1). Not secrets; nothing secret ships in the APK. */
public final class ModelsService {
    public static final String ORIGIN = "https://models.a-list.com";
    public static final String CHAT_URL = ORIGIN + "/v1/chat/completions";
    public static final String CHALLENGE_URL = ORIGIN + "/v1/auth/challenge";
    public static final String TOKEN_URL = ORIGIN + "/v1/auth/token";

    /**
     * The only models the app can name: the exact served ids of the Core serving profiles. The selected id goes
     * into the local-agent-v1 config unchanged, so a reply from any other model still fails the turn.
     */
    public enum Model {
        MUSE("muse-glimmer", "Muse Glimmer", "Reasoning strength: low"),
        /** MODELS-QWEN-SERVE-001 (`serving/qwen-fp8.json`); Muse's reasoning line does not apply to it. */
        QWEN("rose-qwen", "Qwen", null);

        public final String id;
        public final String label;
        /** Operator line before the agent instructions, or null for none. */
        public final String systemPrompt;

        Model(String id, String label, String systemPrompt) {
            this.id = id;
            this.label = label;
            this.systemPrompt = systemPrompt;
        }

        /** The model with exactly this served id; anything else (including null) is the default. */
        public static Model fromId(String id) {
            for (Model m : values()) {
                if (m.id.equals(id)) {
                    return m;
                }
            }
            return DEFAULT;
        }
    }

    /**
     * Qwen by default for the install-open-chat demo (0.4): the service slot serves rose-qwen, so the app needs no
     * setting. This supersedes 0.3's Muse default; Muse stays one explicit Settings choice away.
     */
    public static final Model DEFAULT = Model.QWEN;

    private ModelsService() {
    }
}
