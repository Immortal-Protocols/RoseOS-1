package android.os;

public final class Handler {
    private final Looper looper;
    public Handler(Looper looper) {
        if (looper == null) throw new AssertionError("callback looper is required");
        this.looper=looper;
    }
    public Looper getLooper() { return looper; }
    public void post(Runnable work) { looper.post(work); }
}
