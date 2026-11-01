package com.android.quickstep.fallback;

import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public final class RoseAgentWorkTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static RoseAgentWork.Task task(int id, int display, int user, String pkg, boolean top) {
        return new RoseAgentWork.Task(id, display, user, pkg, pkg + " label", top);
    }
    /** Each display its own ComputerControl device (device id = display id), the task list complete. */
    static RoseAgentWork work(int controller, int user, RoseAgentWork.Automation auto, List<Integer> displays,
            List<RoseAgentWork.Task> tasks) {
        if (displays == null) return RoseAgentWork.of(controller, user, auto, null, tasks, true);
        Map<Integer, Integer> devices = new HashMap<>();
        for (int d : displays) devices.put(d, d);
        return RoseAgentWork.of(controller, user, auto, devices, tasks, true);
    }
    static RoseAgentWork.Automation reported(String... packages) {
        return new RoseAgentWork.Automation(true, Arrays.asList(packages), Arrays.asList());
    }
    public static void main(String[] args) {
        int controller = 10123;
        // Display 7 is Rose's ComputerControl session display. It is private, so DisplayManager.getDisplays() lists
        // only display 0 to Launcher3 (Display.hasAccess); the old read keyed on that list and showed "No background
        // work" while Rose worked. Nothing below takes that display list: 7 is a display of a virtual device whose
        // platform-set profile is DEVICE_PROFILE_COMPUTER_CONTROL; 9 (com.other.cast's) is another app's virtual
        // device, so it is never passed. The automated packages come from the platform's per-owner report.
        // The report is every non-finishing activity on the controller's session displays, its own included.
        RoseAgentWork.Automation auto = reported("com.example.mail", "com.example.maps", RoseAgentWork.CONTROLLER);
        List<Integer> displays = Arrays.asList(7);
        List<RoseAgentWork.Task> tasks = Arrays.asList(
            task(1, 0, 0, "com.human.app", true),            // the human's own task: never an agent card
            task(2, 7, 0, "com.example.mail", true),         // Rose's current screen
            task(3, 7, 0, "com.example.maps", false),        // also on Rose's display, behind
            task(4, 9, 0, "com.other.cast", true),           // another app's virtual display, not Rose's work
            task(5, 7, 10, "com.example.mail", true),        // another user
            task(6, 7, 0, RoseAgentWork.CONTROLLER, false)); // the controller itself
        RoseAgentWork work = work(controller, 0, auto, displays, tasks);
        expect(work.state == RoseAgentWork.State.AVAILABLE, "Real agent work on the private display is available");
        expect(work.items.size() == 2 && work.items.get(0).taskId == 2 && work.items.get(1).taskId == 3,
            "Only the controller's automated tasks on a virtual display, for this user, excluding the controller");
        // Selection is decided against a fresh read taken after the tap, never against the row's snapshot.
        RoseAgentWork.Task mail = work.items.get(0), maps = work.items.get(1);
        expect(RoseAgentWork.decide(mail, work) == RoseAgentWork.Open.WATCH, "The current agent screen sends a request");
        expect(RoseAgentWork.decide(maps, work) == RoseAgentWork.Open.NOT_CURRENT,
            "A task behind the agent screen never opens the viewer onto a different task");
        // Races: the platform changed between the row's read and the tap.
        RoseAgentWork after = work(controller, 0, auto, displays, Arrays.asList(
            task(3, 7, 0, "com.example.maps", true), task(2, 7, 0, "com.example.mail", false)));
        expect(RoseAgentWork.decide(mail, after) == RoseAgentWork.Open.NOT_CURRENT,
            "The tapped card was current at row time but not at tap time: refused");
        expect(RoseAgentWork.decide(maps, after) == RoseAgentWork.Open.WATCH, "The now-current card is allowed");
        RoseAgentWork newSession = work(controller, 0, auto, Arrays.asList(8),
            Arrays.asList(task(2, 8, 0, "com.example.mail", true)));
        expect(RoseAgentWork.decide(mail, newSession) == RoseAgentWork.Open.CHANGED,
            "A new session (new display) under the same task id is a changed card, not the selected one");
        RoseAgentWork replaced = work(controller, 0, reported("com.example.chat"), displays,
            Arrays.asList(task(2, 7, 0, "com.example.chat", true)));
        expect(RoseAgentWork.decide(mail, replaced) == RoseAgentWork.Open.CHANGED, "Same task id, another app: changed");
        RoseAgentWork movedUser = work(controller, 10, auto, displays,
            Arrays.asList(task(2, 7, 10, "com.example.mail", true)));
        expect(RoseAgentWork.decide(mail, movedUser) == RoseAgentWork.Open.CHANGED, "Another user's task is never opened");
        RoseAgentWork gone = work(controller, 0, auto, displays, Arrays.asList(task(3, 7, 0, "com.example.maps", true)));
        expect(RoseAgentWork.decide(mail, gone) == RoseAgentWork.Open.GONE, "A closed task is gone");
        expect(RoseAgentWork.decide(mail, RoseAgentWork.unavailable("x")) == RoseAgentWork.Open.GONE,
            "An unreadable fresh state never opens anything");
        expect(RoseAgentWork.decide(mail, null) == RoseAgentWork.Open.GONE, "No fresh state never opens anything");
        RoseAgentWork ended = work(controller, 0, reported(), displays, tasks);
        expect(RoseAgentWork.decide(mail, ended) == RoseAgentWork.Open.GONE,
            "Once the platform reports the controller automating nothing, its old display's tasks are not agent work");
        RoseAgentWork notVirtual = work(controller, 0, auto, Arrays.asList(9), tasks);
        expect(RoseAgentWork.decide(mail, notVirtual) == RoseAgentWork.Open.GONE,
            "A display that is no longer a ComputerControl device's is not agent work");

        // #156 cross-display false card (old-fails on PR337, whose of() took every virtual device's display): Rose
        // automates mail on its session display 7 while the user also casts mail to display 9, another app's virtual
        // device. The cast is never Rose's card and never counted as in progress.
        List<RoseAgentWork.Task> cast = Arrays.asList(task(2, 7, 0, "com.example.mail", true),
            task(8, 9, 0, "com.example.mail", true));
        RoseAgentWork onlyRose = work(controller, 0, reported("com.example.mail"), Arrays.asList(7), cast);
        expect(onlyRose.items.size() == 1 && onlyRose.items.get(0).taskId == 2 && onlyRose.working() == 1
            && "1 task in progress".equals(onlyRose.linkLabel()) && onlyRose.reason == null,
            "The same package on another app's virtual display is not Rose's work");
        expect(RoseAgentWork.decide(task(8, 9, 0, "com.example.mail", true), onlyRose) == RoseAgentWork.Open.GONE,
            "and is refused if it ever were a card");
        // Another ComputerControl session owner (display 11) also runs mail. Both devices run only apps the controller
        // reports, so either could be Rose's: nothing is attributed and the row says so instead of guessing.
        List<RoseAgentWork.Task> twoAgents = Arrays.asList(task(2, 7, 0, "com.example.mail", true),
            task(3, 7, 0, "com.example.maps", false), task(9, 11, 0, "com.example.mail", true));
        RoseAgentWork.Automation shared = new RoseAgentWork.Automation(true,
            Arrays.asList("com.example.mail", "com.example.maps"), Arrays.asList("com.example.mail"));
        RoseAgentWork both = work(controller, 0, shared, Arrays.asList(7, 11), twoAgents);
        expect(both.state == RoseAgentWork.State.UNAVAILABLE && both.items.isEmpty() && both.unattributed.size() == 3
            && RoseAgentWork.UNCONFIRMED.equals(both.reason) && both.linkLabel().isEmpty() && both.working() == 0,
            "Two devices that could each be Rose's: no card, not empty, the reason shown");
        expect(RoseAgentWork.decide(mail, both) == RoseAgentWork.Open.UNATTRIBUTED
            && RoseAgentWork.describe(RoseAgentWork.Open.UNATTRIBUTED, "Mail").equals("Mail can't be confirmed as Rose's work"),
            "Selecting a card that can no longer be confirmed is refused with that reason");
        // Display 11 also runs chat, which the controller does not report: it is another owner's device, so 7 is the
        // only candidate and is the controller's. Mail, which that other owner also reports, still stays unattributed.
        List<RoseAgentWork.Task> distinct = Arrays.asList(task(2, 7, 0, "com.example.mail", true),
            task(3, 7, 0, "com.example.maps", false), task(9, 11, 0, "com.example.mail", true),
            task(10, 11, 0, "com.example.chat", false));
        RoseAgentWork partly = work(controller, 0, new RoseAgentWork.Automation(true,
            Arrays.asList("com.example.mail", "com.example.maps"), Arrays.asList("com.example.mail", "com.example.chat")),
            Arrays.asList(7, 11), distinct);
        expect(partly.state == RoseAgentWork.State.AVAILABLE && partly.items.size() == 1 && partly.items.get(0).taskId == 3
            && partly.unattributed.size() == 1 && partly.unattributed.get(0).taskId == 2
            && RoseAgentWork.UNATTRIBUTED.equals(partly.reason) && "No active work".equals(partly.linkLabel()),
            "On the one candidate, a package another owner also reports is held back; the rest is Rose's");
        expect(partly.labelled(t -> "x").unattributed.size() == 1, "Labels keep the unattributed facts");
        RoseAgentWork allShared = work(controller, 0, new RoseAgentWork.Automation(true,
            Arrays.asList("com.example.mail"), Arrays.asList("com.example.mail", "com.example.chat")), Arrays.asList(7, 11),
            Arrays.asList(task(2, 7, 0, "com.example.mail", true), task(9, 11, 0, "com.example.mail", true),
            task(10, 11, 0, "com.example.chat", false)));
        expect(allShared.state == RoseAgentWork.State.UNAVAILABLE && allShared.items.isEmpty()
            && RoseAgentWork.UNATTRIBUTED.equals(allShared.reason) && allShared.linkLabel().isEmpty(),
            "When nothing can be attributed the row is unavailable with that reason, never empty and never a card");
        RoseAgentWork othersOnly = work(controller, 0, new RoseAgentWork.Automation(true,
            Arrays.asList(), Arrays.asList("com.example.mail")), Arrays.asList(11), Arrays.asList(task(9, 11, 0,
            "com.example.mail", true)));
        expect(othersOnly.state == RoseAgentWork.State.EMPTY && othersOnly.unattributed.isEmpty(),
            "The controller reports nothing: another agent's own work is not Rose's, a truthful empty row");

        // #156 initial load, callback order (old-fails on PR338, 66dbe45). The platform posts one report per owner and
        // user, with no snapshot-complete marker. Rose (display 7) and another agent (display 11) both run mail.
        List<RoseAgentWork.Task> race = Arrays.asList(task(2, 7, 0, "com.example.mail", true),
            task(9, 11, 0, "com.example.mail", true));
        RoseAgentWork controllerFirst = work(controller, 0, reported("com.example.mail"), Arrays.asList(7, 11), race);
        expect(controllerFirst.items.isEmpty() && controllerFirst.state == RoseAgentWork.State.UNAVAILABLE
            && RoseAgentWork.UNCONFIRMED.equals(controllerFirst.reason)
            && RoseAgentWork.decide(task(9, 11, 0, "com.example.mail", true), controllerFirst) == RoseAgentWork.Open.UNATTRIBUTED,
            "Controller's report first, the other owner's not yet: no card for display 11 (PR338 showed two)");
        RoseAgentWork otherLater = work(controller, 0, new RoseAgentWork.Automation(true, Arrays.asList("com.example.mail"),
            Arrays.asList("com.example.mail")), Arrays.asList(7, 11), race);
        expect(otherLater.items.isEmpty() && RoseAgentWork.UNCONFIRMED.equals(otherLater.reason),
            "Once the other owner's report arrives it is still not attributed: no card was ever published");
        RoseAgentWork otherFirst = work(controller, 0, new RoseAgentWork.Automation(false, Arrays.asList(),
            Arrays.asList("com.example.mail")), Arrays.asList(7, 11), race);
        expect(otherFirst.items.isEmpty() && RoseAgentWork.NOT_REPORTED.equals(otherFirst.reason),
            "The other owner's report first, the controller's not yet: not reported, no card");
        // Different apps: display 11 runs chat, which the controller does not report, so it is not the controller's
        // even before its owner's report arrives; display 7 is then the only candidate.
        List<RoseAgentWork.Task> apart = Arrays.asList(task(2, 7, 0, "com.example.mail", true),
            task(9, 11, 0, "com.example.chat", true));
        RoseAgentWork early = work(controller, 0, reported("com.example.mail"), Arrays.asList(7, 11), apart);
        RoseAgentWork late = work(controller, 0, new RoseAgentWork.Automation(true, Arrays.asList("com.example.mail"),
            Arrays.asList("com.example.chat")), Arrays.asList(7, 11), apart);
        expect(early.items.size() == 1 && early.items.get(0).taskId == 2 && late.items.size() == 1
            && late.items.get(0).taskId == 2 && early.reason == null && late.reason == null,
            "A device running an app the controller does not report is never Rose's, whatever the order");
        // A non-empty report with none of its devices visible: a report still in flight (PR338 said "No background
        // work"). An empty report with only another agent's work is a truthful empty row.
        RoseAgentWork inFlight = work(controller, 0, reported("com.example.mail"), Arrays.asList(7),
            Arrays.asList(task(4, 7, 0, "com.example.chat", true)));
        expect(inFlight.state == RoseAgentWork.State.UNAVAILABLE && RoseAgentWork.UNCONFIRMED.equals(inFlight.reason),
            "A report that does not match the screens is unconfirmed, never \"No background work\"");
        expect(work(controller, 0, reported(), Arrays.asList(7), Arrays.asList(task(4, 7, 0, "com.example.chat", true)))
            .state == RoseAgentWork.State.EMPTY, "The controller reports an ended session: empty");
        Map<Integer, Integer> sevenEleven = new HashMap<>();
        sevenEleven.put(7, 7);
        sevenEleven.put(11, 11);
        RoseAgentWork truncated = RoseAgentWork.of(controller, 0, reported("com.example.mail"), sevenEleven, apart, false);
        expect(truncated.items.isEmpty() && RoseAgentWork.UNCONFIRMED.equals(truncated.reason),
            "A possibly truncated task list could hide one of the controller's devices: unconfirmed");
        Map<Integer, Integer> oneDevice = new HashMap<>();
        oneDevice.put(7, 3);
        oneDevice.put(8, 3);
        RoseAgentWork twoDisplays = RoseAgentWork.of(controller, 0, reported("com.example.mail", "com.example.maps"),
            oneDevice, Arrays.asList(task(2, 7, 0, "com.example.mail", true), task(5, 8, 0, "com.example.maps", true)), true);
        expect(twoDisplays.items.size() == 2 && twoDisplays.reason == null,
            "Two displays of one session device are one candidate");
        RoseAgentWork twoSessions = work(controller, 0, reported("com.example.mail", "com.example.maps"), Arrays.asList(7, 8),
            Arrays.asList(task(2, 7, 0, "com.example.mail", true), task(5, 8, 0, "com.example.maps", true)));
        expect(twoSessions.items.isEmpty() && RoseAgentWork.UNCONFIRMED.equals(twoSessions.reason),
            "Two session devices both matching the controller's report cannot be told apart from another agent with "
            + "the same apps: fails closed even if both are Rose's (the cost of no ownership contract)");
        expect(RoseAgentWork.unconfirmed().state == RoseAgentWork.State.UNAVAILABLE
            && RoseAgentWork.UNCONFIRMED.equals(RoseAgentWork.unconfirmed().reason) && RoseAgentWork.unconfirmed().items.isEmpty(),
            "A report arriving during a read gives no card");
        // Reconnect: a new registration has heard nothing for this user yet, so any session work is not reported.
        expect(work(controller, 0, new RoseAgentWork.Automation(false, Arrays.asList(), Arrays.asList()), Arrays.asList(7),
            race).state == RoseAgentWork.State.UNAVAILABLE, "A fresh registration publishes no card before its report");

        // What remains (needs a platform contract): two worlds the existing reports cannot tell apart.
        // True: display 11 is Rose's (mail), display 7 another agent's (maps); both reports current.
        // Stale: Rose moved display 7 from mail to maps and the other agent display 11 from maps to mail; both new
        // reports are still in flight, so the cached reports are the old ones. The inputs are identical.
        List<RoseAgentWork.Task> swapped = Arrays.asList(task(5, 7, 0, "com.example.maps", true),
            task(9, 11, 0, "com.example.mail", true));
        RoseAgentWork trueWorld = work(controller, 0, new RoseAgentWork.Automation(true, Arrays.asList("com.example.mail"),
            Arrays.asList("com.example.maps")), Arrays.asList(7, 11), swapped);
        RoseAgentWork staleWorld = work(controller, 0, new RoseAgentWork.Automation(true, Arrays.asList("com.example.mail"),
            Arrays.asList("com.example.maps")), Arrays.asList(7, 11), swapped);
        expect(trueWorld.items.size() == 1 && trueWorld.items.get(0).taskId == 9 && staleWorld.items.size() == 1
            && staleWorld.items.get(0).taskId == 9,
            "RESIDUAL: identical inputs, so no client rule can tell them apart; the card is right in one world and "
            + "false in the other until the in-flight reports arrive (missing: a report generation or barrier, or a "
            + "per-device owner, from the platform)");
        expect(RoseAgentWork.describe(RoseAgentWork.Open.NOT_CURRENT, "Maps").equals("Maps is not on Rose's screen right now"), "");
        expect(RoseAgentWork.describe(RoseAgentWork.Open.CHANGED, "Mail").equals("Mail changed; pick it again"), "");
        expect(RoseAgentWork.describe(RoseAgentWork.Open.GONE, "Maps").equals("Maps is no longer running"), "");
        expect(RoseAgentWork.EXTRA_SELECT_DISPLAY.equals("dev.pixel.backgroundcontrol.extra.SELECT_DISPLAY_ID")
            && RoseAgentWork.EXTRA_SELECT_PACKAGE.equals("dev.pixel.backgroundcontrol.extra.SELECT_PACKAGE"), "Request names");
        expect(RoseAgentWork.describe(RoseAgentWork.Open.WATCH, "Mail") == null, "Opening shows no notice");

        RoseAgentWork empty = work(controller, 0, reported(), displays, tasks);
        expect(empty.state == RoseAgentWork.State.EMPTY && empty.items.isEmpty()
            && "No background work".equals(empty.reason) && empty.linkLabel().isEmpty(),
            "The platform reports the controller automating nothing: a truthful empty state");
        RoseAgentWork spoof = work(controller, 0, reported("com.example.mail", "com.human.app",
            "com.other.cast"), Arrays.asList(7), Arrays.asList(task(1, 0, 0, "com.human.app", true),
            task(7, 3, 0, "com.example.mail", true)));
        expect(spoof.state == RoseAgentWork.State.EMPTY,
            "An automated package on the default display or on a display of no virtual device never counts");
        RoseAgentWork pending = work(controller, 0, new RoseAgentWork.Automation(false, Arrays.asList(), Arrays.asList()),
            displays, tasks);
        expect(pending.state == RoseAgentWork.State.UNAVAILABLE && pending.items.isEmpty()
            && RoseAgentWork.NOT_REPORTED.equals(pending.reason) && pending.linkLabel().isEmpty() && pending.working() == 0,
            "Work on a virtual display before the platform's first report for the controller is not reported, never "
            + "\"No background work\" and never a card");
        RoseAgentWork quiet = work(controller, 0, new RoseAgentWork.Automation(false, Arrays.asList(), Arrays.asList()),
            displays, Arrays.asList(task(1, 0, 0, "com.human.app", true)));
        expect(quiet.state == RoseAgentWork.State.EMPTY,
            "With no task on any virtual display there is no agent work, reported or not");
        expect(work(-1, 0, auto, displays, tasks).state == RoseAgentWork.State.UNAVAILABLE,
            "No controller for this user is unavailable, not empty");
        expect(work(controller, 0, null, displays, tasks).state == RoseAgentWork.State.UNAVAILABLE
            && work(controller, 0, auto, null, tasks).state == RoseAgentWork.State.UNAVAILABLE
            && work(controller, 0, auto, displays, null).state == RoseAgentWork.State.UNAVAILABLE,
            "An unreadable platform state is unavailable");
        expect(work(controller, 10, reported("com.example.mail"), displays, tasks).items.size() == 1,
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
        expect(work(controller, 0, reported(), displays, tasks)
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
            "com.example.chat"), oneDevice, Arrays.asList(task(2, 7, 0, "com.example.mail", true),
            task(3, 8, 0, "com.example.maps", true), task(4, 8, 0, "com.example.chat", false)), true);
        expect(two.working() == 2 && "2 tasks in progress".equals(two.linkLabel()),
            "Two screens of Rose's one session device, two in progress");
        RoseAgentWork idle = work(controller, 0, auto, displays, Arrays.asList(
            task(6, 7, 0, RoseAgentWork.CONTROLLER, true), task(3, 7, 0, "com.example.maps", false)));
        expect(idle.state == RoseAgentWork.State.AVAILABLE && idle.working() == 0 && "No active work".equals(idle.linkLabel()),
            "Background apps with the controller itself in front: shown, but nothing claimed in progress");
        expect(RoseAgentWork.failed(RoseAgentWork.Step.TASKS).linkLabel().isEmpty()
            && RoseAgentWork.failed(RoseAgentWork.Step.TASKS).working() == 0, "An unreadable state claims no work");
        expect(work.labelled(t -> "x").working() == work.working(), "Labels never change what is in progress");
        System.out.println("RoseAgentWorkTest: " + checks + " checks passed");
    }
}
