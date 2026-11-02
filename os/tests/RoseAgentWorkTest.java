package com.android.quickstep.fallback;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * RoseAgentWork against the common #156/#225 contract (agreed with App Core and Home): membership and completeness
 * come only from the platform's atomic apps snapshot; tasks attach a member's live view and selection only.
 */
public final class RoseAgentWorkTest {
    static int checks;
    static final int UID = 10123, OTHER_UID = 10456;
    static final String C = RoseAgentWork.CONTROLLER, OTHER = "com.other.agent";

    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static RoseAgentWork.Task task(int id, int display, int user, String pkg, boolean top) {
        return new RoseAgentWork.Task(id, display, user, pkg, pkg + " label", top);
    }
    static Map<Integer, Integer> devices(int... displayDevicePairs) {
        Map<Integer, Integer> out = new HashMap<>();
        for (int i = 0; i < displayDevicePairs.length; i += 2) out.put(displayDevicePairs[i], displayDevicePairs[i + 1]);
        return out;
    }
    static List<Integer> ids(List<RoseAgentWork.Task> tasks) {
        List<Integer> out = new ArrayList<>();
        for (RoseAgentWork.Task t : tasks) out.add(t.taskId);
        return out;
    }
    static List<String> pkgs(List<RoseAgentWork.Task> tasks) {
        List<String> out = new ArrayList<>();
        for (RoseAgentWork.Task t : tasks) out.add(t.packageName);
        return out;
    }
    /** One dispatch as the platform sends it: devices, then the same generation's apps. */
    static void dispatch(RoseAgentWork.Ownership o, long gen, int[] ids, String[] owners, int[] uids, boolean[] complete,
            int[] appIds, String[] apps) {
        o.accept(gen, ids, owners, uids);
        o.acceptApps(gen, ids, owners, uids, complete, appIds, apps);
    }
    /** A ledger holding one controller device (device 7) running these apps, complete. */
    static RoseAgentWork.Owners rose(String... apps) {
        RoseAgentWork.Ownership o = new RoseAgentWork.Ownership();
        int[] appIds = new int[apps.length];
        Arrays.fill(appIds, 7);
        dispatch(o, 1, new int[] {7}, new String[] {C}, new int[] {UID}, new boolean[] {true}, appIds, apps);
        return o.snapshot();
    }
    static RoseAgentWork row(RoseAgentWork.Owners owners, List<RoseAgentWork.Task> tasks) {
        return RoseAgentWork.of(UID, 0, owners, devices(70, 7), tasks);
    }

