package dev.pixel.voice;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** APP-GITHUB-002. Fixtures only: tags, versions, hashes, codenames and builds are invented for the test. They are
 * not published releases or a claim that any update exists or is eligible. No network is used; the scripted
 * server answers only GitHub URLs, so any PixelVoice request would fail the test. */
public final class DirectUpdateTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static final String SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90";
    static final String SIGNER = "650d47f91b7f5af3c530fef53fe92018aa4521efba065bcb1d6fc9b699060bfb";
    static final String SIG_SHA = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0";
    static final String CDN = "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/dc8dfae4-58f4-44d1-b98d-0879fbb4cbda";

    static class Server implements PublicApkDownload.Http {
        final Map<String, Object[]> routes = new HashMap<>(); final List<String> requested = new ArrayList<>(); final List<Map<String, String>> headers = new ArrayList<>();
        Server on(String url, int status, String location, String body) { routes.put(url, new Object[] {status, location, body}); return this; }
        public PublicApkDownload.Response get(String url, Map<String, String> h) throws IOException {
            requested.add(url); headers.add(new HashMap<>(h));
            Object[] r = routes.get(url); if (r == null) throw new IOException("unscripted " + url);
            String body = (String) r[2];
            return new PublicApkDownload.Response((Integer) r[0], (String) r[1], body == null ? null : new ByteArrayInputStream(body.getBytes("UTF-8")));
        }
    }
    static String release(String tag, boolean draft, boolean pre, String... assets) {
        StringBuilder a = new StringBuilder();
        for (String n : assets) a.append(a.length() == 0 ? "" : ",").append("{\"name\":\"").append(n).append("\",\"state\":\"uploaded\",\"browser_download_url\":\"https://evil.example/").append(n).append("\"}");
        return "{\"tag_name\":\"" + tag + "\",\"draft\":" + draft + ",\"prerelease\":" + pre + ",\"assets\":[" + a + "]}";
    }
    static String app(long n) { return release("rose-agent-" + n, false, true, "dev.pixel.voice-" + n + ".apk", "dev.pixel.voice-" + n + ".receipt.json", "dev.pixel.voice-" + n + ".apk.idsig", "SHA256SUMS"); }
    /** A release as the current (schema 1) producer publishes it: no .idsig asset. */
    static String appWithoutIdsig(long n) { return release("rose-agent-" + n, false, true, "dev.pixel.voice-" + n + ".apk", "dev.pixel.voice-" + n + ".receipt.json", "SHA256SUMS"); }
    static String v4(long n) { return ",\"v4Signature\":{\"asset\":\"dev.pixel.voice-" + n + ".apk.idsig\",\"size\":2048,\"sha256\":\"" + SIG_SHA + "\"}"; }
    static String compat(String inner) { return "\"compatibility\":{" + inner + "}"; }
    static final String GOOD_COMPAT = "\"deviceCodenames\":[\"frankel\"],\"abis\":[\"arm64-v8a\"],\"minSdk\":36,\"minOsBuild\":\"2026102805\",\"minControllerVersion\":28";
    /** Schema 2 (APP-IDSIG-1030-M004): schema 1 plus the required v4Signature for the same release. */
    static String receipt(long n, String extra, String compatibility) { return receipt(2, n, v4(n) + extra, compatibility); }
    /** Schema 1, the current producer's shape: readable, but never offered for a Rose OS self-install. */
    static String receipt1(long n, String extra, String compatibility) { return receipt(1, n, extra, compatibility); }
    static String receipt(int schema, long n, String extra, String compatibility) {
        return "{\"receiptType\":\"app-publication\",\"schemaVersion\":" + schema + ",\"packageName\":\"dev.pixel.voice\",\"versionCode\":" + n + ",\"versionName\":\"" + n + "-app-beta\","
            + "\"size\":4096,\"sha256\":\"" + SHA + "\",\"signerSha256\":\"" + SIGNER + "\",\"candidate\":\"0000000000000000000000000000000000000000\","
            + "\"minimumTestedOs\":{\"build\":\"2026102805\",\"controller\":28},\"repository\":\"Immortal-Protocols/pixel-ota\",\"tag\":\"rose-agent-" + n + "\","
            + "\"asset\":\"dev.pixel.voice-" + n + ".apk\",\"apkUrl\":\"https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-" + n + "/dev.pixel.voice-" + n + ".apk\","
            + "\"prerelease\":true" + extra + (compatibility == null ? "" : "," + compatibility) + "}";
    }
    static String receiptUrl(long n) { return "https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-" + n + "/dev.pixel.voice-" + n + ".receipt.json"; }
    static String list(String... releases) { return "[" + String.join(",", releases) + "]"; }
    static final String OS = release("frankel-2026102805", false, false, "frankel-ota.zip");
    static DirectUpdate.Device device(long installed) {
        return new DirectUpdate.Device("frankel", Arrays.asList("arm64-v8a", "armeabi-v7a"), 36, "2026102805", 28, installed, SIGNER);
    }
    static Server published(long n, String receiptJson, String... more) {
        String[] all = new String[more.length + 2]; all[0] = OS; all[1] = app(n); System.arraycopy(more, 0, all, 2, more.length);
        return new Server().on(DirectUpdate.apiUrl(1), 200, null, list(all)).on(receiptUrl(n), 200, null, receiptJson);
    }
    static DirectUpdate.Result check(Server s, long installed) { return DirectUpdate.check(s, device(installed)); }
    static String unusable(String json, long n) {
        try { DirectUpdate.offer(MiniJson.parse(json), n); return null; } catch (IllegalArgumentException e) { return e.getMessage(); }
    }

    public static void main(String[] args) throws Exception {
        // Direct discovery to an offer, with only GitHub reachable.
        Server s = published(306, receipt(306, "", compat(GOOD_COMPAT)), app(305), release("rose-agent-307", true, true, "dev.pixel.voice-307.apk", "dev.pixel.voice-307.receipt.json"));
        DirectUpdate.Result r = check(s, 305);
        expect(r.state == DirectUpdate.State.AVAILABLE && r.offer != null && r.offer.versionCode == 306, "Newest published app release offered: " + r.message);
        expect(r.offer.sha256.equals(SHA) && r.offer.size == 4096 && r.offer.signerSha256.equals(SIGNER)
                && r.offer.apkUrl.equals("https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-306/dev.pixel.voice-306.apk"), "The offer binds hash, size, signer and the canonical APK URL");
        expect(r.offer.hasIdsig() && r.offer.idsigSha256.equals(SIG_SHA) && r.offer.idsigSize == 2048
                && r.offer.idsigUrl.equals("https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-306/dev.pixel.voice-306.apk.idsig"),
                "The offer binds the v4 signature's hash, size and canonical URL in the same release");
        expect(!s.requested.contains(r.offer.idsigUrl) && !s.requested.contains(r.offer.apkUrl), "Checking downloads neither the APK nor its signature");
        for (String u : s.requested) expect(u.startsWith("https://api.github.com/repos/Immortal-Protocols/pixel-ota/releases?") || u.startsWith("https://github.com/Immortal-Protocols/pixel-ota/"), "Only GitHub is contacted: " + u);
        expect(s.headers.get(0).size() == 1 && "application/vnd.github+json".equals(s.headers.get(0).get("Accept")), "The API request carries only an Accept header");
        expect(s.headers.get(1).isEmpty(), "The receipt request carries no headers");
        expect(!s.requested.contains("https://evil.example/dev.pixel.voice-306.receipt.json"), "API-supplied download URLs are never followed");

        // Receipt through the approved CDN redirect.
        Server viaCdn = new Server().on(DirectUpdate.apiUrl(1), 200, null, list(OS, app(306)))
                .on(receiptUrl(306), 302, CDN + "?inert=opaque-fixture", null).on(CDN + "?inert=opaque-fixture", 200, null, receipt(306, "", compat(GOOD_COMPAT)));
        expect(check(viaCdn, 305).state == DirectUpdate.State.AVAILABLE, "Receipt via the observed UUID CDN redirect");

        // Up to date, nothing published, and what discovery ignores.
        expect(check(published(305, receipt(305, "", compat(GOOD_COMPAT))), 305).state == DirectUpdate.State.NO_UPDATE, "Equal version: up to date");
        expect(check(published(304, receipt(304, "", compat(GOOD_COMPAT))), 305).state == DirectUpdate.State.NO_UPDATE, "Older version: up to date, never a downgrade");
        Server ignored = new Server().on(DirectUpdate.apiUrl(1), 200, null, list(OS,
                release("rose-agent-310", true, true, "dev.pixel.voice-310.apk", "dev.pixel.voice-310.receipt.json"),
                release("rose-agent-311", false, false, "dev.pixel.voice-311.apk", "dev.pixel.voice-311.receipt.json"),
                release("rose-agent-312", false, true, "dev.pixel.voice-312.apk"),
                release("rose-agent-313", false, true, "dev.pixel.voice-313.receipt.json"),
                release("rose-agent-0314", false, true, "dev.pixel.voice-0314.apk", "dev.pixel.voice-0314.receipt.json"),
                release("rose-agent-315-test", false, true, "dev.pixel.voice-315.apk", "dev.pixel.voice-315.receipt.json"),
                release("Rose-Agent-316", false, true, "dev.pixel.voice-316.apk", "dev.pixel.voice-316.receipt.json"),
                release("rose-agent-317", false, true, "dev.pixel.voice-318.apk", "dev.pixel.voice-318.receipt.json"),
                "{\"tag_name\":\"rose-agent-319\",\"draft\":false,\"prerelease\":true,\"assets\":[{\"name\":\"dev.pixel.voice-319.apk\",\"state\":\"starter\"},{\"name\":\"dev.pixel.voice-319.receipt.json\",\"state\":\"uploaded\"}]}"));
        r = check(ignored, 305);
        expect(r.state == DirectUpdate.State.UNAVAILABLE && ignored.requested.size() == 1, "Drafts, OS/non-pre releases, missing/unfinished assets and odd tags are never candidates: " + r.message);

        // Discovery failures are errors, never "up to date".
        for (int code : new int[] {403, 429, 500, 302, 404}) {
            Server f = new Server().on(DirectUpdate.apiUrl(1), code, "https://evil.example/", "{}");
            expect(check(f, 305).state == DirectUpdate.State.ERROR && f.requested.size() == 1, "API status " + code + " is an error and nothing is followed");
        }
        for (String bad : new String[] {"{}", "not json", "[", "[{\"tag_name\":\"rose-agent-306\",}]", "[] trailing"})
            expect(check(new Server().on(DirectUpdate.apiUrl(1), 200, null, bad), 305).state == DirectUpdate.State.ERROR, "Malformed list is an error: " + bad);
        Server offline = new Server();
        expect(check(offline, 305).state == DirectUpdate.State.ERROR, "Offline is an error");
        StringBuilder full = new StringBuilder("[");
        for (int i = 0; i < DirectUpdate.PER_PAGE; i++) full.append(i == 0 ? "" : ",").append(release("frankel-" + i, false, false));
        full.append("]");
        Server many = new Server().on(DirectUpdate.apiUrl(1), 200, null, full.toString()).on(DirectUpdate.apiUrl(2), 200, null, full.toString()).on(DirectUpdate.apiUrl(3), 200, null, full.toString());
        expect(check(many, 305).state == DirectUpdate.State.ERROR && many.requested.size() == DirectUpdate.MAX_PAGES, "A list longer than the bound is incomplete, not up to date");
        Server twoPages = new Server().on(DirectUpdate.apiUrl(1), 200, null, full.toString()).on(DirectUpdate.apiUrl(2), 200, null, list(app(306))).on(receiptUrl(306), 200, null, receipt(306, "", compat(GOOD_COMPAT)));
        expect(check(twoPages, 305).state == DirectUpdate.State.AVAILABLE, "A second page is read when the first is full");
        char[] huge = new char[DirectUpdate.MAX_PAGE_BYTES + 10]; Arrays.fill(huge, ' ');
        expect(check(new Server().on(DirectUpdate.apiUrl(1), 200, null, "[" + new String(huge) + "]"), 305).state == DirectUpdate.State.ERROR, "Oversized list is an error");

        // Receipt transport failures.
        expect(check(new Server().on(DirectUpdate.apiUrl(1), 200, null, list(app(306))).on(receiptUrl(306), 404, null, null), 305).state == DirectUpdate.State.ERROR, "Missing receipt is an error");
        expect(check(new Server().on(DirectUpdate.apiUrl(1), 200, null, list(app(306))).on(receiptUrl(306), 302, "https://evil.example/r.json", null), 305).state == DirectUpdate.State.ERROR, "Receipt redirect outside the boundary");
        char[] big = new char[DirectUpdate.MAX_RECEIPT + 1]; Arrays.fill(big, ' ');
        expect(check(new Server().on(DirectUpdate.apiUrl(1), 200, null, list(app(306))).on(receiptUrl(306), 200, null, new String(big)), 305).state == DirectUpdate.State.ERROR, "Oversized receipt");
        expect(PublicApkDownload.initialReceipt(receiptUrl(306)) == null && PublicApkDownload.initial(receiptUrl(306)) != null
                && PublicApkDownload.initialReceipt("https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-306/dev.pixel.voice-306.apk") != null,
                "Receipt and APK boundaries stay separate");

        // The receipt schema: today's publisher receipt (no schemaVersion/compatibility) is unavailable, never eligible.
        String v304shape = "{\"receiptType\":\"app-publication\",\"packageName\":\"dev.pixel.voice\",\"versionCode\":304,\"versionName\":\"304-app-beta\",\"size\":4096,\"sha256\":\"" + SHA
                + "\",\"signerSha256\":\"" + SIGNER + "\",\"candidate\":\"0000000000000000000000000000000000000000\",\"minimumTestedOs\":{\"build\":\"2026102804\",\"controller\":28},"
                + "\"repository\":\"Immortal-Protocols/pixel-ota\",\"tag\":\"rose-agent-304\",\"asset\":\"dev.pixel.voice-304.apk\",\"apkUrl\":\"https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/dev.pixel.voice-304.apk\",\"prerelease\":true}";
        r = check(published(304, v304shape), 303);
        expect(r.state == DirectUpdate.State.UNAVAILABLE && r.offer == null, "A receipt without the direct-discovery schema is unavailable: " + r.message);
        String[][] bad = {
            {receipt(306, "", compat(GOOD_COMPAT)).replace("\"schemaVersion\":2", "\"schemaVersion\":3"), "schema 3"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("\"schemaVersion\":2,", ""), "no schema"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("app-publication", "app-release"), "receipt type"},
            {receipt1(306, v4(306), compat(GOOD_COMPAT)), "a v4Signature on a schema 1 receipt"},
            {receipt(2, 306, "", compat(GOOD_COMPAT)), "schema 2 without v4Signature"},
            {receipt(2, 306, ",\"v4Signature\":null", compat(GOOD_COMPAT)), "v4Signature null"},
            {receipt(2, 306, ",\"v4Signature\":\"dev.pixel.voice-306.apk.idsig\"", compat(GOOD_COMPAT)), "v4Signature not an object"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("dev.pixel.voice-306.apk.idsig", "dev.pixel.voice-305.apk.idsig"), "sidecar for another version"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("dev.pixel.voice-306.apk.idsig", "dev.pixel.voice-306.idsig"), "sidecar name"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("dev.pixel.voice-306.apk.idsig", "dev.pixel.voice-306.apk"), "sidecar naming the APK"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("\"asset\":\"dev.pixel.voice-306.apk.idsig\"", "\"asset\":\"https://evil.example/dev.pixel.voice-306.apk.idsig\""), "sidecar as a URL"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("\"size\":2048", "\"size\":0"), "sidecar size 0"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("\"size\":2048", "\"size\":" + (DirectUpdate.MAX_IDSIG + 1)), "sidecar too large"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("\"size\":2048", "\"size\":\"2048\""), "sidecar size string"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace(SIG_SHA, SIG_SHA.toUpperCase()), "sidecar sha case"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace(SIG_SHA, SIG_SHA.substring(1)), "sidecar sha length"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("\"sha256\":\"" + SIG_SHA + "\"}", "\"sha256\":\"" + SIG_SHA + "\",\"url\":\"https://evil.example/x\"}"), "sidecar url field"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace(",\"sha256\":\"" + SIG_SHA + "\"", ""), "sidecar without hash"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("\"packageName\":\"dev.pixel.voice\"", "\"packageName\":\"dev.pixel.other\""), "package"},
            {receipt(307, "", compat(GOOD_COMPAT)), "version differs from tag"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("\"repository\":\"Immortal-Protocols/pixel-ota\"", "\"repository\":\"evil/pixel-ota\""), "repository"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("download/rose-agent-306/dev.pixel.voice-306.apk", "download/rose-agent-306/other.apk"), "apkUrl"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("\"prerelease\":true", "\"prerelease\":false"), "prerelease"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("\"size\":4096", "\"size\":0"), "size 0"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("\"size\":4096", "\"size\":4096.5"), "fractional size"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace(SHA, "XYZ"), "sha"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("\"signerSha256\":\"" + SIGNER + "\"", "\"signerSha256\":null"), "signer"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("\"versionName\":\"306-app-beta\"", "\"versionName\":\"\""), "versionName"},
            {receipt(306, "", null), "no compatibility"},
            {receipt(306, "", compat("\"deviceCodenames\":[\"frankel\"],\"abis\":[\"arm64-v8a\"],\"minSdk\":36,\"minOsBuild\":\"2026102805\"")), "missing controller key"},
            {receipt(306, "", compat(GOOD_COMPAT + ",\"maxSdk\":40")), "unknown compatibility key"},
            {receipt(306, "", compat(GOOD_COMPAT.replace("[\"frankel\"]", "[]"))), "empty codenames"},
            {receipt(306, "", compat(GOOD_COMPAT.replace("[\"frankel\"]", "[\"Frankel\"]"))), "codename case"},
            {receipt(306, "", compat(GOOD_COMPAT.replace("[\"frankel\"]", "[\"*\"]"))), "wildcard codename"},
            {receipt(306, "", compat(GOOD_COMPAT.replace("[\"arm64-v8a\"]", "[\"mips\"]"))), "unknown ABI"},
            {receipt(306, "", compat(GOOD_COMPAT.replace("[\"arm64-v8a\"]", "[\"universal\",\"arm64-v8a\"]"))), "universal mixed"},
            {receipt(306, "", compat(GOOD_COMPAT.replace("[\"arm64-v8a\"]", "[\"arm64-v8a\",\"arm64-v8a\"]"))), "duplicate ABI"},
            {receipt(306, "", compat(GOOD_COMPAT.replace("\"minSdk\":36", "\"minSdk\":\"36\""))), "minSdk string"},
            {receipt(306, "", compat(GOOD_COMPAT.replace("\"2026102805\"", "\"2805\""))), "short OS build"},
            {receipt(306, "", compat(GOOD_COMPAT.replace("\"2026102805\"", "2026102805"))), "numeric OS build"},
            {receipt(306, "", compat(GOOD_COMPAT.replace("\"minControllerVersion\":28", "\"minControllerVersion\":0"))), "controller 0"},
            {receipt(306, "", compat(GOOD_COMPAT)).replace("\"schemaVersion\":2", "\"schemaVersion\":2,\"schemaVersion\":2"), "duplicate key"},
        };
        for (String[] b : bad) expect(unusable(b[0], 306) != null, "Unusable receipt: " + b[1]);
        for (String[] b : bad) {
            Server x = published(306, b[0]); DirectUpdate.Result y = check(x, 305);
            expect(y.state == DirectUpdate.State.UNAVAILABLE && y.offer == null, "Unusable receipt is unavailable end to end: " + b[1]);
        }
        expect(unusable(receipt(306, "", compat(GOOD_COMPAT.replace("\"minControllerVersion\":28", "\"minControllerVersion\":null"))), 306) == null, "An explicit null controller requirement is allowed");
        expect(unusable(receipt(306, "", compat(GOOD_COMPAT.replace("[\"arm64-v8a\"]", "[\"universal\"]"))), 306) == null, "The explicit universal ABI marker alone is allowed");

        expect(DirectUpdate.MAX_IDSIG == 1024 * 1024 && unusable(receipt(306, "", compat(GOOD_COMPAT)).replace("\"size\":2048", "\"size\":" + DirectUpdate.MAX_IDSIG), 306) == null,
                "A sidecar of exactly the 1 MiB bound is accepted");

        // #202: schema 1 (today's producer) still parses, but is never offered for a Rose OS self-install.
        String schema1 = receipt1(306, "", compat(GOOD_COMPAT));
        DirectUpdate.Offer legacy = DirectUpdate.offer(MiniJson.parse(schema1), 306);
        expect(!legacy.hasIdsig() && legacy.idsigUrl == null && DirectUpdate.incompatibility(legacy, device(305)) == null,
                "A schema 1 receipt parses and is otherwise compatible (published receipts stay readable)");
        for (Server x : new Server[] {published(306, schema1), new Server().on(DirectUpdate.apiUrl(1), 200, null, list(OS, appWithoutIdsig(306))).on(receiptUrl(306), 200, null, schema1)}) {
            r = check(x, 305);
            expect(r.state == DirectUpdate.State.UNAVAILABLE && r.offer == null && r.message.contains("signature file") && r.message.contains("306"),
                    "Schema 1 is visibly unavailable for self-install, never an APK-only install: " + r.message);
        }
        expect(check(published(306, schema1), 306).state == DirectUpdate.State.NO_UPDATE, "...while an equal version is still up to date");
        // A schema 2 receipt whose release does not list the .idsig asset as uploaded is unavailable too.
        r = check(new Server().on(DirectUpdate.apiUrl(1), 200, null, list(OS, appWithoutIdsig(306))).on(receiptUrl(306), 200, null, receipt(306, "", compat(GOOD_COMPAT))), 305);
        expect(r.state == DirectUpdate.State.UNAVAILABLE && r.offer == null && r.message.contains("isn’t in the release"), "Missing sidecar asset: unavailable: " + r.message);
        String unfinished = "{\"tag_name\":\"rose-agent-306\",\"draft\":false,\"prerelease\":true,\"assets\":[{\"name\":\"dev.pixel.voice-306.apk\",\"state\":\"uploaded\"},"
                + "{\"name\":\"dev.pixel.voice-306.receipt.json\",\"state\":\"uploaded\"},{\"name\":\"dev.pixel.voice-306.apk.idsig\",\"state\":\"starter\"}]}";
        r = check(new Server().on(DirectUpdate.apiUrl(1), 200, null, list(OS, unfinished)).on(receiptUrl(306), 200, null, receipt(306, "", compat(GOOD_COMPAT))), 305);
        expect(r.state == DirectUpdate.State.UNAVAILABLE && r.offer == null, "An unfinished sidecar upload is not a sidecar");
        // The sidecar belongs to the newest release only: an older release's .idsig does not stand in.
        r = check(new Server().on(DirectUpdate.apiUrl(1), 200, null, list(OS, app(305), appWithoutIdsig(306))).on(receiptUrl(306), 200, null, receipt(306, "", compat(GOOD_COMPAT))), 304);
        expect(r.state == DirectUpdate.State.UNAVAILABLE && r.offer == null, "An older release's sidecar is never substituted");
        expect(!DirectUpdate.uploaded(MiniJson.parse(app(306).replace("306.apk.idsig", "307.apk.idsig")), "dev.pixel.voice-306.apk.idsig")
                && DirectUpdate.uploaded(MiniJson.parse(app(306)), "dev.pixel.voice-306.apk.idsig"), "Asset presence is by exact canonical name");

        // This phone's eligibility.
        DirectUpdate.Offer o = DirectUpdate.offer(MiniJson.parse(receipt(306, "", compat(GOOD_COMPAT))), 306);
        expect(DirectUpdate.incompatibility(o, device(305)) == null, "Matching phone is eligible");
        DirectUpdate.Device[] wrong = {
            new DirectUpdate.Device("husky", Arrays.asList("arm64-v8a"), 36, "2026102805", 28, 305, SIGNER),
            new DirectUpdate.Device(null, Arrays.asList("arm64-v8a"), 36, "2026102805", 28, 305, SIGNER),
            new DirectUpdate.Device("frankel", Arrays.asList("x86_64"), 36, "2026102805", 28, 305, SIGNER),
            new DirectUpdate.Device("frankel", Arrays.asList("arm64-v8a"), 35, "2026102805", 28, 305, SIGNER),
            new DirectUpdate.Device("frankel", Arrays.asList("arm64-v8a"), 36, "2026102804", 28, 305, SIGNER),
            new DirectUpdate.Device("frankel", Arrays.asList("arm64-v8a"), 36, null, 28, 305, SIGNER),
            new DirectUpdate.Device("frankel", Arrays.asList("arm64-v8a"), 36, "unknown", 28, 305, SIGNER),
            new DirectUpdate.Device("frankel", Arrays.asList("arm64-v8a"), 36, "2026102805", 27, 305, SIGNER),
            new DirectUpdate.Device("frankel", Arrays.asList("arm64-v8a"), 36, "2026102805", -1, 305, SIGNER),
            new DirectUpdate.Device("frankel", Arrays.asList("arm64-v8a"), 36, "2026102805", 28, 305, SHA),
        };
        for (DirectUpdate.Device d : wrong) {
            expect(DirectUpdate.incompatibility(o, d) != null, "Ineligible phone: " + d.codename + "/" + d.abis + "/" + d.sdk + "/" + d.osBuild + "/" + d.controller);
            DirectUpdate.Result y = DirectUpdate.check(published(306, receipt(306, "", compat(GOOD_COMPAT))), d);
            expect(y.state == DirectUpdate.State.UNAVAILABLE && y.offer == null, "Ineligible phone gets no offer end to end");
        }
        DirectUpdate.Offer noController = DirectUpdate.offer(MiniJson.parse(receipt(306, "", compat(GOOD_COMPAT.replace("\"minControllerVersion\":28", "\"minControllerVersion\":null")))), 306);
        expect(DirectUpdate.incompatibility(noController, new DirectUpdate.Device("frankel", Arrays.asList("arm64-v8a"), 36, "2026102805", -1, 305, SIGNER)) == null, "No controller needed when the requirement is null");
        DirectUpdate.Offer universal = DirectUpdate.offer(MiniJson.parse(receipt(306, "", compat(GOOD_COMPAT.replace("[\"arm64-v8a\"]", "[\"universal\"]")))), 306);
        expect(DirectUpdate.incompatibility(universal, new DirectUpdate.Device("frankel", Arrays.asList("x86_64"), 36, "2026102805", 28, 305, SIGNER)) == null, "Universal APK on any listed ABI");
        expect(DirectUpdate.check(published(306, receipt(306, "", compat(GOOD_COMPAT))), new DirectUpdate.Device("frankel", Arrays.asList("arm64-v8a"), 36, "2026102805", 28, -1, SIGNER)).state
                == DirectUpdate.State.ERROR, "Unreadable installed version is an error, never up to date");

        // The JSON reader itself.
        for (String j : new String[] {"{\"a\":1,\"a\":2}", "{\"a\":1} x", "[1,]", "{'a':1}", "01", "\"\u0001\"", "[" + "[".repeat(40) + "]".repeat(41)})
            { boolean threw = false; try { MiniJson.parse(j); } catch (IllegalArgumentException e) { threw = true; } expect(threw, "Strict JSON rejects " + j.substring(0, Math.min(12, j.length()))); }
        expect(Long.valueOf(306).equals(((Map<?, ?>) MiniJson.parse("{\"v\":306}")).get("v")) && MiniJson.parse("1.5") instanceof Double, "Whole numbers are Long, fractions are not");

        System.out.println("DirectUpdateTest: " + checks + " checks passed");
    }
}
