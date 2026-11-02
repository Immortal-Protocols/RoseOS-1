package dev.pixel.backgroundcontrol;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** #156/#225 parity probe: runs the production controller CurrentApps on one controller input per stdin line and prints
 * the reply's status, complete, incompleteReason, source, sessionStatus and apps (packageName:front:target), as
 * Controller.currentApps would (test_current_apps_parity.py writes the lines from qa/parity/current-apps-parity.json). Line: locked TAB session TAB status TAB blocked TAB target TAB front TAB platform, where "-" is null, lists
 * are comma separated, and platform is five typed values separated by ";" (L:long, S:list, I:int, B:boolean, X:string). */
public final class CurrentAppsParityProbe {
    static Object value(String typed) {
        String t = typed.substring(0, 1), v = typed.substring(2);
        switch (t) {
            case "L": return Long.parseLong(v);
            case "I": return Integer.parseInt(v);
            case "B": return Boolean.parseBoolean(v);
            case "S": return v.isEmpty() ? new String[0] : v.split(",", -1);
            case "A": { String[] p = v.isEmpty() ? new String[0] : v.split(","); int[] a = new int[p.length]; for (int i = 0; i < p.length; i++) a[i] = Integer.parseInt(p[i]); return a; }
            case "Z": { String[] p = v.isEmpty() ? new String[0] : v.split(","); boolean[] a = new boolean[p.length]; for (int i = 0; i < p.length; i++) a[i] = Boolean.parseBoolean(p[i]); return a; }
            default: return v;
        }
    }
    static String nul(String s) { return "-".equals(s) ? null : s; }
    public static void main(String[] args) throws Exception {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        String controller = args[0];
        for (String line; (line = in.readLine()) != null; ) {
            if (line.isEmpty()) continue;
            String[] f = line.split("\t", -1);
            boolean locked = Boolean.parseBoolean(f[0]), session = Boolean.parseBoolean(f[1]), blocked = Boolean.parseBoolean(f[3]);
            String status = f[2];
            // target: one package, or every bound live slot's target (comma separated) for a multi-session case.
            List<String> targets = "-".equals(f[4]) ? new ArrayList<String>() : Arrays.asList(f[4].split(","));
            String target = targets.isEmpty() ? null : targets.get(0);
            List<String> front = "-".equals(f[5]) ? null : f[5].isEmpty() ? new ArrayList<String>() : Arrays.asList(f[5].split(","));
            CurrentApps.Platform platform = null;
            if (!"-".equals(f[6])) {
                String[] p = f[6].split(";", -1);
                platform = CurrentApps.platform(value(p[0]), value(p[1]), value(p[2]), value(p[3]), value(p[4]), 0);
            }
            // The owned-device snapshot (PR374), when the line has one; read only when unlocked, whatever the session.
            CurrentApps.Owned owned = null;
            if (f.length > 7 && !"-".equals(f[7]) && !locked) {
                String[] o = f[7].split(";", -1);
                owned = CurrentApps.owned(value(o[0]), value(o[1]), value(o[2]), value(o[3]), value(o[4]));
            }
            // As Controller.currentApps: owned devices first; else the per-session platform answer, consulted only for a
            // live, unlocked session; else the session windows.
            boolean live = session && CurrentApps.live(status);
            // As Controller.bindings: targets only for a live session; one front app per session (its topmost application
            // window) only when ready and unblocked. A multi-session line lists each bound slot's one front.
            List<String> fronts = new ArrayList<>();
            if (session && "ready".equals(status) && !blocked && front != null)
                fronts = targets.size() > 1 ? front : front.isEmpty() ? fronts : front.subList(0, 1);
            CurrentApps.Result r = owned != null ? CurrentApps.ofDevices(locked, live ? targets : new ArrayList<String>(), owned, fronts, controller)
                : platform != null && live && !locked ? CurrentApps.of(locked, session, status, target, platform, front, controller)
                : CurrentApps.of(locked, session, status, blocked, target, front, controller);
            // As Controller.currentApps' reply: status, complete, incompleteReason, source, sessionStatus (live only), apps.
            StringBuilder b = new StringBuilder(r.status).append('\t').append(r.complete).append('\t').append(r.incompleteReason)
                .append('\t').append(r.source).append('\t').append(CurrentApps.LIVE.equals(r.status) && session ? status : "").append('\t');
            for (int i = 0; i < r.apps.size(); i++) {
                CurrentApps.App a = r.apps.get(i);
                b.append(i == 0 ? "" : ",").append(a.packageName).append(':').append(a.front).append(':').append(a.target);
            }
            System.out.println(b);
        }
    }
}
