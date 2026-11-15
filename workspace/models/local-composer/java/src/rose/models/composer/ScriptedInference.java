package rose.models.composer;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * SCRIPTED, NOT A MODEL: returns fixed outputs (or raises fixed failures) in order. For tests, parity vectors and
 * wiring the page before any runtime is admitted. Its results must never be reported as model results.
 */
public final class ScriptedInference implements LocalInference {
    private final List<Object> script; // String output, or Refused / RuntimeException to throw
    public final List<Request> calls = new ArrayList<Request>();

    public ScriptedInference(Object... script) {
        this.script = new ArrayList<Object>();
        Collections.addAll(this.script, script);
    }

    @Override public Map<String, String> info() {
        Map<String, String> m = new LinkedHashMap<String, String>();
        m.put("id", "scripted");
        m.put("revision", "n/a");
        m.put("quantization", "n/a");
        m.put("runtime", "none (no model)");
        return m;
    }

    @Override public Generation generate(Request request) throws Refused {
        calls.add(request);
        if (script.isEmpty()) throw new IllegalStateException("script exhausted");
        Object next = script.remove(0);
        if (next instanceof Refused) throw (Refused) next;
        if (next instanceof RuntimeException) throw (RuntimeException) next;
        return new Generation((String) next, null);
    }
}
