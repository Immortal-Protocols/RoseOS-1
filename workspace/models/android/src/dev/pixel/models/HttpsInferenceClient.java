package dev.pixel.models;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Platform HTTPS transport for one local-agent-v1 need_inference at a time, on a worker thread,
 * with the platform trust store and the app's network security config (no cleartext). It first
 * obtains the bearer from the CredentialSource (which may renew or enroll on the same worker), then
 * POSTs the agent's request body unchanged and reports the raw status and body, or a transport
 * failure kind. A credential it cannot obtain is reported as the auth server's status (401, 403,
 * 429, 503) or transport kind, plus a fixed reason for the UI. Redirects are not followed, so the
 * bearer is only ever sent to its own origin. Cancel stops credential work and disconnects the
 * socket; the agent also rejects any reply that still arrives. No exception text is reported.
 */
final class HttpsInferenceClient implements InferenceClient {
    static final int CONNECT_TIMEOUT_MS = 10_000;
    static final int MAX_RESPONSE_BYTES = 4 * 1024 * 1024;

    /** Opens the connection for a URL; the platform's HTTPS stack outside tests. */
    interface Opener {
        HttpURLConnection open(URL url) throws IOException;
    }

    private final String url;
    private final CredentialSource credentials;
    private final Opener opener;
    /** One daemon worker for the process: one request at a time, and no thread left per client. */
    private static final ExecutorService WORKER = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "rose-models-https");
        t.setDaemon(true);
        return t;
    });

    HttpsInferenceClient(String url, CredentialSource credentials) {
        this(url, credentials, u -> (javax.net.ssl.HttpsURLConnection) u.openConnection());
    }

    HttpsInferenceClient(String url, CredentialSource credentials, Opener opener) {
        if (EndpointPolicy.check(url) != null) {
            throw new IllegalArgumentException("endpoint rejected: " + EndpointPolicy.check(url));
        }
        this.url = url;
        this.credentials = credentials;
        this.opener = opener;
    }

    /** Runs a task on the network worker (startup credential check). */
    static void background(Runnable task) {
        WORKER.execute(task);
    }

    @Override
    public boolean renews() {
        return true;
    }

    @Override
    public Call send(String requestBody, long timeoutMs, Callback callback) {
        return dispatch(requestBody, timeoutMs, callback, false);
    }

    @Override
    public Call resend(String requestBody, long timeoutMs, Callback callback) {
        return dispatch(requestBody, timeoutMs, callback, true);
    }

    private Call dispatch(final String requestBody, final long timeoutMs, final Callback callback, final boolean renew) {
        final HttpURLConnection[] connection = {null};
        final boolean[] cancelled = {false};
        final CredentialSource.Cancel cancel = new CredentialSource.Cancel() {
            @Override
            public boolean isCancelled() {
                synchronized (connection) {
                    return cancelled[0];
                }
            }

            @Override
            public void track(HttpURLConnection c) {
                synchronized (connection) {
                    connection[0] = c;
                    if (cancelled[0] && c != null) {
                        c.disconnect();
                    }
                }
            }
        };
        WORKER.execute(() -> {
            EndpointCredential credential;
            try {
                credential = renew ? credentials.renew(cancel) : credentials.current(cancel);
                if (!credential.matches(url)) {
                    throw CredentialSource.AuthFailure.status(401, "auth_wrong_origin");
                }
            } catch (CredentialSource.AuthFailure f) {
                if (f.isCancelled() || cancel.isCancelled()) {
                    return;
                }
                callback.onAuthProblem(f.reason);
                if (f.transportKind != null) {
                    callback.onFailure(f.transportKind);
                } else {
                    callback.onResponse(f.status, null, "");
                }
                return;
            }
            int status = -1;
            String retryAfter = null;
            String body = null;
            String failure = null;
            boolean connected = false;
            try {
                HttpURLConnection c = opener.open(new URL(url));
                synchronized (connection) {
                    if (cancelled[0]) {
                        return;
                    }
                    connection[0] = c;
                }
                c.setConnectTimeout(CONNECT_TIMEOUT_MS);
                c.setReadTimeout((int) Math.min(Integer.MAX_VALUE, Math.max(1, timeoutMs)));
                c.setInstanceFollowRedirects(false);
                c.setRequestMethod("POST");
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                c.setRequestProperty("Accept", "application/json");
                c.setRequestProperty("Authorization", credential.authorizationHeader());
                byte[] out = requestBody.getBytes(StandardCharsets.UTF_8);
                c.setFixedLengthStreamingMode(out.length);
                c.connect();
                connected = true;
                try (OutputStream os = c.getOutputStream()) {
                    os.write(out);
                }
                status = c.getResponseCode();
                retryAfter = c.getHeaderField("Retry-After");
                InputStream stream = status >= 400 ? c.getErrorStream() : c.getInputStream();
                body = stream == null ? "" : readBounded(stream);
            } catch (TooLarge e) {
                failure = "response_too_large";
            } catch (javax.net.ssl.SSLException e) {
                failure = "tls";
            } catch (java.net.SocketTimeoutException e) {
                failure = connected ? "timeout_response" : "timeout_connect";
            } catch (IOException | RuntimeException e) {
                failure = "connect";
            } finally {
                synchronized (connection) {
                    if (connection[0] != null) {
                        connection[0].disconnect();
                    }
                    if (cancelled[0]) {
                        return;
                    }
                }
            }
            if (failure != null) {
                callback.onFailure(failure);
            } else {
                callback.onResponse(status, retryAfter, body);
            }
        });
        return () -> {
            synchronized (connection) {
                cancelled[0] = true;
                if (connection[0] != null) {
                    connection[0].disconnect();
                }
            }
        };
    }

    /** The body exceeded MAX_RESPONSE_BYTES. */
    private static final class TooLarge extends IOException {
        TooLarge() {
            super("response larger than " + MAX_RESPONSE_BYTES + " bytes");
        }
    }

    private static String readBounded(InputStream in) throws IOException {
        try (InputStream input = in) {
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[16 * 1024];
            int n;
            while ((n = input.read(chunk)) != -1) {
                if (buffer.size() + n > MAX_RESPONSE_BYTES) {
                    throw new TooLarge();
                }
                buffer.write(chunk, 0, n);
            }
            return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
        }
    }
}
