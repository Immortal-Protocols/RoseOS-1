package dev.pixel.builder;

/** Project 1: clientRequestId reuse (RequestIds), builder API v1 §3.1 and §3.6. Host JVM. */
public final class RequestIdsTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    public static void main(String[] args) {
        int[] n = {0};
        RequestIds ids = new RequestIds(() -> "id" + (++n[0]));
        String a = ids.forText("A tip calculator");
        expect("id1".equals(a), "first request gets an id");
        expect(a.equals(ids.forText("A tip calculator")), "retrying the same text after a lost connection reuses it");
        String b = ids.forText("A tip calculator that splits the bill");
        expect(!a.equals(b), "changed text is a new request");
        expect(b.equals(ids.forText("A tip calculator that splits the bill")), "and its retry reuses the new id");
        ids.done();
        expect(!b.equals(ids.forText("A tip calculator that splits the bill")), "after the builder answers, the same text is a new request");
        ids.done();
        String answered = ids.forText("A habit tracker");
        ids.done();
        expect(!answered.equals(ids.forText("A habit tracker")), "once the builder has answered, sending it again is a new request with a new id");
        System.out.println("RequestIdsTest: " + checks + " checks passed");
    }
}
