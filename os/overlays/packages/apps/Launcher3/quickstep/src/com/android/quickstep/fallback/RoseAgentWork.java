/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * RoseOS (#156): which running tasks are Rose Agent's work, for the agent row of the fallback overview.
 *
 * Rose holds HOME, so Launcher3 Quickstep shows its fallback overview (OverviewComponentObserver). Its existing human
 * task carousel is unchanged; this adds the agent row. Agent work is identified only by system-attested facts, never
 * by names an app chose, and never by package coincidence. The session's display stays private (ComputerControlSession
 * creates it without VIRTUAL_DISPLAY_FLAG_PUBLIC; DisplayManager.getDisplays() never lists it to Launcher3).
 *
 * Ownership comes from the platform's device-ownership snapshot to the recents component
 * (AutomatedPackageListener.onAutomatedDevicesChanged, the contract agreed with OS Core for #156): each callback is the
 * complete set of this user's ComputerControl session devices currently running apps, with the owner package and uid
 * the platform recorded for each, and a generation that rises on every change. Device ids and display ids are never
 * reused and a device's owner never changes, so task -> display -> device -> owner is a fact once known, whatever the
 * order in which apps or reports change. The display -> device map is the platform's own (getVirtualDevices(), whose
 * profile DEVICE_PROFILE_COMPUTER_CONTROL only session devices have).
 *
 * A task is Rose's when it is this user's, on a display of a session device the snapshot says CONTROLLER owns with
 * CONTROLLER's own uid, and is not the controller itself. Nothing is guessed: before the first valid snapshot the row
 * says not reported; a session task whose device or display ownership is missing is held as unconfirmed, never a card
 * and never "none"; a malformed snapshot is not applied and the row says ownership could not be read. The controller
 * still binds a selection to its own session display (agent-pip WatchBinding.fromSelection). Pure logic, no Android
 * types.
 */
final class RoseAgentWork {
    static final String CONTROLLER = "dev.pixel.backgroundcontrol";

    enum State { AVAILABLE, EMPTY, UNAVAILABLE }

    /** One running task as the platform reports it. */
    static final class Task {
        final int taskId, displayId, userId;
        final String packageName, label;
        final boolean topOfDisplay;
        Task(int taskId, int displayId, int userId, String packageName, String label, boolean topOfDisplay) {
            this.taskId = taskId; this.displayId = displayId; this.userId = userId;
            this.packageName = packageName; this.label = label; this.topOfDisplay = topOfDisplay;
        }
    }

    /** A session device's owner as the platform recorded it. */
    static final class Owner {
        final String packageName;
        final int uid;
        Owner(String packageName, int uid) { this.packageName = packageName; this.uid = uid; }
    }

    /**
     * The ownership the platform has reported, as applied by Ownership. received: a valid snapshot was applied since
     * this registration. malformed: the last snapshot was rejected, so nothing is known until a valid one arrives.
     */
    static final class Owners {
        static final Owners NONE = new Owners(false, false, Collections.emptyMap());
        final boolean received, malformed;
        final Map<Integer, Owner> byDevice;
        Owners(boolean received, boolean malformed, Map<Integer, Owner> byDevice) {
            this.received = received; this.malformed = malformed;
            this.byDevice = Collections.unmodifiableMap(new HashMap<>(byDevice));
        }
    }

    /**
     * Applies the platform's snapshots in order. Not thread-safe: the strip's holder serializes it. Per registration
     * the first valid snapshot is applied whatever its generation; after that only a higher generation (a repeated or
     * lower one is a duplicate or out of order). A malformed snapshot (missing or unequal arrays, a duplicate device,
     * a missing owner package, a negative uid, a negative generation) is never partly applied: the known ownership is
     * dropped and the generation baseline stays where it was.
     */
    static final class Ownership {
        private boolean baseline, malformed;
        private long generation;
        private Map<Integer, Owner> owners = new HashMap<>();

        /** A new registration: nothing is known until its first valid snapshot. */
        void reset() {
            baseline = false; malformed = false; generation = 0; owners = new HashMap<>();
        }

        /** True when what is known changed (the row should be read again). */
        boolean accept(long gen, int[] deviceIds, String[] ownerPackages, int[] ownerUids) {
            Map<Integer, Owner> next = parse(gen, deviceIds, ownerPackages, ownerUids);
            if (next == null) {
                boolean changed = !malformed || !owners.isEmpty();
                malformed = true;
                owners = new HashMap<>();
                return changed;
            }
            if (baseline && gen <= generation) return false;
            baseline = true; malformed = false; generation = gen; owners = next;
            return true;
        }

        Owners snapshot() {
            return new Owners(baseline && !malformed, malformed, owners);
        }

