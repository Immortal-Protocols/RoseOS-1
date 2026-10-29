package dev.pixel.models;

/**
 * The one access token for this process, held in memory only. It is gone when the process exits,
 * when the user clears it, or when the endpoint changes to another origin.
 */
public final class CredentialStore {
    private static EndpointCredential current;

    private CredentialStore() {
    }

    public static synchronized void set(EndpointCredential credential) {
        current = credential;
    }

    public static synchronized void clear() {
        current = null;
    }

    /** The credential for this endpoint's origin, or null; a credential for another origin is dropped. */
    public static synchronized EndpointCredential forEndpoint(String endpoint) {
        if (current != null && !current.matches(endpoint)) {
            current = null;
        }
        return current;
    }

    public static synchronized boolean isSet() {
        return current != null;
    }
}
