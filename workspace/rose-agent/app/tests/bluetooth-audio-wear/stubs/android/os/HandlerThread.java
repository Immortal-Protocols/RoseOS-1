package android.os;

/** Host fixture only: a distinct looper, not a real Android thread or scheduling-performance test. */
public final class HandlerThread {
    private final Looper looper=new Looper();
    private boolean started;
    public HandlerThread(String name) {
        if (name == null || name.isEmpty()) throw new AssertionError("watcher thread needs a name");
    }
    public void start() { if (started) throw new AssertionError("thread started twice"); started=true; }
    public Looper getLooper() {
        if (!started) throw new AssertionError("thread must start before registration");
        return looper;
    }
}
