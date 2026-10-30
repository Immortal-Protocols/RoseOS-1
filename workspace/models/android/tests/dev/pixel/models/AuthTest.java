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
 * Origin-bound bearer transport and the host's 401 renewal, with a synthetic, obviously non-secret
 * marker. No socket is opened: the HTTPS client runs against in-memory connections.
 */
final class AuthTest {
    static final String MARKER = "SYNTHETIC-NOT-A-SECRET-7f3a";
    static final String ENDPOINT = "https://infer.example.test/v1/chat/completions";

    private AuthTest() {
    }

    static void all() {
        credentialIsBoundToOneOrigin();
        credentialNeverPrintsTheToken();
        defaultsArePublicHttps();
        foreignOriginCredentialIsNeverSent();
        requestCarriesBearerOnlyToBoundOrigin();
        redirectIsNotFollowed();
        transportFailureIsRedacted();
        authFailureReachesAgentAsStatusWithReason();
        unauthorizedAndForbiddenAreShownWithoutBody();
        one401RenewsAndResendsOnce();
        second401EndsTheTurn();
        renewalNeverCreatesAThirdDispatch();
        stopDuringRenewedResendFencesIt();
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
    }

    static void credentialNeverPrintsTheToken() {
        EndpointCredential c = EndpointCredential.bind(ENDPOINT, MARKER);
        check(!c.toString().contains(MARKER) && c.toString().contains("redacted"), "toString masked: " + c);
    }

    static void defaultsArePublicHttps() {
        check(EndpointPolicy.check(ModelsService.CHAT_URL) == null, "chat URL is valid https");
        check("https://models.a-list.com".equals(EndpointCredential.origin(ModelsService.CHAT_URL))
                && EndpointCredential.origin(ModelsService.TOKEN_URL).equals(EndpointCredential.origin(ModelsService.CHAT_URL))
                && EndpointCredential.origin(ModelsService.CHALLENGE_URL).equals(EndpointCredential.origin(ModelsService.CHAT_URL)),
                "auth and chat share the public origin");
        check("muse-glimmer".equals(ModelsService.MODEL), "default model");
    }

    static void foreignOriginCredentialIsNeverSent() {
        Fake fake = new Fake(200, "{}", null);
        Result r = call(new HttpsInferenceClient(ENDPOINT,
                fixed(EndpointCredential.bind("https://other.example.test/v1", MARKER)), fake::open));
        check(fake.opened.isEmpty() && r.status == 401 && "auth_wrong_origin".equals(r.authProblem),
                "foreign-origin credential: nothing opened, 401 auth_wrong_origin");
    }

    static void requestCarriesBearerOnlyToBoundOrigin() {
        Fake fake = new Fake(200, "{\"ok\":true}", null);
        Result r = call(client(fake));
        check(fake.opened.size() == 1 && fake.opened.get(0).equals(ENDPOINT), "one connection to the bound URL");
        check(("Bearer " + MARKER).equals(fake.headers.get("Authorization")), "bearer header sent");
        check("POST".equals(fake.getRequestMethod()) && !fake.getInstanceFollowRedirects(), "POST, redirects off");
        check(r.status == 200 && "{\"model\":\"m\"}".equals(fake.sent()), "body sent unchanged, status reported");
    }

    static void redirectIsNotFollowed() {
        Fake fake = new Fake(302, "", "https://other.example.test/steal");
        Result r = call(client(fake));
        check(fake.opened.size() == 1 && r.status == 302, "302 reported raw, Location not followed");
    }

    static void transportFailureIsRedacted() {
        Fake fake = new Fake(0, "", null);
        fake.failure = new IOException("connect failed for Authorization: Bearer " + MARKER);
        Result r = call(client(fake));
        check("connect".equals(r.failure), "failure kind only: " + r.failure);
    }

