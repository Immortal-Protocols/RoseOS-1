package dev.pixel.models;

import static dev.pixel.models.HostTest.check;

import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.MessageDigest;
import java.security.Signature;
import java.security.spec.ECGenParameterSpec;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Collections;
import java.util.Deque;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * service-auth-v1 client (DeviceAuth) against a scripted in-memory server and a software P-256 key
 * standing in for Android Keystore. Synthetic attestation; proves the client protocol only.
 */
final class DeviceAuthTest {
    static final String ORIGIN = "https://auth.example.test";
    static final String CHALLENGE = ORIGIN + "/v1/auth/challenge";
    static final String TOKEN = ORIGIN + "/v1/auth/token";
    static final String CHAT = ORIGIN + "/v1/chat/completions";
    static final String BEARER = "SYNTHETIC-BEARER-NOT-A-SECRET-91c2";

    private DeviceAuthTest() {
    }

    static void all() {
        try {
            run();
        } catch (Exception e) {
            throw new AssertionError(e);
        }
    }

    static void run() throws Exception {
        freshInstallEnrollsAutomatically();
        restartRenewsWithoutNewKeyOrTyping();
        expiryRenewsBeforeUse();
        revokedIsTerminalUntilExplicitReset();
        refusedEnrollmentIsTerminal();
        rateLimitHonorsRetryAfterAndIsBounded();
        unavailableIsBoundedAndTlsIsNotRetried();
        rejectedAndMalformedRepliesDoNotRetry();
        authOriginsMustMatch();
        cancelDuringBackoffStops();
        endToEndInferenceUsesTheIssuedBearer();
    }

    static void freshInstallEnrollsAutomatically() throws Exception {
        Server s = new Server();
        SoftKey key = new SoftKey();
        MemStore store = new MemStore();
        s.challenge("c-1").token(200, tokenReply(key));
        EndpointCredential c = auth(s, key, store).current(NEVER);
        check(c.matches(CHAT) && ("Bearer " + BEARER).equals(c.authorizationHeader()), "fresh install gets a bearer");
        Map<String, Object> enroll = Json.object(Json.parse(s.bodies.get(1)));
        check("c-1".equals(Json.string(enroll, "challenge")) && enroll.get("attestation") instanceof List
                && key.verify("c-1", Json.string(enroll, "signature")), "enroll: challenge, attestation, valid signature");
        check(key.created == 1 && key.lastChallenge.equals("c-1"), "key attests the server challenge");
        check(DeviceAuth.State.ENROLLED.toString().equals(store.get(DeviceAuth.STATE).toUpperCase())
                && key.keyIdHex().equals(store.get(DeviceAuth.KEY_ID)), "state and public key id stored");
        check(!store.values().contains(BEARER), "bearer is not persisted");
    }

    static void restartRenewsWithoutNewKeyOrTyping() throws Exception {
        Server s = new Server();
        SoftKey key = new SoftKey();
        MemStore store = new MemStore();
        s.challenge("c-1").token(200, tokenReply(key));
        auth(s, key, store).current(NEVER);
        s.challenge("c-2").token(200, tokenReply(key));
        DeviceAuth restarted = auth(s, key, store); // new process: no bearer in memory
        restarted.current(NEVER);
        Map<String, Object> renew = Json.object(Json.parse(s.bodies.get(3)));
        check(key.created == 1 && !renew.containsKey("attestation") && key.keyIdHex().equals(Json.string(renew, "key_id"))
                && key.verify("c-2", Json.string(renew, "signature")), "restart renews by proof of possession, same key");
    }

    static void expiryRenewsBeforeUse() throws Exception {
        Server s = new Server();
        SoftKey key = new SoftKey();
        long[] now = {1_000_000};
        DeviceAuth a = new DeviceAuth(CHALLENGE, TOKEN, key, new MemStore(), s::open, () -> now[0], ms -> now[0] += ms);
        s.challenge("c-1").token(200, tokenReply(key));
        a.current(NEVER);
        a.current(NEVER);
        check(s.opened.size() == 2, "a valid bearer is reused");
        now[0] += 3600_000 - DeviceAuth.RENEW_MARGIN_MS;
        s.challenge("c-2").token(200, tokenReply(key));
        a.current(NEVER);
        check(s.opened.size() == 4 && key.created == 1, "near expiry: renewed, no new key");
    }

