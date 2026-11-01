package com.android.server.companion.virtual.computercontrol;

import android.companion.virtual.computercontrol.IAutomatedPackageListener;
import android.os.Handler;
import android.os.UserHandle;
import android.util.ArraySet;
import android.util.Pair;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * RoseOS (#156): drives the patched production AutomatedPackagesRepository (Android types stubbed only) through the
 * nine paired scenarios agreed with the Launcher3 consumer, and checks the frozen contract on every delivered call:
 * full snapshots for the listener's own user, a complete (even empty) snapshot after each registration, every content
 * change at a higher generation and a repeated generation with identical content. The attribution join is a reference
 * for the contract (owner package AND uid, display via getVirtualDevices), not the Launcher3 consumer.
 */
public final class AutomatedDevicesContractTest {
    static final String ROSE = "dev.pixel.backgroundcontrol", OTHER = "com.example.otheragent";
    static final int ROSE_UID = 10123, OTHER_UID = 10200, ROSE_UID_USER10 = 1010123;
    static int checks;

    static void expect(boolean ok, String what) { checks++; if (!ok) throw new AssertionError(what); }

    static final class Owner {
        final String pkg; final int uid;
        Owner(String pkg, int uid) { this.pkg = pkg; this.uid = uid; }
        @Override public boolean equals(Object o) { return o instanceof Owner && pkg.equals(((Owner) o).pkg) && uid == ((Owner) o).uid; }
        @Override public int hashCode() { return Objects.hash(pkg, uid); }
        @Override public String toString() { return pkg + "/" + uid; }
    }

    /** One listener: every delivered snapshot checked against the contract, and the reference consumer's state. */
    static final class Recorder implements IAutomatedPackageListener {
        final List<String> calls = new ArrayList<>();
        final Map<Long, Map<Integer, Owner>> byGeneration = new HashMap<>();
        long last = -1;
        Map<Integer, Owner> lastContent;
        Map<Integer, Owner> owners = null;  // reference consumer: null until the first valid snapshot
        int snapshots;
        @Override public void onAutomatedPackagesChanged(String owner, List<String> packages, UserHandle user) {
            calls.add("packages:" + owner);
        }
        @Override public void onAutomatedDevicesChanged(long generation, int[] ids, String[] pkgs, int[] uids) {
            calls.add("devices:" + generation);
            snapshots++;
            expect(generation >= 0 && ids.length == pkgs.length && ids.length == uids.length, "well-formed snapshot");
            Map<Integer, Owner> content = new HashMap<>();
            for (int i = 0; i < ids.length; i++) {
                expect(pkgs[i] != null && !pkgs[i].isEmpty() && uids[i] >= 0, "owner package and uid present");
                expect(content.put(ids[i], new Owner(pkgs[i], uids[i])) == null, "no duplicate device id");
            }
            // Frozen semantics: a repeated generation has identical content; a content change has a higher generation.
            Map<Integer, Owner> seen = byGeneration.putIfAbsent(generation, content);
            expect(seen == null || seen.equals(content), "a repeated generation has identical content");
            expect(generation >= last, "generations never go backwards for one listener");
            if (lastContent != null && !lastContent.equals(content)) expect(generation > last, "a content change has a higher generation");
            last = generation; lastContent = content;
            owners = content;
        }
        /** Reference join: display -> device (getVirtualDevices) -> listed owner with Rose's package AND this user's uid. */
        boolean roses(int display, Map<Integer, Integer> displayToDevice, int roseUid) {
            Integer device = displayToDevice.get(display);
            Owner owner = owners == null || device == null ? null : owners.get(device);
            return owner != null && ROSE.equals(owner.pkg) && owner.uid == roseUid;
        }
    }

    static ArraySet<Pair<Integer, String>> running(int uid, String... packages) {
        ArraySet<Pair<Integer, String>> set = new ArraySet<>();
        for (String p : packages) set.add(new Pair<>(uid, p));
        return set;
    }

    public static void main(String[] args) throws Exception {
        Handler handler = new Handler();
        AutomatedPackagesRepository repository = new AutomatedPackagesRepository(handler);
        Map<Integer, Integer> displays = new HashMap<>();  // display id -> device id, as getVirtualDevices reports

        // 1. Initial empty: a complete snapshot even with no session.
        Recorder user0 = new Recorder();
        repository.registerAutomatedPackageListener(user0, 0);
        handler.drain();
        expect(user0.snapshots == 1 && user0.owners.isEmpty(), "initial: one complete empty snapshot");

        // 2. Rose's session device 3 shows display 7.
        displays.put(7, 3);
        repository.update(3, ROSE, ROSE_UID, running(10500, "com.example.mail"));
        handler.drain();
        expect(user0.owners.equals(Map.of(3, new Owner(ROSE, ROSE_UID))), "Rose's device is listed with owner and uid");
        expect(user0.roses(7, displays, ROSE_UID), "display 7 is Rose's");

        // 3. Another owner's device 4 (display 11) automates the same app package.
        displays.put(11, 4);
        repository.update(4, OTHER, OTHER_UID, running(10500, "com.example.mail"));
        handler.drain();
        expect(user0.roses(7, displays, ROSE_UID) && !user0.roses(11, displays, ROSE_UID), "same package elsewhere is not Rose's");

        // 9. Simultaneous app swap (PR341 residual): every queued message processed alone keeps exact ownership.
        repository.update(3, ROSE, ROSE_UID, running(10600, "com.example.maps"));
        repository.update(4, OTHER, OTHER_UID, running(10500, "com.example.mail"));
        repository.update(4, OTHER, OTHER_UID, running(10500, "com.example.mail", "com.example.notes"));
        int before = user0.snapshots;
        while (!handler.queue.isEmpty()) {
            handler.queue.poll().run();
            expect(user0.roses(7, displays, ROSE_UID) && !user0.roses(11, displays, ROSE_UID), "swap: no false card at any message");
        }
        expect(user0.snapshots == before, "an app moving between agents changes no owner, so no new snapshot");

        // 8. Foreign user: user 10's controller (same package, another uid) is never in user 0's snapshot.
        Recorder user10 = new Recorder();
        repository.registerAutomatedPackageListener(user10, 10);
        displays.put(12, 6);
        repository.update(6, ROSE, ROSE_UID_USER10, running(1010500, "com.example.mail"));
        // A user-0 (USER_SYSTEM) app on user 10's session display is allowed by isActivityLaunchAllowed; still not Rose's.
        repository.update(6, ROSE, ROSE_UID_USER10, running(10500, "com.example.mail"));
        handler.drain();
        expect(!user0.owners.containsKey(6), "user 0 never sees user 10's session device");
        expect(!user0.roses(12, displays, ROSE_UID), "a user-0 task on user 10's session display is not user 0's Rose");
        expect(user10.owners.equals(Map.of(6, new Owner(ROSE, ROSE_UID_USER10))), "user 10 sees only its own device");

        // 4. Rose closes: the closed session device leaves at a higher generation, even before its apps stop.
        long held = user0.last;
        repository.onDeviceClosed(3);
        handler.drain();
        expect(!user0.owners.containsKey(3) && user0.last > held, "a closed device is dropped at a new generation");
        expect(!user0.roses(7, displays, ROSE_UID), "nothing on the closed device is Rose's");

        // 5. A task on a display whose device is not listed (no report yet, or owner uid unknown) is never Rose's.
        displays.put(13, 7);
        expect(!user0.roses(13, displays, ROSE_UID), "a device with no report yet is unknown");
        long beforeUnknown = user0.last;
        repository.update(7, ROSE, running(10500, "com.example.mail"));  // the pre-#156 path: owner uid unknown
        handler.drain();
        expect(!user0.owners.containsKey(7) && user0.last == beforeUnknown, "an owner without a uid is never listed");
        expect(!user0.roses(13, displays, ROSE_UID), "nor attributed");

        // 6. Ordering: a registration made while changes are still queued may first see an earlier queued generation,
        // never a lower one after a higher one, and every delivered call satisfied the frozen semantics (Recorder).
        Recorder late = new Recorder();
        repository.update(8, ROSE, ROSE_UID, running(10500, "com.example.notes"));
        repository.registerAutomatedPackageListener(late, 0);
        repository.update(8, ROSE, ROSE_UID, running(10500));
        repository.onDeviceClosed(8);
        handler.drain();
        expect(late.snapshots >= 1 && late.owners.equals(user0.owners) && late.last == user0.last,
                "a late registration converges on the same state and generation");

        // 7. Reconnect: a fresh registration (restarted Launcher3, service re-bind) gets the current state at once.
        Recorder again = new Recorder();
        repository.registerAutomatedPackageListener(again, 0);
        handler.drain();
        expect(again.calls.equals(List.of("devices:" + user0.last)) || again.calls.get(0).equals("devices:" + user0.last),
                "reconnect: the current generation first");
        expect(again.owners.equals(user0.owners), "reconnect: the complete current state");
        expect(again.owners.isEmpty() || !again.owners.containsKey(3), "reconnect never revives a closed device");

        // Privacy: snapshots carry device ids, owner packages and owner uids only; never automated package names.
        for (Recorder r : new Recorder[] {user0, user10, late, again})
            for (Map<Integer, Owner> content : r.byGeneration.values())
                for (Owner o : content.values())
                    expect(o.pkg.equals(ROSE) || o.pkg.equals(OTHER), "only owner packages are named");
        System.out.println("AutomatedDevicesContractTest: " + checks + " checks passed");
    }
}
