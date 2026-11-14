package rose.models.composer;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/** A typed composer failure. {@link #category} is one of {@link #FAILURES} and is all a caller may show or log. */
public final class Refused extends Exception {
    private static final long serialVersionUID = 1L;

    public static final List<String> FAILURES = Collections.unmodifiableList(Arrays.asList(
            "bad_request", "catalog_mismatch", "stale_snapshot", "empty_context", "model_unavailable", "timeout",
            "cancelled", "invalid_output", "unknown_ref", "disallowed_ref", "over_limit", "duplicate_ref"));

    public final String category;

    public Refused(String category) {
        super(category, null, false, false);
        if (!FAILURES.contains(category)) throw new IllegalArgumentException("unknown failure category");
        this.category = category;
    }
}