    static void revokedIsTerminalUntilExplicitReset() throws Exception {
        Server s = new Server();
        SoftKey key = new SoftKey();
        MemStore store = new MemStore();
        s.challenge("c-1").token(200, tokenReply(key));
        auth(s, key, store).current(NEVER);
        s.challenge("c-2").token(403, "{\"error\":\"revoked\"}");
        DeviceAuth a = auth(s, key, store);
        expect(a, 403, "auth_revoked");
        check(key.exists() && "revoked".equals(store.get(DeviceAuth.STATE)), "403: key kept, revoked recorded");
        int opened = s.opened.size();
        expect(auth(s, key, store), 403, "auth_revoked");
        check(s.opened.size() == opened && key.created == 1, "restart: no request, no new key, no re-enrollment");
        try {
            a.reset();
        } catch (Exception e) {
            throw new AssertionError(e);
        }
        s.challenge("c-3").token(200, tokenReply(key));
        a.current(NEVER);
        check(key.created == 2 && "enrolled".equals(store.get(DeviceAuth.STATE)), "explicit reset enrolls a new key");
    }

    static void refusedEnrollmentIsTerminal() throws Exception {
        Server s = new Server();
        SoftKey key = new SoftKey();
        MemStore store = new MemStore();
        s.challenge("c-1").token(403, "{\"error\":\"enrollment_closed\"}");
        expect(auth(s, key, store), 403, "auth_refused");
        check(s.opened.size() == 2 && "refused".equals(store.get(DeviceAuth.STATE)) && key.exists(),
                "refused once: no retry, key kept");
        expect(auth(s, key, store), 403, "auth_refused");
        check(s.opened.size() == 2, "refused persists across restart without requests");
    }

    static void rateLimitHonorsRetryAfterAndIsBounded() throws Exception {
        Server s = new Server();
        SoftKey key = new SoftKey();
        List<Long> waits = new ArrayList<>();
        long[] now = {0};
        DeviceAuth a = new DeviceAuth(CHALLENGE, TOKEN, key, new MemStore(), s::open, () -> now[0], ms -> {
            waits.add(ms);
            now[0] += ms;
        });
        for (int i = 0; i < 3; i++) {
            s.reply(CHALLENGE, 429, "{}", "7");
        }
        expect(a, 429, "auth_rate_limited");
        long waited = 0;
        for (long w : waits) {
            waited += w;
        }
        check(s.opened.size() == 3 && waited == 14_000, "3 attempts, Retry-After 7 s honored twice: " + waited);
        check(DeviceAuth.retryAfterMs("0") == 1000 && DeviceAuth.retryAfterMs("9999") == DeviceAuth.MAX_RETRY_AFTER_MS
                && DeviceAuth.retryAfterMs("soon") == 1000, "Retry-After floor 1 s, capped, unparseable -> 1 s");
    }

    static void unavailableIsBoundedAndTlsIsNotRetried() throws Exception {
        Server s = new Server();
        SoftKey key = new SoftKey();
        for (int i = 0; i < 3; i++) {
            s.reply(CHALLENGE, 503, "{}", null);
        }
        expect(auth(s, key, new MemStore()), 503, "auth_unavailable");
        check(s.opened.size() == 3, "5xx: exactly 3 attempts");
        Server t = new Server();
        t.failNext(CHALLENGE, new javax.net.ssl.SSLHandshakeException("bad cert " + BEARER));
        try {
            auth(t, key, new MemStore()).current(NEVER);
            check(false, "tls accepted");
        } catch (CredentialSource.AuthFailure f) {
            check("tls".equals(f.transportKind) && t.opened.size() == 1 && !String.valueOf(f.getMessage()).contains(BEARER),
                    "tls: one attempt, kind only");
        }
    }

    static void rejectedAndMalformedRepliesDoNotRetry() throws Exception {
        Server s = new Server();
        SoftKey key = new SoftKey();
        s.challenge("c-1").token(401, "{\"error\":\"bad_signature\"}");
        expect(auth(s, key, new MemStore()), 401, "auth_rejected");
        check(s.opened.size() == 2, "401 on token: no retry");
        Server m = new Server();
        m.reply(CHALLENGE, 200, "{\"nonce\":\"x\"}", null);
        expect(auth(m, key, new MemStore()), 502, "auth_invalid_reply");
        Server w = new Server();
        w.challenge("c-1").token(200, "{\"access_token\":\"" + BEARER + "\",\"token_type\":\"Bearer\",\"expires_in\":3600,"
                + "\"key_id\":\"" + "0000000000000000000000000000000000000000000000000000000000000000" + "\"}");
        expect(auth(w, key, new MemStore()), 502, "auth_invalid_reply");
        Server r = new Server();
        r.reply(CHALLENGE, 302, "", null);
        expect(auth(r, key, new MemStore()), 401, "auth_rejected");
        check(r.opened.size() == 1, "redirect not followed or retried");
    }

    static void authOriginsMustMatch() throws Exception {
        try {
            new DeviceAuth(CHALLENGE, "https://other.example.test/v1/auth/token", new SoftKey(), new MemStore(),
                    u -> null, () -> 0, ms -> { });
            check(false, "split origins accepted");
        } catch (IllegalArgumentException e) {
            check(true, "auth URLs must share one https origin");
        }
    }

