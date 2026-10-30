package app.seamlessupdate.client;

import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Device-qualified release discovery for a device whose builds are published as separate releases of one
 * repository (Rango on Immortal-Protocols/pixel-ota), where /releases/latest names another device. Kept free of
 * Android types so the rules can be tested offline; Service parses the public GitHub release list into Release.
 *
 * The Updater keeps its own contract unchanged: it fetches DEVICE-channel and DEVICE-ota_update-N.zip (or an
 * incremental) from one base URL. This chooses that base as url + tag + "/" for exactly one release, so the
 * metadata and the OTA come from the SAME release. Eligible: published (not a draft), tagged DEVICE-N (N the
 * ten-digit build), newer than the running build, with exactly one uploaded DEVICE-channel and one uploaded
 * DEVICE-ota_update-N.zip. Anything else (another device, an incomplete or ambiguous release, a list that may
 * continue past the pages read) is never chosen, and nothing falls back to another device or feed. Metadata,
 * package verification and signature checks still run as before; discovery authorizes nothing.
 */
final class RoseReleases {
    static final int PER_PAGE = 100, MAX_PAGES = 3;

    static final class Asset {
        final String name, state;
        Asset(String name, String state) { this.name = name; this.state = state; }
    }

    static final class Release {
        final String tag;
        final boolean draft;
        final List<Asset> assets;
        Release(String tag, boolean draft, List<Asset> assets) { this.tag = tag; this.draft = draft; this.assets = assets; }
    }

    /** The chosen release (its tag), none newer, or a reason nothing can be decided. */
    static final class Choice {
        final String tag, error;
        Choice(String tag, String error) { this.tag = tag; this.error = error; }
        boolean upToDate() { return tag == null && error == null; }
    }

    private RoseReleases() {}

    static String page(String index, int page) {
        return index + "?per_page=" + PER_PAGE + "&page=" + page;
    }

    /** The ten-digit build of an eligible release, or -1. */
    static long eligible(Release r, String device, String wireChannel) {
        if (r == null || r.draft || r.tag == null || r.assets == null) return -1;
        Matcher m = Pattern.compile(Pattern.quote(device) + "-([0-9]{10})").matcher(r.tag);
        if (!m.matches()) return -1;
        long build = Long.parseLong(m.group(1));
        String metadata = device + "-" + wireChannel, ota = device + "-ota_update-" + m.group(1) + ".zip";
        int metadataCount = 0, otaCount = 0;
        for (Asset a : r.assets) {
            if (a == null || a.name == null) continue;
            if (a.name.equals(metadata)) { if (!"uploaded".equals(a.state)) return -1; metadataCount++; }
            if (a.name.equals(ota)) { if (!"uploaded".equals(a.state)) return -1; otaCount++; }
        }
        return metadataCount == 1 && otaCount == 1 ? build : -1;
    }

    /**
     * The newest eligible release newer than the running build. complete: the pages read reached the end of
     * the list. A newer build must not be hidden behind an unread page, so an incomplete list decides nothing.
     */
    static Choice choose(List<Release> releases, boolean complete, String device, String wireChannel, String runningBuild) {
        if (device == null || !device.matches("[a-z0-9_]{1,32}")) return new Choice(null, "unknown device");
        if (wireChannel == null || !wireChannel.matches("[a-z0-9][a-z0-9-]{0,31}")) return new Choice(null, "unknown channel");
        long running = runningBuild != null && runningBuild.matches("[0-9]{10}") ? Long.parseLong(runningBuild) : -1;
        long best = -1;
        String tag = null;
        for (Release r : releases) {
            long build = eligible(r, device, wireChannel);
            if (build > best) { best = build; tag = r.tag; }
        }
        if (!complete) return new Choice(null, "release list longer than " + MAX_PAGES * PER_PAGE + " entries");
        if (tag == null || best <= running) return new Choice(null, null);
        return new Choice(tag, null);
    }

    /** The per-release base under the configured download prefix (…/releases/download/). */
    static String base(String downloadPrefix, String tag) {
        return downloadPrefix + tag + "/";
    }
}
