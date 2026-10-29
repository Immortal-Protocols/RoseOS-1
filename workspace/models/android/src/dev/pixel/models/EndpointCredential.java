package dev.pixel.models;

import java.util.Locale;

/**
 * A bearer token bound to one HTTPS origin (scheme, host and port). It lives only in process
 * memory: it is never written to SharedPreferences, files, logs or artifacts, and toString shows
 * only the origin and a mask. A request goes out with it only when its URL has the same origin.
 */
public final class EndpointCredential {
    static final int MAX_TOKEN_CHARS = 8192;

    private final String origin;
    private final String token;

    private EndpointCredential(String origin, String token) {
        this.origin = origin;
        this.token = token;
    }

    /** Binds token to the origin of endpoint. Throws IllegalArgumentException without echoing it. */
    public static EndpointCredential bind(String endpoint, String token) {
        String origin = origin(endpoint);
        if (origin == null) {
            throw new IllegalArgumentException("the endpoint is not a valid https:// URL");
        }
        if (token == null || token.isEmpty()) {
            throw new IllegalArgumentException("the access token is empty");
        }
        if (token.length() > MAX_TOKEN_CHARS) {
            throw new IllegalArgumentException("the access token is longer than " + MAX_TOKEN_CHARS + " characters");
        }
        for (int i = 0; i < token.length(); i++) {
            char c = token.charAt(i);
            if (c <= 0x20 || c >= 0x7f) {
                throw new IllegalArgumentException("the access token contains spaces, control or non-ASCII characters");
            }
        }
        return new EndpointCredential(origin, token);
    }

    /** "https://host[:port]" with a lower-case host and the default port omitted; null if not https. */
    static String origin(String url) {
        if (url == null || EndpointPolicy.check(url) != null) {
            return null;
        }
        String authority = url.substring("https://".length()).split("[/?#]", 2)[0].toLowerCase(Locale.ROOT);
        if (authority.endsWith(":443")) {
            authority = authority.substring(0, authority.length() - 4);
        }
        return "https://" + authority;
    }

    public String origin() {
        return origin;
    }

    public boolean matches(String url) {
        return origin.equals(origin(url));
    }

    /** The Authorization header value. Only HttpsInferenceClient calls this. */
    String authorizationHeader() {
        return "Bearer " + token;
    }

    @Override
    public String toString() {
        return "EndpointCredential{" + origin + ", token=<redacted>}";
    }
}
