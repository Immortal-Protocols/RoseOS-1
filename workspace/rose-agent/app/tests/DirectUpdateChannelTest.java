package dev.pixel.voice;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.function.Supplier;

/** APP-CHANNEL-1029-C1. Explicit Alpha/Beta/Prod selection in DirectUpdate. Fixtures reuse DirectUpdateTest's
 * scripted GitHub server and canonical receipts; tags in them are adversarial examples, not publication
 * namespaces, and nothing here is evidence that any channel or update is published. */
public final class DirectUpdateChannelTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    /** Any request at all fails the test: a channel without a route must not reach the network. */
    static final class NoNetwork implements PublicApkDownload.Http {
        final List<String> requested = new ArrayList<>();
        public PublicApkDownload.Response get(String url, Map<String, String> h) throws IOException {
            requested.add(url); throw new IOException("unexpected request " + url);
        }
    }
    static String describe(DirectUpdate.Result r) {
        DirectUpdate.Offer o = r.offer;
        return r.state + "|" + r.message + "|" + (o == null ? "-" : o.versionCode + "," + o.versionName + "," + o.size + "," + o.sha256 + ","
                + o.signerSha256 + "," + o.apkUrl + "," + o.codenames + "," + o.abis + "," + o.minSdk + "," + o.minOsBuild + "," + o.minController);
    }
    /** The legacy check and explicit Beta give the same result and make the same requests with the same headers. */
    static void sameAsLegacy(String name, Supplier<DirectUpdateTest.Server> fixture, DirectUpdate.Device device) {
        DirectUpdateTest.Server legacy = fixture.get(), beta = fixture.get();
        String a = describe(DirectUpdate.check(legacy, device)), b = describe(DirectUpdate.check(beta, device, DirectUpdate.Channel.BETA));
        expect(a.equals(b), name + ": Beta differs from the legacy check\n legacy " + a + "\n beta   " + b);
        expect(legacy.requested.equals(beta.requested) && legacy.headers.equals(beta.headers), name + ": requests differ " + legacy.requested + " / " + beta.requested);
    }

    public static void main(String[] args) {
        final String good = DirectUpdateTest.compat(DirectUpdateTest.GOOD_COMPAT);
        // Legacy check == explicit Beta across the existing behaviour: offers, up to date, unusable or ineligible receipts,
        // GitHub errors and the page bound.
        sameAsLegacy("available", () -> DirectUpdateTest.published(306, DirectUpdateTest.receipt(306, "", good), DirectUpdateTest.app(305)), DirectUpdateTest.device(305));
        sameAsLegacy("installed 308 is up to date", () -> DirectUpdateTest.published(308, DirectUpdateTest.receipt(308, "", good), DirectUpdateTest.app(307)), DirectUpdateTest.device(308));
        sameAsLegacy("installed 308, older offered", () -> DirectUpdateTest.published(307, DirectUpdateTest.receipt(307, "", good)), DirectUpdateTest.device(308));
        sameAsLegacy("installed 308, newer 309", () -> DirectUpdateTest.published(309, DirectUpdateTest.receipt(309, "", good), DirectUpdateTest.app(308)), DirectUpdateTest.device(308));
        sameAsLegacy("unknown receipt field", () -> DirectUpdateTest.published(306, DirectUpdateTest.receipt(306, ",\"channel\":\"alpha\"", good)), DirectUpdateTest.device(305));
        sameAsLegacy("other signer", () -> DirectUpdateTest.published(306, DirectUpdateTest.receipt(306, "", good)),
                new DirectUpdate.Device("frankel", Arrays.asList("arm64-v8a"), 36, "2026102805", 28, 305, DirectUpdateTest.SHA));
        sameAsLegacy("other phone", () -> DirectUpdateTest.published(306, DirectUpdateTest.receipt(306, "", good)),
                new DirectUpdate.Device("other", Arrays.asList("arm64-v8a"), 36, "2026102805", 28, 305, DirectUpdateTest.SIGNER));
        for (int code : new int[] {403, 429, 500})
            sameAsLegacy("list status " + code, () -> new DirectUpdateTest.Server().on(DirectUpdate.apiUrl(1), code, null, "[]"), DirectUpdateTest.device(305));
        sameAsLegacy("no release", () -> new DirectUpdateTest.Server().on(DirectUpdate.apiUrl(1), 200, null, DirectUpdateTest.list(DirectUpdateTest.OS)), DirectUpdateTest.device(305));
        sameAsLegacy("receipt redirect outside the boundary", () -> new DirectUpdateTest.Server().on(DirectUpdate.apiUrl(1), 200, null, DirectUpdateTest.list(DirectUpdateTest.app(306)))
                .on(DirectUpdateTest.receiptUrl(306), 302, "https://evil.example/r.json", null), DirectUpdateTest.device(305));
        StringBuilder full = new StringBuilder("[");
        for (int i = 0; i < DirectUpdate.PER_PAGE; i++) full.append(i == 0 ? "" : ",").append(DirectUpdateTest.release("frankel-" + i, false, false));
        String fullPage = full.append("]").toString();
        sameAsLegacy("page bound", () -> new DirectUpdateTest.Server().on(DirectUpdate.apiUrl(1), 200, null, fullPage).on(DirectUpdate.apiUrl(2), 200, null, fullPage)
                .on(DirectUpdate.apiUrl(3), 200, null, fullPage), DirectUpdateTest.device(305));
        sameAsLegacy("unreachable", NoNetworkServer::new, DirectUpdateTest.device(305));

        // Mixed list: only the canonical rose-agent-N prerelease with both uploaded assets is a Beta candidate.
        // These names are adversarial fixtures, not chosen Alpha/Prod namespaces.
        String[] adversarial = {
            DirectUpdateTest.release("rose-agent-alpha-320", false, true, "dev.pixel.voice-320.apk", "dev.pixel.voice-320.receipt.json"),
            DirectUpdateTest.release("rose-agent-prod-321", false, false, "dev.pixel.voice-321.apk", "dev.pixel.voice-321.receipt.json"),
            DirectUpdateTest.release("alpha-rose-agent-322", false, true, "dev.pixel.voice-322.apk", "dev.pixel.voice-322.receipt.json"),
            DirectUpdateTest.release("Rose-Agent-323", false, true, "dev.pixel.voice-323.apk", "dev.pixel.voice-323.receipt.json"),
            DirectUpdateTest.release("rose-agent-0324", false, true, "dev.pixel.voice-0324.apk", "dev.pixel.voice-0324.receipt.json"),
            DirectUpdateTest.release("rose-agent-325", false, false, "dev.pixel.voice-325.apk", "dev.pixel.voice-325.receipt.json"),
            DirectUpdateTest.release("rose-agent-326", true, true, "dev.pixel.voice-326.apk", "dev.pixel.voice-326.receipt.json"),
            DirectUpdateTest.release("rose-agent-327", false, true, "dev.pixel.voice-327.apk"),
        };
        sameAsLegacy("mixed list", () -> DirectUpdateTest.published(309, DirectUpdateTest.receipt(309, "", good), adversarial), DirectUpdateTest.device(308));
        DirectUpdate.Result mixed = DirectUpdate.check(DirectUpdateTest.published(309, DirectUpdateTest.receipt(309, "", good), adversarial), DirectUpdateTest.device(308), DirectUpdate.Channel.BETA);
        expect(mixed.state == DirectUpdate.State.AVAILABLE && mixed.offer.versionCode == 309, "Beta keeps the exact canonical tag rules: " + mixed.message);

        // Alpha and Prod are not configured: unavailable, no offer, no request, never "up to date", never Beta.
        for (DirectUpdate.Channel channel : new DirectUpdate.Channel[] {DirectUpdate.Channel.ALPHA, DirectUpdate.Channel.PROD}) {
            for (long installed : new long[] {305, 308}) {
                NoNetwork net = new NoNetwork();
                DirectUpdate.Result r = DirectUpdate.check(net, DirectUpdateTest.device(installed), channel);
                expect(r.state == DirectUpdate.State.UNAVAILABLE && r.offer == null, channel + " is unavailable with no offer");
                expect(net.requested.isEmpty(), channel + " made no request: " + net.requested);
                expect(!r.message.toLowerCase().contains("up to date") && r.message.contains(channel.label), channel + " says it is unpublished: " + r.message);
            }
            // Even with a Beta release ready to offer, an unconfigured channel never reaches it.
            DirectUpdateTest.Server beta = DirectUpdateTest.published(306, DirectUpdateTest.receipt(306, "", good));
            DirectUpdate.Result r = DirectUpdate.check(beta, DirectUpdateTest.device(305), channel);
            expect(r.state == DirectUpdate.State.UNAVAILABLE && r.offer == null && beta.requested.isEmpty(), channel + " does not fall back to Beta");
        }

        // Selection: exactly alpha, beta or prod; anything else is rejected rather than mapped to Beta.
        expect(DirectUpdate.Channel.parse("alpha") == DirectUpdate.Channel.ALPHA && DirectUpdate.Channel.parse("beta") == DirectUpdate.Channel.BETA
                && DirectUpdate.Channel.parse("prod") == DirectUpdate.Channel.PROD, "The three channel names parse");
        for (String bad : new String[] {null, "", "Beta", "BETA", " beta", "beta ", "stable", "production", "rose-agent", "legacy", "default", "alpha,beta"}) {
            boolean rejected;
            try { DirectUpdate.Channel.parse(bad); rejected = false; } catch (IllegalArgumentException e) { rejected = true; }
            expect(rejected, "Rejected selection: " + bad);
        }
        NoNetwork net = new NoNetwork();
        boolean nullRejected;
        try { DirectUpdate.check(net, DirectUpdateTest.device(305), null); nullRejected = false; } catch (IllegalArgumentException e) { nullRejected = true; }
        expect(nullRejected && net.requested.isEmpty(), "A missing selection is rejected before any request");
        System.out.println("DirectUpdateChannelTest: " + checks + " checks passed");
    }
    /** A server with no routes: every request fails as unreachable. */
    static final class NoNetworkServer extends DirectUpdateTest.Server {}
}
