package dev.pixel.models;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;

/**
 * service-auth-v1 client: a hardware-attested device key enrolls once with an admin invite from a
 * scanned QR code, then each access token (at most 3600 s) is renewed by proof of possession over a
 * fresh server challenge. The bearer and the invite live in memory only; the store keeps just the
 * public key id and the enrollment state. A key is recorded as pending before its enrollment is sent;
 * a timeout, 5xx or lost reply re-sends that enrollment with the same key and chain (the server
 * recognizes the install without spending the code twice), and after a restart the key renews.
 *
 * 403 invite_required/invite_invalid waits for an explicit new scan. Any other 403 is terminal: the
 * bearer is cleared, the key and a revoked/refused state are kept, and nothing re-enrolls until
 * {@link #reset()} is explicitly selected. 429 and 5xx/timeouts retry a bounded number of times
 * (honoring Retry-After); 400/401 never retry.
 * Requests go only to the fixed origin, follow no redirects and read at most 64 KiB.
 */
public final class DeviceAuth implements CredentialSource {
    static final int MAX_REPLY_BYTES = 64 * 1024;
    static final int CONNECT_TIMEOUT_MS = 10_000;
    static final int READ_TIMEOUT_MS = 20_000;
    static final int MAX_ATTEMPTS = 3;
    static final long MAX_RETRY_AFTER_MS = 30_000;
    static final long RENEW_MARGIN_MS = 60_000;
    static final long MAX_TTL_S = 3600; // the agreed bearer TTL
    static final String STATE = "auth.state";
    static final String KEY_ID = "auth.key_id";

    /** Non-secret persistent state (SharedPreferences in the app). */
    public interface Store {
        String get(String key);

        void put(String key, String value);

        void remove(String key);
    }

    interface Clock {
        long nowMs();
    }

    interface Sleeper {
        void sleep(long ms) throws InterruptedException;
    }

    public enum State { NEW, PENDING, ENROLLED, INVITE_NEEDED, REVOKED, REFUSED }

    private final String challengeUrl;
    private final String tokenUrl;
    private final DeviceKey key;
    private final Store store;
    private final HttpsInferenceClient.Opener opener;
    private final Clock clock;
    private final Sleeper sleeper;
    private final java.util.concurrent.atomic.AtomicReference<String> invite =
            new java.util.concurrent.atomic.AtomicReference<>();
    private EndpointCredential bearer;
    private long expiresAtMs;

    public DeviceAuth(DeviceKey key, Store store) {
        this(ModelsService.CHALLENGE_URL, ModelsService.TOKEN_URL, key, store,
                u -> (javax.net.ssl.HttpsURLConnection) u.openConnection(), System::currentTimeMillis, Thread::sleep);
    }

    DeviceAuth(String challengeUrl, String tokenUrl, DeviceKey key, Store store, HttpsInferenceClient.Opener opener,
               Clock clock, Sleeper sleeper) {
        if (EndpointPolicy.check(challengeUrl) != null || EndpointPolicy.check(tokenUrl) != null
                || !EndpointCredential.origin(challengeUrl).equals(EndpointCredential.origin(tokenUrl))) {
            throw new IllegalArgumentException("auth URLs must be https on one origin");
        }
        this.challengeUrl = challengeUrl;
        this.tokenUrl = tokenUrl;
        this.key = key;
        this.store = store;
        this.opener = opener;
        this.clock = clock;
        this.sleeper = sleeper;
    }

    /** Reads only the persisted state, so the UI never waits on the network lock. */
    public State state() {
        String s = store.get(STATE);
        if (s == null) {
            return State.NEW;
        }
        switch (s) {
            case "pending":
                return State.PENDING;
            case "enrolled":
                return State.ENROLLED;
            case "invite_needed":
                return State.INVITE_NEEDED;
            case "revoked":
                return State.REVOKED;
            case "refused":
                return State.REFUSED;
            default:
                return State.NEW;
        }
    }