    static void cancelDuringBackoffStops() throws Exception {
        Server s = new Server();
        for (int i = 0; i < 3; i++) {
            s.reply(CHALLENGE, 503, "{}", null);
        }
        final boolean[] cancelled = {false};
        long[] now = {0};
        DeviceAuth a = new DeviceAuth(CHALLENGE, TOKEN, new SoftKey(), new MemStore(), s::open, () -> now[0], ms -> {
            now[0] += ms;
            cancelled[0] = true; // Stop pressed during the first backoff
        });
        try {
            a.current(new CredentialSource.Cancel() {
                public boolean isCancelled() {
                    return cancelled[0];
                }

                public void track(HttpURLConnection c) {
                }
            });
            check(false, "cancel ignored");
        } catch (CredentialSource.AuthFailure f) {
            check(f.isCancelled() && s.opened.size() == 1, "Stop during backoff: cancelled, no further request");
        }
    }

    static void endToEndInferenceUsesTheIssuedBearer() throws Exception {
        Server s = new Server();
        SoftKey key = new SoftKey();
        s.challenge("c-1").token(200, tokenReply(key));
        s.reply(CHAT, 200, "{\"ok\":true}", null);
        HttpsInferenceClient client = new HttpsInferenceClient(CHAT, auth(s, key, new MemStore()), s::open);
        final int[] status = {-1};
        final CountDownLatch done = new CountDownLatch(1);
        client.send("{\"model\":\"muse-glimmer\"}", 5000, new InferenceClient.Callback() {
            public void onResponse(int st, String retryAfter, String body) {
                status[0] = st;
                done.countDown();
            }

            public void onFailure(String kind) {
                done.countDown();
            }
        });
        try {
            check(done.await(10, TimeUnit.SECONDS), "answered");
        } catch (InterruptedException e) {
            throw new AssertionError(e);
        }
        AuthTest.Fake chat = s.last(CHAT);
        check(status[0] == 200 && ("Bearer " + BEARER).equals(chat.headers.get("Authorization")), "no settings: enroll then chat");
        check(s.fakes.get(0).headers.get("Authorization") == null && s.fakes.get(1).headers.get("Authorization") == null,
                "auth calls carry no bearer");
    }

    // ---- harness ----

    static final CredentialSource.Cancel NEVER = new CredentialSource.Cancel() {
        public boolean isCancelled() {
            return false;
        }

        public void track(HttpURLConnection c) {
        }
    };

    static DeviceAuth auth(Server s, DeviceKey key, MemStore store) {
        return new DeviceAuth(CHALLENGE, TOKEN, key, store, s::open, System::currentTimeMillis, ms -> { });
    }

    static void expect(DeviceAuth a, int status, String reason) {
        try {
            a.current(NEVER);
            check(false, "expected " + reason);
        } catch (CredentialSource.AuthFailure f) {
            check(f.status == status && reason.equals(f.reason), "expected " + status + " " + reason + ", got "
                    + f.status + " " + f.reason);
        }
    }

    /** Read when the server answers, after the client has created its key. */
    static java.util.function.Supplier<String> tokenReply(SoftKey key) {
        return () -> "{\"access_token\":\"" + BEARER + "\",\"token_type\":\"Bearer\",\"expires_in\":3600,\"key_id\":\""
                + key.keyIdHex() + "\"}";
    }

    static final class MemStore implements DeviceAuth.Store {
        final Map<String, String> map = new HashMap<>();

        public String get(String k) {
            return map.get(k);
        }

        public void put(String k, String v) {
            map.put(k, v);
        }

        public void remove(String k) {
            map.remove(k);
        }

        java.util.Collection<String> values() {
            return map.values();
        }
    }

    /** Software P-256 key; synthetic "attestation" names the challenge it was created for. */
    static final class SoftKey implements DeviceKey {
        KeyPair pair;
        int created;
        String lastChallenge;

        public boolean exists() {
            return pair != null;
        }

        public void create(byte[] challenge) throws Exception {
            KeyPairGenerator g = KeyPairGenerator.getInstance("EC");
            g.initialize(new ECGenParameterSpec("secp256r1"));
            pair = g.generateKeyPair();
            created++;
            lastChallenge = new String(challenge, StandardCharsets.UTF_8);
        }

        public String keyId() {
            return keyIdHex();
        }

        String keyIdHex() {
            try {
                StringBuilder hex = new StringBuilder();
                for (byte b : MessageDigest.getInstance("SHA-256").digest(pair.getPublic().getEncoded())) {
                    hex.append(String.format("%02x", b));
                }
                return hex.toString();
            } catch (Exception e) {
                throw new AssertionError(e);
            }
        }

