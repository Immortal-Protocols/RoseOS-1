package dev.pixel.voice;

import dev.pixel.voice.CurrentApps.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/** #156/#225 parity: Home's consumer test of App Core's common fixture qa/parity/current-apps-parity.json (App Core
 * owns the file and its schema; Home owns this reader/render consumer test). Each case's controllerReply (the
 * current_apps reply shape) goes through Home's real reader and tracker, and must land in the same category
 * (cards / genuine_empty / cannot_confirm) and the same app set that Launcher3 must show for the case's platform input.
 * Cases without a controllerReply are Launcher3-only (Home sees the controller's one session). Host JVM only.
 * Usage: CurrentAppsParityTest <fixture path>. */
public final class CurrentAppsParityTest {
    static int checks;
    static void expect(boolean c, String m) { checks++; if (!c) throw new AssertionError(m); }

    static String category(Tracker t) {
        switch (t.state()) { case AVAILABLE: return "cards"; case EMPTY: return "genuine_empty"; default: return "cannot_confirm"; }
    }
    static List<String> shown(Tracker t) { List<String> o = new ArrayList<>(); for (App a : t.apps()) o.add(a.packageName); Collections.sort(o); return o; }
    static String str(Map<?, ?> m, String k) { Object v = m.get(k); return v instanceof String ? (String) v : null; }
    static Snapshot reply(String registration, Map<?, ?> r) {
        List<ReplyApp> apps = null; Object a = r.get("apps");
        if (a instanceof List) { apps = new ArrayList<>(); for (Object x : (List<?>) a) { Map<?, ?> m = (Map<?, ?>) x;
            apps.add(new ReplyApp(str(m, "packageName"), Boolean.TRUE.equals(m.get("front")), Boolean.TRUE.equals(m.get("target")))); } }
        Object g = r.get("generation"), c = r.get("complete");
        // The agreed two-app shape (owned_devices with sessions[] bindings) takes the same path as the activity.
        List<Binding> bindings = bindings(r.get("sessions"));
        if ("owned_devices".equals(str(r, "source")) && bindings != null) {
            List<ReplyApp> withSessions = null;
            if (a instanceof List) { withSessions = new ArrayList<>(); for (Object x : (List<?>) a) { Map<?, ?> m = (Map<?, ?>) x;
                withSessions.add(new ReplyApp(str(m, "packageName"), Boolean.TRUE.equals(m.get("front")), Boolean.TRUE.equals(m.get("target")), str(m, "sessionId"))); } }
            return CurrentApps.fromBindings(registration, str(r, "status"), g instanceof Number ? ((Number) g).longValue() : null, withSessions, bindings,
                str(r, "controllerEpoch"), c instanceof Boolean ? (Boolean) c : null);
        }
        return CurrentApps.fromReply(registration, str(r, "status"), str(r, "sessionId"), str(r, "taskId"), g instanceof Number ? ((Number) g).longValue() : null,
            apps, str(r, "instance"), str(r, "sessionStatus"), c instanceof Boolean ? (Boolean) c : null, str(r, "source"));
    }
    /** sessions[] as bindings (slot/bound present), or null when the reply has none in that shape. */
    static List<Binding> bindings(Object v) {
        if (!(v instanceof List)) return null;
        List<Binding> out = new ArrayList<>();
        for (Object x : (List<?>) v) {
            if (!(x instanceof Map)) return null; Map<?, ?> m = (Map<?, ?>) x;
            if (!m.containsKey("bound") || !m.containsKey("slot")) return null;
            Object slot = m.get("slot");
            out.add(new Binding(slot instanceof Number ? ((Number) slot).intValue() : -1, str(m, "sessionId"), str(m, "taskId"), str(m, "targetPackage"), str(m, "sessionStatus"),
                Boolean.TRUE.equals(m.get("bound")), Boolean.TRUE.equals(m.get("frontKnown"))));
        }
        return out;
    }
    @SuppressWarnings("unchecked")
    static void check(Tracker t, Map<?, ?> expected, String what) {
        String cat = (String) expected.get("category");
        List<String> apps = new ArrayList<>(); for (Object o : (List<Object>) expected.get("apps")) apps.add((String) o); Collections.sort(apps);
        expect(category(t).equals(cat), what + ": category " + category(t) + " != " + cat);
        expect(shown(t).equals(apps), what + ": apps " + shown(t) + " != " + apps);
        if (!"cards".equals(cat)) expect(t.apps().isEmpty(), what + ": no app cards outside 'cards'");
    }

    @SuppressWarnings("unchecked")
    public static void main(String[] args) throws Exception {
        Map<?, ?> fx = (Map<?, ?>) MiniJson.parse(new String(Files.readAllBytes(Paths.get(args[0])), StandardCharsets.UTF_8));
        Map<String, Map<?, ?>> byName = new java.util.HashMap<>();
        int home = 0;
        for (Object o : (List<Object>) fx.get("cases")) {
            Map<?, ?> c = (Map<?, ?>) o; String name = (String) c.get("name"); byName.put(name, c);
            Object r = c.get("controllerReply");
            if (!(r instanceof Map)) continue;   // a Launcher3-only case (Home sees the controller's one session)
            Tracker t = new Tracker(); t.apply(reply("case-" + name, (Map<?, ?>) r));
            check(t, (Map<?, ?>) c.get("expected"), name); home++;
        }
        expect(home >= 24, "Home covers every case that has a controller reply (" + home + ")");
        for (Object o : (List<Object>) fx.get("sequences")) {
            Map<?, ?> s = (Map<?, ?>) o; Tracker t = new Tracker(); int i = 0;
            for (Object st : (List<Object>) s.get("steps")) {
                Map<?, ?> step = (Map<?, ?>) st; i++;
                // Home: every read is its own registration; a reconnect is a new source that has not reported yet.
                if (Boolean.TRUE.equals(step.get("reconnect"))) t.apply(Snapshot.unavailable("reconnect-" + i, 0, CurrentApps.NOT_REPORTED));
                else t.apply(reply("read-" + i, (Map<?, ?>) byName.get(step.get("case")).get("controllerReply")));
                check(t, (Map<?, ?>) step.get("expected"), s.get("name") + " step " + i);
            }
        }
        System.out.println("CurrentAppsParityTest: " + checks + " checks passed");
    }
}
