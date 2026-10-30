package com.android.quickstep.fallback;

import java.util.Arrays;
import java.util.List;

public final class RoseAgentWorkTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static RoseAgentWork.Task task(int id, int display, int user, String pkg, boolean top) {
        return new RoseAgentWork.Task(id, display, user, pkg, pkg + " label", top);
    }
    public static void main(String[] args) {
        int controller = 10123;
        List<RoseAgentWork.Display> displays = Arrays.asList(new RoseAgentWork.Display(0, -1),
            new RoseAgentWork.Display(7, controller), new RoseAgentWork.Display(9, 10999));
        List<RoseAgentWork.Task> tasks = Arrays.asList(
            task(1, 0, 0, "com.human.app", true),            // the human's own task: never an agent card
            task(2, 7, 0, "com.example.mail", true),         // Rose's current screen
            task(3, 7, 0, "com.example.maps", false),        // also on Rose's display, behind
            task(4, 9, 0, "com.other.cast", true),           // another app's virtual display
            task(5, 7, 10, "com.example.mail", true),        // another user
            task(6, 7, 0, RoseAgentWork.CONTROLLER, false)); // the controller itself
        RoseAgentWork work = RoseAgentWork.of(controller, 0, displays, tasks);
        expect(work.state == RoseAgentWork.State.AVAILABLE, "Real agent work is available");
        expect(work.items.size() == 2 && work.items.get(0).taskId == 2 && work.items.get(1).taskId == 3,
            "Only tasks on the controller-owned display, for this user, excluding the controller");
        expect(RoseAgentWork.open(2, work) == RoseAgentWork.Open.WATCH, "The current agent screen opens the viewer");
        expect(RoseAgentWork.open(3, work) == RoseAgentWork.Open.NOT_CURRENT,
            "A task behind the agent screen never opens the viewer onto a different task");
        expect(RoseAgentWork.open(4, work) == RoseAgentWork.Open.GONE, "A task that is not agent work is not opened");
        expect(RoseAgentWork.describe(RoseAgentWork.Open.NOT_CURRENT, "Maps").equals("Maps is not on Rose's screen right now"), "");
        expect(RoseAgentWork.describe(RoseAgentWork.Open.GONE, "Maps").equals("Maps is no longer running"), "");
        expect(RoseAgentWork.describe(RoseAgentWork.Open.WATCH, "Mail") == null, "Opening shows no notice");

        RoseAgentWork empty = RoseAgentWork.of(controller, 0, Arrays.asList(new RoseAgentWork.Display(0, -1)), tasks);
        expect(empty.state == RoseAgentWork.State.EMPTY && empty.items.isEmpty()
            && "Rose is not working in any app".equals(empty.reason), "No agent display is a truthful empty state");
        RoseAgentWork spoof = RoseAgentWork.of(controller, 0, Arrays.asList(new RoseAgentWork.Display(9, 10999)), tasks);
        expect(spoof.state == RoseAgentWork.State.EMPTY, "Another owner's display never counts");
        expect(RoseAgentWork.of(-1, 0, displays, tasks).state == RoseAgentWork.State.UNAVAILABLE,
            "No controller for this user is unavailable, not empty");
        expect(RoseAgentWork.of(controller, 0, null, tasks).state == RoseAgentWork.State.UNAVAILABLE,
            "An unreadable platform state is unavailable");
        expect(RoseAgentWork.of(controller, 10, displays, tasks).items.size() == 1, "The other user sees only their work");
        expect(RoseAgentWork.unavailable("x").items.isEmpty(), "Unavailable shows no cards");
        System.out.println("RoseAgentWorkTest: " + checks + " checks passed");
    }
}
