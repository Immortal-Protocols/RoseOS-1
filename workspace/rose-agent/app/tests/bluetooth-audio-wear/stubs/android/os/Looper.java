package android.os;

import java.util.ArrayDeque;

/** Deterministic scheduling: a blocked UI looper queues work; an independent watcher can still run. */
public final class Looper {
    private static final Looper MAIN=new Looper();
    private final ArrayDeque<Runnable> pending=new ArrayDeque<>();
    private boolean blocked;
    public static Looper getMainLooper() { return MAIN; }
    public void setBlocked(boolean blocked) {
        this.blocked=blocked;
        if (!blocked) while (!pending.isEmpty()) pending.remove().run();
    }
    public void reset() { pending.clear(); blocked=false; }
    void post(Runnable work) { if (blocked) pending.add(work); else work.run(); }
}