    /**
     * A code from a scanned QR (InviteLink), kept in memory only and sent with the next enrollment.
     * Refused (false) while revoked/refused, which need an explicit reset first, or already enrolled.
     */
    public boolean offerInvite(String code) {
        State state = state();
        if (!InviteLink.isCode(code) || state == State.REVOKED || state == State.REFUSED || state == State.ENROLLED) {
            return false;
        }
        invite.set(code);
        return true;
    }

    @Override
    public synchronized EndpointCredential current(Cancel cancel) throws AuthFailure {
        if (bearer != null && clock.nowMs() < expiresAtMs - RENEW_MARGIN_MS) {
            return bearer;
        }
        return renew(cancel);
    }

    @Override
    public synchronized EndpointCredential renew(Cancel cancel) throws AuthFailure {
        bearer = null;
        AuthFailure last = null;
        for (int attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
            if (cancel.isCancelled()) {
                throw AuthFailure.cancelled();
            }
            try {
                return step(cancel);
            } catch (Retry retry) {
                last = retry.failure;
                if (attempt < MAX_ATTEMPTS) {
                    pause(retry.waitMs, cancel);
                }
            }
        }
        throw last;
    }

    /**
     * One attempt (service-auth-v1 enrollment sequence). A key whose enrollment outcome is unknown is never
     * discarded: with an invite in memory its enrollment is re-sent with the same key and chain on a fresh signed
     * challenge (the server returns the same install without spending the code again, or enrolls it now); without one
     * (after a restart) it proves possession by renewal. Only a 401 challenge_mismatch on a never-enrolled key, during
     * an explicit scan, allows a new key.
     */
    private EndpointCredential step(Cancel cancel) throws AuthFailure, Retry {
        State state = state();
        if (state == State.REVOKED) {
            throw AuthFailure.status(403, "auth_revoked");
        }
        if (state == State.REFUSED) {
            throw AuthFailure.status(403, "auth_refused");
        }
        boolean present = keyPresent();
        if (state == State.ENROLLED && present) {
            try {
                return renewOnce(cancel);
            } catch (KeyUnknown e) {
                throw AuthFailure.status(401, "auth_rejected"); // the server forgot an enrolled key: no auto re-enroll
            }
        }
        boolean pendingKey = present && (state == State.PENDING || state == State.INVITE_NEEDED);
        String code = invite.get();
        if (code == null) {
            if (state == State.PENDING && pendingKey) {
                try {
                    EndpointCredential credential = renewOnce(cancel);
                    store.put(STATE, "enrolled");
                    return credential;
                } catch (KeyUnknown e) {
                    // never committed: the same key enrolls again after a scan
                }
            }
            store.put(STATE, "invite_needed");
            throw AuthFailure.status(403, "auth_invite_needed");
        }
        if (pendingKey) {
            try {
                return enrollOnce(cancel, code, true);
            } catch (StaleKey e) {
                // never enrolled and its attested challenge expired; this explicit scan may replace it
            }
        }
        try {
            return enrollOnce(cancel, code, false);
        } catch (StaleKey e) {
            throw AuthFailure.status(401, "auth_rejected"); // not thrown for a new key
        }
    }

    /** Explicitly selected only: forget the key, state and any invite; the next scan enrolls again. */
    public synchronized void reset() throws Exception {
        bearer = null;
        invite.set(null);
        key.delete();
        store.remove(STATE);
        store.remove(KEY_ID);
    }

    private boolean keyPresent() {
        try {
            return key.exists() && key.keyId().equals(store.get(KEY_ID));
        } catch (Exception e) {
            return false;
        }
    }

