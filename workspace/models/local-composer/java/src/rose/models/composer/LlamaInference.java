package rose.models.composer;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/** Rose server local inference for the pinned Qwen3 model. No socket, subprocess or remote fallback. */
public final class LlamaInference implements LocalInference, AutoCloseable {
    public static final String MODEL_ID = "Qwen/Qwen3-1.7B-GGUF";
    public static final String REVISION = "90862c4b9d2787eaed51d12237eafdfe7c5f6077";
    public static final String SHA256 = "061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a";
    public static final String RUNTIME_REVISION = "dca216ad63c586f0db465324f785c5a418faef58";
    public static final long MODEL_BYTES = 1834426016L;
    private final File file;
    private long handle;
    private boolean closed;

    public LlamaInference(File file) { this.file = file; }

    @Override public Map<String, String> info() {
        Map<String, String> m = new LinkedHashMap<String, String>();
        m.put("id", MODEL_ID); m.put("revision", REVISION); m.put("quantization", "Q8_0");
        m.put("sha256", SHA256); m.put("runtime", "llama.cpp JNI@" + RUNTIME_REVISION);
        return Collections.unmodifiableMap(m);
    }

    private void open(long timeoutMs, Cancellation cancellation) throws Refused {
        if (closed) throw new Refused("model_unavailable");
        if (handle != 0) return;
        if (!file.isFile() || file.length() != MODEL_BYTES) throw new Refused("model_unavailable");
        try { System.loadLibrary("rose_composer"); }
        catch (LinkageError | SecurityException e) { throw new Refused("model_unavailable"); }
        handle = nativeOpen(utf8(file.getAbsolutePath()), timeoutMs, cancellation);
        if (handle == 0) throw new Refused("model_unavailable");
    }

    /** Optional worker-thread preload. Its time is separate from composer generation timing. */
    public synchronized void prepare() throws Refused { open(60000, Cancellation.NEVER); }

    @Override public synchronized Generation generate(Request request) throws Refused {
        if (request.cancellation.isCancelled()) throw new Refused("cancelled");
        if (request.deadlineMs <= 0) throw new Refused("timeout");
        long started = System.nanoTime();
        boolean cold = handle == 0;
        open(request.deadlineMs, request.cancellation);
        long loaded = System.nanoTime();
        long left = request.deadlineMs - (loaded - started) / 1000000L;
        if (left <= 0) throw new Refused("timeout");
        PlacementPlan plan = request.context == null ? null : new PlacementPlan(request.context, request.user);
        byte[] output = nativeGenerate(handle, utf8(plan == null ? request.system : PlacementPlan.SYSTEM),
                utf8(plan == null ? request.user : plan.user), utf8(plan == null ? request.grammarGbnf : plan.grammar),
                plan == null ? request.maxTokens : plan.maxTokens, left, request.cancellation);
        if (output == null) throw new Refused("model_unavailable");
        String text = new String(output, StandardCharsets.UTF_8);
        if (plan != null) text = plan.layout(text);
        Map<String, Object> stats = new LinkedHashMap<String, Object>();
        stats.put("decisionMaxTokens", plan == null ? request.maxTokens : plan.maxTokens);
        stats.put("decisionFormat", plan == null ? "layout" : "selection-v1");
        stats.put("loadedDuringCall", cold); stats.put("loadMs", (loaded - started) / 1000000.0);
        return new Generation(text, stats);
    }

    @Override public synchronized void close() {
        closed = true;
        if (handle != 0) { nativeClose(handle); handle = 0; }
    }

    private static byte[] utf8(String s) { return s.getBytes(StandardCharsets.UTF_8); }
    private static native long nativeOpen(byte[] path, long timeoutMs, Cancellation cancellation) throws Refused;
    private static native byte[] nativeGenerate(long handle, byte[] system, byte[] user, byte[] grammar,
            int maxTokens, long timeoutMs, Cancellation cancellation) throws Refused;
    private static native void nativeClose(long handle);
}
