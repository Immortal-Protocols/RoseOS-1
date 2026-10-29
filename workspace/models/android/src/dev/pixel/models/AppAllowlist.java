package dev.pixel.models;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * The user's locally selected apps, keyed by opaque app_id. The model can only name an app_id;
 * this class maps it to one launcher component that is actually installed. Package names,
 * components, Intent URIs or extras from model text are never accepted.
 */
public final class AppAllowlist {
    private static final Pattern APP_ID = Pattern.compile("[a-z0-9][a-z0-9_-]{0,47}");

    /** One launcher activity on the device (MAIN/LAUNCHER). */
    public static final class Launchable {
        public final String packageName;
        public final String activityName;
        public final String label;

        public Launchable(String packageName, String activityName, String label) {
            this.packageName = packageName;
            this.activityName = activityName;
            this.label = label;
        }
    }

    public static final class Entry {
        public final String appId;
        public final String packageName;
        public final String label;

        Entry(String appId, String packageName, String label) {
            this.appId = appId;
            this.packageName = packageName;
            this.label = label;
        }
    }

    /** Either a single component to launch, or the reason none can be. */
    public static final class Resolution {
        public final LaunchResult.Status refusal;
        public final String detail;
        public final Launchable target;

        private Resolution(LaunchResult.Status refusal, String detail, Launchable target) {
            this.refusal = refusal;
            this.detail = detail;
            this.target = target;
        }
    }

    private final Map<String, Entry> byId;

    private AppAllowlist(Map<String, Entry> byId) {
        this.byId = byId;
    }

    public static AppAllowlist empty() {
        return new AppAllowlist(new LinkedHashMap<>());
    }

    /** Builds an allowlist from apps the user picked, assigning readable, unique app_ids. */
    public static AppAllowlist fromSelection(List<Launchable> selected) {
        Map<String, Entry> map = new LinkedHashMap<>();
        Set<String> packages = new HashSet<>();
        for (Launchable app : selected) {
            if (!packages.add(app.packageName)) {
                continue;
            }
            String id = uniqueId(slug(app.label), map.keySet());
            map.put(id, new Entry(id, app.packageName, app.label));
        }
        return new AppAllowlist(map);
    }

    public List<Entry> entries() {
        return Collections.unmodifiableList(new ArrayList<>(byId.values()));
    }

    public boolean isEmpty() {
        return byId.isEmpty();
    }

    /** Resolves a model-supplied app_id against the selection and what is installed now. */
    public Resolution resolve(String appId, List<Launchable> installed) {
        if (appId == null || !APP_ID.matcher(appId).matches() || !byId.containsKey(appId)) {
            return new Resolution(LaunchResult.Status.NOT_ALLOWED, "not in the selected apps", null);
        }
        Entry entry = byId.get(appId);
        List<Launchable> matches = new ArrayList<>();
        for (Launchable app : installed) {
            if (app.packageName.equals(entry.packageName)) {
                matches.add(app);
            }
        }
        if (matches.isEmpty()) {
            return new Resolution(LaunchResult.Status.NOT_FOUND, entry.label + " is not installed", null);
        }
        if (matches.size() > 1) {
            return new Resolution(LaunchResult.Status.NOT_FOUND,
                    entry.label + " has " + matches.size() + " launcher entries; not guessing", null);
        }
        return new Resolution(null, entry.label, matches.get(0));
    }

    /** One line per entry: app_id, package, label (tab separated). */
    public String serialize() {
        StringBuilder out = new StringBuilder();
        for (Entry e : byId.values()) {
            out.append(e.appId).append('\t').append(e.packageName).append('\t')
                    .append(e.label.replace('\t', ' ').replace('\n', ' ')).append('\n');
        }
        return out.toString();
    }

    public static AppAllowlist parse(String text) {
        Map<String, Entry> map = new LinkedHashMap<>();
        if (text != null) {
            for (String line : text.split("\n")) {
                String[] parts = line.split("\t", 3);
                if (parts.length == 3 && APP_ID.matcher(parts[0]).matches() && !parts[1].isEmpty()) {
                    map.put(parts[0], new Entry(parts[0], parts[1], parts[2]));
                }
            }
        }
        return new AppAllowlist(map);
    }

    static String slug(String label) {
        String s = label == null ? "" : label.toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9]+", "-");
        s = s.replaceAll("^-+|-+$", "");
        if (s.isEmpty()) {
            s = "app";
        }
        return s.length() > 40 ? s.substring(0, 40) : s;
    }

    private static String uniqueId(String base, Set<String> taken) {
        String id = base;
        for (int n = 2; taken.contains(id); n++) {
            id = base + "-" + n;
        }
        return id;
    }
}