        public List<String> attestation() {
            return Collections.singletonList(Base64.getEncoder().encodeToString(
                    ("synthetic-attestation:" + lastChallenge).getBytes(StandardCharsets.UTF_8)));
        }

        public String sign(byte[] data) throws Exception {
            Signature s = Signature.getInstance("SHA256withECDSA");
            s.initSign(pair.getPrivate());
            s.update(data);
            return Base64.getEncoder().encodeToString(s.sign());
        }

        boolean verify(String challenge, String signature) {
            try {
                Signature s = Signature.getInstance("SHA256withECDSA");
                s.initVerify(pair.getPublic());
                s.update(challenge.getBytes(StandardCharsets.UTF_8));
                return s.verify(Base64.getDecoder().decode(signature));
            } catch (Exception e) {
                return false;
            }
        }

        public void delete() {
            pair = null;
        }
    }

    /** Scripted replies per URL; each connection is a fresh in-memory exchange. */
    static final class Server {
        final Map<String, Deque<AuthTest.Fake>> queues = new HashMap<>();
        final List<String> opened = new ArrayList<>();
        final List<String> bodies = new ArrayList<>();
        final List<AuthTest.Fake> fakes = new ArrayList<>();

        Server challenge(String challenge) {
            return reply(CHALLENGE, 200, "{\"challenge\":" + Json.quote(challenge) + ",\"expires_in\":60}", null);
        }

        Server token(int status, String body) {
            return reply(TOKEN, status, body, null);
        }

        Server token(int status, java.util.function.Supplier<String> body) {
            queues.computeIfAbsent(TOKEN, k -> new ArrayDeque<>()).add(new LazyFake(status, body));
            return this;
        }

        Server reply(String url, int status, String body, String retryAfter) {
            AuthTest.Fake f = new AuthTest.Fake(status, body, null);
            f.retryAfter = retryAfter;
            queues.computeIfAbsent(url, k -> new ArrayDeque<>()).add(f);
            return this;
        }

        void failNext(String url, java.io.IOException failure) {
            AuthTest.Fake f = new AuthTest.Fake(0, "", null);
            f.failure = failure;
            queues.computeIfAbsent(url, k -> new ArrayDeque<>()).add(f);
        }

        HttpURLConnection open(URL u) {
            Deque<AuthTest.Fake> q = queues.get(u.toString());
            if (q == null || q.isEmpty()) {
                throw new AssertionError("unexpected request to " + u);
            }
            AuthTest.Fake f = q.poll();
            opened.add(u.toString());
            fakes.add(f);
            final AuthTest.Fake fake = f;
            bodies.add(null);
            final int index = bodies.size() - 1;
            return new Recording(fake, () -> bodies.set(index, fake.sent()));
        }

        AuthTest.Fake last(String url) {
            for (int i = opened.size() - 1; i >= 0; i--) {
                if (opened.get(i).equals(url)) {
                    return fakes.get(i);
                }
            }
            return null;
        }
    }

    static final class LazyFake extends AuthTest.Fake {
        final java.util.function.Supplier<String> lazy;

        LazyFake(int status, java.util.function.Supplier<String> lazy) {
            super(status, "", null);
            this.lazy = lazy;
        }

        @Override
        public java.io.InputStream getInputStream() {
            return new java.io.ByteArrayInputStream(lazy.get().getBytes(StandardCharsets.UTF_8));
        }
    }

    /** Delegates to a Fake and records the request body once the response is read. */
    static final class Recording extends HttpURLConnection {
        final AuthTest.Fake f;
        final Runnable onResponse;

        Recording(AuthTest.Fake f, Runnable onResponse) {
            super(null);
            this.f = f;
            this.onResponse = onResponse;
        }

        public void setRequestProperty(String k, String v) {
            f.setRequestProperty(k, v);
        }

        public void setRequestMethod(String m) throws java.net.ProtocolException {
            f.setRequestMethod(m);
        }

        public void setInstanceFollowRedirects(boolean b) {
            f.setInstanceFollowRedirects(b);
        }

        public void connect() throws java.io.IOException {
            f.connect();
        }

        public java.io.OutputStream getOutputStream() {
            return f.getOutputStream();
        }

        public int getResponseCode() {
            onResponse.run();
            return f.getResponseCode();
        }

        public String getHeaderField(String n) {
            return f.getHeaderField(n);
        }

        public java.io.InputStream getInputStream() {
            return f.getInputStream();
        }

        public java.io.InputStream getErrorStream() {
            return f.getErrorStream();
        }

        public void disconnect() {
            f.disconnect();
        }

        public boolean usingProxy() {
            return false;
        }
    }
}