    private EndpointCredential enrollOnce(Cancel cancel, String code, boolean reuseKey)
            throws AuthFailure, Retry, StaleKey {
        byte[] challenge = challenge(cancel);
        String body;
        String keyId;
        try {
            if (!reuseKey) {
                key.create(challenge); // its attestation carries this first challenge for good
            }
            keyId = key.keyId();
            StringBuilder chain = new StringBuilder("[");
            List<String> certs = key.attestation();
            for (int i = 0; i < certs.size(); i++) {
                chain.append(i == 0 ? "" : ",").append(Json.quote(certs.get(i)));
            }
            body = "{\"challenge\":" + Json.quote(new String(challenge, StandardCharsets.UTF_8))
                    + ",\"attestation\":" + chain.append(']') + ",\"signature\":" + Json.quote(key.sign(challenge))
                    + ",\"invite\":" + Json.quote(code) + "}";
        } catch (Exception e) {
            throw AuthFailure.status(500, "auth_key_unavailable");
        }
        store.put(KEY_ID, keyId);
        store.put(STATE, "pending"); // before sending: an unknown outcome is retried with this same key
        Reply reply = post(tokenUrl, body, cancel);
        if (reuseKey && reply.status == 401 && "challenge_mismatch".equals(errorCode(reply))) {
            throw new StaleKey();
        }
        if (reply.status == 403) {
            String error = errorCode(reply);
            if ("invite_required".equals(error) || "invite_invalid".equals(error)) {
                invite.set(null);
                store.put(STATE, "invite_needed"); // waits for an explicit new scan; the key is kept
                throw AuthFailure.status(403, "auth_invite_invalid");
            }
            store.put(STATE, "refused"); // enrollment_closed, attestation_revoked, ...: terminal, key kept
            throw AuthFailure.status(403, "auth_refused");
        }
        EndpointCredential credential = token(reply, keyId);
        store.put(STATE, "enrolled");
        invite.set(null);
        return credential;
    }

    /** A never-enrolled key whose attested challenge the server no longer accepts (401 challenge_mismatch). */
    private static final class StaleKey extends Exception {
    }

    private EndpointCredential renewOnce(Cancel cancel) throws AuthFailure, Retry, KeyUnknown {
        byte[] challenge = challenge(cancel);
        String keyId = store.get(KEY_ID);
        String signature;
        try {
            signature = key.sign(challenge);
        } catch (Exception e) {
            throw AuthFailure.status(500, "auth_key_unavailable");
        }
        Reply reply = post(tokenUrl, "{\"challenge\":" + Json.quote(new String(challenge, StandardCharsets.UTF_8))
                + ",\"key_id\":" + Json.quote(keyId) + ",\"signature\":" + Json.quote(signature) + "}", cancel);
        if (reply.status == 401 && "key_unknown".equals(errorCode(reply))) {
            throw new KeyUnknown();
        }
        if (reply.status == 403) {
            store.put(STATE, "revoked"); // the key is kept; recovery is an explicit reset
            throw AuthFailure.status(403, "auth_revoked");
        }
        return token(reply, keyId);
    }

    /** The server never registered this key (renewal answered 401 key_unknown). */
    private static final class KeyUnknown extends Exception {
    }

    /** The fixed lowercase error code of a JSON error reply, or "" (never echoed to the user). */
    static String errorCode(Reply reply) {
        try {
            String code = Json.string(Json.object(Json.parse(reply.body)), "error");
            return code.matches("[a-z_]{1,40}") ? code : "";
        } catch (RuntimeException e) {
            return "";
        }
    }

    private byte[] challenge(Cancel cancel) throws AuthFailure, Retry {
        Reply reply = post(challengeUrl, "{}", cancel);
        if (reply.status == 403) {
            throw AuthFailure.status(403, "auth_refused"); // enrollment closed; not recorded as a key state
        }
        accept(reply);
        try {
            String challenge = Json.string(Json.object(Json.parse(reply.body)), "challenge");
            if (challenge.isEmpty() || challenge.length() > 4096) {
                throw new IllegalArgumentException();
            }
            return challenge.getBytes(StandardCharsets.UTF_8);
        } catch (RuntimeException e) {
            throw AuthFailure.status(502, "auth_invalid_reply");
        }
    }

    private EndpointCredential token(Reply reply, String keyId) throws AuthFailure, Retry {
        accept(reply);
        try {
            Map<String, Object> o = Json.object(Json.parse(reply.body));
            long ttl = Json.number(o, "expires_in");
            if (!"Bearer".equals(Json.string(o, "token_type")) || !keyId.equals(Json.string(o, "key_id"))
                    || ttl < 1 || ttl > MAX_TTL_S) {
                throw new IllegalArgumentException();
            }
            bearer = EndpointCredential.bind(tokenUrl, Json.string(o, "access_token")); // auth origin = chat origin
            expiresAtMs = clock.nowMs() + ttl * 1000;
            return bearer;
        } catch (RuntimeException e) {
            throw AuthFailure.status(502, "auth_invalid_reply");
        }
    }

