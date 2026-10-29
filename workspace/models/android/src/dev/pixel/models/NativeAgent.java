package dev.pixel.models;

/**
 * Core's local-agent-v1 Agent through the thin JNI adapter (librose_chat_android). Java owns the
 * native handle: it is destroyed exactly once, and no step runs after or during destruction.
 */
final class NativeAgent implements AgentPort {
    static final String LIBRARY = "rose_chat_android";
    static final String CLOSED = "{\"contract\":\"local-agent-v1\",\"outputs\":[{\"type\":\"rejected\",\"reason\":\"agent_closed\"}]}";

    private long handle;

    static {
        System.loadLibrary(LIBRARY);
    }

    /** Throws IllegalArgumentException if Core rejects the config. */
    NativeAgent(String configJson) {
        handle = nativeCreate(configJson);
    }

    @Override
    public synchronized String step(String inputJson) {
        return handle == 0 ? CLOSED : nativeStep(handle, inputJson);
    }

    @Override
    public synchronized void close() {
        long h = handle;
        handle = 0;
        if (h != 0) {
            nativeDestroy(h);
        }
    }

    private static native long nativeCreate(String configJson);

    private static native String nativeStep(long handle, String inputJson);

    private static native void nativeDestroy(long handle);
}
