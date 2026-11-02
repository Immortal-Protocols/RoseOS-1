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
        expect(twoRow.items.isEmpty() && RoseAgentWork.UNCONFIRMED.equals(twoRow.reason) && twoRow.linkLabel().isEmpty(),
            "more than one controller device (old and new): cannot confirm, no app cards");
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
        expect(RoseAgentWork.UNCONFIRMED.equals(RoseAgentWork.of(UID, 0, life.snapshot(), devices(70, 7, 80, 8),
            Collections.emptyList()).reason), "old and new devices together: cannot confirm");
        dispatch(life, 5, new int[0], new String[0], new int[0], new boolean[0], new int[0], new String[0]);
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
