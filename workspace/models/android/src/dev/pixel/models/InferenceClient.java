package dev.pixel.models;

/** Sends one inference request off the UI thread. Callbacks may arrive late or twice; the
 * controller fences them. */
public interface InferenceClient {
    interface Callback {
        void onResult(String body, String error);
    }

    interface Call {
        void cancel();
    }

    Call send(String requestBody, Callback callback);
}