    static void authFailureReachesAgentAsStatusWithReason() {
        Fake fake = new Fake(200, "{}", null);
        CredentialSource refused = new CredentialSource() {
            public EndpointCredential current(Cancel c) throws AuthFailure {
                throw AuthFailure.status(403, "auth_revoked");
            }

            public EndpointCredential renew(Cancel c) throws AuthFailure {
                throw AuthFailure.status(403, "auth_revoked");
            }
        };
        Result r = call(new HttpsInferenceClient(ENDPOINT, refused, fake::open));
        check(fake.opened.isEmpty() && r.status == 403 && "auth_revoked".equals(r.authProblem),
                "revoked: no inference request, 403 with reason");
        check(AuthMessages.text("auth_revoked").contains("Reset sign-in"), "revoked text names the reset");
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

    static void one401RenewsAndResendsOnce() {
        HostTest.Rig r = new HostTest.Rig();
        r.client.renewing = true;
        r.host.send("hi");
        r.client.callbacks.get(0).onResponse(401, null, "");
        check(r.client.resends == 1 && r.client.bodies.size() == 2 && r.client.bodies.get(1).equals(r.client.bodies.get(0)),
                "401: renew and resend the same request once");
        r.client.reply(1, HostTest.finalText("hello"));
        check(!r.host.hasActiveTurn() && String.join(" ", r.view.transcript).contains("hello"), "resent request answers");
    }

    static void second401EndsTheTurn() {
        HostTest.Rig r = new HostTest.Rig();
        r.client.renewing = true;
        r.host.send("hi");
        r.client.callbacks.get(0).onResponse(401, null, "");
        r.client.callbacks.get(1).onResponse(401, null, "");
        check(r.client.bodies.size() == 2 && !r.host.hasActiveTurn()
                && String.join(" ", r.view.transcript).contains("unauthorized"), "second 401 is shown, no third request");
    }

    /** A renewed resend uses the turn's second attempt; the agent's later request is never sent. */
    static void renewalNeverCreatesAThirdDispatch() {
        HostTest.Rig r = new HostTest.Rig();
        r.client.renewing = true;
        r.host.send("open the calculator");
        r.client.callbacks.get(0).onResponse(401, null, "");
        r.client.reply(1, HostTest.openApp("calculator"));
        r.delay.runAll(); // the launch receipt settles; the agent wants its final request
        check(r.client.bodies.size() == 2, "no third inference request: " + r.client.bodies.size());
        check(!r.host.hasActiveTurn() && r.platform.started.size() == 1, "turn ended; the one launch stands");
    }

    static void stopDuringRenewedResendFencesIt() {
        HostTest.Rig r = new HostTest.Rig();
        r.client.renewing = true;
        r.host.send("hi");
        r.client.callbacks.get(0).onResponse(401, null, "");
        r.host.stop();
        check(r.client.cancels >= 1 && !r.host.hasActiveTurn(), "Stop cancels the renewed resend");
        int rejectedBefore = r.host.rejectedCount();
        r.client.reply(1, HostTest.finalText("late"));
        check(!String.join(" ", r.view.transcript).contains("late") && r.host.rejectedCount() == rejectedBefore + 1,
                "late reply after Stop is fenced");
    }

    // ---- harness ----

    static CredentialSource fixed(final EndpointCredential credential) {
        return new CredentialSource() {
            public EndpointCredential current(Cancel c) {
                return credential;
            }

            public EndpointCredential renew(Cancel c) {
                return credential;
            }
        };
    }

    static HttpsInferenceClient client(Fake fake) {
        return new HttpsInferenceClient(ENDPOINT, fixed(EndpointCredential.bind(ENDPOINT, MARKER)), fake::open);
    }

    static final class Result {
        int status = -1;
        String failure;
        String authProblem;
    }

    static Result call(HttpsInferenceClient client) {
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

            public void onAuthProblem(String reason) {
                result.authProblem = reason;
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
    static class Fake extends HttpURLConnection {
        final List<String> opened = new ArrayList<>();
        final Map<String, String> headers = new HashMap<>();
        final ByteArrayOutputStream out = new ByteArrayOutputStream();
        final int status;
        final String body;
        final String location;
        String retryAfter;
        IOException failure;
        boolean disconnected;

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
            if ("Location".equalsIgnoreCase(name)) {
                return location;
            }
            return "Retry-After".equalsIgnoreCase(name) ? retryAfter : null;
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
            disconnected = true;
        }

        @Override
        public boolean usingProxy() {
            return false;
        }
    }
}
