package dev.pixel.models;

/** What still blocks Send: endpoint, model id and an access token for that endpoint's origin. */
public final class SetupCheck {
    private SetupCheck() {
    }

    /** Returns null when chat may start, otherwise the reason it may not. */
    public static String problem(String endpoint, String model, EndpointCredential credential) {
        String endpointProblem = EndpointPolicy.check(endpoint);
        if (endpointProblem != null) {
            return endpointProblem;
        }
        if (model == null || model.trim().isEmpty()) {
            return "no model id is configured";
        }
        if (credential == null) {
            return "no access token is set for this endpoint (it is kept in memory only, so enter it after each restart)";
        }
        if (!credential.matches(endpoint)) {
            return "the access token belongs to another endpoint";
        }
        return null;
    }
}
