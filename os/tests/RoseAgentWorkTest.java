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
        // Selection is decided against a fresh read taken after the tap, never against the row's snapshot.
        RoseAgentWork.Task mail = work.items.get(0), maps = work.items.get(1);
        expect(RoseAgentWork.decide(mail, work) == RoseAgentWork.Open.WATCH, "The current agent screen sends a request");
        expect(RoseAgentWork.decide(maps, work) == RoseAgentWork.Open.NOT_CURRENT,
            "A task behind the agent screen never opens the viewer onto a different task");
        // Races: the platform changed between the row's read and the tap.
        RoseAgentWork after = RoseAgentWork.of(controller, 0, displays, Arrays.asList(
            task(3, 7, 0, "com.example.maps", true), task(2, 7, 0, "com.example.mail", false)));
        expect(RoseAgentWork.decide(mail, after) == RoseAgentWork.Open.NOT_CURRENT,
            "The tapped card was current at row time but not at tap time: refused");
        expect(RoseAgentWork.decide(maps, after) == RoseAgentWork.Open.WATCH, "The now-current card is allowed");
        RoseAgentWork newSession = RoseAgentWork.of(controller, 0, Arrays.asList(new RoseAgentWork.Display(0, -1),
            new RoseAgentWork.Display(8, controller)), Arrays.asList(task(2, 8, 0, "com.example.mail", true)));
        expect(RoseAgentWork.decide(mail, newSession) == RoseAgentWork.Open.CHANGED,
            "A new session (new display) under the same task id is a changed card, not the selected one");
        RoseAgentWork replaced = RoseAgentWork.of(controller, 0, displays, Arrays.asList(task(2, 7, 0, "com.example.chat", true)));
        expect(RoseAgentWork.decide(mail, replaced) == RoseAgentWork.Open.CHANGED, "Same task id, another app: changed");
        RoseAgentWork movedUser = RoseAgentWork.of(controller, 10, displays, Arrays.asList(task(2, 7, 10, "com.example.mail", true)));
        expect(RoseAgentWork.decide(mail, movedUser) == RoseAgentWork.Open.CHANGED, "Another user's task is never opened");
        RoseAgentWork gone = RoseAgentWork.of(controller, 0, displays, Arrays.asList(task(3, 7, 0, "com.example.maps", true)));
        expect(RoseAgentWork.decide(mail, gone) == RoseAgentWork.Open.GONE, "A closed task is gone");
        expect(RoseAgentWork.decide(mail, RoseAgentWork.unavailable("x")) == RoseAgentWork.Open.GONE,
            "An unreadable fresh state never opens anything");
        expect(RoseAgentWork.decide(mail, null) == RoseAgentWork.Open.GONE, "No fresh state never opens anything");
        RoseAgentWork stolen = RoseAgentWork.of(controller, 0, Arrays.asList(new RoseAgentWork.Display(7, 10999)), tasks);
        expect(RoseAgentWork.decide(mail, stolen) == RoseAgentWork.Open.GONE,
            "A display no longer owned by the controller is not agent work");
        expect(RoseAgentWork.describe(RoseAgentWork.Open.NOT_CURRENT, "Maps").equals("Maps is not on Rose's screen right now"), "");
        expect(RoseAgentWork.describe(RoseAgentWork.Open.CHANGED, "Mail").equals("Mail changed; pick it again"), "");
        expect(RoseAgentWork.describe(RoseAgentWork.Open.GONE, "Maps").equals("Maps is no longer running"), "");
        expect(RoseAgentWork.EXTRA_SELECT_DISPLAY.equals("dev.pixel.backgroundcontrol.extra.SELECT_DISPLAY_ID")
            && RoseAgentWork.EXTRA_SELECT_PACKAGE.equals("dev.pixel.backgroundcontrol.extra.SELECT_PACKAGE"), "Request names");
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
