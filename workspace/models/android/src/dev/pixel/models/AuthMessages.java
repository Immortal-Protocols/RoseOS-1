package dev.pixel.models;

/** User-facing text for DeviceAuth's fixed reason codes. Never contains a token or server text. */
final class AuthMessages {
    private AuthMessages() {
    }

    static String text(String reason) {
        switch (reason) {
            case "auth_revoked":
                return "This phone's sign-in was revoked. Settings → Reset sign-in to enroll again.";
            case "auth_refused":
                return "The Muse Glimmer service is not accepting this phone right now. Settings → Reset sign-in to try again.";
            case "auth_rate_limited":
                return "The service is busy signing in phones. Try again shortly.";
            case "auth_unavailable":
            case "connect":
            case "timeout_connect":
            case "timeout_response":
                return "The Muse Glimmer service could not be reached. Try again shortly.";
            case "tls":
                return "The service's secure connection could not be verified.";
            case "auth_key_unavailable":
                return "This phone's secure key store is unavailable.";
            default:
                return "Signing in to the Muse Glimmer service failed.";
        }
    }
}
