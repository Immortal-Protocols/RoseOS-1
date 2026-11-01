package com.android.quickstep.fallback;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Replays fixtures/rose-switcher/automated-devices-scenarios.json (converted line by line by test_rose_switcher.py)
 * through the production RoseAgentWork.Ownership and RoseAgentWork.of. Reads stdin; prints one result line per
 * scenario: name|state|cards|unconfirmed|reason. Lines: S name, R (reset), G gen ids pkgs uids (each "n:a,b" or "~" for
 * a null array; "^" for a null element), D display:device,..., L released,..., T id display user pkg top,
 * E (evaluate).
 */
public final class RoseAgentScenarios {
    /** "n:a,b,..." with n elements (so [""] and [] differ), or "~" for a null array. */
    static String[] elements(String s) {
        if (s.equals("~")) return null;
        int colon = s.indexOf(':');
        int n = Integer.parseInt(s.substring(0, colon));
        String[] parts = n == 0 ? new String[0] : s.substring(colon + 1).split(",", -1);
        if (parts.length != n) throw new IllegalArgumentException("element count");
        return parts;
    }
    static int[] ints(String s) {
        String[] parts = elements(s);
        if (parts == null) return null;
        int[] out = new int[parts.length];
        for (int i = 0; i < parts.length; i++) out[i] = Integer.parseInt(parts[i]);
        return out;
    }
    static String[] strings(String s) {
        String[] parts = elements(s);
        if (parts != null) for (int i = 0; i < parts.length; i++) if (parts[i].equals("^")) parts[i] = null;
        return parts;
    }
    static String join(List<RoseAgentWork.Task> tasks) {
        StringBuilder b = new StringBuilder();
        for (RoseAgentWork.Task t : tasks) b.append(b.length() == 0 ? "" : ",").append(t.taskId);
        return b.toString();
    }

    public static void main(String[] args) throws Exception {
        int controllerUid = Integer.parseInt(args[0]), user = Integer.parseInt(args[1]);
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        String name = null;
        RoseAgentWork.Ownership ownership = null;
        Map<Integer, Integer> devices = null;
        List<Integer> released = null;
        List<RoseAgentWork.Task> tasks = null;
        for (String line; (line = in.readLine()) != null; ) {
            String[] f = line.split("\t", -1);
            switch (f[0]) {
                case "S":
                    name = f[1]; ownership = new RoseAgentWork.Ownership(); devices = new HashMap<>();
                    released = new ArrayList<>(); tasks = new ArrayList<>();
                    break;
                case "R": ownership.reset(); break;
                case "G": ownership.accept(Long.parseLong(f[1]), ints(f[2]), strings(f[3]), ints(f[4])); break;
                case "D": devices.put(Integer.parseInt(f[1]), Integer.parseInt(f[2])); break;
                case "L": released.add(Integer.parseInt(f[1])); break;
                case "T":
                    tasks.add(new RoseAgentWork.Task(Integer.parseInt(f[1]), Integer.parseInt(f[2]), Integer.parseInt(f[3]),
                        f[4], f[4], Boolean.parseBoolean(f[5])));
                    break;
                case "E":
                    RoseAgentWork w = RoseAgentWork.of(controllerUid, user, ownership.snapshot(), devices, released, tasks, true);
                    System.out.println(name + "|" + w.state + "|" + join(w.items) + "|" + join(w.unattributed) + "|"
                        + (w.reason == null ? "~" : w.reason));
                    break;
                default: throw new IllegalArgumentException("unknown line");
            }
        }
    }
}