    /** Throws for any non-200 reply: 429/5xx are retried by the caller, everything else is final. */
    private static void accept(Reply reply) throws AuthFailure, Retry {
        if (reply.status == 200) {
            return;
        }
        if (reply.status == 429) {
            throw new Retry(AuthFailure.status(429, "auth_rate_limited"), retryAfterMs(reply.retryAfter));
        }
        if (reply.status >= 500) {
            throw new Retry(AuthFailure.status(503, "auth_unavailable"), 1000);
        }
        throw AuthFailure.status(reply.status == 400 ? 400 : 401, "auth_rejected");
    }

    static long retryAfterMs(String header) {
        try {
            return Math.min(MAX_RETRY_AFTER_MS, Math.max(1000, Long.parseLong(header.trim()) * 1000));
        } catch (RuntimeException e) {
            return 1000;
        }
    }

    private void pause(long ms, Cancel cancel) throws AuthFailure {
        long end = clock.nowMs() + ms;
        try {
            while (clock.nowMs() < end) {
                if (cancel.isCancelled()) {
                    throw AuthFailure.cancelled();
                }
                sleeper.sleep(Math.min(100, end - clock.nowMs()));
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw AuthFailure.cancelled();
        }
    }

    /** A failure worth one more bounded attempt. */
    private static final class Retry extends Exception {
        final AuthFailure failure;
        final long waitMs;

        Retry(AuthFailure failure, long waitMs) {
            super(failure.reason);
            this.failure = failure;
            this.waitMs = waitMs;
        }
    }

    static final class Reply {
        final int status;
        final String retryAfter;
        final String body;

        Reply(int status, String retryAfter, String body) {
            this.status = status;
            this.retryAfter = retryAfter;
            this.body = body;
        }
    }

    private Reply post(String url, String json, Cancel cancel) throws AuthFailure, Retry {
        if (cancel.isCancelled()) {
            throw AuthFailure.cancelled();
        }
        boolean connected = false;
        HttpURLConnection c = null;
        try {
            c = opener.open(new URL(url));
            cancel.track(c);
            c.setConnectTimeout(CONNECT_TIMEOUT_MS);
            c.setReadTimeout(READ_TIMEOUT_MS);
            c.setInstanceFollowRedirects(false);
            c.setRequestMethod("POST");
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/json");
            c.setRequestProperty("Accept", "application/json");
            byte[] out = json.getBytes(StandardCharsets.UTF_8);
            c.setFixedLengthStreamingMode(out.length);
            c.connect();
            connected = true;
            try (OutputStream os = c.getOutputStream()) {
                os.write(out);
            }
            int status = c.getResponseCode();
            InputStream stream = status >= 400 ? c.getErrorStream() : c.getInputStream();
            return new Reply(status, c.getHeaderField("Retry-After"), stream == null ? "" : read(stream));
        } catch (TooLarge e) {
            throw AuthFailure.status(502, "auth_invalid_reply");
        } catch (javax.net.ssl.SSLException e) {
            throw AuthFailure.transport("tls"); // a certificate problem is not retried
        } catch (java.net.SocketTimeoutException e) {
            throw new Retry(AuthFailure.transport(connected ? "timeout_response" : "timeout_connect"), 1000);
        } catch (IOException | RuntimeException e) {
            if (cancel.isCancelled()) {
                throw AuthFailure.cancelled();
            }
            throw new Retry(AuthFailure.transport("connect"), 1000);
        } finally {
            cancel.track(null);
            if (c != null) {
                c.disconnect();
            }
        }
    }

    private static final class TooLarge extends IOException {
    }

    private static String read(InputStream in) throws IOException {
        try (InputStream input = in) {
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[8192];
            int n;
            while ((n = input.read(chunk)) != -1) {
                if (buffer.size() + n > MAX_REPLY_BYTES) {
                    throw new TooLarge();
                }
                buffer.write(chunk, 0, n);
            }
            return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
        }
    }
}
