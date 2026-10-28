package dev.pixel.voice;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** APP-GITHUB-001. Controlled responses only: these URLs, bytes and asset ids are fixtures, not a published
 * release, and no network is used. Exercises the real PublicApkDownload policy and download loop. */
public final class PublicApkDownloadTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static final String GOOD = "https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/rose-agent-304.apk";
    static final String CDN = "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/987654321";
    static final String SECRET_QUERY = "?X-Amz-Signature=SECRET-TOKEN&response-content-disposition=attachment";
    static final byte[] BYTES = "fixture apk bytes, not an APK".getBytes();

    /** A scripted server: answers by exact URL, and records every request it saw. */
    static class Script implements PublicApkDownload.Http {
        final Map<String, int[]> status = new HashMap<>(); final Map<String, String> location = new HashMap<>(); final Map<String, byte[]> body = new HashMap<>();
        final List<String> requested = new ArrayList<>(); final List<Map<String, String>> headers = new ArrayList<>();
        Script answer(String url, int code, String loc, byte[] bytes) { status.put(url, new int[] {code}); location.put(url, loc); body.put(url, bytes); return this; }
        public PublicApkDownload.Response get(String url, Map<String, String> h) throws IOException {
            requested.add(url); headers.add(new HashMap<>(h));
            if (!status.containsKey(url)) throw new IOException("unscripted request");
            byte[] b = body.get(url);
            return new PublicApkDownload.Response(status.get(url)[0], location.get(url), b == null ? null : new ByteArrayInputStream(b));
        }
    }
    static String sha(byte[] b) throws Exception { return PublicApkDownload.hex(MessageDigest.getInstance("SHA-256").digest(b)); }
    static String fails(Script s, String url, String expectedSha, long size) {
        try { PublicApkDownload.fetch(s, url, new ByteArrayOutputStream(), expectedSha, size, 1 << 20); return null; }
        catch (IOException e) { return e.getMessage(); }
    }

    public static void main(String[] args) throws Exception {
        String id = sha(BYTES); long size = BYTES.length;

        // Permitted shapes: direct 200, and exactly one 302 to the approved CDN path then 200.
        Script direct = new Script().answer(GOOD, 200, null, BYTES);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        expect(PublicApkDownload.fetch(direct, GOOD, out, id, size, 1 << 20) == size && java.util.Arrays.equals(out.toByteArray(), BYTES), "Direct 200 downloads the exact bytes");
        Script viaCdn = new Script().answer(GOOD, 302, CDN + SECRET_QUERY, null).answer(CDN + SECRET_QUERY, 200, null, BYTES);
        expect(PublicApkDownload.fetch(viaCdn, GOOD, new ByteArrayOutputStream(), id, size, 1 << 20) == size, "One 302 to the approved CDN path, then 200");
        expect(viaCdn.requested.size() == 2 && viaCdn.requested.get(1).equals(CDN + SECRET_QUERY), "The redirect query is used only for that one request");
        for (Map<String, String> h : viaCdn.headers) expect(h.isEmpty(), "No credentials, cookies, referer or other headers are sent");
        expect(PublicApkDownload.initial("https://github.com:443/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/rose-agent-304.apk") == null, "Explicit port 443 is the same origin");

        // Initial URLs outside the boundary are refused before any request.
        String[] badInitial = {
            null, "", "http://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/a.apk",
            "https://github.com.evil.example/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/a.apk",
            "https://evil.example/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/a.apk",
            "https://GitHub.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/a.apk",
            "https://github.com@evil.example/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/a.apk",
            "https://user:pw@github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/a.apk",
            "https://github.com:8443/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/a.apk",
            "https://github.com/Immortal-Protocols/other-repo/releases/download/rose-agent-304/a.apk",
            "https://github.com/immortal-protocols/pixel-ota/releases/download/rose-agent-304/a.apk",
            "https://github.com/Immortal-Protocols/pixel-ota/releases/latest/download/a.apk",
            "https://github.com/Immortal-Protocols/pixel-ota/releases/download/latest/a.apk",
            "https://github.com/Immortal-Protocols/pixel-ota/releases/download/beta/a.apk",
            "https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/a.apk?x=1",
            "https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/a.apk#frag",
            "https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304%2F..%2Fx/a.apk",
            "https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/..%2Fa.apk",
            "https://github.com/Immortal-Protocols/pixel-ota/releases/download/../download/rose-agent-304/a.apk",
            "https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/./a.apk",
            "https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304\\a.apk",
            "https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/sub/a.apk",
            "https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/a.zip",
            "https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-304/.apk",
            "https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose agent 304/a.apk",
            CDN, CDN + SECRET_QUERY,
        };
        for (String url : badInitial) {
            Script s = new Script();
            expect(fails(s, url, id, size) != null && s.requested.isEmpty(), "Refused before any request: " + url);
        }

        // Redirects outside the boundary fail closed; the CDN is never contacted.
        String[] badRedirect = {
            null, "", "/relative/path", "http://release-assets.githubusercontent.com/github-production-release-asset/1430356511/987654321",
            "https://objects.githubusercontent.com/github-production-release-asset/1430356511/987654321",
            "https://release-assets.githubusercontent.com.evil.example/github-production-release-asset/1430356511/987654321",
            "https://evil.example/github-production-release-asset/1430356511/987654321",
            "https://release-assets.githubusercontent.com/github-production-release-asset/999999/987654321",
            "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/../1/2",
            "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/987654321/extra",
            "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/abc",
            "https://user@release-assets.githubusercontent.com/github-production-release-asset/1430356511/987654321",
            "https://release-assets.githubusercontent.com:444/github-production-release-asset/1430356511/987654321",
            "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/987654321#x",
            GOOD,
        };
        for (String loc : badRedirect) {
            Script s = new Script().answer(GOOD, 302, loc, null);
            expect(fails(s, GOOD, id, size) != null && s.requested.size() == 1, "Redirect refused without following: " + loc);
        }
        for (int code : new int[] {301, 303, 307, 308}) {
            Script s = new Script().answer(GOOD, code, CDN, null);
            expect(fails(s, GOOD, id, size) != null && s.requested.size() == 1, "Unsupported redirect status " + code);
        }
        Script loop = new Script().answer(GOOD, 302, CDN, null).answer(CDN, 302, CDN, null);
        expect(fails(loop, GOOD, id, size).contains("more than once") && loop.requested.size() == 2, "A second redirect (including a loop) is refused");
        Script back = new Script().answer(GOOD, 302, CDN, null).answer(CDN, 302, GOOD, null);
        expect(fails(back, GOOD, id, size) != null && back.requested.size() == 2, "Redirect back to the origin is refused");

        // Bytes must match the selected record exactly.
        expect(fails(new Script().answer(GOOD, 200, null, BYTES), GOOD, sha("other".getBytes()), size).contains("didn’t match"), "Wrong hash");
        byte[] truncated = java.util.Arrays.copyOf(BYTES, BYTES.length - 1);
        expect(fails(new Script().answer(GOOD, 200, null, truncated), GOOD, id, size).contains("size"), "Truncated download");
        byte[] longer = java.util.Arrays.copyOf(BYTES, BYTES.length + 5);
        expect(fails(new Script().answer(GOOD, 200, null, longer), GOOD, id, size).contains("larger"), "Oversize download stops");
        expect(fails(new Script().answer(GOOD, 200, null, BYTES), GOOD, id, 0) != null, "Missing size is refused");
        expect(fails(new Script().answer(GOOD, 200, null, BYTES), GOOD, id, (1 << 20) + 1) != null, "Size above the cap is refused");
        expect(fails(new Script().answer(GOOD, 404, null, null), GOOD, id, size).contains("404"), "HTTP failure is reported");
        expect(fails(new Script().answer(GOOD, 302, CDN + SECRET_QUERY, null).answer(CDN + SECRET_QUERY, 403, null, null), GOOD, id, size).contains("403"), "CDN failure is reported");

        // No failure message carries a URL or the CDN query.
        List<String> messages = new ArrayList<>();
        messages.add(fails(new Script().answer(GOOD, 302, CDN + SECRET_QUERY, null).answer(CDN + SECRET_QUERY, 403, null, null), GOOD, id, size));
        messages.add(fails(new Script().answer(GOOD, 302, CDN + SECRET_QUERY, null).answer(CDN + SECRET_QUERY, 302, CDN + SECRET_QUERY, null), GOOD, id, size));
        messages.add(fails(new Script().answer(GOOD, 302, "https://evil.example/x" + SECRET_QUERY, null), GOOD, id, size));
        messages.add(fails(new Script().answer(GOOD, 302, CDN + SECRET_QUERY, null).answer(CDN + SECRET_QUERY, 200, null, truncated), GOOD, id, size));
        Script throwing = new Script() {
            public PublicApkDownload.Response get(String url, Map<String, String> h) throws IOException {
                if (url.equals(GOOD)) return new PublicApkDownload.Response(302, CDN + SECRET_QUERY, null);
                throw new java.net.SocketTimeoutException("timeout reading " + url);
            }
        };
        messages.add(fails(throwing, GOOD, id, size));
        Script brokenBody = new Script() {
            public PublicApkDownload.Response get(String url, Map<String, String> h) {
                return new PublicApkDownload.Response(200, null, new java.io.InputStream() {
                    public int read() throws IOException { throw new IOException("reset by " + CDN + SECRET_QUERY); }
                });
            }
        };
        messages.add(fails(brokenBody, GOOD, id, size));
        for (String m : messages) expect(m != null && !m.contains("SECRET") && !m.contains("http") && !m.contains("?"), "Message is free of URLs and queries: " + m);

        System.out.println("PublicApkDownloadTest: " + checks + " checks passed");
    }
}
