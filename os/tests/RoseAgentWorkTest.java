package com.android.quickstep.fallback;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class RoseAgentWorkTest {
    static int checks;
    static final int CONTROLLER_UID = 10123, OTHER_UID = 10456;
    static final String OTHER = "com.other.agent";

    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static RoseAgentWork.Task task(int id, int display, int user, String pkg, boolean top) {
        return new RoseAgentWork.Task(id, display, user, pkg, pkg + " label", top);
    }
    /** display -> device pairs, as getVirtualDevices() lists COMPUTER_CONTROL devices now. */
    static Map<Integer, Integer> devices(int... displayDevicePairs) {
        Map<Integer, Integer> out = new HashMap<>();
        for (int i = 0; i < displayDevicePairs.length; i += 2) out.put(displayDevicePairs[i], displayDevicePairs[i + 1]);
        return out;
    }
    /** An Ownership fed one valid snapshot. */
    static RoseAgentWork.Owners owned(long gen, int[] deviceIds, String[] packages, int[] uids) {
        RoseAgentWork.Ownership o = new RoseAgentWork.Ownership();
        expect(o.accept(gen, deviceIds, packages, uids), "a valid first snapshot is applied");
        return o.snapshot();
    }
    static RoseAgentWork.Owners roseOwns(int... deviceIds) {
        String[] packages = new String[deviceIds.length];
        int[] uids = new int[deviceIds.length];
        Arrays.fill(packages, RoseAgentWork.CONTROLLER);
        Arrays.fill(uids, CONTROLLER_UID);
        return owned(1, deviceIds, packages, uids);
    }
    /**
     * The per-device apps the platform reports in the same dispatch when it is current: every package of the owner's
     * user with an activity (here: a task) on that device's displays. Owners not yet received stay so.
     */
    static RoseAgentWork.Owners withTaskApps(RoseAgentWork.Owners o, Map<Integer, Integer> devices,
            List<RoseAgentWork.Task> tasks) {
        if (o == null || !o.received || devices == null || tasks == null) return o;
        Map<Integer, Set<String>> apps = new HashMap<>();
        for (int d : o.byDevice.keySet()) apps.put(d, new HashSet<>());
        for (RoseAgentWork.Task t : tasks) {
            Integer d = devices.get(t.displayId);
            if (d != null && apps.containsKey(d) && t.userId == o.byDevice.get(d).uid / 100000) apps.get(d).add(t.packageName);
        }
        return new RoseAgentWork.Owners(true, false, o.byDevice, o.generation, true, false, o.generation, apps,
            Collections.emptySet());
    }
    static RoseAgentWork derived(int controllerUid, int user, RoseAgentWork.Owners owners, Map<Integer, Integer> devices,
            List<Integer> released, List<RoseAgentWork.Task> tasks, boolean complete) {
        return RoseAgentWork.of(controllerUid, user, withTaskApps(owners, devices, tasks), devices, released, tasks, complete);
    }
    static RoseAgentWork work(RoseAgentWork.Owners owners, Map<Integer, Integer> sessionDevices, List<RoseAgentWork.Task> tasks) {
        return derived(CONTROLLER_UID, 0, owners, sessionDevices, Collections.emptyList(), tasks, true);
    }
    static List<Integer> ids(List<RoseAgentWork.Task> tasks) {
        List<Integer> out = new ArrayList<>();
        for (RoseAgentWork.Task t : tasks) out.add(t.taskId);
        return out;
    }

    public static void main(String[] args) {
        // Display 7 is Rose's ComputerControl session display (device 7, owned by the controller with its own uid). It is
        // private, so DisplayManager.getDisplays() never lists it to Launcher3; nothing here needs it to.
        RoseAgentWork.Owners rose = roseOwns(7);
        List<RoseAgentWork.Task> tasks = Arrays.asList(
            task(1, 0, 0, "com.human.app", true),            // the human's own task: never an agent card
            task(2, 7, 0, "com.example.mail", true),         // Rose's current screen
            task(3, 7, 0, "com.example.maps", false),        // also on Rose's display, behind
            task(4, 9, 0, "com.other.cast", true),           // another app's virtual display (not a session device)
            task(5, 7, 10, "com.example.mail", true),        // another user
            task(6, 7, 0, RoseAgentWork.CONTROLLER, false)); // the controller itself
        RoseAgentWork work = work(rose, devices(7, 7), tasks);
        expect(work.state == RoseAgentWork.State.AVAILABLE && ids(work.items).equals(Arrays.asList(2, 3))
            && work.reason == null && work.unattributed.isEmpty(),
            "Only this user's tasks on the controller-owned session device, excluding the controller");

        // Selection is decided against a fresh read taken after the tap, never against the row's snapshot.
        RoseAgentWork.Task mail = work.items.get(0), maps = work.items.get(1);
        expect(RoseAgentWork.decide(mail, work) == RoseAgentWork.Open.WATCH, "The current agent screen sends a request");
        expect(RoseAgentWork.decide(maps, work) == RoseAgentWork.Open.NOT_CURRENT,
            "A task behind the agent screen never opens the viewer onto a different task");
        RoseAgentWork after = work(rose, devices(7, 7), Arrays.asList(task(3, 7, 0, "com.example.maps", true),
            task(2, 7, 0, "com.example.mail", false)));
        expect(RoseAgentWork.decide(mail, after) == RoseAgentWork.Open.NOT_CURRENT, "Current at row time, not at tap time");
        expect(RoseAgentWork.decide(maps, after) == RoseAgentWork.Open.WATCH, "The now-current card is allowed");
        RoseAgentWork moved = work(roseOwns(8), devices(8, 8), Arrays.asList(task(2, 8, 0, "com.example.mail", true)));
        expect(RoseAgentWork.decide(mail, moved) == RoseAgentWork.Open.CHANGED, "Same task id on a new session display");
        RoseAgentWork replaced = work(rose, devices(7, 7), Arrays.asList(task(2, 7, 0, "com.example.chat", true)));
        expect(RoseAgentWork.decide(mail, replaced) == RoseAgentWork.Open.CHANGED, "Same task id, another app: changed");
        RoseAgentWork otherUser = derived(CONTROLLER_UID, 10, rose, devices(7, 7), Collections.emptyList(),
            Arrays.asList(task(2, 7, 10, "com.example.mail", true)), true);
        expect(RoseAgentWork.decide(mail, otherUser) != RoseAgentWork.Open.WATCH && otherUser.items.isEmpty(),
            "Another user's task on Rose's device is not in the platform's list for Rose's user: never a card, never opened");
        RoseAgentWork gone = work(rose, devices(7, 7), Arrays.asList(task(3, 7, 0, "com.example.maps", true)));
        expect(RoseAgentWork.decide(mail, gone) == RoseAgentWork.Open.GONE, "A closed task is gone");
        expect(RoseAgentWork.decide(mail, RoseAgentWork.unavailable("x")) == RoseAgentWork.Open.GONE
            && RoseAgentWork.decide(mail, null) == RoseAgentWork.Open.GONE, "An unreadable fresh state never opens anything");
        expect(RoseAgentWork.describe(RoseAgentWork.Open.NOT_CURRENT, "Maps").equals("Maps is not on Rose's screen right now")
            && RoseAgentWork.describe(RoseAgentWork.Open.CHANGED, "Mail").equals("Mail changed; pick it again")
            && RoseAgentWork.describe(RoseAgentWork.Open.GONE, "Maps").equals("Maps is no longer running")
            && RoseAgentWork.describe(RoseAgentWork.Open.UNATTRIBUTED, "Mail").equals("Mail can't be confirmed as Rose's work")
            && RoseAgentWork.describe(RoseAgentWork.Open.WATCH, "Mail") == null, "Selection notices");
        expect(RoseAgentWork.EXTRA_SELECT_DISPLAY.equals("dev.pixel.backgroundcontrol.extra.SELECT_DISPLAY_ID")
            && RoseAgentWork.EXTRA_SELECT_PACKAGE.equals("dev.pixel.backgroundcontrol.extra.SELECT_PACKAGE"), "Request names");

        // The paired scenarios agreed with OS Core (fixtures/rose-switcher/automated-devices-scenarios.json mirrors them).
        // 1. Initial empty snapshot: with no session task an empty row; with one, unconfirmed (never a card).
        RoseAgentWork.Owners none = owned(1, new int[0], new String[0], new int[0]);
        expect(work(none, devices(), Arrays.asList(task(1, 0, 0, "com.human.app", true))).state == RoseAgentWork.State.EMPTY,
            "1a: empty snapshot, no session task: empty");
        RoseAgentWork loading = work(none, devices(7, 7), Arrays.asList(task(2, 7, 0, "com.example.mail", true)));
        expect(loading.state == RoseAgentWork.State.UNAVAILABLE && loading.items.isEmpty()
            && RoseAgentWork.UNCONFIRMED.equals(loading.reason) && ids(loading.unattributed).equals(Arrays.asList(2))
            && RoseAgentWork.decide(task(2, 7, 0, "com.example.mail", true), loading) == RoseAgentWork.Open.UNATTRIBUTED,
            "1b: a session task before its device's ownership is reported: unconfirmed, selection refused");
        // 2. Rose's session alone.
        expect(ids(work(rose, devices(7, 7), Arrays.asList(task(2, 7, 0, "com.example.mail", true))).items)
            .equals(Arrays.asList(2)), "2: Rose's session device: Rose's card");
        // 3. Another owner runs the same app on a second session device: only Rose's is a card, in any report order.
        RoseAgentWork.Owners both = owned(2, new int[] {7, 11}, new String[] {RoseAgentWork.CONTROLLER, OTHER},
            new int[] {CONTROLLER_UID, OTHER_UID});
        List<RoseAgentWork.Task> sameApp = Arrays.asList(task(2, 7, 0, "com.example.mail", true),
            task(9, 11, 0, "com.example.mail", true));
        RoseAgentWork three = work(both, devices(7, 7, 11, 11), sameApp);
        expect(ids(three.items).equals(Arrays.asList(2)) && three.reason == null && three.unattributed.isEmpty(),
            "3: the other owner's device is never Rose's; no package coincidence");
        RoseAgentWork threeEarly = work(rose, devices(7, 7, 11, 11), sameApp);
        expect(threeEarly.items.isEmpty() && threeEarly.state == RoseAgentWork.State.UNAVAILABLE
            && ids(threeEarly.unattributed).equals(Arrays.asList(9, 2)) && RoseAgentWork.UNCONFIRMED.equals(threeEarly.reason),
            "3: before the other device's ownership arrives nothing is shown (common contract), never a partial row");
        // 4. Rose's session closed: the stale entry has no task on it and gives no card.
        expect(work(rose, devices(11, 11), Arrays.asList(task(9, 11, 0, "com.example.mail", true))).items.isEmpty(),
            "4: a stale owner entry never makes another device's task Rose's");
        // 5. A display released but still holding a task: unconfirmed, never a card.
        RoseAgentWork released = derived(CONTROLLER_UID, 0, rose, devices(), Arrays.asList(7),
            Arrays.asList(task(2, 7, 0, "com.example.mail", true)), true);
        expect(released.items.isEmpty() && RoseAgentWork.UNCONFIRMED.equals(released.reason)
            && ids(released.unattributed).equals(Arrays.asList(2)), "5: released session display: unconfirmed");
        expect(work(rose, devices(), Arrays.asList(task(4, 9, 0, "com.other.cast", true))).state == RoseAgentWork.State.EMPTY,
            "5: a display that was never a session display (a cast) is not agent work at all");
        // 6. Generations: the first valid snapshot per registration, then only higher ones.
        RoseAgentWork.Ownership ledger = new RoseAgentWork.Ownership();
        expect(!ledger.snapshot().received, "6: nothing received before the first snapshot");
        expect(ledger.accept(5, new int[] {7}, new String[] {RoseAgentWork.CONTROLLER}, new int[] {CONTROLLER_UID}),
            "6: first valid snapshot applied whatever its generation");
        expect(!ledger.accept(5, new int[] {11}, new String[] {OTHER}, new int[] {OTHER_UID})
            && !ledger.accept(4, new int[0], new String[0], new int[0]), "6: equal or lower generation ignored");
        expect(ledger.snapshot().byDevice.keySet().equals(Collections.singleton(7)), "6: the ignored ones changed nothing");
        expect(ledger.accept(6, new int[0], new String[0], new int[0]) && ledger.snapshot().byDevice.isEmpty()
            && ledger.snapshot().received, "6: a higher generation replaces the whole map, even with an empty one");
        // 7. Reconnect: a new registration starts from nothing; not reported until its initial snapshot arrives.
        ledger.reset();
        RoseAgentWork reconnect = work(ledger.snapshot(), devices(7, 7), Arrays.asList(task(2, 7, 0, "com.example.mail", true)));
        expect(reconnect.items.isEmpty() && RoseAgentWork.NOT_REPORTED.equals(reconnect.reason),
            "7: after re-registration nothing is shown until its snapshot");
        expect(ledger.accept(1, new int[] {7}, new String[] {RoseAgentWork.CONTROLLER}, new int[] {CONTROLLER_UID})
            && ids(work(ledger.snapshot(), devices(7, 7), Arrays.asList(task(2, 7, 0, "com.example.mail", true))).items)
                .equals(Arrays.asList(2)), "7: the new initial snapshot, even with a lower generation, restores the row");
        // 8. Another user's controller session (same package, that user's uid) is never this user's card.
        RoseAgentWork.Owners otherUsers = owned(1, new int[] {7}, new String[] {RoseAgentWork.CONTROLLER},
            new int[] {1010123});
        expect(work(otherUsers, devices(7, 7), Arrays.asList(task(2, 7, 0, "com.example.mail", true))).items.isEmpty(),
            "8: owner uid must be the controller's uid for this user");
        expect(work(owned(1, new int[] {7}, new String[] {"com.spoof.backgroundcontrol"}, new int[] {CONTROLLER_UID}),
            devices(7, 7), Arrays.asList(task(2, 7, 0, "com.example.mail", true))).items.isEmpty(),
            "8: owner package must be exactly the controller");
        // 9. Simultaneous swaps (the PR341 residual): Rose moves 7 from mail to maps, the other agent 11 from maps to
        // mail. Ownership is per device, so each task stays with its device's owner in every order.
        List<RoseAgentWork.Task> swapped = Arrays.asList(task(5, 7, 0, "com.example.maps", true),
            task(9, 11, 0, "com.example.mail", true));
        RoseAgentWork nine = work(both, devices(7, 7, 11, 11), swapped);
        expect(ids(nine.items).equals(Arrays.asList(5)) && nine.unattributed.isEmpty(),
            "9: the swap leaves Rose's card on Rose's device only");

        // Fresh boot (#344 producer): the registration snapshot carries the repository's generation, 0 until the first
        // device change. It is a valid, complete, empty snapshot, not malformed (old-fails on f0b21f6: "could not be
        // read (ownership)" before any session ever ran).
        RoseAgentWork.Ownership boot = new RoseAgentWork.Ownership();
        expect(boot.accept(0, new int[0], new String[0], new int[0]) && boot.snapshot().received
            && !boot.snapshot().malformed, "boot: generation 0 initial snapshot applied");
        expect(work(boot.snapshot(), devices(), Arrays.asList(task(1, 0, 0, "com.human.app", true))).state
            == RoseAgentWork.State.EMPTY, "boot: no session work is the truthful empty row");
        expect(boot.accept(1, new int[] {7}, new String[] {RoseAgentWork.CONTROLLER}, new int[] {CONTROLLER_UID})
            && !boot.accept(0, new int[0], new String[0], new int[0]), "boot: then only higher generations");

        // Malformed snapshots are never partly applied; ownership is dropped and the baseline kept.
        RoseAgentWork.Ownership strict = new RoseAgentWork.Ownership();
        strict.accept(3, new int[] {7}, new String[] {RoseAgentWork.CONTROLLER}, new int[] {CONTROLLER_UID});
        Object[][] malformed = {
            {3L + 1, null, new String[0], new int[0]},
            {3L + 1, new int[0], null, new int[0]},
            {3L + 1, new int[0], new String[0], null},
            {3L + 1, new int[] {7, 8}, new String[] {RoseAgentWork.CONTROLLER}, new int[] {1, 2}},
            {3L + 1, new int[] {7}, new String[] {RoseAgentWork.CONTROLLER}, new int[] {1, 2}},
            {3L + 1, new int[] {7, 7}, new String[] {RoseAgentWork.CONTROLLER, OTHER}, new int[] {CONTROLLER_UID, OTHER_UID}},
            {3L + 1, new int[] {7}, new String[] {null}, new int[] {CONTROLLER_UID}},
            {3L + 1, new int[] {7}, new String[] {""}, new int[] {CONTROLLER_UID}},
            {3L + 1, new int[] {7}, new String[] {RoseAgentWork.CONTROLLER}, new int[] {-1}},
            {-1L, new int[0], new String[0], new int[0]},
            {-4L, new int[0], new String[0], new int[0]}};
        for (Object[] m : malformed) {
            RoseAgentWork.Ownership o = new RoseAgentWork.Ownership();
            o.accept(3, new int[] {7}, new String[] {RoseAgentWork.CONTROLLER}, new int[] {CONTROLLER_UID});
            o.accept((Long) m[0], (int[]) m[1], (String[]) m[2], (int[]) m[3]);
            RoseAgentWork.Owners s = o.snapshot();
            RoseAgentWork row = work(s, devices(7, 7), Arrays.asList(task(2, 7, 0, "com.example.mail", true)));
            expect(s.malformed && !s.received && s.byDevice.isEmpty() && row.items.isEmpty()
                && "Rose's work could not be read (ownership)".equals(row.reason),
                "malformed snapshot rejected whole, ownership dropped, row names the step");
            expect(!o.accept(3, new int[] {7}, new String[] {RoseAgentWork.CONTROLLER}, new int[] {CONTROLLER_UID})
                && o.accept(4, new int[] {7}, new String[] {RoseAgentWork.CONTROLLER}, new int[] {CONTROLLER_UID})
                && o.snapshot().received && !o.snapshot().malformed,
                "the baseline did not advance: the old generation stays rejected, the next valid one restores");
        }
        expect(work(new RoseAgentWork.Ownership().snapshot(), devices(), Arrays.asList(task(1, 0, 0, "com.human.app", true)))
            .state == RoseAgentWork.State.EMPTY, "Not received, no session task: a truthful empty row");

        // Read-level fail-closed states.
        expect(derived(CONTROLLER_UID, 0, rose, devices(7, 7), Collections.emptyList(), tasks, false).items.isEmpty()
            && RoseAgentWork.UNCONFIRMED.equals(derived(CONTROLLER_UID, 0, rose, devices(7, 7),
                Collections.emptyList(), tasks, false).reason), "A possibly truncated task list: unconfirmed");
        expect(RoseAgentWork.unconfirmed().items.isEmpty() && RoseAgentWork.UNCONFIRMED.equals(RoseAgentWork.unconfirmed().reason),
            "A snapshot applied during a read gives no card");
        expect(derived(-1, 0, rose, devices(7, 7), Collections.emptyList(), tasks, true).state
            == RoseAgentWork.State.UNAVAILABLE, "No controller for this user is unavailable, not empty");
        expect(derived(CONTROLLER_UID, 0, null, devices(7, 7), Collections.emptyList(), tasks, true).state
            == RoseAgentWork.State.UNAVAILABLE
            && derived(CONTROLLER_UID, 0, rose, null, Collections.emptyList(), tasks, true).state
            == RoseAgentWork.State.UNAVAILABLE
            && derived(CONTROLLER_UID, 0, rose, devices(7, 7), null, tasks, true).state
            == RoseAgentWork.State.UNAVAILABLE
            && derived(CONTROLLER_UID, 0, rose, devices(7, 7), Collections.emptyList(), null, true).state
            == RoseAgentWork.State.UNAVAILABLE, "An unreadable platform state is unavailable");
        expect("Rose's work could not be read (tasks)".equals(RoseAgentWork.failed(RoseAgentWork.Step.TASKS).reason)
            && "Rose's work could not be read (controller)".equals(RoseAgentWork.failed(RoseAgentWork.Step.CONTROLLER).reason)
            && "Rose's work could not be read (displays)".equals(RoseAgentWork.failed(RoseAgentWork.Step.DISPLAYS).reason)
            && "Rose's work could not be read (ownership)".equals(RoseAgentWork.failed(RoseAgentWork.Step.OWNERSHIP).reason),
            "A failed platform read names its step with a fixed word");
        RoseAgentWork empty = work(roseOwns(), devices(7, 7), Arrays.asList(task(1, 0, 0, "com.human.app", true)));
        expect(empty.state == RoseAgentWork.State.EMPTY && "No background work".equals(empty.reason)
            && empty.linkLabel().isEmpty(), "No session task: the HiFi empty state");

        // Labels are looked up only after filtering; one failed or empty lookup never hides valid work.
        List<Integer> asked = new ArrayList<>();
        RoseAgentWork labelled = work.labelled(t -> {
            asked.add(t.taskId);
            if (t.taskId == 3) throw new IllegalStateException("private detail of a failed lookup");
            return t.packageName.equals("com.example.mail") ? "Mail" : null;
        });
        expect(asked.equals(Arrays.asList(2, 3)), "Only the kept agent tasks are looked up");
        expect(labelled.items.get(0).label.equals("Mail") && labelled.items.get(1).label.equals("com.example.maps"),
            "A failed lookup shows the package name; the others keep their label");
        expect(labelled.items.get(1).taskId == 3 && labelled.items.get(1).displayId == 7 && labelled.items.get(1).userId == 0
            && !labelled.items.get(1).topOfDisplay, "Every boundary fact is kept");
        expect(RoseAgentWork.decide(labelled.items.get(0), labelled) == RoseAgentWork.Open.WATCH, "Selection still works");
        expect(threeEarly.labelled(t -> "x") == threeEarly, "An unconfirmed row looks no label up");
        List<Integer> lookups = new ArrayList<>();
        expect(loading.labelled(t -> { lookups.add(t.taskId); return "x"; }) == loading && lookups.isEmpty(),
            "Unconfirmed work looks nothing up");

        // The HiFi link between the carousels counts only Rose's current screens.
        expect(work.working() == 1 && "1 task in progress".equals(work.linkLabel()), "Mail current, Maps behind");
        RoseAgentWork two = work(roseOwns(3), devices(7, 3, 8, 3), Arrays.asList(task(2, 7, 0, "com.example.mail", true),
            task(3, 8, 0, "com.example.maps", true), task(4, 8, 0, "com.example.chat", false)));
        expect(two.working() == 2 && "2 tasks in progress".equals(two.linkLabel()), "Two screens of Rose's session device");
        RoseAgentWork idle = work(rose, devices(7, 7), Arrays.asList(task(6, 7, 0, RoseAgentWork.CONTROLLER, true),
            task(3, 7, 0, "com.example.maps", false)));
        expect(idle.state == RoseAgentWork.State.AVAILABLE && idle.working() == 0 && "No active work".equals(idle.linkLabel()),
            "Background apps with the controller itself in front: shown, nothing claimed in progress");
        expect(RoseAgentWork.failed(RoseAgentWork.Step.TASKS).linkLabel().isEmpty(), "An unreadable state claims no work");
        // Live view (#156): only Rose's front cards are captured, never while locked; anything else says why.
        RoseAgentWork.Task front = task(2, 7, 0, "com.example.mail", true), behind = task(3, 7, 0, "com.example.maps", false);
        expect(RoseAgentWork.preview(front, false) == RoseAgentWork.Preview.CAPTURE
            && RoseAgentWork.preview(behind, false) == RoseAgentWork.Preview.BEHIND
            && RoseAgentWork.preview(front, true) == RoseAgentWork.Preview.LOCKED
            && RoseAgentWork.preview(behind, true) == RoseAgentWork.Preview.LOCKED, "Which cards may be captured");
        expect(RoseAgentWork.previewText(RoseAgentWork.Preview.CAPTURE, true) == null
            && RoseAgentWork.PREVIEW_NONE.equals(RoseAgentWork.previewText(RoseAgentWork.Preview.CAPTURE, false))
            && RoseAgentWork.PREVIEW_BEHIND.equals(RoseAgentWork.previewText(RoseAgentWork.Preview.BEHIND, false))
            && RoseAgentWork.PREVIEW_LOCKED.equals(RoseAgentWork.previewText(RoseAgentWork.Preview.LOCKED, true))
            && RoseAgentWork.PREVIEW_LOCKED.equals(RoseAgentWork.previewText(RoseAgentWork.Preview.LOCKED, false)),
            "A frame only when this read captured one; otherwise the honest reason, never an older frame");
        expect(work.hasLiveView(false) && !work.hasLiveView(true), "Rose's front card is captured only while unlocked");
        expect(!loading.hasLiveView(false) && !threeEarly.hasLiveView(false),
            "Unconfirmed tasks are never captured (only a complete row's items are)");
        expect(!work(rose, devices(7, 7), Arrays.asList(task(6, 7, 0, RoseAgentWork.CONTROLLER, true),
            task(3, 7, 0, "com.example.maps", false))).hasLiveView(false), "Nothing in front of Rose's screen to capture");

        // Lifecycle, one ledger, successive reads: membership is the platform's current state at each read, never history.
        RoseAgentWork.Ownership life = new RoseAgentWork.Ownership();
        life.accept(1, new int[] {7}, new String[] {RoseAgentWork.CONTROLLER}, new int[] {CONTROLLER_UID});
        RoseAgentWork launched = work(life.snapshot(), devices(7, 7), Arrays.asList(task(2, 7, 0, "com.example.mail", true)));
        expect(ids(launched.items).equals(Arrays.asList(2)) && launched.hasLiveView(false), "launch: the app appears, live");
        RoseAgentWork second = work(life.snapshot(), devices(7, 7), Arrays.asList(task(3, 7, 0, "com.example.maps", true),
            task(2, 7, 0, "com.example.mail", false)));
        expect(ids(second.items).equals(Arrays.asList(3, 2)) && RoseAgentWork.preview(second.items.get(1), false)
            == RoseAgentWork.Preview.BEHIND, "a second app on the same session device appears; the first is behind it");
        RoseAgentWork changed = work(life.snapshot(), devices(7, 7), Arrays.asList(task(3, 7, 0, "com.example.maps", true),
            task(2, 7, 0, "com.example.mail", false)));
        expect(ids(changed.items).equals(ids(second.items)), "content change: same members; the frame is captured afresh per read");
        RoseAgentWork closed = work(life.snapshot(), devices(7, 7), Arrays.asList(task(2, 7, 0, "com.example.mail", true)));
        expect(ids(closed.items).equals(Arrays.asList(2)), "close: the closed app is gone at once");
        life.accept(2, new int[0], new String[0], new int[0]);
        RoseAgentWork releasedControl = derived(CONTROLLER_UID, 0, life.snapshot(), devices(), Arrays.asList(7),
            Arrays.asList(task(2, 7, 0, "com.example.mail", true)), true);
        expect(releasedControl.items.isEmpty() && !releasedControl.hasLiveView(false),
            "control released (session closed, display released): no card and no capture, even if the task lingers");
        RoseAgentWork gone2 = derived(CONTROLLER_UID, 0, life.snapshot(), devices(), Arrays.asList(7),
            Collections.emptyList(), true);
        RoseAgentWork reentry = derived(CONTROLLER_UID, 0, life.snapshot(), devices(), Arrays.asList(7),
            Collections.emptyList(), true);
        expect(gone2.state == RoseAgentWork.State.EMPTY && reentry.state == RoseAgentWork.State.EMPTY && reentry.items.isEmpty(),
            "re-entry and recreation do not resurrect the old card: each read keeps nothing");
        life.reset();
        RoseAgentWork reconnected = work(life.snapshot(), devices(8, 8), Arrays.asList(task(4, 8, 0, "com.example.chat", true)));
        expect(reconnected.items.isEmpty() && RoseAgentWork.NOT_REPORTED.equals(reconnected.reason),
            "reconnect: not reported until the new registration's snapshot");
        life.accept(1, new int[] {8}, new String[] {RoseAgentWork.CONTROLLER}, new int[] {CONTROLLER_UID});
        expect(ids(work(life.snapshot(), devices(8, 8), Arrays.asList(task(4, 8, 0, "com.example.chat", true))).items)
            .equals(Arrays.asList(4)), "after reconnect only the new session's app, never the old one");
        // #156 parity: membership is the platform's per-device app list (onAutomatedDeviceAppsChanged), front or hidden,
        // never task history. One ledger fed exactly as one dispatch delivers: devices, then apps, same generation.
        String C = RoseAgentWork.CONTROLLER;
        RoseAgentWork.Ownership par = new RoseAgentWork.Ownership();
        par.accept(4, new int[] {7}, new String[] {C}, new int[] {CONTROLLER_UID});
        expect(RoseAgentWork.NOT_REPORTED.equals(RoseAgentWork.of(CONTROLLER_UID, 0, par.snapshot(), devices(7, 7),
            Collections.emptyList(), Arrays.asList(task(2, 7, 0, "com.example.mail", true)), true).reason),
            "devices without their apps yet: not reported, never a guessed card");
        par.acceptApps(4, new int[] {7}, new String[] {C}, new int[] {CONTROLLER_UID}, new boolean[] {true},
            new int[] {7, 7, 7}, new String[] {"com.example.mail", "com.example.maps", "com.example.notes"});
        List<RoseAgentWork.Task> parTasks = Arrays.asList(task(2, 7, 0, "com.example.mail", true),
            task(3, 7, 0, "com.example.maps", false), task(8, 7, 0, "com.example.maps", false));
        RoseAgentWork parity = RoseAgentWork.of(CONTROLLER_UID, 0, par.snapshot(), devices(7, 7), Collections.emptyList(),
            parTasks, true);
        expect(ids(parity.items).equals(Arrays.asList(2, 3, RoseAgentWork.NO_TASK)) && parity.reason == null,
            "one card per member app: mail (front), maps (its front-most task only), notes (hidden in another task)");
        RoseAgentWork.Task notes = parity.items.get(2);
        expect(notes.packageName.equals("com.example.notes") && notes.displayId == 7 && !notes.topOfDisplay
            && RoseAgentWork.preview(notes, false) == RoseAgentWork.Preview.BEHIND
            && RoseAgentWork.decide(notes, parity) == RoseAgentWork.Open.NOT_CURRENT,
            "a member without its own task: shown, never captured, never opened as Rose's current screen");
        expect(parity.working() == 1 && "1 task in progress".equals(parity.linkLabel()), "only the front app is in progress");
        // A task on Rose's device whose app is not (yet) in the platform's list is in flight: held, not a card.
        RoseAgentWork inFlightApp = RoseAgentWork.of(CONTROLLER_UID, 0, par.snapshot(), devices(7, 7), Collections.emptyList(),
            Arrays.asList(task(9, 7, 0, "com.example.chat", true), task(2, 7, 0, "com.example.mail", false)), true);
        expect(inFlightApp.items.isEmpty() && ids(inFlightApp.unattributed).equals(Arrays.asList(9, 2, -1, -1))
            && RoseAgentWork.UNCONFIRMED.equals(inFlightApp.reason),
            "an unlisted running task makes the whole row unconfirmed (common contract): no partial cards");
        // A read between the two callbacks of one dispatch (devices at 5, apps still at 4) is not one state.
        par.accept(5, new int[] {7}, new String[] {C}, new int[] {CONTROLLER_UID});
        RoseAgentWork between = RoseAgentWork.of(CONTROLLER_UID, 0, par.snapshot(), devices(7, 7), Collections.emptyList(),
            parTasks, true);
        expect(between.items.isEmpty() && RoseAgentWork.UNCONFIRMED.equals(between.reason), "mid-dispatch read: unconfirmed");
        // Launch: the next dispatch lists a new app; a finished logical task removes nothing; close removes it.
        par.acceptApps(5, new int[] {7}, new String[] {C}, new int[] {CONTROLLER_UID}, new boolean[] {true},
            new int[] {7, 7}, new String[] {"com.example.mail", "com.example.chat"});
        RoseAgentWork launchedApp = RoseAgentWork.of(CONTROLLER_UID, 0, par.snapshot(), devices(7, 7), Collections.emptyList(),
            Arrays.asList(task(9, 7, 0, "com.example.chat", true), task(2, 7, 0, "com.example.mail", false)), true);
        expect(ids(launchedApp.items).equals(Arrays.asList(9, 2)) && launchedApp.reason == null,
            "launch: the platform's list and the task agree; maps and notes left the list and are gone");
        par.accept(6, new int[] {7}, new String[] {C}, new int[] {CONTROLLER_UID});
        par.acceptApps(6, new int[] {7}, new String[] {C}, new int[] {CONTROLLER_UID}, new boolean[] {true},
            new int[] {7}, new String[] {"com.example.mail"});
        RoseAgentWork closedApp = RoseAgentWork.of(CONTROLLER_UID, 0, par.snapshot(), devices(7, 7), Collections.emptyList(),
            Arrays.asList(task(2, 7, 0, "com.example.mail", true)), true);
        expect(ids(closedApp.items).equals(Arrays.asList(2)), "close: chat's activities finished, so it is gone");
        RoseAgentWork stillRunning = RoseAgentWork.of(CONTROLLER_UID, 0, par.snapshot(), devices(7, 7),
            Collections.emptyList(), Arrays.asList(task(2, 7, 0, "com.example.mail", true)), true);
        expect(ids(stillRunning.items).equals(Arrays.asList(2)),
            "a completed logical task with mail still running keeps mail: membership is activities, not task status");
        // Another user's activity on Rose's device (deviceComplete false): the listed apps show, the row says some is not.
        par.accept(7, new int[] {7}, new String[] {C}, new int[] {CONTROLLER_UID});
        par.acceptApps(7, new int[] {7}, new String[] {C}, new int[] {CONTROLLER_UID}, new boolean[] {false},
            new int[] {7}, new String[] {"com.example.mail"});
        RoseAgentWork partly2 = RoseAgentWork.of(CONTROLLER_UID, 0, par.snapshot(), devices(7, 7), Collections.emptyList(),
            Arrays.asList(task(2, 7, 0, "com.example.mail", true)), true);
        expect(partly2.items.isEmpty() && partly2.state == RoseAgentWork.State.UNAVAILABLE
            && RoseAgentWork.UNCONFIRMED.equals(partly2.reason) && ids(partly2.unattributed).equals(Arrays.asList(2))
            && RoseAgentWork.decide(task(2, 7, 0, "com.example.mail", true), partly2) == RoseAgentWork.Open.UNATTRIBUTED,
            "incomplete device (common contract): no app cards, cannot-confirm status, selection refused");
        par.accept(8, new int[] {7}, new String[] {C}, new int[] {CONTROLLER_UID});
        par.acceptApps(8, new int[] {7}, new String[] {C}, new int[] {CONTROLLER_UID}, new boolean[] {false},
            new int[0], new String[0]);
        RoseAgentWork onlyOther = RoseAgentWork.of(CONTROLLER_UID, 0, par.snapshot(), devices(7, 7), Collections.emptyList(),
            Collections.emptyList(), true);
        expect(onlyOther.state == RoseAgentWork.State.UNAVAILABLE && RoseAgentWork.UNCONFIRMED.equals(onlyOther.reason),
            "only another user's activity on Rose's device: unavailable, not \"No background work\"");
        // Malformed per-device apps are never partly applied; the row names the step and the baseline is kept.
        Object[][] badApps = {
            {new boolean[] {true, true}, new int[0], new String[0]},
            {null, new int[0], new String[0]},
            {new boolean[] {true}, new int[] {7}, new String[0]},
            {new boolean[] {true}, new int[] {11}, new String[] {"com.example.mail"}},
            {new boolean[] {true}, new int[] {7, 7}, new String[] {"com.example.mail", "com.example.mail"}},
            {new boolean[] {true}, new int[] {7}, new String[] {null}},
            {new boolean[] {true}, new int[] {7}, new String[] {""}},
            {new boolean[] {true}, null, new String[0]}};
        for (Object[] bad : badApps) {
            RoseAgentWork.Ownership o = new RoseAgentWork.Ownership();
            o.accept(3, new int[] {7}, new String[] {C}, new int[] {CONTROLLER_UID});
            o.acceptApps(3, new int[] {7}, new String[] {C}, new int[] {CONTROLLER_UID}, new boolean[] {true},
                new int[] {7}, new String[] {"com.example.mail"});
            o.acceptApps(4, new int[] {7}, new String[] {C}, new int[] {CONTROLLER_UID}, (boolean[]) bad[0], (int[]) bad[1],
                (String[]) bad[2]);
            RoseAgentWork row = RoseAgentWork.of(CONTROLLER_UID, 0, o.snapshot(), devices(7, 7), Collections.emptyList(),
                Arrays.asList(task(2, 7, 0, "com.example.mail", true)), true);
            expect(o.snapshot().appsMalformed && row.items.isEmpty()
                && "Rose's work could not be read (ownership)".equals(row.reason), "malformed apps rejected whole");
            expect(!o.acceptApps(3, new int[] {7}, new String[] {C}, new int[] {CONTROLLER_UID}, new boolean[] {true},
                new int[] {7}, new String[] {"com.example.mail"}) && o.acceptApps(4, new int[] {7}, new String[] {C},
                new int[] {CONTROLLER_UID}, new boolean[] {true}, new int[] {7}, new String[] {"com.example.mail"}),
                "the apps baseline did not advance: the old generation stays rejected, the next valid one restores");
        }
        // Two members without tasks: refusing one never names the other.
        RoseAgentWork.Ownership twoDev = new RoseAgentWork.Ownership();
        twoDev.accept(1, new int[] {7, 8}, new String[] {C, C}, new int[] {CONTROLLER_UID, CONTROLLER_UID});
        twoDev.acceptApps(1, new int[] {7, 8}, new String[] {C, C}, new int[] {CONTROLLER_UID, CONTROLLER_UID},
            new boolean[] {true, true}, new int[] {7, 8}, new String[] {"com.example.notes", "com.example.docs"});
        RoseAgentWork placed = RoseAgentWork.of(CONTROLLER_UID, 0, twoDev.snapshot(), devices(7, 7), Collections.emptyList(),
            Collections.emptyList(), true);
        RoseAgentWork.Task notesCard = new RoseAgentWork.Task(RoseAgentWork.NO_TASK, 7, 0, "com.example.notes", "Notes", false);
        expect(placed.items.isEmpty() && RoseAgentWork.UNCONFIRMED.equals(placed.reason) && placed.unattributed.size() == 2
            && RoseAgentWork.decide(notesCard, placed) == RoseAgentWork.Open.UNATTRIBUTED,
            "two Rose session devices (not one controller session) and an unplaceable member: no cards, refused");
        expect(RoseAgentWork.decide(new RoseAgentWork.Task(RoseAgentWork.NO_TASK, 0, 0, "com.example.docs", "Docs", false),
            placed) == RoseAgentWork.Open.UNATTRIBUTED, "and the unconfirmed one is refused as unconfirmed");

        // The common #156/#225 rendering contract (Home, App Core, OS Product): complete is shown, complete-empty is
        // genuinely empty, anything else shows no app cards with an explicit status.
        RoseAgentWork.Ownership pair = new RoseAgentWork.Ownership();
        pair.accept(1, new int[] {7, 8}, new String[] {C, C}, new int[] {CONTROLLER_UID, CONTROLLER_UID});
        pair.acceptApps(1, new int[] {7, 8}, new String[] {C, C}, new int[] {CONTROLLER_UID, CONTROLLER_UID},
            new boolean[] {true, true}, new int[] {7, 8}, new String[] {"com.example.mail", "com.example.maps"});
        RoseAgentWork twoSessionsRow = RoseAgentWork.of(CONTROLLER_UID, 0, pair.snapshot(), devices(7, 7, 8, 8),
            Collections.emptyList(), Arrays.asList(task(2, 7, 0, "com.example.mail", true), task(5, 8, 0, "com.example.maps", true)), true);
        expect(twoSessionsRow.items.isEmpty() && RoseAgentWork.UNCONFIRMED.equals(twoSessionsRow.reason),
            "two Rose session devices, each complete: App Core's single-session transport cannot describe them, so neither "
            + "surface shows cards (no surface shows more or fewer apps than the other)");
        RoseAgentWork lockedRow = RoseAgentWork.locked();
        expect(lockedRow.items.isEmpty() && lockedRow.state == RoseAgentWork.State.UNAVAILABLE
            && RoseAgentWork.LOCKED.equals(lockedRow.reason) && !lockedRow.hasLiveView(false), "locked: no app cards, said so");
        expect(RoseAgentWork.EMPTY.equals(work(roseOwns(), devices(), Collections.emptyList()).reason),
            "complete and empty: genuinely empty");

        par.reset();
        expect(!par.snapshot().received && !par.snapshot().appsReceived && par.snapshot().appsByDevice.isEmpty(),
            "reconnect clears devices and apps");

        System.out.println("RoseAgentWorkTest: " + checks + " checks passed");
    }
}
