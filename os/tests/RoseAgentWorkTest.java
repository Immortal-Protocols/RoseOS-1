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
    static RoseAgentWork.Automation reported(String... packages) {
        return new RoseAgentWork.Automation(true, Arrays.asList(packages));
    }
    public static void main(String[] args) {
        int controller = 10123;
        // Display 7 is Rose's ComputerControl session display. It is private, so DisplayManager.getDisplays() lists
        // only display 0 to Launcher3 (Display.hasAccess); the old read keyed on that list and showed "No background
        // work" while Rose worked. Nothing below takes a display list: 7 and 9 are what getDeviceIdForDisplayId says
        // belong to a virtual device (9: another app's), and the controller's automated packages come from the
        // platform's report for CONTROLLER only.
        RoseAgentWork.Automation auto = reported("com.example.mail", "com.example.maps");
        List<Integer> displays = Arrays.asList(7, 9);
        List<RoseAgentWork.Task> tasks = Arrays.asList(
            task(1, 0, 0, "com.human.app", true),            // the human's own task: never an agent card
            task(2, 7, 0, "com.example.mail", true),         // Rose's current screen
            task(3, 7, 0, "com.example.maps", false),        // also on Rose's display, behind
            task(4, 9, 0, "com.other.cast", true),           // another app's virtual display, not Rose's work
            task(5, 7, 10, "com.example.mail", true),        // another user
            task(6, 7, 0, RoseAgentWork.CONTROLLER, false)); // the controller itself
        RoseAgentWork work = RoseAgentWork.of(controller, 0, auto, displays, tasks);
        expect(work.state == RoseAgentWork.State.AVAILABLE, "Real agent work on the private display is available");
        expect(work.items.size() == 2 && work.items.get(0).taskId == 2 && work.items.get(1).taskId == 3,
            "Only the controller's automated tasks on a virtual display, for this user, excluding the controller");
        // Selection is decided against a fresh read taken after the tap, never against the row's snapshot.
        RoseAgentWork.Task mail = work.items.get(0), maps = work.items.get(1);
        expect(RoseAgentWork.decide(mail, work) == RoseAgentWork.Open.WATCH, "The current agent screen sends a request");
        expect(RoseAgentWork.decide(maps, work) == RoseAgentWork.Open.NOT_CURRENT,
            "A task behind the agent screen never opens the viewer onto a different task");
        // Races: the platform changed between the row's read and the tap.
        RoseAgentWork after = RoseAgentWork.of(controller, 0, auto, displays, Arrays.asList(
            task(3, 7, 0, "com.example.maps", true), task(2, 7, 0, "com.example.mail", false)));
        expect(RoseAgentWork.decide(mail, after) == RoseAgentWork.Open.NOT_CURRENT,
            "The tapped card was current at row time but not at tap time: refused");
        expect(RoseAgentWork.decide(maps, after) == RoseAgentWork.Open.WATCH, "The now-current card is allowed");
        RoseAgentWork newSession = RoseAgentWork.of(controller, 0, auto, Arrays.asList(8),
            Arrays.asList(task(2, 8, 0, "com.example.mail", true)));
        expect(RoseAgentWork.decide(mail, newSession) == RoseAgentWork.Open.CHANGED,
            "A new session (new display) under the same task id is a changed card, not the selected one");
        RoseAgentWork replaced = RoseAgentWork.of(controller, 0, reported("com.example.chat"), displays,
            Arrays.asList(task(2, 7, 0, "com.example.chat", true)));
        expect(RoseAgentWork.decide(mail, replaced) == RoseAgentWork.Open.CHANGED, "Same task id, another app: changed");
        RoseAgentWork movedUser = RoseAgentWork.of(controller, 10, auto, displays,
            Arrays.asList(task(2, 7, 10, "com.example.mail", true)));
        expect(RoseAgentWork.decide(mail, movedUser) == RoseAgentWork.Open.CHANGED, "Another user's task is never opened");
        RoseAgentWork gone = RoseAgentWork.of(controller, 0, auto, displays, Arrays.asList(task(3, 7, 0, "com.example.maps", true)));
        expect(RoseAgentWork.decide(mail, gone) == RoseAgentWork.Open.GONE, "A closed task is gone");
        expect(RoseAgentWork.decide(mail, RoseAgentWork.unavailable("x")) == RoseAgentWork.Open.GONE,
            "An unreadable fresh state never opens anything");
        expect(RoseAgentWork.decide(mail, null) == RoseAgentWork.Open.GONE, "No fresh state never opens anything");
        RoseAgentWork ended = RoseAgentWork.of(controller, 0, reported(), displays, tasks);
        expect(RoseAgentWork.decide(mail, ended) == RoseAgentWork.Open.GONE,
            "Once the platform reports the controller automating nothing, its old display's tasks are not agent work");
        RoseAgentWork notVirtual = RoseAgentWork.of(controller, 0, auto, Arrays.asList(9), tasks);
        expect(RoseAgentWork.decide(mail, notVirtual) == RoseAgentWork.Open.GONE,
            "A display the platform no longer places on a virtual device is not agent work");
        expect(RoseAgentWork.describe(RoseAgentWork.Open.NOT_CURRENT, "Maps").equals("Maps is not on Rose's screen right now"), "");
        expect(RoseAgentWork.describe(RoseAgentWork.Open.CHANGED, "Mail").equals("Mail changed; pick it again"), "");
        expect(RoseAgentWork.describe(RoseAgentWork.Open.GONE, "Maps").equals("Maps is no longer running"), "");
        expect(RoseAgentWork.EXTRA_SELECT_DISPLAY.equals("dev.pixel.backgroundcontrol.extra.SELECT_DISPLAY_ID")
            && RoseAgentWork.EXTRA_SELECT_PACKAGE.equals("dev.pixel.backgroundcontrol.extra.SELECT_PACKAGE"), "Request names");
        expect(RoseAgentWork.describe(RoseAgentWork.Open.WATCH, "Mail") == null, "Opening shows no notice");

        RoseAgentWork empty = RoseAgentWork.of(controller, 0, reported(), displays, tasks);
        expect(empty.state == RoseAgentWork.State.EMPTY && empty.items.isEmpty()
            && "No background work".equals(empty.reason) && empty.linkLabel().isEmpty(),
            "The platform reports the controller automating nothing: a truthful empty state");
        RoseAgentWork spoof = RoseAgentWork.of(controller, 0, reported("com.example.mail", "com.human.app",
            "com.other.cast"), Arrays.asList(7), Arrays.asList(task(1, 0, 0, "com.human.app", true),
            task(7, 3, 0, "com.example.mail", true)));
        expect(spoof.state == RoseAgentWork.State.EMPTY,
            "An automated package on the default display or on a display of no virtual device never counts");
        RoseAgentWork pending = RoseAgentWork.of(controller, 0, new RoseAgentWork.Automation(false, Arrays.asList()),
            displays, tasks);
        expect(pending.state == RoseAgentWork.State.UNAVAILABLE && pending.items.isEmpty()
            && RoseAgentWork.NOT_REPORTED.equals(pending.reason) && pending.linkLabel().isEmpty() && pending.working() == 0,
            "Work on a virtual display before the platform's first report for the controller is not reported, never "
            + "\"No background work\" and never a card");
        RoseAgentWork quiet = RoseAgentWork.of(controller, 0, new RoseAgentWork.Automation(false, Arrays.asList()),
            displays, Arrays.asList(task(1, 0, 0, "com.human.app", true)));
        expect(quiet.state == RoseAgentWork.State.EMPTY,
            "With no task on any virtual display there is no agent work, reported or not");
        expect(RoseAgentWork.of(-1, 0, auto, displays, tasks).state == RoseAgentWork.State.UNAVAILABLE,
            "No controller for this user is unavailable, not empty");
        expect(RoseAgentWork.of(controller, 0, null, displays, tasks).state == RoseAgentWork.State.UNAVAILABLE
            && RoseAgentWork.of(controller, 0, auto, null, tasks).state == RoseAgentWork.State.UNAVAILABLE
            && RoseAgentWork.of(controller, 0, auto, displays, null).state == RoseAgentWork.State.UNAVAILABLE,
            "An unreadable platform state is unavailable");
        expect(RoseAgentWork.of(controller, 10, reported("com.example.mail"), displays, tasks).items.size() == 1,
            "The other user sees only their work");
        expect(RoseAgentWork.unavailable("x").items.isEmpty(), "Unavailable shows no cards");

        // Labels are looked up only after filtering; one failed or empty lookup never hides valid work.
        java.util.List<Integer> asked = new java.util.ArrayList<>();
        RoseAgentWork labelled = work.labelled(t -> {
            asked.add(t.taskId);
            if (t.taskId == 3) throw new IllegalStateException("private detail of a failed lookup");
            return t.packageName.equals("com.example.mail") ? "Mail" : null;
        });
        expect(asked.equals(Arrays.asList(2, 3)), "Only the kept agent tasks are looked up: never the human's, another "
            + "display's, another user's or the controller's");
        expect(labelled.state == RoseAgentWork.State.AVAILABLE && labelled.items.size() == 2, "Nothing dropped or added");
        expect(labelled.items.get(0).label.equals("Mail") && labelled.items.get(1).label.equals("com.example.maps"),
            "A failed lookup shows the package name; the others keep their label");
        expect(labelled.items.get(1).taskId == 3 && labelled.items.get(1).displayId == 7 && labelled.items.get(1).userId == 0
            && labelled.items.get(1).topOfDisplay == false, "Every boundary fact is kept");
        expect(RoseAgentWork.decide(labelled.items.get(0), labelled) == RoseAgentWork.Open.WATCH, "Selection still works");
        RoseAgentWork allFail = work.labelled(t -> { throw new SecurityException("cross-user"); });
        expect(allFail.state == RoseAgentWork.State.AVAILABLE && allFail.items.get(0).label.equals("com.example.mail"),
            "Even if every lookup fails the work stays available, by package name");
        java.util.List<Integer> none = new java.util.ArrayList<>();
        expect(RoseAgentWork.of(controller, 0, reported(), displays, tasks)
            .labelled(t -> { none.add(t.taskId); return "x"; }).state == RoseAgentWork.State.EMPTY
            && pending.labelled(t -> { none.add(t.taskId); return "x"; }) == pending && none.isEmpty(),
            "Empty or not-yet-reported work looks nothing up");
        expect(RoseAgentWork.failed(RoseAgentWork.Step.TASKS).state == RoseAgentWork.State.UNAVAILABLE
            && "Rose's work could not be read (tasks)".equals(RoseAgentWork.failed(RoseAgentWork.Step.TASKS).reason)
            && "Rose's work could not be read (controller)".equals(RoseAgentWork.failed(RoseAgentWork.Step.CONTROLLER).reason)
            && "Rose's work could not be read (displays)".equals(RoseAgentWork.failed(RoseAgentWork.Step.DISPLAYS).reason)
            && "Rose's work could not be read (automation)".equals(RoseAgentWork.failed(RoseAgentWork.Step.AUTOMATION).reason),
            "A failed platform read names its step with a fixed word");

        // The HiFi link between the carousels counts only Rose's current screens, from the platform read.
        expect(work.working() == 1 && "1 task in progress".equals(work.linkLabel()),
            "Mail is Rose's current screen; Maps is behind it, a background app, not in progress");
        RoseAgentWork two = RoseAgentWork.of(controller, 0, reported("com.example.mail", "com.example.maps",
            "com.example.chat"), Arrays.asList(7, 8), Arrays.asList(task(2, 7, 0, "com.example.mail", true),
            task(3, 8, 0, "com.example.maps", true), task(4, 8, 0, "com.example.chat", false)));
        expect(two.working() == 2 && "2 tasks in progress".equals(two.linkLabel()), "Two agent screens, two in progress");
        RoseAgentWork idle = RoseAgentWork.of(controller, 0, auto, displays, Arrays.asList(
            task(6, 7, 0, RoseAgentWork.CONTROLLER, true), task(3, 7, 0, "com.example.maps", false)));
        expect(idle.state == RoseAgentWork.State.AVAILABLE && idle.working() == 0 && "No active work".equals(idle.linkLabel()),
            "Background apps with the controller itself in front: shown, but nothing claimed in progress");
        expect(RoseAgentWork.failed(RoseAgentWork.Step.TASKS).linkLabel().isEmpty()
            && RoseAgentWork.failed(RoseAgentWork.Step.TASKS).working() == 0, "An unreadable state claims no work");
        expect(work.labelled(t -> "x").working() == work.working(), "Labels never change what is in progress");
        System.out.println("RoseAgentWorkTest: " + checks + " checks passed");
    }
}
