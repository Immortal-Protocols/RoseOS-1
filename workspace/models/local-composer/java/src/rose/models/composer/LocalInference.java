package rose.models.composer;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * The one seam between the composer and an on-device model runtime. A binding (llama.cpp, LiteRT-LM, ...) lives
 * outside this package and is not part of this source: no runtime, weights or device route is admitted for it.
 * Implementations must stay on the phone; the composer never asks a server to generate a layout.
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

        public Request(String system, String user, String grammarGbnf, int maxTokens, long deadlineMs,
                       Cancellation cancellation) {
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