        private static Map<Integer, Owner> parse(long gen, int[] deviceIds, String[] packages, int[] uids) {
            // The registration snapshot carries the repository's generation, 0 until its first device change.
            if (gen < 0 || deviceIds == null || packages == null || uids == null) return null;
            if (deviceIds.length != packages.length || deviceIds.length != uids.length) return null;
            Map<Integer, Owner> out = new HashMap<>();
            for (int i = 0; i < deviceIds.length; i++) {
                if (packages[i] == null || packages[i].isEmpty() || uids[i] < 0) return null;
                if (out.put(deviceIds[i], new Owner(packages[i], uids[i])) != null) return null;
            }
            return out;
        }
    }

    final State state;
    final List<Task> items;
    /** Session tasks whose ownership the platform has not confirmed: never cards, and selecting one is refused. */
    final List<Task> unattributed;
    final String reason;

    private RoseAgentWork(State state, List<Task> items, String reason) {
        this(state, items, new ArrayList<>(), reason);
    }

    private RoseAgentWork(State state, List<Task> items, List<Task> unattributed, String reason) {
        this.state = state; this.items = Collections.unmodifiableList(items);
        this.unattributed = Collections.unmodifiableList(unattributed); this.reason = reason;
    }

    /** The selected HiFi's empty agent carousel (ce126f56 renderSwitcher: "No background work"). */
    static final String EMPTY = "No background work";
    /** Work runs on an agent screen, but the platform has not reported session ownership since registration. */
    static final String NOT_REPORTED = "Rose's work has not been reported yet";
    /** An agent screen's ownership is not (or no longer) in the platform's report. */
    static final String UNCONFIRMED = "Rose's work can't be confirmed right now";

    /**
     * How many items are their display's current screen: the apps Rose is actually working in now, as the platform
     * reports it (front task of a display Rose works on). Tasks behind them are Rose's background apps, not active.
     */
    int working() {
        int n = 0;
        for (Task t : items) if (t.topOfDisplay) n++;
        return n;
    }

    /**
     * The HiFi link label between the carousels: "N tasks in progress" from working(), "No active work" when Rose has
     * background apps but none is current, nothing when there is no agent work or it could not be read.
     */
    String linkLabel() {
        if (state != State.AVAILABLE) return "";
        int n = working();
        return n > 0 ? n + (n == 1 ? " task in progress" : " tasks in progress") : "No active work";
    }

    static RoseAgentWork unavailable(String reason) {
        return new RoseAgentWork(State.UNAVAILABLE, new ArrayList<>(), reason);
    }

    /** Which platform read failed: a fixed word for the status and log, never the exception's own text. */
    enum Step { CONTROLLER, OWNERSHIP, TASKS, DISPLAYS }

    static RoseAgentWork failed(Step step) {
        return unavailable("Rose's work could not be read (" + step.name().toLowerCase(Locale.ROOT) + ")");
    }

    /** One task's app label from the platform; it may fail. */
    interface Labels {
        String of(Task task) throws Exception;
    }

    /**
     * This work with each kept item's app label, looked up only after of() has filtered to the controller's own
     * session devices and the current user. A lookup that fails or is empty shows the package name: it never drops, adds
     * or reorders an item, and never turns the work unavailable.
     */
    RoseAgentWork labelled(Labels labels) {
        if (state != State.AVAILABLE) return this;
        List<Task> out = new ArrayList<>();
        for (Task t : items) {
            String label;
            try {
                label = labels.of(t);
            } catch (Exception e) {
                label = null;
            }
            if (label == null || label.isEmpty()) label = t.packageName;
            out.add(new Task(t.taskId, t.displayId, t.userId, t.packageName, label, t.topOfDisplay));
        }
        return new RoseAgentWork(state, out, unattributed, reason);
    }

    /**
     * controllerUid: the uid of CONTROLLER for the current user, or -1 when it is not installed for that user.
     * currentUser: only that user's agent work is shown (user/profile boundary).
     * owners: the applied ownership snapshot.
     * sessionDevices: display id to device id, for the virtual devices whose platform-set profile is
     * DEVICE_PROFILE_COMPUTER_CONTROL, as read now.
     * releasedDisplays: session displays seen in an earlier read and no longer listed (ids are never reused).
     * complete: false when the task list may be truncated.
     */
    static RoseAgentWork of(int controllerUid, int currentUser, Owners owners, Map<Integer, Integer> sessionDevices,
            Collection<Integer> releasedDisplays, List<Task> tasks, boolean complete) {
        if (controllerUid < 0) return unavailable("Rose agent control is not installed for this user");
        if (owners == null || sessionDevices == null || releasedDisplays == null || tasks == null) {
            return unavailable("Running work could not be read");
        }
        if (owners.malformed) return failed(Step.OWNERSHIP);
        List<Task> session = new ArrayList<>();
        for (Task t : tasks) {
            if (t.userId != currentUser || t.displayId <= 0) continue;
            if (sessionDevices.containsKey(t.displayId) || releasedDisplays.contains(t.displayId)) session.add(t);
        }
        if (session.isEmpty()) return new RoseAgentWork(State.EMPTY, new ArrayList<>(), EMPTY);
        if (!owners.received) return unavailable(NOT_REPORTED);
        if (!complete) return unavailable(UNCONFIRMED);
        List<Task> items = new ArrayList<>(), held = new ArrayList<>();
        for (Task t : session) {
            Integer device = sessionDevices.get(t.displayId);
            Owner owner = device == null ? null : owners.byDevice.get(device);
            if (owner == null) {
                if (!CONTROLLER.equals(t.packageName)) held.add(t);
                continue;
            }
            if (!CONTROLLER.equals(owner.packageName) || owner.uid != controllerUid) continue;
            if (!CONTROLLER.equals(t.packageName)) items.add(t);
        }
        if (!items.isEmpty()) return new RoseAgentWork(State.AVAILABLE, items, held, held.isEmpty() ? null : UNCONFIRMED);
        if (!held.isEmpty()) return new RoseAgentWork(State.UNAVAILABLE, items, held, UNCONFIRMED);
        return new RoseAgentWork(State.EMPTY, items, EMPTY);
    }

