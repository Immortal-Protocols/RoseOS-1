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
 * Platform HTTPS transport for one inference request at a time, on a worker thread, with the
 * platform trust store and the app's network security config (no cleartext). POSTs the engine's
 * request body to the configured URL unchanged. Cancel disconnects the socket; the controller
 * also fences any reply that still arrives. No credentials are configured in this shell.
 */
final class HttpsInferenceClient implements InferenceClient {
    static final int CONNECT_TIMEOUT_MS = 10_000;
    static final int READ_TIMEOUT_MS = 120_000;
    static final int MAX_RESPONSE_BYTES = 4 * 1024 * 1024;

    private final String url;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();

    /** url must already pass EndpointPolicy.check. */
    HttpsInferenceClient(String url) {
        this.url = url;
    }

    @Override
    public Call send(final String requestBody, final Callback callback) {
        final HttpsURLConnection[] connection = {null};
        final boolean[] cancelled = {false};
        worker.execute(() -> {
            String body = null;
            String error = null;
            try {
                HttpsURLConnection c = (HttpsURLConnection) new URL(url).openConnection();
                synchronized (connection) {
                    if (cancelled[0]) {
                        return;
                    }
                    connection[0] = c;
                }
                c.setConnectTimeout(CONNECT_TIMEOUT_MS);
                c.setReadTimeout(READ_TIMEOUT_MS);
                c.setInstanceFollowRedirects(false);
                c.setRequestMethod("POST");
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                c.setRequestProperty("Accept", "application/json");
                byte[] out = requestBody.getBytes(StandardCharsets.UTF_8);
                c.setFixedLengthStreamingMode(out.length);
                try (OutputStream os = c.getOutputStream()) {
                    os.write(out);
                }
                int status = c.getResponseCode();
                if (status < 200 || status > 299) {
                    error = "HTTP " + status;
                } else {
                    body = readBounded(c.getInputStream());
                }
            } catch (IOException e) {
                error = e.getClass().getSimpleName();
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
            callback.onResult(body, error);
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

    private static String readBounded(InputStream in) throws IOException {
        try (InputStream input = in) {
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[16 * 1024];
            int n;
            while ((n = input.read(chunk)) != -1) {
                if (buffer.size() + n > MAX_RESPONSE_BYTES) {
                    throw new IOException("response larger than " + MAX_RESPONSE_BYTES + " bytes");
                }
                buffer.write(chunk, 0, n);
            }
            return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
        }
    }
}
