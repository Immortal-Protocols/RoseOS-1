package dev.pixel.voice;

import java.util.LinkedHashMap;
import java.util.Map;

/** Nov 7 plan 1O: the glasses-on hook fires once per real not-worn to worn transition of a wear sensor, and never on a
 * Bluetooth connection or the hinge (unfold) advertisement. */
public final class GlassesWearHookTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static final GlassesWearHook.Source WEAR = new GlassesWearHook.Source(GlassesWearHook.Source.WEAR_SENSOR, "test-don-state");
    static final GlassesWearHook.Source BT = new GlassesWearHook.Source(GlassesWearHook.Source.CONNECTION, "hfp");
    static final GlassesWearHook.Source HINGE = new GlassesWearHook.Source(GlassesWearHook.Source.HINGE, "rb-meta-advertisement");
    static final int WORN = GlassesWearHook.WORN, NOT_WORN = GlassesWearHook.NOT_WORN, UNKNOWN = GlassesWearHook.UNKNOWN;
    static final long GAP = GlassesWearHook.MIN_REMOVED_MS;

    static CustomizationConfig hook(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        return CustomizationConfig.DEFAULTS.apply(CustomizationConfig.GLASSES_HOOK, m);
    }

    public static void main(String[] a) {
        CustomizationConfig tray = hook("enabled", true);
        CustomizationConfig app = hook("enabled", true, "action", "app", "packageName", "dev.pixel.glassescamera");
        CustomizationConfig off = hook("enabled", false);

        // Connection and hinge are not wear: refused, state untouched.
        GlassesWearHook h = new GlassesWearHook();
        for (GlassesWearHook.Source s : new GlassesWearHook.Source[]{BT, HINGE, null, new GlassesWearHook.Source(null, "x"), new GlassesWearHook.Source("Wear_Sensor", "x")})
            for (int state : new int[]{NOT_WORN, WORN}) {
                GlassesWearHook.Decision d = h.observe(s, state, 0, tray);
                expect(GlassesWearHook.Decision.REFUSED.equals(d.kind) && !d.fires(), "non-wear source refused");
                expect(d.action == null && d.packageName == null && d.reason != null, "refusal carries no action");
            }
        expect(h.state() == UNKNOWN && h.fires() == 0 && h.sourceName() == null, "refused sources change nothing");
        h.observe(WEAR, NOT_WORN, 0, tray);
        expect(!h.observe(BT, WORN, 10_000, tray).fires() && h.state() == NOT_WORN, "connection after removal is not a put-on");
        expect(!h.observe(HINGE, WORN, 10_000, tray).fires() && h.state() == NOT_WORN, "hinge open after removal is not a put-on");
        expect(GlassesWearHook.Decision.REFUSED.equals(h.observe(WEAR, 7, 10_000, tray).kind) && h.state() == NOT_WORN, "unknown state value refused");
        expect(h.observe(WEAR, WORN, 10_000, tray).fires(), "the real wear signal then fires");

        // First worn after start does not fire.
        h = new GlassesWearHook();
        GlassesWearHook.Decision d = h.observe(WEAR, WORN, 0, tray);
        expect(!d.fires() && GlassesWearHook.Decision.NONE.equals(d.kind) && h.state() == WORN && "test-don-state".equals(h.sourceName()), "first worn only establishes state");
        expect(!h.observe(WEAR, WORN, 1000, tray).fires(), "repeated worn");
        // Removal then put-on fires once with the configured action.
        h.observe(WEAR, NOT_WORN, 2000, tray);
        h.observe(WEAR, NOT_WORN, 3000, tray);
        d = h.observe(WEAR, WORN, 2000 + GAP, tray);
        expect(d.fires() && "tray".equals(d.action) && d.packageName == null, "put-on fires tray");
        expect(h.fires() == 1 && h.lastFiredAt() == 2000 + GAP, "fire counted");
        for (long t = 1; t <= 5; t++) expect(!h.observe(WEAR, WORN, 2000 + GAP + t * 1000, tray).fires(), "repeated worn does not fire");
        expect(h.fires() == 1, "still one fire");

        // Flicker shorter than MIN_REMOVED_MS is the same wearing; the next real removal fires.
        h.observe(WEAR, NOT_WORN, 20_000, tray);
        expect(!h.observe(WEAR, WORN, 20_000 + GAP - 1, tray).fires(), "flicker does not fire");
        expect(h.fires() == 1 && h.state() == WORN, "flicker tracked as worn");
        h.observe(WEAR, NOT_WORN, 30_000, tray);
        expect(h.observe(WEAR, WORN, 30_000 + GAP, tray).fires() && h.fires() == 2, "the following real removal fires");
        // The removal time is the first not-worn report, not the latest.
        h.observe(WEAR, NOT_WORN, 40_000, tray);
        h.observe(WEAR, NOT_WORN, 40_000 + GAP - 10, tray);
        expect(h.observe(WEAR, WORN, 40_000 + GAP, tray).fires(), "long removal measured from its start");

        // Disabled: never fires, but tracks state; enabling while worn waits for the next put-on.
        h = new GlassesWearHook();
        h.observe(WEAR, NOT_WORN, 0, off);
        d = h.observe(WEAR, WORN, GAP, off);
        expect(!d.fires() && h.state() == WORN && h.fires() == 0, "disabled hook tracks but does not fire");
        expect(!h.observe(WEAR, WORN, GAP + 1000, tray).fires(), "enabling while worn does not fire");
        h.observe(WEAR, NOT_WORN, 10_000, tray);
        expect(h.observe(WEAR, WORN, 10_000 + GAP, tray).fires() && h.fires() == 1, "next put-on after enabling fires");
        h.observe(WEAR, NOT_WORN, 20_000, tray);
        expect(!h.observe(WEAR, WORN, 20_000 + GAP, null).fires(), "no config does not fire");
        h.observe(WEAR, NOT_WORN, 30_000, off);
        expect(!h.observe(WEAR, WORN, 30_000 + GAP, off).fires() && h.fires() == 1, "disabled again");

        // The action is read at the transition.
        h = new GlassesWearHook();
        h.observe(WEAR, WORN, 0, tray);
        h.observe(WEAR, NOT_WORN, 1000, tray);
        d = h.observe(WEAR, WORN, 1000 + GAP, app);
        expect(d.fires() && "app".equals(d.action) && "dev.pixel.glassescamera".equals(d.packageName), "app action carries its package");
        h.observe(WEAR, NOT_WORN, 10_000, app);
        d = h.observe(WEAR, WORN, 10_000 + GAP, app.apply(CustomizationConfig.GLASSES_HOOK, mapOf("action", "tray")));
        expect(d.fires() && "tray".equals(d.action) && d.packageName == null, "back to tray");

        // UNKNOWN resets arming.
        h = new GlassesWearHook();
        h.observe(WEAR, NOT_WORN, 0, tray);
        h.observe(WEAR, UNKNOWN, GAP, tray);
        expect(h.state() == UNKNOWN, "unknown state");
        expect(!h.observe(WEAR, WORN, 2 * GAP, tray).fires(), "worn after unknown does not fire");
        h.observe(WEAR, NOT_WORN, 3 * GAP, tray);
        h.observe(WEAR, UNKNOWN, 3 * GAP + 10, tray);
        h.observe(WEAR, NOT_WORN, 3 * GAP + 20, tray);
        expect(!h.observe(WEAR, WORN, 4 * GAP, tray).fires(), "unknown during removal restarts the removal (" + (4 * GAP - 3 * GAP - 20) + "ms < gap)");
        h.observe(WEAR, NOT_WORN, 5 * GAP, tray);
        expect(h.observe(WEAR, WORN, 6 * GAP, tray).fires() && h.fires() == 1, "armed again after a full removal");

        expect("worn".equals(GlassesWearHook.stateName(WORN)) && "not_worn".equals(GlassesWearHook.stateName(NOT_WORN))
                && "unknown".equals(GlassesWearHook.stateName(UNKNOWN)), "state names");
        // Owner's choice (2026-11-07): any Bluetooth audio device connecting is glasses on, disconnecting is removal.
        GlassesWearHook.Source audio = new GlassesWearHook.Source(GlassesWearHook.Source.BLUETOOTH_AUDIO, "bluetooth_audio_connection");
        expect(audio.isWear() && !BT.isWear() && !HINGE.isWear(), "Bluetooth audio drives the hook; a generic connection or the hinge does not");
        GlassesWearHook b = new GlassesWearHook();
        expect(!b.observe(audio, NOT_WORN, 0, tray).fires(), "start with nothing connected: baseline, no fire");
        GlassesWearHook.Decision on = b.observe(audio, WORN, 5_000, tray);
        expect(on.fires() && CustomizationConfig.ACTION_TRAY.equals(on.action), "a Bluetooth audio device connecting fires once");
        expect(!b.observe(audio, WORN, 6_000, tray).fires(), "a second device while one is connected is not a new put-on");
        expect(!b.observe(audio, NOT_WORN, 10_000, tray).fires() && b.state() == NOT_WORN, "the last device disconnecting is removal");
        expect(!b.observe(audio, WORN, 11_000, tray).fires(), "a reconnect within " + GlassesWearHook.MIN_REMOVED_MS + " ms is a blip");
        expect(!b.observe(audio, NOT_WORN, 12_000, tray).fires() && b.observe(audio, WORN, 20_000, app).fires(), "a real removal then reconnect fires again, with the current action");
        GlassesWearHook c = new GlassesWearHook();
        expect(!c.observe(audio, WORN, 0, tray).fires(), "already connected at start: not a transition");
        expect(!c.observe(audio, NOT_WORN, 1_000, off).fires() && !c.observe(audio, WORN, 9_000, off).fires(), "disabled: tracks, never fires");
        System.out.println("GlassesWearHookTest: " + checks + " checks passed");
    }

    static Map<String, Object> mapOf(String k, Object v) { Map<String, Object> m = new LinkedHashMap<>(); m.put(k, v); return m; }
}
