package rose.models.composer;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * The seam between the composer and a local model runtime on the Rose server. LlamaInference binds the pinned
 * llama.cpp runtime in Rose server's owned worker; native sources and acquisition live under runtime/.
 * The phone sends snapshots to Rose server and consumes the validated layout.
 */
public interface LocalInference {
    /** Model identity recorded with every result: id, revision, quantization, runtime (and version). */
    Map<String, String> info();

    /**
     * Generate one layout candidate. Throw {@link Refused} with {@code timeout} or {@code cancelled} when that is
     * what happened; any other exception becomes {@code model_unavailable}. The returned text is untrusted.
     */
    Generation generate(Request request) throws Refused;

    /** Everything a runtime needs for one constrained call. */
    final class Request {
        public final String system, user, grammarGbnf;
        public final int maxTokens;
        public final long deadlineMs;           // time left for this call
        public final Cancellation cancellation;
        /** Selected aliases for runtimes that use a compact decision plan before emitting the layout. */
        public final ContextSelector.Context context;

        public Request(String system, String user, String grammarGbnf, int maxTokens, long deadlineMs,
                       Cancellation cancellation) {
            this(system, user, grammarGbnf, maxTokens, deadlineMs, cancellation, null);
        }

        public Request(String system, String user, String grammarGbnf, int maxTokens, long deadlineMs,
                       Cancellation cancellation, ContextSelector.Context context) {
            this.context = context;
            this.system = system;
            this.user = user;
            this.grammarGbnf = grammarGbnf;
            this.maxTokens = maxTokens;
            this.deadlineMs = deadlineMs;
            this.cancellation = cancellation;
        }
    }

    /** Raw output plus whatever the runtime reports (prompt/output tokens and ms); stats may be empty. */
    final class Generation {
        public final String text;
        public final Map<String, Object> stats;

        public Generation(String text, Map<String, Object> stats) {
            this.text = text;
            this.stats = stats == null ? Collections.<String, Object>emptyMap()
                    : Collections.unmodifiableMap(new LinkedHashMap<String, Object>(stats));
        }
    }

    /** Polled before and after generation; a runtime should also poll it while decoding. */
    interface Cancellation {
        boolean isCancelled();

        Cancellation NEVER = new Cancellation() {
            @Override public boolean isCancelled() { return false; }
        };
    }
}
