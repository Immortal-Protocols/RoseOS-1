package dev.pixel.builder;

import java.io.File;
import java.security.GeneralSecurityException;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import javax.net.ssl.SSLSocketFactory;

/**
 * Project 1, Rose Agent: the builder transport over an Endpoint (the Agent's pairing). Only what needs no network: a
 * phone that can't sign for its pairing is asked to pair again (never told the builder is unreachable), and a pairing
 * with no address is refused up front. Nothing here opens a connection; signing happens before any I/O.
 */
public final class HttpsBuilderApiTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    static final class Fake implements HttpsBuilderApi.Endpoint {
        final List<String> hosts; int signs;
        Fake(List<String> hosts) { this.hosts = hosts; }
        @Override public List<String> hosts() { return hosts; }
        @Override public int port() { return 8443; }
        @Override public SSLSocketFactory tls() { return (SSLSocketFactory) SSLSocketFactory.getDefault(); }
        @Override public Map<String, String> headers(String method, String path) throws GeneralSecurityException {
            signs++;
            throw new GeneralSecurityException("pairing key unavailable");
        }
    }

    interface Call { void run(HttpsBuilderApi api) throws Exception; }

    static void asksToPairAgain(String what, Call call) throws Exception {
        Fake f = new Fake(Arrays.asList("100.64.0.1", "100.64.0.2"));
        HttpsBuilderApi api = new HttpsBuilderApi(f);
        try { call.run(api); throw new AssertionError(what + ": no refusal"); }
        catch (BuilderApi.Unreachable u) { expect(u.pairAgain, what + ": asks to pair again"); }
        expect(f.signs == 1, what + ": tried once, not at every address (" + f.signs + ")");
    }

    public static void main(String[] args) throws Exception {
        asksToPairAgain("library", BuilderApi::projects);
        asksToPairAgain("create", api -> api.create("r1", "a stopwatch"));
        asksToPairAgain("retry", api -> api.retry("j1"));
        asksToPairAgain("download", api -> api.download("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", 10,
            File.createTempFile("artifact", ".apk")));
        // A request whose body may have reached the server never goes to a second address; one that never left may.
        expect(!HttpsBuilderApi.mayTryNextHost(true), "a sent body stays at its address");
        expect(HttpsBuilderApi.mayTryNextHost(false), "an unsent request tries the next address");
        try { new HttpsBuilderApi(new Fake(Collections.<String>emptyList())); throw new AssertionError("accepted no address"); }
        catch (GeneralSecurityException e) { checks++; }
        System.out.println("HttpsBuilderApiTest: " + checks + " checks passed");
    }
}
