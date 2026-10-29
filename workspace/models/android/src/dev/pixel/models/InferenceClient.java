package dev.pixel.models;

/**
 * Carries one local-agent-v1 need_inference to the endpoint off the UI thread. It reports either
 * the raw HTTP status and body or a transport failure kind (connect, timeout_connect,
 * timeout_response, tls, response_too_large). Callbacks may arrive late; the host and Core fence them.
 */
public interface InferenceClient {
    interface Callback {
        void onResponse(int status, String retryAfter, String body);

        void onFailure(String kind);
    }

    interface Call {
        void cancel();
    }

    Call send(String requestBody, long timeoutMs, Callback callback);
}