    /** A report arrived while the platform was being read: the read is not one consistent state. */
    static RoseAgentWork unconfirmed() {
        return unavailable(UNCONFIRMED);
    }

    enum Open { WATCH, NOT_CURRENT, CHANGED, UNATTRIBUTED, GONE }

    /** Selection-request extras for the controller's viewer (agent-pip WatchBinding): Android facts, never Rose IDs. */
    static final String EXTRA_SELECT_DISPLAY = "dev.pixel.backgroundcontrol.extra.SELECT_DISPLAY_ID";
    static final String EXTRA_SELECT_PACKAGE = "dev.pixel.backgroundcontrol.extra.SELECT_PACKAGE";

    /**
     * What selecting a card may do, decided against a FRESH read taken after the tap, never the row's snapshot.
     * The card's task must still be agent work on the same display, for the same user, with the same package, and
     * be that display's current screen. Only then is a selection request sent; the controller then binds it to its
     * own live session or refuses. Any other outcome says why and never shows a different task.
     */
    static Open decide(Task card, RoseAgentWork fresh) {
        if (fresh != null) {
            for (Task t : fresh.unattributed) if (t.taskId == card.taskId) return Open.UNATTRIBUTED;
        }
        if (fresh == null || fresh.state != State.AVAILABLE) return Open.GONE;
        for (Task t : fresh.items) {
            if (t.taskId != card.taskId) continue;
            if (t.displayId != card.displayId || t.userId != card.userId || !t.packageName.equals(card.packageName)) {
                return Open.CHANGED;
            }
            return t.topOfDisplay ? Open.WATCH : Open.NOT_CURRENT;
        }
        return Open.GONE;
    }

    static String describe(Open result, String label) {
        switch (result) {
            case WATCH: return null;
            case NOT_CURRENT: return label + " is not on Rose's screen right now";
            case CHANGED: return label + " changed; pick it again";
            case UNATTRIBUTED: return label + " can't be confirmed as Rose's work";
            default: return label + " is no longer running";
        }
    }

    /**
     * A card's live view (#156): what the platform currently draws for that task, never an icon, a cached recents
     * thumbnail or the last frame. CAPTURE: take a fresh capture now (TaskSnapshotManager.takeTaskSnapshot with
     * updateCache false: real content, not stored in the recents cache, secure windows redacted by the platform, and
     * only for a visible task). BEHIND: the task is behind another on Rose's screen, so the platform has nothing visible
     * to capture. LOCKED: nothing of Rose's screens is shown while the phone is locked. Only Rose's own cards (items)
     * are ever captured; unconfirmed tasks, other agents' and the human's never are.
     */
    enum Preview { CAPTURE, BEHIND, LOCKED }

    static final String PREVIEW_BEHIND = "Behind another app";
    static final String PREVIEW_LOCKED = "Hidden while locked";
    static final String PREVIEW_NONE = "Preview unavailable";

    static Preview preview(Task card, boolean locked) {
        if (locked) return Preview.LOCKED;
        return card.topOfDisplay ? Preview.CAPTURE : Preview.BEHIND;
    }

    /** The text a card shows instead of a frame; null when it shows the frame it was captured with in this read. */
    static String previewText(Preview preview, boolean captured) {
        switch (preview) {
            case LOCKED: return PREVIEW_LOCKED;
            case BEHIND: return PREVIEW_BEHIND;
            default: return captured ? null : PREVIEW_NONE;
        }
    }

    /** Whether this work has a card whose view should be captured again while the row is visible. */
    boolean hasLiveView(boolean locked) {
        if (state != State.AVAILABLE) return false;
        for (Task t : items) if (preview(t, locked) == Preview.CAPTURE) return true;
        return false;
    }
}
