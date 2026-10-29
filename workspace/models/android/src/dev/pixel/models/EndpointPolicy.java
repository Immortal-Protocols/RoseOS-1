package dev.pixel.models;

/** Inference endpoint rules for a phone: HTTPS only, no embedded credentials. */
public final class EndpointPolicy {
    private EndpointPolicy() {
    }

    /** Returns null when the URL is acceptable, otherwise the reason it is not. */
    public static String check(String url) {
        if (url == null || url.trim().isEmpty()) {
            return "no inference endpoint is configured";
        }
        if (!url.equals(url.trim()) || url.matches(".*\\s.*")) {
            return "the endpoint URL contains whitespace";
        }
        if (!url.startsWith("https://")) {
            return "the endpoint must use https://; cleartext is not allowed on the phone";
        }
        String authority = url.substring("https://".length()).split("[/?#]", 2)[0];
        if (authority.isEmpty()) {
            return "the endpoint URL has no host";
        }
        if (authority.contains("@")) {
            return "the endpoint URL must not embed credentials";
        }
        return null;
    }
}
