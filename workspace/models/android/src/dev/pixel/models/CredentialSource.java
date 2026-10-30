package dev.pixel.models;

/**
 * Supplies the bearer for inference. Called on the network worker; may block on the network. A
 * call aborts with {@link AuthFailure} when it cannot supply a credential or {@code cancel} is set.
 */
public interface CredentialSource {
    /** Cooperative cancellation for one dispatch (Stop, turn end, activity destroyed). */
    interface Cancel {
        boolean isCancelled();

        /** Registers the open connection so cancel can disconnect it; null clears it. */
        void track(java.net.HttpURLConnection connection);
    }

    /** A valid credential, renewing or enrolling first when needed. */
    EndpointCredential current(Cancel cancel) throws AuthFailure;

    /** A freshly renewed credential (after the endpoint answered 401). */
    EndpointCredential renew(Cancel cancel) throws AuthFailure;

    /**
     * Why no credential could be supplied. For the agent it is either the auth server's HTTP status
     * (401, 403, 429, 503) or a transport kind; {@code reason} is a fixed code for the UI.
     */
    final class AuthFailure extends Exception {
        final int status;
        final String transportKind;
        final String reason;

        AuthFailure(int status, String transportKind, String reason) {
            super(reason);
            this.status = status;
            this.transportKind = transportKind;
            this.reason = reason;
        }

        static AuthFailure status(int status, String reason) {
            return new AuthFailure(status, null, reason);
        }

        static AuthFailure transport(String kind) {
            return new AuthFailure(0, kind, kind);
        }

        static AuthFailure cancelled() {
            return new AuthFailure(0, null, "cancelled");
        }

        boolean isCancelled() {
            return "cancelled".equals(reason);
        }
    }
}