    public static void main(String[] args) {
        // Device 7 (display 70) is Rose's session device: the controller owns it with this user's uid.
        RoseAgentWork.Owners mailMaps = rose("com.example.mail", "com.example.maps", C);
        List<RoseAgentWork.Task> tasks = Arrays.asList(
            task(1, 0, 0, "com.human.app", true),          // the human's own task: never an agent card
            task(2, 70, 0, "com.example.mail", true),      // Rose's current screen
            task(3, 70, 0, "com.example.maps", false),     // behind it on Rose's display
            task(8, 70, 0, "com.example.maps", false),     // a second maps task: one card per app
            task(4, 90, 0, "com.other.cast", true),        // another app's display: not a session device
            task(5, 70, 10, "com.example.mail", true),     // another user's task: never attached
            task(6, 70, 0, C, false));                     // the controller itself
        RoseAgentWork work = row(mailMaps, tasks);
        expect(work.state == RoseAgentWork.State.AVAILABLE && ids(work.items).equals(Arrays.asList(2, 3))
            && work.reason == null, "the snapshot's members, each with its front-most task, controller excluded");

        // Selection is decided against a fresh read taken after the tap.
        RoseAgentWork.Task mail = work.items.get(0), maps = work.items.get(1);
        expect(RoseAgentWork.decide(mail, work) == RoseAgentWork.Open.WATCH, "the current agent screen sends a request");
        expect(RoseAgentWork.decide(maps, work) == RoseAgentWork.Open.NOT_CURRENT, "a task behind it never opens");
        RoseAgentWork swapped = row(mailMaps, Arrays.asList(task(3, 70, 0, "com.example.maps", true),
            task(2, 70, 0, "com.example.mail", false)));
        expect(RoseAgentWork.decide(mail, swapped) == RoseAgentWork.Open.NOT_CURRENT
            && RoseAgentWork.decide(maps, swapped) == RoseAgentWork.Open.WATCH, "current at row time, not at tap time");
        RoseAgentWork replaced = row(rose("com.example.chat"), Arrays.asList(task(2, 70, 0, "com.example.chat", true)));
        expect(RoseAgentWork.decide(mail, replaced) == RoseAgentWork.Open.CHANGED, "same task id, another app: changed");
        RoseAgentWork gone = row(rose("com.example.maps"), Arrays.asList(task(3, 70, 0, "com.example.maps", true)));
        expect(RoseAgentWork.decide(mail, gone) == RoseAgentWork.Open.GONE, "a closed app is gone");
        expect(RoseAgentWork.decide(mail, RoseAgentWork.unconfirmed()) == RoseAgentWork.Open.UNATTRIBUTED
            && RoseAgentWork.decide(mail, RoseAgentWork.locked()) == RoseAgentWork.Open.UNATTRIBUTED
            && RoseAgentWork.decide(mail, RoseAgentWork.failed(RoseAgentWork.Step.OWNERSHIP)) == RoseAgentWork.Open.UNATTRIBUTED,
            "a fresh read that cannot confirm Rose's work never opens anything, and says so");
        expect(RoseAgentWork.decide(mail, row(rose(), Collections.emptyList())) == RoseAgentWork.Open.GONE
            && RoseAgentWork.decide(mail, null) == RoseAgentWork.Open.GONE, "confirmed empty or no read: gone");
        expect(RoseAgentWork.describe(RoseAgentWork.Open.NOT_CURRENT, "Maps").equals("Maps is not on Rose's screen right now")
            && RoseAgentWork.describe(RoseAgentWork.Open.CHANGED, "Mail").equals("Mail changed; pick it again")
            && RoseAgentWork.describe(RoseAgentWork.Open.GONE, "Maps").equals("Maps is no longer running")
            && RoseAgentWork.describe(RoseAgentWork.Open.UNATTRIBUTED, "Mail").equals("Mail can't be confirmed as Rose's work")
            && RoseAgentWork.describe(RoseAgentWork.Open.WATCH, "Mail") == null, "selection notices");
        expect(RoseAgentWork.EXTRA_SELECT_DISPLAY.equals("dev.pixel.backgroundcontrol.extra.SELECT_DISPLAY_ID")
            && RoseAgentWork.EXTRA_SELECT_PACKAGE.equals("dev.pixel.backgroundcontrol.extra.SELECT_PACKAGE"), "request names");

        // The contract's classes.
        RoseAgentWork.Ownership none = new RoseAgentWork.Ownership();
        dispatch(none, 0, new int[0], new String[0], new int[0], new boolean[0], new int[0], new String[0]);
        expect(row(none.snapshot(), tasks).state == RoseAgentWork.State.EMPTY
            && RoseAgentWork.EMPTY.equals(row(none.snapshot(), tasks).reason) && row(none.snapshot(), tasks).linkLabel().isEmpty(),
            "no controller device (generation 0 at boot is valid): confirmed empty, whatever tasks exist");
        expect(row(rose(C), tasks).state == RoseAgentWork.State.EMPTY, "only the controller itself on its device: empty");
        expect(row(rose(), tasks).state == RoseAgentWork.State.EMPTY, "the open session's apps all left: empty");
        RoseAgentWork.Ownership two = new RoseAgentWork.Ownership();
        dispatch(two, 1, new int[] {7, 8}, new String[] {C, C}, new int[] {UID, UID}, new boolean[] {true, true},
            new int[] {7, 8}, new String[] {"com.example.mail", "com.example.maps"});
        RoseAgentWork twoRow = RoseAgentWork.of(UID, 0, two.snapshot(), devices(70, 7, 80, 8), tasks);
        expect(twoRow.state == RoseAgentWork.State.AVAILABLE && twoRow.reason == null
            && pkgs(twoRow.items).equals(Arrays.asList("com.example.mail", "com.example.maps")),
            "two complete controller devices: the apps of both, bound to a session or not");
        RoseAgentWork.Ownership partial = new RoseAgentWork.Ownership();
        dispatch(partial, 1, new int[] {7}, new String[] {C}, new int[] {UID}, new boolean[] {false}, new int[] {7},
            new String[] {"com.example.mail"});
        expect(row(partial.snapshot(), tasks).items.isEmpty() && RoseAgentWork.UNCONFIRMED.equals(row(partial.snapshot(), tasks).reason),
            "another user's activity omitted (deviceComplete false): cannot confirm");
        RoseAgentWork.Ownership others = new RoseAgentWork.Ownership();
        dispatch(others, 1, new int[] {7, 9}, new String[] {C, OTHER}, new int[] {UID, OTHER_UID},
            new boolean[] {true, false}, new int[] {7, 9}, new String[] {"com.example.mail", "com.example.chat"});
        RoseAgentWork othersRow = RoseAgentWork.of(UID, 0, others.snapshot(), devices(70, 7, 90, 9), tasks);
        expect(pkgs(othersRow.items).equals(Arrays.asList("com.example.mail")) && othersRow.reason == null,
            "another owner's device, even incomplete, is not Rose's scope");
        RoseAgentWork.Ownership otherUser = new RoseAgentWork.Ownership();
        dispatch(otherUser, 1, new int[] {7}, new String[] {C}, new int[] {1010123}, new boolean[] {true}, new int[] {7},
            new String[] {"com.example.mail"});
        expect(row(otherUser.snapshot(), tasks).state == RoseAgentWork.State.EMPTY, "the controller of another user is not this user's");
        RoseAgentWork.Ownership spoof = new RoseAgentWork.Ownership();
        dispatch(spoof, 1, new int[] {7}, new String[] {"com.spoof.backgroundcontrol"}, new int[] {UID}, new boolean[] {true},
            new int[] {7}, new String[] {"com.example.mail"});
        expect(row(spoof.snapshot(), tasks).state == RoseAgentWork.State.EMPTY, "only exactly the controller package");
        RoseAgentWork notYet = row(new RoseAgentWork.Ownership().snapshot(), tasks);
        expect(notYet.items.isEmpty() && RoseAgentWork.NOT_REPORTED.equals(notYet.reason), "before the first snapshot: not reported");
        RoseAgentWork.Ownership devicesOnly = new RoseAgentWork.Ownership();
        devicesOnly.accept(1, new int[] {7}, new String[] {C}, new int[] {UID});
        expect(RoseAgentWork.NOT_REPORTED.equals(row(devicesOnly.snapshot(), tasks).reason),
            "devices without the dispatch's apps snapshot: not reported (only the apps snapshot decides)");
        expect(RoseAgentWork.locked().items.isEmpty() && RoseAgentWork.LOCKED.equals(RoseAgentWork.locked().reason)
            && !RoseAgentWork.locked().hasLiveView(false), "locked: no app cards, nothing captured");
        expect(RoseAgentWork.unconfirmed().items.isEmpty() && RoseAgentWork.UNCONFIRMED.equals(RoseAgentWork.unconfirmed().reason),
            "a snapshot applied during a read: cannot confirm that read");

        // Two apps on two of Rose's devices (snapshot-only: every complete owned device counts).
        // Device 7 (display 70) runs mail, device 8 (displays 80, 81) runs maps; device 9 is another owner's.
        RoseAgentWork.Ownership pair = new RoseAgentWork.Ownership();
        dispatch(pair, 3, new int[] {8, 7, 9}, new String[] {C, C, OTHER}, new int[] {UID, UID, OTHER_UID},
            new boolean[] {true, true, true}, new int[] {8, 7, 7, 8, 9},
            new String[] {"com.example.maps", "com.example.mail", C, C, "com.example.maps"});
        Map<Integer, Integer> pairDisplays = devices(70, 7, 80, 8, 81, 8, 90, 9);
        List<RoseAgentWork.Task> pairTasks = Arrays.asList(
            task(21, 80, 0, "com.example.maps", true),     // maps: device 8's current screen
            task(22, 70, 0, "com.example.maps", false),    // a maps task on device 7: not device 8's member's task
            task(23, 70, 0, "com.example.mail", true),     // mail: device 7's current screen
            task(24, 90, 0, "com.example.maps", true),     // another owner's device: never attached
            task(25, 81, 10, "com.example.maps", true));   // another user's task on device 8: never attached
        RoseAgentWork pairRow = RoseAgentWork.of(UID, 0, pair.snapshot(), pairDisplays, pairTasks);
        expect(pairRow.state == RoseAgentWork.State.AVAILABLE && pairRow.reason == null
            && pkgs(pairRow.items).equals(Arrays.asList("com.example.mail", "com.example.maps"))
            && ids(pairRow.items).equals(Arrays.asList(23, 21)),
            "two devices: each member with its own device's front task, in device order; the controller on both is no "
            + "duplicate, and another owner's device running the same app is not Rose's scope");
        expect(pairRow.working() == 2 && "2 tasks in progress".equals(pairRow.linkLabel())
            && pairRow.hasLiveView(false) && !pairRow.hasLiveView(true)
            && RoseAgentWork.preview(pairRow.items.get(0), false) == RoseAgentWork.Preview.CAPTURE
            && RoseAgentWork.preview(pairRow.items.get(1), false) == RoseAgentWork.Preview.CAPTURE,
            "each device's front task is its own live view, captured only unlocked");
        expect(RoseAgentWork.decide(pairRow.items.get(1), pairRow) == RoseAgentWork.Open.WATCH
            && pairRow.items.get(1).displayId == 80,
            "selection stays per device: the request names the member's own display (the controller binds or refuses)");
        RoseAgentWork pairHidden = RoseAgentWork.of(UID, 0, pair.snapshot(), pairDisplays,
            Arrays.asList(task(22, 70, 0, "com.example.maps", true), task(23, 70, 0, "com.example.mail", false)));
        RoseAgentWork.Task mapsNoTask = pairHidden.items.get(1);
        expect(pkgs(pairHidden.items).equals(Arrays.asList("com.example.mail", "com.example.maps"))
            && mapsNoTask.taskId == RoseAgentWork.NO_TASK && mapsNoTask.displayId == 80
            && RoseAgentWork.preview(mapsNoTask, false) == RoseAgentWork.Preview.NONE
            && RoseAgentWork.preview(pairHidden.items.get(0), false) == RoseAgentWork.Preview.BEHIND,
            "no task is reused across devices: maps has none on its own device, so 'Preview unavailable', never live");
        RoseAgentWork.Ownership dup = new RoseAgentWork.Ownership();
        dispatch(dup, 1, new int[] {7, 8}, new String[] {C, C}, new int[] {UID, UID}, new boolean[] {true, true},
            new int[] {7, 8, 8}, new String[] {"com.example.maps", "com.example.maps", "com.example.mail"});
        RoseAgentWork dupRow = RoseAgentWork.of(UID, 0, dup.snapshot(), devices(70, 7, 80, 8), pairTasks);
        expect(dupRow.state == RoseAgentWork.State.UNAVAILABLE && dupRow.items.isEmpty()
            && RoseAgentWork.DUPLICATE.equals(dupRow.reason) && !dupRow.hasLiveView(false)
            && RoseAgentWork.decide(pairRow.items.get(0), dupRow) == RoseAgentWork.Open.UNATTRIBUTED,
            "one app on two devices: cannot confirm, no cards at all, nothing captured or opened");
        RoseAgentWork.Ownership onePartial = new RoseAgentWork.Ownership();
        dispatch(onePartial, 1, new int[] {7, 8}, new String[] {C, C}, new int[] {UID, UID}, new boolean[] {true, false},
            new int[] {7, 8}, new String[] {"com.example.mail", "com.example.maps"});
        RoseAgentWork onePartialRow = RoseAgentWork.of(UID, 0, onePartial.snapshot(), devices(70, 7, 80, 8), pairTasks);
        expect(onePartialRow.items.isEmpty() && RoseAgentWork.UNCONFIRMED.equals(onePartialRow.reason),
            "one of two devices incomplete: cannot confirm, never the complete device's apps alone");
        RoseAgentWork.Ownership controllerOnly = new RoseAgentWork.Ownership();
        dispatch(controllerOnly, 1, new int[] {7, 8}, new String[] {C, C}, new int[] {UID, UID},
            new boolean[] {true, true}, new int[] {7, 8}, new String[] {C, C});
        expect(RoseAgentWork.of(UID, 0, controllerOnly.snapshot(), devices(70, 7, 80, 8), tasks).state
            == RoseAgentWork.State.EMPTY, "two devices running only the controller: confirmed empty");
        RoseAgentWork.Ownership mixed = new RoseAgentWork.Ownership();
        dispatch(mixed, 1, new int[] {7, 8}, new String[] {C, C}, new int[] {UID, UID}, new boolean[] {true, true},
            new int[] {7, 8}, new String[] {C, "com.example.maps"});
        expect(pkgs(RoseAgentWork.of(UID, 0, mixed.snapshot(), devices(70, 7, 80, 8), tasks).items)
            .equals(Arrays.asList("com.example.maps")), "a controller-only device next to one running maps: maps only");
        RoseAgentWork.Ownership mineAndOtherUser = new RoseAgentWork.Ownership();
        dispatch(mineAndOtherUser, 1, new int[] {7, 8}, new String[] {C, C}, new int[] {UID, 1010123},
            new boolean[] {true, false}, new int[] {7, 8}, new String[] {"com.example.mail", "com.example.mail"});
        expect(pkgs(RoseAgentWork.of(UID, 0, mineAndOtherUser.snapshot(), devices(70, 7, 80, 8), tasks).items)
            .equals(Arrays.asList("com.example.mail")),
            "another user's controller device is not this user's scope: neither a duplicate nor incomplete here");
        expect(RoseAgentWork.decide(pairRow.items.get(0), RoseAgentWork.locked()) == RoseAgentWork.Open.UNATTRIBUTED,
            "locked with two devices: no cards, nothing opened");

        // Tasks never add, remove or hold back a member.
        RoseAgentWork unlisted = row(rose("com.example.mail"), Arrays.asList(task(9, 70, 0, "com.example.chat", true),
            task(2, 70, 0, "com.example.mail", false)));
        expect(pkgs(unlisted.items).equals(Arrays.asList("com.example.mail")) && unlisted.reason == null
            && ids(unlisted.items).equals(Arrays.asList(2)),
            "a running task whose app the snapshot does not list is not a member and holds nothing back");
        RoseAgentWork hidden = row(rose("com.example.mail", "com.example.notes"), Arrays.asList(task(2, 70, 0, "com.example.mail", true)));
        RoseAgentWork.Task notes = hidden.items.get(1);
        expect(pkgs(hidden.items).equals(Arrays.asList("com.example.mail", "com.example.notes"))
            && notes.taskId == RoseAgentWork.NO_TASK && notes.displayId == 70,
            "a member with no visible task is still a member (front or hidden, as the platform lists it)");
        expect(RoseAgentWork.preview(notes, false) == RoseAgentWork.Preview.NONE
            && RoseAgentWork.PREVIEW_NONE.equals(RoseAgentWork.previewText(RoseAgentWork.Preview.NONE, false))
            && !RoseAgentWork.PREVIEW_BEHIND.equals(RoseAgentWork.previewText(RoseAgentWork.preview(notes, false), false)),
            "no visible task: 'Preview unavailable', never a claimed reason such as 'Behind another app'");
        expect(RoseAgentWork.decide(notes, hidden) == RoseAgentWork.Open.NOT_CURRENT, "and it is never opened");
        RoseAgentWork noDisplay = RoseAgentWork.of(UID, 0, rose("com.example.mail"), devices(), Collections.emptyList());
        expect(pkgs(noDisplay.items).equals(Arrays.asList("com.example.mail")) && noDisplay.items.get(0).displayId == 0,
            "the device's display not listed (released): the member is still the platform's, with no preview");
        expect(row(rose("com.example.mail"), Collections.emptyList()).items.size() == 1, "no task at all: the member stays");
        RoseAgentWork twoDisplays = RoseAgentWork.of(UID, 0, rose("com.example.mail", "com.example.maps"), devices(70, 7, 71, 7),
            Arrays.asList(task(2, 70, 0, "com.example.mail", true), task(5, 71, 0, "com.example.maps", true)));
        expect(twoDisplays.working() == 2 && "2 tasks in progress".equals(twoDisplays.linkLabel()),
            "two displays of Rose's one device, two in progress");

        // Live view: only Rose's front cards with a visible task, never while locked.
        expect(work.hasLiveView(false) && !work.hasLiveView(true) && !hidden.items.isEmpty(), "front card captured only unlocked");
        expect(RoseAgentWork.preview(mail, false) == RoseAgentWork.Preview.CAPTURE
            && RoseAgentWork.preview(maps, false) == RoseAgentWork.Preview.BEHIND
            && RoseAgentWork.preview(mail, true) == RoseAgentWork.Preview.LOCKED, "which cards may be captured");
        expect(RoseAgentWork.previewText(RoseAgentWork.Preview.CAPTURE, true) == null
            && RoseAgentWork.PREVIEW_NONE.equals(RoseAgentWork.previewText(RoseAgentWork.Preview.CAPTURE, false))
            && RoseAgentWork.PREVIEW_BEHIND.equals(RoseAgentWork.previewText(RoseAgentWork.Preview.BEHIND, false))
            && RoseAgentWork.PREVIEW_LOCKED.equals(RoseAgentWork.previewText(RoseAgentWork.Preview.LOCKED, true)),
            "a frame only when this read captured one; otherwise the honest reason");
        expect(!row(rose("com.example.notes"), Collections.emptyList()).hasLiveView(false), "no visible task: nothing to capture");

        // Ledger: generation and malformed rules for both callbacks.
        RoseAgentWork.Ownership ledger = new RoseAgentWork.Ownership();
        expect(!ledger.snapshot().received && !ledger.snapshot().appsReceived, "nothing before the first snapshot");
        dispatch(ledger, 5, new int[] {7}, new String[] {C}, new int[] {UID}, new boolean[] {true}, new int[] {7},
            new String[] {"com.example.mail"});
        expect(!ledger.acceptApps(5, new int[] {7}, new String[] {C}, new int[] {UID}, new boolean[] {true}, new int[0],
            new String[0]) && !ledger.acceptApps(4, new int[0], new String[0], new int[0], new boolean[0], new int[0],
            new String[0]), "an equal or lower generation is ignored");
        expect(pkgs(row(ledger.snapshot(), Collections.emptyList()).items).equals(Arrays.asList("com.example.mail")),
            "the ignored ones changed nothing");
        dispatch(ledger, 6, new int[0], new String[0], new int[0], new boolean[0], new int[0], new String[0]);
        expect(row(ledger.snapshot(), tasks).state == RoseAgentWork.State.EMPTY, "a higher generation replaces it whole");
        ledger.reset();
        expect(RoseAgentWork.NOT_REPORTED.equals(row(ledger.snapshot(), tasks).reason), "reconnect: not reported");
        dispatch(ledger, 1, new int[] {8}, new String[] {C}, new int[] {UID}, new boolean[] {true}, new int[] {8},
            new String[] {"com.example.chat"});
        expect(pkgs(RoseAgentWork.of(UID, 0, ledger.snapshot(), devices(80, 8), Collections.emptyList()).items)
            .equals(Arrays.asList("com.example.chat")), "the new registration's first snapshot, lower generation, restores");
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
            dispatch(o, 3, new int[] {7}, new String[] {C}, new int[] {UID}, new boolean[] {true}, new int[] {7},
                new String[] {"com.example.mail"});
            o.acceptApps(4, new int[] {7}, new String[] {C}, new int[] {UID}, (boolean[]) bad[0], (int[]) bad[1], (String[]) bad[2]);
            RoseAgentWork r = row(o.snapshot(), tasks);
            expect(r.items.isEmpty() && "Rose's work could not be read (ownership)".equals(r.reason),
                "a malformed apps snapshot is rejected whole and named");
            expect(!o.acceptApps(3, new int[] {7}, new String[] {C}, new int[] {UID}, new boolean[] {true}, new int[] {7},
                new String[] {"com.example.mail"}) && o.acceptApps(4, new int[] {7}, new String[] {C}, new int[] {UID},
                new boolean[] {true}, new int[] {7}, new String[] {"com.example.mail"}), "the baseline did not advance");
        }
        Object[][] badDevices = {
            {-1L, new int[0], new String[0], new int[0]},
            {2L, null, new String[0], new int[0]},
            {2L, new int[] {7, 8}, new String[] {C}, new int[] {UID, UID}},
            {2L, new int[] {7, 7}, new String[] {C, C}, new int[] {UID, UID}},
            {2L, new int[] {7}, new String[] {""}, new int[] {UID}},
            {2L, new int[] {7}, new String[] {C}, new int[] {-1}}};
        for (Object[] bad : badDevices) {
            RoseAgentWork.Ownership o = new RoseAgentWork.Ownership();
            o.accept((Long) bad[0], (int[]) bad[1], (String[]) bad[2], (int[]) bad[3]);
            expect(o.snapshot().malformed && "Rose's work could not be read (ownership)".equals(row(o.snapshot(), tasks).reason),
                "a malformed devices snapshot is rejected whole and named");
        }

