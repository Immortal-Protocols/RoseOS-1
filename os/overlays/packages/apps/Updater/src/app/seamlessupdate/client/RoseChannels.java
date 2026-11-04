package app.seamlessupdate.client;

import java.util.Set;

/**
 * RoseOS release channels, kept free of Android types so the rules can be tested offline.
 *
 * Each choice has a semantic label, a persisted value (the "channel" preference) and the wire
 * channel the Updater requests as DEVICE-channel, whose metadata must name the same channel:
 *
 *   label  persisted  wire
 *   Prod   prod       prod     (default for new installations)
 *   Beta   stable     stable   (the existing Pixel feed, frankel-stable; kept for existing phones)
 *   Alpha  alpha      alpha
 *
 * Which wire channels are actually published is a build-time fact supplied by the release
 * publisher; an unpublished channel is reported as unavailable and never fetched.
 */
final class RoseChannels {
    static final String PROD = "prod";
    static final String LEGACY_BETA = "stable";
    static final String ALPHA = "alpha";
    static final String SECURITY_PREVIEW_SUFFIX = "-security-preview";
    /** Set on the first boot of a build carrying these rules, so migration runs at most once. */
    static final String KEY_MIGRATED = "rose_channel_migrated";

    private RoseChannels() {}

    /**
     * The channel to store on boot, or null to store nothing. A missing "channel" key alone does
     * not mean a new installation: the stock Updater only stores it once its settings screen is
     * opened. A phone that reaches these rules through an OTA keeps the existing Beta feed; a new
     * installation keeps no stored value and so follows the Prod default. Once the marker is set,
     * later OTAs never move a phone.
     */
    static String migration(boolean hasStoredChannel, boolean migratedBefore, boolean firstBootAfterOta) {
        if (hasStoredChannel || migratedBefore || !firstBootAfterOta) return null;
        return LEGACY_BETA;
    }

    /** Preserve an explicit choice; setup may opt in by default only to a published preview feed. */
    static boolean securityPreviewAtSetup(int stored, String baseChannel, Set<String> published) {
        if (stored == 0) return false;
        if (stored == 1) return true;
        return published.contains(baseChannel + SECURITY_PREVIEW_SUFFIX);
    }

    /** A normal feed the user can explicitly return to from an unavailable preview variant. */
    static String publishedBaseOfPreview(String channel, Set<String> published) {
        if (channel == null || !channel.endsWith(SECURITY_PREVIEW_SUFFIX)) return null;
        String base = channel.substring(0, channel.length() - SECURITY_PREVIEW_SUFFIX.length());
        return published.contains(base) ? base : null;
    }

    /**
     * Whether to request DEVICE-channel. An explicit sys.update.channel override keeps its stock
     * behaviour; otherwise only a published channel is fetched.
     */
    static boolean shouldFetch(String override, String channel, Set<String> published) {
        return (override != null && !override.isEmpty()) || published.contains(channel);
    }
}
