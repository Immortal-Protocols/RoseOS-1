package dev.pixel.models;

/**
 * LOCAL FAKE transport for the fake engine: answers after a delay with no network access.
 * Cancel suppresses the answer, like a disconnected request.
 */
public final class FakeInferenceClient implements InferenceClient {
    /** Runs a task after a delay on some thread. */
    public interface Scheduler {
        void schedule(Runnable task, long delayMs);
    }

    private final Scheduler scheduler;
    private final long delayMs;

    public FakeInferenceClient(Scheduler scheduler, long delayMs) {
        this.scheduler = scheduler;
        this.delayMs = delayMs;
    }

    @Override
    public Call send(String requestBody, final Callback callback) {
        final boolean[] cancelled = {false};
        scheduler.schedule(() -> {
            synchronized (cancelled) {
                if (!cancelled[0]) {
                    callback.onResult("{\"fake\":true}", null);
                }
            }
        }, delayMs);
        return () -> {
            synchronized (cancelled) {
                cancelled[0] = true;
            }
        };
    }
}
