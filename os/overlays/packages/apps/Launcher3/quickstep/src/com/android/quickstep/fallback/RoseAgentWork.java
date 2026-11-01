/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

/**
 * RoseOS (#156): which running tasks are Rose Agent's work, for the agent row of the fallback overview.
 *
 * Rose holds HOME, so Launcher3 Quickstep shows its fallback overview (OverviewComponentObserver). Its existing human
 * task carousel is unchanged; this adds the agent row. Agent work is identified only by system-attested facts, never
 * by names an app chose: a ComputerControl session's display is created by the platform's VirtualDeviceImpl with the
 * session owner's package and uid (DisplayManagerInternal.createVirtualDisplay(..., mOwnerPackageName, mOwnerUid)),
 * so a task counts when its display's owner uid is the installed controller package's uid. The signer-gated
 * AgentProvider is not read. Nothing is invented: no display or no task is a truthful empty state, a failed query an
 * unavailable state. Pure logic, no Android types.
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

    /** A display with its system-recorded owner; ownerUid -1 when it has none. */
    static final class Display {
        final int displayId, ownerUid;
        Display(int displayId, int ownerUid) { this.displayId = displayId; this.ownerUid = ownerUid; }
    }

    final State state;
    final List<Task> items;
    final String reason;

    private RoseAgentWork(State state, List<Task> items, String reason) {
        this.state = state; this.items = Collections.unmodifiableList(items); this.reason = reason;
    }

    /** The selected HiFi's empty agent carousel (ce126f56 renderSwitcher: "No background work"). */
    static final String EMPTY = "No background work";

    /**
     * How many items are their display's current screen: the apps Rose is actually working in now, as the platform
     * reports it (front task of a controller-owned display). Tasks behind them are Rose's background apps, not active.
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
    enum Step { CONTROLLER, DISPLAYS, TASKS }

    static RoseAgentWork failed(Step step) {
        return unavailable("Rose's work could not be read (" + step.name().toLowerCase(Locale.ROOT) + ")");
    }

    /** One task's app label from the platform; it may fail. */
    interface Labels {
        String of(Task task) throws Exception;
    }

    /**
     * This work with each kept item's app label, looked up only after of() has filtered to the controller's own
     * displays and the current user. A lookup that fails or is empty shows the package name: it never drops, adds
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
        return new RoseAgentWork(state, out, reason);
    }

    /**
     * controllerUid: the uid of CONTROLLER for the current user, or -1 when it is not installed for that user.
     * currentUser: only that user's agent work is shown (user/profile boundary).
     */
    static RoseAgentWork of(int controllerUid, int currentUser, List<Display> displays, List<Task> tasks) {
        if (controllerUid < 0) return unavailable("Rose agent control is not installed for this user");
        if (displays == null || tasks == null) return unavailable("Running work could not be read");
        List<Integer> owned = new ArrayList<>();
        for (Display d : displays) if (d.ownerUid == controllerUid && d.displayId > 0) owned.add(d.displayId);
        List<Task> items = new ArrayList<>();
        for (Task t : tasks) {
            if (t.userId == currentUser && owned.contains(t.displayId) && !CONTROLLER.equals(t.packageName)) items.add(t);
        }
        if (items.isEmpty()) return new RoseAgentWork(State.EMPTY, items, EMPTY);
        return new RoseAgentWork(State.AVAILABLE, items, null);
    }

    enum Open { WATCH, NOT_CURRENT, CHANGED, GONE }

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
            default: return label + " is no longer running";
        }
    }
}
