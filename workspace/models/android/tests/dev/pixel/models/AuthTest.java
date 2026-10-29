package dev.pixel.models;

import static dev.pixel.models.HostTest.check;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Endpoint-scoped bearer auth with a synthetic, obviously non-secret marker. No socket is opened:
 * the HTTPS client runs against an in-memory connection.
 */
final class AuthTest {
    static final String MARKER = "SYNTHETIC-NOT-A-SECRET-7f3a";
    static final String ENDPOINT = "https://infer.example.test/v1/chat/completions";

    private AuthTest() {
    }

    static void all() {
        credentialIsBoundToOneOrigin();
        credentialNeverPrintsTheToken();
        storeDropsTokenOnOriginChangeAndClear();
        sendNeedsTokenForThisEndpoint();
        clientRefusesMissingOrForeignCredential();
        requestCarriesBearerOnlyToBoundOrigin();
        redirectIsNotFollowed();
        transportFailureIsRedacted();
        unauthorizedAndForbiddenAreShownWithoutBody();
        CredentialStore.clear();
    }

    static void credentialIsBoundToOneOrigin() {
        EndpointCredential c = EndpointCredential.bind(ENDPOINT, MARKER);
        check(c.origin().equals("https://infer.example.test"), "origin " + c.origin());
        check(c.matches("https://INFER.example.test:443/other/path?x=1"), "same origin, other path and default port");
        for (String other : new String[] {"https://other.example.test/v1/chat/completions",
                "https://infer.example.test:8443/v1/chat/completions", "http://infer.example.test/v1/chat/completions",
                "https://infer.example.test.evil.test/v1", "https://user@infer.example.test/v1", ""}) {
            check(!c.matches(other), "not sent to " + other);
        }
        for (String bad : new String[] {"", "two words", "line\nbreak", "tab\tbad", "café"}) {
            try {
                EndpointCredential.bind(ENDPOINT, bad);
                check(false, "token accepted: " + bad);
            } catch (IllegalArgumentException e) {
                check(!e.getMessage().contains(bad.isEmpty() ? MARKER : bad), "rejection does not echo the token");
            }
        }
        try {
            EndpointCredential.bind("http://infer.example.test/v1", MARKER);
            check(false, "cleartext endpoint accepted");
        } catch (IllegalArgumentException e) {
            check(!e.getMessage().contains(MARKER), "cleartext rejection redacted");
        }
    }

    static void credentialNeverPrintsTheToken() {
        EndpointCredential c = EndpointCredential.bind(ENDPOINT, MARKER);
        check(!c.toString().contains(MARKER) && c.toString().contains("redacted"), "toString masked: " + c);
        check(!String.valueOf(new Object[] {c}[0]).contains(MARKER), "string conversion masked");
    }

    static void storeDropsTokenOnOriginChangeAndClear() {
        CredentialStore.set(EndpointCredential.bind(ENDPOINT, MARKER));
        check(CredentialStore.forEndpoint(ENDPOINT + "?v=2") != null, "same origin keeps the token");
        check(CredentialStore.forEndpoint("https://other.example.test/v1") == null && !CredentialStore.isSet(),
                "another origin drops the token");
        check(CredentialStore.forEndpoint(ENDPOINT) == null, "dropped token does not come back");
        CredentialStore.set(EndpointCredential.bind(ENDPOINT, MARKER));
        CredentialStore.clear();
        check(!CredentialStore.isSet() && CredentialStore.forEndpoint(ENDPOINT) == null, "clear removes it");
    }

    static void sendNeedsTokenForThisEndpoint() {
        check(SetupCheck.problem(ENDPOINT, "muse-glimmer", null).startsWith("no access token"), "missing token blocks Send");
        check(SetupCheck.problem(ENDPOINT, "muse-glimmer",
                EndpointCredential.bind("https://other.example.test/v1", MARKER)).contains("another endpoint"),
                "foreign token blocks Send");
        check(SetupCheck.problem(ENDPOINT, "", EndpointCredential.bind(ENDPOINT, MARKER)).contains("model"),
                "model still required");
        check(SetupCheck.problem(ENDPOINT, "muse-glimmer", EndpointCredential.bind(ENDPOINT, MARKER)) == null,
                "endpoint + model + matching token allows Send");
    }