        // Lifecycle through one registration (successive dispatches).
        RoseAgentWork.Ownership life = new RoseAgentWork.Ownership();
        dispatch(life, 0, new int[0], new String[0], new int[0], new boolean[0], new int[0], new String[0]);
        expect(row(life.snapshot(), Collections.emptyList()).state == RoseAgentWork.State.EMPTY, "boot: confirmed empty");
        dispatch(life, 1, new int[] {7}, new String[] {C}, new int[] {UID}, new boolean[] {true}, new int[] {7},
            new String[] {"com.example.mail"});
        expect(pkgs(row(life.snapshot(), Arrays.asList(task(2, 70, 0, "com.example.mail", true))).items)
            .equals(Arrays.asList("com.example.mail")), "launch: the app appears");
        dispatch(life, 2, new int[] {7}, new String[] {C}, new int[] {UID}, new boolean[] {true}, new int[] {7, 7},
            new String[] {"com.example.mail", "com.example.maps"});
        expect(pkgs(row(life.snapshot(), Collections.emptyList()).items).equals(Arrays.asList("com.example.mail", "com.example.maps")),
            "a second app on the same device appears");
        dispatch(life, 3, new int[] {7}, new String[] {C}, new int[] {UID}, new boolean[] {true}, new int[] {7},
            new String[] {"com.example.maps"});
        expect(pkgs(row(life.snapshot(), Collections.emptyList()).items).equals(Arrays.asList("com.example.maps")),
            "close: mail's activities finished, so it is gone (a finished logical task alone removes nothing)");
        // A failed close: the controller kept its session, the platform still lists the device: its apps stay shown,
        // as App Core's getOwnedAutomatedDevices also reports them.
        expect(pkgs(row(life.snapshot(), Collections.emptyList()).items).equals(Arrays.asList("com.example.maps")),
            "a failed close changes nothing the platform reports: the same apps on both surfaces");
        dispatch(life, 4, new int[] {7, 8}, new String[] {C, C}, new int[] {UID, UID}, new boolean[] {true, true},
            new int[] {7, 8}, new String[] {"com.example.maps", "com.example.chat"});
        expect(pkgs(RoseAgentWork.of(UID, 0, life.snapshot(), devices(70, 7, 80, 8), Collections.emptyList()).items)
            .equals(Arrays.asList("com.example.maps", "com.example.chat")),
            "old (lingering) and new devices together: the apps of both, in device order");
        dispatch(life, 5, new int[] {8}, new String[] {C}, new int[] {UID}, new boolean[] {true}, new int[] {8},
            new String[] {"com.example.chat"});
        expect(pkgs(RoseAgentWork.of(UID, 0, life.snapshot(), devices(80, 8), Collections.emptyList()).items)
            .equals(Arrays.asList("com.example.chat")), "the old device removed: its apps are gone at that generation");
        dispatch(life, 6, new int[0], new String[0], new int[0], new boolean[0], new int[0], new String[0]);
        expect(row(life.snapshot(), tasks).state == RoseAgentWork.State.EMPTY, "all released: confirmed empty");

