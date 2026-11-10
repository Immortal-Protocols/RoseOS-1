package android.os;

public final class Handler {
    public Handler(Looper looper) {
        if (looper != Looper.getMainLooper()) throw new AssertionError("must use main looper");
    }
}
