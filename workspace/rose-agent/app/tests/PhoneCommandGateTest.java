package dev.pixel.voice;

import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/** W1 (APP-FIX-006). Drives the bridge's generic main-thread sequence (VoiceService.Bridge.phoneCommand:
 * claim, post to the main thread, bounded wait) with the real PhoneCommandGate, a real executor standing in
 * for the main looper, and an in-memory receipt store standing in for SharedPreferences. */
public final class PhoneCommandGateTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static final Map<String, String> receipts = new ConcurrentHashMap<>();
    static final Object lock = new Object();
    static final PhoneCommandGate gate = new PhoneCommandGate(new PhoneCommandGate.Receipts() {
        public String get(String id) { return receipts.get(id); }
        public void put(String id, String json) { receipts.put(id, json); }
    }, lock);
    static final ExecutorService main = Executors.newSingleThreadExecutor(r -> { Thread t = new Thread(r, "main-looper"); t.setDaemon(true); return t; });
    static final AtomicInteger posted = new AtomicInteger(), executed = new AtomicInteger();

    interface Action { String run() throws Exception; }
    /** The bridge's generic path with the same order of steps as VoiceService. */
    static String bridge(String id, Action action, long waitMs) throws Exception {
        String owned = gate.claim(id); if (owned != null) return owned;
        CompletableFuture<String> task = new CompletableFuture<>();
        posted.incrementAndGet();
        main.execute(() -> {
            String result;
            try { result = action.run(); } catch (Exception e) { result = "{\"status\":\"error\"}"; }
            executed.incrementAndGet(); receipts.put(id, result); task.complete(result);
        });
        return gate.await(task, id, waitMs);
    }
    static void reset() { posted.set(0); executed.set(0); }

    public static void main(String[] args) throws Exception {
        // Ordinary success: answered within the wait, once.
        reset();
        expect(bridge("ok", () -> "{\"status\":\"ok\"}", 2000).equals("{\"status\":\"ok\"}"), "Success is returned directly");
        expect(bridge("ok", () -> "{\"status\":\"ok\",\"again\":true}", 2000).equals("{\"status\":\"ok\"}"), "Re-query returns the saved receipt");
        expect(posted.get() == 1 && executed.get() == 1, "One action for one id");

        // An actual execution failure and an expiry before execution stay distinct terminal outcomes.
        reset();
        expect(bridge("fail", () -> { throw new IllegalStateException("tap failed"); }, 2000).equals("{\"status\":\"error\"}"), "Real failure is an error");
        expect(bridge("late", () -> "{\"status\":\"expired\"}", 2000).equals("{\"status\":\"expired\"}"), "Expired before running is expired");
        expect(posted.get() == 2 && executed.get() == 2, "Each ran once");

        // Main thread busy past the wait: the answer is running, not error, and re-queries never post again.
        reset();
        CountDownLatch busy = new CountDownLatch(1);
        main.execute(() -> { try { busy.await(); } catch (InterruptedException ignored) {} });
        String first = bridge("held", () -> "{\"status\":\"launch_requested\"}", 50);
        expect(first.equals(PhoneCommandGate.RUNNING), "A wait that runs out reports running, not a failure");
        for (int i = 0; i < 5; i++)
            expect(bridge("held", () -> "{\"status\":\"second\"}", 50).equals(PhoneCommandGate.RUNNING), "Re-query " + i + " finds the owner");
        expect(posted.get() == 1 && executed.get() == 0, "Exactly one queued action, none run yet");
        busy.countDown(); main.submit(() -> {}).get(2, TimeUnit.SECONDS);
        expect(executed.get() == 1, "The one action runs once released");
        expect(bridge("held", () -> "{\"status\":\"second\"}", 50).equals("{\"status\":\"launch_requested\"}"), "Then the real receipt is returned");
        expect(posted.get() == 1, "Still a single action");

        // Timeout while the action's observation is still in flight: the saved intermediate receipt is reported.
        reset();
        CountDownLatch observing = new CountDownLatch(1);
        String opened = bridge("observe", () -> {
            receipts.put("observe", "{\"status\":\"launch_requested\"}");
            observing.await();
            return "{\"status\":\"launch_requested\",\"observation\":\"ok\"}";
        }, 50);
        expect(opened.equals("{\"status\":\"launch_requested\"}"), "The action already ran; its saved receipt is reported, not an error");
        expect(bridge("observe", () -> "{\"status\":\"second\"}", 50).equals("{\"status\":\"launch_requested\"}") && posted.get() == 1, "No second launch");
        observing.countDown(); main.submit(() -> {}).get(2, TimeUnit.SECONDS);
        expect(bridge("observe", () -> "{\"status\":\"second\"}", 50).contains("\"observation\":\"ok\""), "The final observed receipt replaces it");

        main.shutdownNow();
        System.out.println("PhoneCommandGateTest: " + checks + " checks passed");
    }
}
