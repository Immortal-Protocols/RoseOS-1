/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

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

    static RoseAgentWork unavailable(String reason) {
        return new RoseAgentWork(State.UNAVAILABLE, new ArrayList<>(), reason);
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
        if (items.isEmpty()) return new RoseAgentWork(State.EMPTY, items, "Rose is not working in any app");
        return new RoseAgentWork(State.AVAILABLE, items, null);
    }

    enum Open { WATCH, NOT_CURRENT, GONE }

    /**
     * What selecting a card may do. The viewer mirrors the agent display's current screen, so it opens only when the
     * chosen task is still that screen; otherwise the card says so instead of showing a different task.
     */
    static Open open(int taskId, RoseAgentWork now) {
        for (Task t : now.items) if (t.taskId == taskId) return t.topOfDisplay ? Open.WATCH : Open.NOT_CURRENT;
        return Open.GONE;
    }

    static String describe(Open result, String label) {
        switch (result) {
            case WATCH: return null;
            case NOT_CURRENT: return label + " is not on Rose's screen right now";
            default: return label + " is no longer running";
        }
    }
}
