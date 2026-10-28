package dev.pixel.voice;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public final class CallbackOwnerTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    /** The voice page's request table (index.html api()/nativeResponse): IDs from 1, settled by ID once. */
    static final class Page {
        int requestSeq; final Map<String, String> pending = new HashMap<>(); final List<String> settled = new ArrayList<>();
        String request(String route) { String id = String.valueOf(++requestSeq); pending.put(id, route); return id; }
        void nativeResponse(String id, String body) { String route = pending.remove(id); if (route != null) settled.add(route + "=" + body); }
    }
    /** VoiceService delivery: a completion carries its bridge's generation and is checked when it runs. */
    static boolean deliver(CallbackOwner owner, int generation, Page page, String id, String body) {
        return owner.run(generation, () -> page.nativeResponse(id, body));
    }

    public static void main(String[] args) {
        CallbackOwner owner = new CallbackOwner();
        int first = owner.open(); Page old = new Page();
        // The old page's first request is in flight when its renderer is lost.
        String lateId = old.request("/events");
        int second = owner.open(); Page fresh = new Page();
        // The replacement page numbers from zero again: its conversation-mode request reuses the same ID.
        String freshId = fresh.request("/conversation-mode");
        expect(freshId.equals(lateId), "Both generations hold a pending request with the same ID");
        expect(!deliver(owner, first, fresh, lateId, "{\"error\":\"old\"}"), "A late completion from the old view is dropped");
        expect(fresh.pending.containsKey(freshId) && fresh.settled.isEmpty(), "and leaves the new page's request pending");
        expect(!deliver(owner, first, old, lateId, "late") && old.settled.isEmpty(), "nor is it evaluated into the dead view");
        expect(deliver(owner, second, fresh, freshId, "real"), "The new owner's response is delivered");
        expect(fresh.settled.equals(java.util.Arrays.asList("/conversation-mode=real")) && fresh.pending.isEmpty(),
                "and settles its request normally");
        String failing = fresh.request("/voice/session");
        expect(deliver(owner, second, fresh, failing, "{\"error\":\"409\"}") && !fresh.pending.containsKey(failing),
                "Error responses are delivered the same way");

        // A completion queued before replacement but run after it is checked when it runs, not when queued.
        String queuedId = fresh.request("/events"); int queuedGeneration = owner.current();
        int third = owner.open(); Page later = new Page();
        expect(!deliver(owner, queuedGeneration, fresh, queuedId, "late"), "Queued work crossing a replacement is dropped");
        expect(owner.owns(third) && !owner.owns(second) && !owner.owns(first), "Only the newest generation owns callbacks");

        // Service teardown invalidates every generation, including the current one.
        later.request("/events"); owner.close();
        expect(!deliver(owner, third, later, "1", "late") && later.settled.isEmpty(), "Nothing is delivered after the service is destroyed");
        expect(!owner.owns(owner.open()), "and a closed owner stays closed");

        System.out.println("CallbackOwnerTest: " + checks + " checks passed");
    }
}
