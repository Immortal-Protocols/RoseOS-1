package dev.pixel.models;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import javax.net.ssl.HttpsURLConnection;

/**
 * Platform HTTPS transport for one local-agent-v1 need_inference at a time, on a worker thread,
 * with the platform trust store and the app's network security config (no cleartext). POSTs the
 * agent's request body to the configured URL unchanged and reports the raw status and body, or a
 * transport failure kind. Redirects are not followed. Cancel disconnects the socket; the agent also
 * rejects any reply that still arrives. No credentials are configured in this app.
 */
final class HttpsInferenceClient implements InferenceClient {
    static final int CONNECT_TIMEOUT_MS = 10_000;
    static final int MAX_RESPONSE_BYTES = 4 * 1024 * 1024;

    private final String url;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();

    /** url must already pass EndpointPolicy.check. */
    HttpsInferenceClient(String url) {
        this.url = url;
    }

    @Override
    public Call send(final String requestBody, final long timeoutMs, final Callback callback) {
        final HttpsURLConnection[] connection = {null};
        final boolean[] cancelled = {false};
        worker.execute(() -> {
            int status = -1;
            String retryAfter = null;
            String body = null;
            String failure = null;
            boolean connected = false;
            try {
                HttpsURLConnection c = (HttpsURLConnection) new URL(url).openConnection();
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
