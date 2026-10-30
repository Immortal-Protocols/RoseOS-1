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
    static final String CDN = "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/dc8dfae4-58f4-44d1-b98d-0879fbb4cbda";
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
        // The asset-id path GitHub was observed to redirect to (2804 SHA256SUMS, 15:22:55Z), with an inert stand-in query.
        String observed = "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/dc8dfae4-58f4-44d1-b98d-0879fbb4cbda";
        expect(PublicApkDownload.redirect(observed + "?inert=opaque-fixture") == null, "The observed UUID asset path is accepted, query left opaque");
        Script observedFlow = new Script().answer(GOOD, 302, observed + "?inert=opaque-fixture", null).answer(observed + "?inert=opaque-fixture", 200, null, BYTES);
        expect(PublicApkDownload.fetch(observedFlow, GOOD, new ByteArrayOutputStream(), id, size, 1 << 20) == size, "Download via the observed UUID redirect");
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
            null, "", "/relative/path", "http://release-assets.githubusercontent.com/github-production-release-asset/1430356511/dc8dfae4-58f4-44d1-b98d-0879fbb4cbda",
            "https://objects.githubusercontent.com/github-production-release-asset/1430356511/dc8dfae4-58f4-44d1-b98d-0879fbb4cbda",
            "https://release-assets.githubusercontent.com.evil.example/github-production-release-asset/1430356511/dc8dfae4-58f4-44d1-b98d-0879fbb4cbda",
            "https://evil.example/github-production-release-asset/1430356511/dc8dfae4-58f4-44d1-b98d-0879fbb4cbda",
            "https://release-assets.githubusercontent.com/github-production-release-asset/999999/dc8dfae4-58f4-44d1-b98d-0879fbb4cbda",
            "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/../1/2",
            "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/dc8dfae4-58f4-44d1-b98d-0879fbb4cbda/extra",
            "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/abc",
            "https://user@release-assets.githubusercontent.com/github-production-release-asset/1430356511/dc8dfae4-58f4-44d1-b98d-0879fbb4cbda",
            "https://release-assets.githubusercontent.com:444/github-production-release-asset/1430356511/dc8dfae4-58f4-44d1-b98d-0879fbb4cbda",
            "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/dc8dfae4-58f4-44d1-b98d-0879fbb4cbda#x",
            GOOD,
            "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/987654321",
            "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/DC8DFAE4-58F4-44D1-B98D-0879FBB4CBDA",
            "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/dc8dfae458f444d1b98d0879fbb4cbda",
            "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/dc8dfae4-58f4-44d1-b98d-0879fbb4cbda0",
            "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/dc8dfae4-58f4-44d1-b98d-0879fbb4cbd",
            "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/dc8dfae4-58f4-44d1-b98d-0879fbb4cbdg",
            "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/dc8dfae4-58f4-44d1-b98d-0879fbb4cbda%2F..",
            "https://release-assets.githubusercontent.com/github-production-release-asset/1430356511/{dc8dfae4-58f4-44d1-b98d-0879fbb4cbda}",
            "https://release-assets.githubusercontent.com/github-production-release-asset/1430356512/dc8dfae4-58f4-44d1-b98d-0879fbb4cbda",
            "https://release-assets.githubusercontent.com/github-production-release-asset/dc8dfae4-58f4-44d1-b98d-0879fbb4cbda",
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

        // APP-IDSIG-1030-M004: the v4 signature file (.apk.idsig) uses the same boundary under its own name pattern,
        // bound by its own hash and size, and the APK and signature boundaries never stand in for each other.
        String sig = "https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-312/dev.pixel.voice-312.apk.idsig";
        String apk = "https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-312/dev.pixel.voice-312.apk";
        byte[] SIG = "fixture v4 signature bytes, not a real idsig".getBytes();
        String sigSha = sha(SIG); long sigSize = SIG.length;
        expect(PublicApkDownload.initialSignature(sig) == null && PublicApkDownload.initial(sig) != null && PublicApkDownload.initialReceipt(sig) != null,
                "Only the signature boundary accepts an .apk.idsig");
        expect(PublicApkDownload.initialSignature(apk) != null && PublicApkDownload.initialSignature(apk + ".idsig.apk") != null
                && PublicApkDownload.initialSignature(sig.replace(".apk.idsig", ".idsig")) != null, "The signature boundary accepts nothing else");
        for (String u : new String[] {sig.replace("Immortal-Protocols/pixel-ota", "evil/pixel-ota"), sig.replace("https://github.com", "https://evil.example"),
                sig.replace("https:", "http:"), sig + "?x=1", sig.replace("rose-agent-312", "latest"), sig.replace("https://", "https://user@"),
                sig.replace("github.com", "github.com:8443")})
            expect(PublicApkDownload.initialSignature(u) != null, "Outside the signature boundary: " + u);
        Script sigDirect = new Script().answer(sig, 200, null, SIG);
        ByteArrayOutputStream sigOut = new ByteArrayOutputStream();
        expect(PublicApkDownload.fetchSignature(sigDirect, sig, sigOut, sigSha, sigSize, 1 << 20) == sigSize && java.util.Arrays.equals(sigOut.toByteArray(), SIG)
                && sigDirect.headers.get(0).isEmpty(), "The signature downloads exactly, anonymously");
        Script sigCdn = new Script().answer(sig, 302, CDN + SECRET_QUERY, null).answer(CDN + SECRET_QUERY, 200, null, SIG);
        expect(PublicApkDownload.fetchSignature(sigCdn, sig, new ByteArrayOutputStream(), sigSha, sigSize, 1 << 20) == sigSize, "The signature via the approved CDN redirect");
        String[][] sigFails = {
            {"hash", null}, {"size", null}, {"oversize", null}, {"redirect", null}, {"double", null}, {"status", null}, {"apk-url", null}, {"empty", null},
        };
        for (String[] f : sigFails) {
            String why;
            try {
                switch (f[0]) {
                    case "hash": PublicApkDownload.fetchSignature(new Script().answer(sig, 200, null, SIG), sig, new ByteArrayOutputStream(), id, sigSize, 1 << 20); break;
                    case "size": PublicApkDownload.fetchSignature(new Script().answer(sig, 200, null, SIG), sig, new ByteArrayOutputStream(), sigSha, sigSize + 1, 1 << 20); break;
                    case "oversize": PublicApkDownload.fetchSignature(new Script().answer(sig, 200, null, SIG), sig, new ByteArrayOutputStream(), sigSha, (1 << 20) + 1, 1 << 20); break;
                    case "redirect": PublicApkDownload.fetchSignature(new Script().answer(sig, 302, "https://evil.example/x.idsig", null), sig, new ByteArrayOutputStream(), sigSha, sigSize, 1 << 20); break;
                    case "double": PublicApkDownload.fetchSignature(new Script().answer(sig, 302, CDN, null).answer(CDN, 302, CDN, null), sig, new ByteArrayOutputStream(), sigSha, sigSize, 1 << 20); break;
                    case "status": PublicApkDownload.fetchSignature(new Script().answer(sig, 404, null, null), sig, new ByteArrayOutputStream(), sigSha, sigSize, 1 << 20); break;
                    case "apk-url": PublicApkDownload.fetchSignature(new Script().answer(apk, 200, null, SIG), apk, new ByteArrayOutputStream(), sigSha, sigSize, 1 << 20); break;
                    default: PublicApkDownload.fetchSignature(new Script().answer(sig, 200, null, null), sig, new ByteArrayOutputStream(), sigSha, sigSize, 1 << 20);
                }
                why = null;
            } catch (IOException e) { why = e.getMessage(); }
            expect(why != null && !why.contains("SECRET") && !why.contains("http"), "Signature download refused without leaking an address: " + f[0]);
        }
        expect(fails(new Script().answer(sig, 200, null, BYTES), sig, id, size) != null, "The APK fetch never accepts a signature address");

        System.out.println("PublicApkDownloadTest: " + checks + " checks passed");
    }
}