        // Labels, link and read-level failures.
        List<Integer> asked = new ArrayList<>();
        RoseAgentWork labelled = work.labelled(t -> {
            asked.add(t.taskId);
            if (t.taskId == 3) throw new IllegalStateException("private detail of a failed lookup");
            return t.packageName.equals("com.example.mail") ? "Mail" : null;
        });
        expect(asked.equals(Arrays.asList(2, 3)), "only the members are looked up");
        expect(labelled.items.get(0).label.equals("Mail") && labelled.items.get(1).label.equals("com.example.maps"),
            "a failed lookup shows the package name");
        expect(RoseAgentWork.decide(labelled.items.get(0), labelled) == RoseAgentWork.Open.WATCH, "selection still works");
        expect(notYet.labelled(t -> "x") == notYet, "a row without members looks nothing up");
        expect(work.working() == 1 && "1 task in progress".equals(work.linkLabel()), "mail current, maps behind");
        RoseAgentWork idle = row(rose("com.example.maps", C), Arrays.asList(task(6, 70, 0, C, true),
            task(3, 70, 0, "com.example.maps", false)));
        expect(idle.working() == 0 && "No active work".equals(idle.linkLabel()), "the controller in front: nothing in progress");
        expect(RoseAgentWork.of(-1, 0, mailMaps, devices(70, 7), tasks).state == RoseAgentWork.State.UNAVAILABLE,
            "no controller for this user: unavailable");
        expect(RoseAgentWork.of(UID, 0, null, devices(70, 7), tasks).state == RoseAgentWork.State.UNAVAILABLE
            && RoseAgentWork.of(UID, 0, mailMaps, null, tasks).state == RoseAgentWork.State.UNAVAILABLE
            && RoseAgentWork.of(UID, 0, mailMaps, devices(70, 7), null).state == RoseAgentWork.State.UNAVAILABLE,
            "an unreadable platform state is unavailable");
        expect("Rose's work could not be read (tasks)".equals(RoseAgentWork.failed(RoseAgentWork.Step.TASKS).reason)
            && "Rose's work could not be read (controller)".equals(RoseAgentWork.failed(RoseAgentWork.Step.CONTROLLER).reason)
            && "Rose's work could not be read (displays)".equals(RoseAgentWork.failed(RoseAgentWork.Step.DISPLAYS).reason)
            && "Rose's work could not be read (ownership)".equals(RoseAgentWork.failed(RoseAgentWork.Step.OWNERSHIP).reason),
            "a failed platform read names its step");
        System.out.println("RoseAgentWorkTest: " + checks + " checks passed");
    }
}
