package dev.pixel.models;

/** Public service defaults (service-auth-v1). Not secrets; nothing secret ships in the APK. */
public final class ModelsService {
    public static final String ORIGIN = "https://models.a-list.com";
    public static final String CHAT_URL = ORIGIN + "/v1/chat/completions";
    public static final String CHALLENGE_URL = ORIGIN + "/v1/auth/challenge";
    public static final String TOKEN_URL = ORIGIN + "/v1/auth/token";
    public static final String MODEL = "muse-glimmer";

    private ModelsService() {
    }
}
