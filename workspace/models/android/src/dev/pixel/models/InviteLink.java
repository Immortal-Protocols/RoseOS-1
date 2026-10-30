package dev.pixel.models;

import java.util.regex.Pattern;

/**
 * The admin QR invite: exactly {@code dev.pixel.models://enroll?invite=rgi1.<43 base64url chars>}.
 * Anything else (other scheme/host/path, extra or repeated parameters, a fragment, userinfo, a port,
 * percent-encoding, or a different code shape) is rejected. The QR never sets the origin or the model.
 */
final class InviteLink {
    static final String PREFIX = "dev.pixel.models://enroll?invite=";
    private static final Pattern CODE = Pattern.compile("rgi1\\.[A-Za-z0-9_-]{43}");

    private InviteLink() {
    }

    /** The invite code of an exact invite link, or null. */
    static String parse(String uri) {
        if (uri == null || !uri.startsWith(PREFIX)) {
            return null;
        }
        String code = uri.substring(PREFIX.length());
        return isCode(code) ? code : null;
    }

    static boolean isCode(String code) {
        return code != null && CODE.matcher(code).matches();
    }
}