    static void clientRefusesMissingOrForeignCredential() {
        for (EndpointCredential c : new EndpointCredential[] {null,
                EndpointCredential.bind("https://other.example.test/v1", MARKER)}) {
            try {
                new HttpsInferenceClient(ENDPOINT, c, u -> {
                    throw new AssertionError("opened");
                });
                check(false, "client built without a matching token");
            } catch (IllegalArgumentException e) {
                check(!e.getMessage().contains(MARKER), "refusal redacted");
            }
        }
    }

    static void requestCarriesBearerOnlyToBoundOrigin() {
        Fake fake = new Fake(200, "{\"ok\":true}", null);
        Result r = call(fake);
        check(fake.opened.size() == 1 && fake.opened.get(0).equals(ENDPOINT), "one connection to the bound URL");
        check(("Bearer " + MARKER).equals(fake.headers.get("Authorization")), "bearer header sent");
        check("POST".equals(fake.getRequestMethod()) && !fake.getInstanceFollowRedirects(), "POST, redirects off");
        check(r.status == 200 && "{\"model\":\"m\"}".equals(fake.sent()), "body sent unchanged, status reported");
    }

    static void redirectIsNotFollowed() {
        Fake fake = new Fake(302, "", "https://other.example.test/steal");
        Result r = call(fake);
        check(fake.opened.size() == 1 && r.status == 302, "302 reported raw, Location not followed");
    }

    static void transportFailureIsRedacted() {
        Fake fake = new Fake(0, "", null);
        fake.failure = new IOException("connect failed for Authorization: Bearer " + MARKER);
        Result r = call(fake);
        check("connect".equals(r.failure), "failure kind only: " + r.failure);
    }

    /** 401/403 with a body that echoes the token: the user sees the status class, never the body. */
    static void unauthorizedAndForbiddenAreShownWithoutBody() {
        int[] statuses = {401, 403};
        String[] shown = {"unauthorized", "forbidden"};
        for (int i = 0; i < statuses.length; i++) {
            HostTest.Rig r = new HostTest.Rig();
            r.host.send("hi");
            r.client.callbacks.get(0).onResponse(statuses[i], null, "{\"error\":\"bad token " + MARKER + "\"}");
            String all = r.view.transcript + " " + r.view.statuses;
            check(!r.host.hasActiveTurn() && all.contains(shown[i]), statuses[i] + " shown as " + shown[i] + ": " + all);
            check(!all.contains(MARKER), statuses[i] + " body not shown");
        }
    }

    // ---- harness ----

    static final class Result {
        int status = -1;
        String failure;
    }

    static Result call(Fake fake) {
        HttpsInferenceClient client = new HttpsInferenceClient(ENDPOINT, EndpointCredential.bind(ENDPOINT, MARKER), fake::open);
        final Result result = new Result();
        final CountDownLatch done = new CountDownLatch(1);
        client.send("{\"model\":\"m\"}", 5000, new InferenceClient.Callback() {
            public void onResponse(int status, String retryAfter, String body) {
                result.status = status;
                done.countDown();
            }

            public void onFailure(String kind) {
                result.failure = kind;
                done.countDown();
            }
        });
        try {
            check(done.await(10, TimeUnit.SECONDS), "client answered");
        } catch (InterruptedException e) {
            throw new AssertionError(e);
        }
        return result;
    }

    /** An in-memory HTTP exchange; records what the client sent. */
    static final class Fake extends HttpURLConnection {
        final List<String> opened = new ArrayList<>();
        final Map<String, String> headers = new HashMap<>();
        final ByteArrayOutputStream out = new ByteArrayOutputStream();
        final int status;
        final String body;
        final String location;
        IOException failure;

        Fake(int status, String body, String location) {
            super(null);
            this.status = status;
            this.body = body;
            this.location = location;
        }

        HttpURLConnection open(URL u) {
            opened.add(u.toString());
            return this;
        }

        String sent() {
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        }

        @Override
        public void setRequestProperty(String key, String value) {
            headers.put(key, value);
        }

        @Override
        public void connect() throws IOException {
            if (failure != null) {
                throw failure;
            }
        }

        @Override
        public OutputStream getOutputStream() {
            return out;
        }

        @Override
        public int getResponseCode() {
            return status;
        }

        @Override
        public String getHeaderField(String name) {
            return "Location".equalsIgnoreCase(name) ? location : null;
        }

        @Override
        public InputStream getInputStream() {
            return new ByteArrayInputStream(body.getBytes(StandardCharsets.UTF_8));
        }

        @Override
        public InputStream getErrorStream() {
            return getInputStream();
        }

        @Override
        public void disconnect() {
        }

        @Override
        public boolean usingProxy() {
            return false;
        }
    }
}
