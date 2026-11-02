package com.android.server.companion.virtual.computercontrol;

import android.companion.virtual.computercontrol.AutomatedApps;
import android.os.Handler;
import android.util.ArraySet;
import android.util.Pair;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Deque;
import java.util.List;
import java.util.function.BooleanSupplier;

/**
 * RoseOS (multi-session): drives the patched production ComputerControlSessionAdmission, AutomatedApps and
 * AutomatedPackagesRepository (Android types stubbed only) through the contract agreed with App Core.
 * <ul>
 *   <li>Admission: a second session only for the signer-pinned owner next to its own (same uid), counted across
 *       every owner, never a third.</li>
 *   <li>The name rule re-applied at creation, so requests in flight are covered.</li>
 *   <li>sessionLimit is a capability, not admission.</li>
 *   <li>deviceId and displayId are fixed and historical once closed.</li>
 *   <li>Each session's lifecycle is independent, with user filtering kept.</li>
 * </ul>
 * The processor's creation flow is modelled as in ComputerControlSessionProcessor: validateRequest checks the name on
 * the binder thread, then every createSession runs on one handler and re-checks name and admission under mSessions
 * before publishing. The Python harness checks the patched processor calls exactly these functions in that order.
 */
public final class MultiSessionContractTest {
    static final String ROSE = "dev.pixel.backgroundcontrol", OTHER = "com.example.otheragent";
    static final int ROSE_UID = 10123, OTHER_UID = 10200, ROSE_UID_USER10 = 1010123;
    static final int MAX = 1;  // ComputerControlSessionProcessor.MAXIMUM_CONCURRENT_SESSIONS, as pinned
    static int checks;

    static void expect(boolean ok, String what) { checks++; if (!ok) throw new AssertionError(what); }

    /** A supplier that records whether it was asked. */
    static final class Probe implements BooleanSupplier {
        final boolean value; int asked;
        Probe(boolean value) { this.value = value; }
        @Override public boolean getAsBoolean() { asked++; return value; }
    }

    static ComputerControlSessionAdmission.Held held(int uid, String pkg, String name, boolean prototype) {
        return new ComputerControlSessionAdmission.Held(uid, pkg, name, () -> prototype);
    }

    static boolean admits(List<ComputerControlSessionAdmission.Held> held, int uid, boolean prototype) {
        return ComputerControlSessionAdmission.admits(MAX, held, uid, () -> prototype);
    }

    static void admission() {
        List<ComputerControlSessionAdmission.Held> none = List.of();
        // Empty: anyone, as pinned, and no prototype check is needed for the first session.
        Probe asked = new Probe(false);
        expect(ComputerControlSessionAdmission.admits(MAX, none, OTHER_UID, asked) && asked.asked == 0,
                "the first session needs no prototype check");
        expect(admits(none, ROSE_UID, true), "Rose's first session");

        // Rose next to its own session: both signer-pinned, same uid.
        List<ComputerControlSessionAdmission.Held> rose = List.of(held(ROSE_UID, ROSE, "pixel-voice-background", true));
        expect(admits(rose, ROSE_UID, true), "a second session for the same signer-pinned owner");

        // Other-owner cap one, both ways.
        expect(!admits(rose, OTHER_UID, false), "another owner is refused while Rose holds a session");
        expect(!admits(rose, OTHER_UID, true), "even another pinned owner uid never sits next to Rose");
        List<ComputerControlSessionAdmission.Held> other = List.of(held(OTHER_UID, OTHER, "s", false));
        expect(!admits(other, ROSE_UID, true), "Rose is refused while another owner holds a session");
        expect(!admits(other, OTHER_UID, false), "an unpinned owner keeps the pinned limit of one");

        // Same signer requirement on both sides.
        expect(!admits(rose, ROSE_UID, false), "the requester must pass the signer-pinned check");
        List<ComputerControlSessionAdmission.Held> unpinnedHeld = List.of(held(ROSE_UID, ROSE, "a", false));
        expect(!admits(unpinnedHeld, ROSE_UID, true), "the held session's owner must pass it too");
        Probe heldAsked = new Probe(true);
        List<ComputerControlSessionAdmission.Held> lazy =
                List.of(new ComputerControlSessionAdmission.Held(ROSE_UID, ROSE, "a", heldAsked));
        expect(!ComputerControlSessionAdmission.admits(MAX, lazy, OTHER_UID, () -> true) && heldAsked.asked == 0,
                "a different uid is refused before any signer check");

        // Same package and signer, another user (another uid): never combined.
        expect(!admits(rose, ROSE_UID_USER10, true), "Rose in another user is another owner");

        // Never a third: two held, even both Rose's, even when one is a failed close still counted.
        List<ComputerControlSessionAdmission.Held> two = List.of(held(ROSE_UID, ROSE, "pixel-voice-background", true),
                held(ROSE_UID, ROSE, "pixel-voice-background-1", true));
        expect(!admits(two, ROSE_UID, true), "no third session");
        expect(!admits(two, OTHER_UID, false), "no other owner beside two");
    }

    static void names() {
        List<ComputerControlSessionAdmission.Held> rose = List.of(held(ROSE_UID, ROSE, "pixel-voice-background", true));
        expect(ComputerControlSessionAdmission.isNameTaken(rose, ROSE, "pixel-voice-background"), "same owner, same name");
        expect(!ComputerControlSessionAdmission.isNameTaken(rose, ROSE, "pixel-voice-background-1"), "per-slot names differ");
        expect(!ComputerControlSessionAdmission.isNameTaken(rose, OTHER, "pixel-voice-background"),
                "another owner may use the same name (the pinned rule is per owner package)");
        expect(ComputerControlSessionAdmission.isNameTaken(List.of(held(ROSE_UID, ROSE, null, true)), ROSE, null),
                "null names compare with Objects.equals, as validateRequest does");
    }

    /** One request through the modelled processor: validated on the binder thread, created later on the handler. */
    static final class Request {
        final int uid; final String pkg, name; final boolean prototype;
        String outcome;
        Request(int uid, String pkg, String name, boolean prototype) {
            this.uid = uid; this.pkg = pkg; this.name = name; this.prototype = prototype;
        }
    }

    /** ComputerControlSessionProcessor's flow with the patched rules: mSessions, a validate step and the handler. */
    static final class Processor {
        final List<ComputerControlSessionAdmission.Held> sessions = new ArrayList<>();
        final Deque<Request> handler = new ArrayDeque<>();
        /** processNewSessionRequest: validateRequest's name check, then createSession is posted. */
        boolean request(Request r) {
            if (ComputerControlSessionAdmission.isNameTaken(sessions, r.pkg, r.name)) {
                r.outcome = "IllegalArgumentException";  // synchronous, to the caller
                return false;
            }
            handler.add(r);
            return true;
        }
        /** createSession, one at a time on the handler: name re-check, admission, then publish. */
        void runHandler() {
            while (!handler.isEmpty()) {
                Request r = handler.poll();
                if (ComputerControlSessionAdmission.isNameTaken(sessions, r.pkg, r.name)
                        || !ComputerControlSessionAdmission.admits(MAX, sessions, r.uid, () -> r.prototype)) {
                    r.outcome = "ERROR_SESSION_LIMIT_REACHED";
                    continue;
                }
                sessions.add(held(r.uid, r.pkg, r.name, r.prototype));
                r.outcome = "created";
            }
        }
    }

    static void inFlight() {
        // Two same-name requests both pass validation before either is created: the second fails at creation.
        Processor p = new Processor();
        Request a = new Request(ROSE_UID, ROSE, "pixel-voice-background", true);
        Request b = new Request(ROSE_UID, ROSE, "pixel-voice-background", true);
        expect(p.request(a) && p.request(b), "both pass the binder-thread name check while neither exists");
        p.runHandler();
        expect(a.outcome.equals("created") && b.outcome.equals("ERROR_SESSION_LIMIT_REACHED") && p.sessions.size() == 1,
                "the in-flight duplicate name is refused at creation");

        // Once created, the same name is refused synchronously, as pinned.
        Request c = new Request(ROSE_UID, ROSE, "pixel-voice-background", true);
        expect(!p.request(c) && c.outcome.equals("IllegalArgumentException"), "a held name throws at request time");

        // Rose's second slot is admitted; then another owner's in-flight request finds the limit at creation.
        Request slot1 = new Request(ROSE_UID, ROSE, "pixel-voice-background-1", true);
        Request otherOwner = new Request(OTHER_UID, OTHER, "s", false);
        expect(p.request(slot1) && p.request(otherOwner), "both validated");
        p.runHandler();
        expect(slot1.outcome.equals("created") && otherOwner.outcome.equals("ERROR_SESSION_LIMIT_REACHED"),
                "Rose's second slot, then the other owner refused");

        // Another owner first: Rose's two in-flight requests both wait behind it and both fail.
        Processor q = new Processor();
        Request first = new Request(OTHER_UID, OTHER, "s", false);
        Request r0 = new Request(ROSE_UID, ROSE, "pixel-voice-background", true);
        Request r1 = new Request(ROSE_UID, ROSE, "pixel-voice-background-1", true);
        q.request(first); q.request(r0); q.request(r1);
        q.runHandler();
        expect(first.outcome.equals("created") && r0.outcome.equals("ERROR_SESSION_LIMIT_REACHED")
                && r1.outcome.equals("ERROR_SESSION_LIMIT_REACHED"), "the other owner keeps the device to itself");

        // Three distinct Rose names in flight: two created, the third refused.
        Processor t = new Processor();
        Request[] three = { new Request(ROSE_UID, ROSE, "a", true), new Request(ROSE_UID, ROSE, "b", true),
                new Request(ROSE_UID, ROSE, "c", true) };
        for (Request r : three) t.request(r);
        t.runHandler();
        expect(three[0].outcome.equals("created") && three[1].outcome.equals("created")
                && three[2].outcome.equals("ERROR_SESSION_LIMIT_REACHED"), "never a third, even when in flight");

        // A failed close stays in mSessions: it still counts, and its name still collides.
        Processor f = new Processor();
        f.sessions.add(held(ROSE_UID, ROSE, "pixel-voice-background", true));  // close threw before mOnClosedListener
        Request again = new Request(ROSE_UID, ROSE, "pixel-voice-background", true);
        expect(!f.request(again), "a failed close keeps its name taken");
        Request alongside = new Request(ROSE_UID, ROSE, "pixel-voice-background-1", true);
        f.request(alongside); f.runHandler();
        expect(alongside.outcome.equals("created"), "the failed-close session counts as one of Rose's two");
        Request third = new Request(ROSE_UID, ROSE, "c", true);
        f.request(third); f.runHandler();
        expect(third.outcome.equals("ERROR_SESSION_LIMIT_REACHED"), "and the pair is then full");
    }

    static void sessionLimit() {
        expect(ComputerControlSessionAdmission.sessionLimit(MAX, new String[] {ROSE}, ROSE::equals) == 2,
                "2 for a single-package uid that passes the signer-pinned check");
        expect(ComputerControlSessionAdmission.sessionLimit(MAX, new String[] {OTHER}, ROSE::equals) == 1, "1 when unpinned");
        expect(ComputerControlSessionAdmission.sessionLimit(MAX, new String[] {ROSE, "dev.pixel.shared"}, p -> true) == 1,
                "1 for a shared uid, even when every package passes");
        expect(ComputerControlSessionAdmission.sessionLimit(MAX, null, p -> true) == 1, "1 when the uid has no packages");
        expect(ComputerControlSessionAdmission.sessionLimit(MAX, new String[0], p -> true) == 1, "1 for none");
        expect(ComputerControlSessionAdmission.sessionLimit(MAX, new String[] {null}, p -> true) == 1, "1 for a null name");
        boolean threw = false;
        try {
            ComputerControlSessionAdmission.sessionLimit(MAX, new String[] {ROSE}, p -> { throw new SecurityException(); });
        } catch (SecurityException e) {
            threw = true;  // the processor catches it and reports the pinned limit (checked in the Python harness)
        }
        expect(threw, "a failed identity lookup is not reported as 2");

        // Capability, not admission: Rose's limit is 2 in each state below, yet each refuses.
        int limit = ComputerControlSessionAdmission.sessionLimit(MAX, new String[] {ROSE}, ROSE::equals);
        expect(limit == 2 && !admits(List.of(held(OTHER_UID, OTHER, "s", false)), ROSE_UID, true),
                "limit 2, but another owner's session refuses Rose");
        expect(limit == 2 && !admits(List.of(held(ROSE_UID, ROSE, "a", true), held(ROSE_UID, ROSE, "b", true)),
                ROSE_UID, true), "limit 2, but both slots held (one may be a failed close)");
        expect(ComputerControlSessionAdmission.sessionLimit(MAX, new String[] {ROSE}, ROSE::equals) == limit,
                "the limit does not change with sessions held: it is not free slots");
    }

    static void ids() {
        AutomatedApps unbound = new AutomatedApps(4, new String[] {"com.example.mail"}, 0, true, false);
        expect(unbound.getDeviceId() == AutomatedApps.INVALID_ID && unbound.getDisplayId() == AutomatedApps.INVALID_ID,
                "no ids before the session sets them");
        AutomatedApps open = unbound.forSession(31, 47);
        expect(open.getDeviceId() == 31 && open.getDisplayId() == 47 && open.getGeneration() == 4
                && Arrays.equals(open.getPackageNames(), new String[] {"com.example.mail"}) && open.isComplete()
                && !open.isClosed() && open.getUserId() == 0, "an open reply: live apps plus the session's ids");
        expect(unbound.getDeviceId() == AutomatedApps.INVALID_ID, "forSession never changes the original");
        AutomatedApps closed = AutomatedApps.closed(0).forSession(31, 47);
        expect(closed.isClosed() && closed.getPackageNames().length == 0 && closed.getGeneration() == Long.MAX_VALUE
                && closed.getDeviceId() == 31 && closed.getDisplayId() == 47,
                "closed: the same ids, kept as history, with no membership");
        for (AutomatedApps a : new AutomatedApps[] {open, closed}) {
            android.os.Parcel parcel = android.os.Parcel.obtain();
            a.writeToParcel(parcel, 0);
            AutomatedApps copy = AutomatedApps.CREATOR.createFromParcel(parcel);
            expect(copy.getDeviceId() == a.getDeviceId() && copy.getDisplayId() == a.getDisplayId()
                    && copy.isClosed() == a.isClosed() && copy.getGeneration() == a.getGeneration()
                    && Arrays.equals(copy.getPackageNames(), a.getPackageNames()), "ids survive the binder parcel");
        }
    }

    static ArraySet<Pair<Integer, String>> running(int uid, String... packages) {
        ArraySet<Pair<Integer, String>> set = new ArraySet<>();
        for (String p : packages) set.add(new Pair<>(uid, p));
        return set;
    }

    /** Two Rose sessions on one repository: independent membership, close, and user filtering. */
    static void lifecycle() {
        Handler handler = new Handler();
        AutomatedPackagesRepository repository = new AutomatedPackagesRepository(handler);
        repository.update(31, ROSE, ROSE_UID, running(10500, "com.android.calculator2"));
        repository.update(32, ROSE, ROSE_UID, running(10600, "com.android.deskclock"));
        handler.drain();
        AutomatedApps calculator = repository.automatedAppsForDevice(31, ROSE_UID).forSession(31, 47);
        AutomatedApps clock = repository.automatedAppsForDevice(32, ROSE_UID).forSession(32, 48);
        expect(Arrays.asList(calculator.getPackageNames()).equals(List.of("com.android.calculator2"))
                && Arrays.asList(clock.getPackageNames()).equals(List.of("com.android.deskclock")),
                "each session reads only its own device");
        android.os.Bundle both = repository.ownedAutomatedDevices(ROSE_UID);
        expect(Arrays.stream(both.getIntArray("deviceIds")).sorted().boxed().toList().equals(List.of(31, 32)),
                "the owner's list joins both sessions by deviceId");

        // Stop Calculator: only its device leaves; Clock's reads and generation are untouched.
        long clockGeneration = repository.automatedAppsForDevice(32, ROSE_UID).getGeneration();
        repository.onDeviceClosed(31);
        handler.drain();
        expect(Arrays.stream(repository.ownedAutomatedDevices(ROSE_UID).getIntArray("deviceIds")).boxed().toList()
                .equals(List.of(32)), "stopping one session leaves the other listed");
        AutomatedApps clockAfter = repository.automatedAppsForDevice(32, ROSE_UID);
        expect(clockAfter.getGeneration() == clockGeneration
                && Arrays.asList(clockAfter.getPackageNames()).equals(List.of("com.android.deskclock")),
                "the other session's apps and generation are unchanged");
        // Calculator's retained id never matches a listed device again, even after a late report.
        repository.update(31, ROSE, ROSE_UID, running(10500, "com.android.calculator2"));
        handler.drain();
        expect(Arrays.stream(repository.ownedAutomatedDevices(ROSE_UID).getIntArray("deviceIds")).noneMatch(d -> d == 31),
                "a closed session's deviceId is history: never listed again");

        // User filtering on one of two sessions: the other user's app is omitted and only that device is incomplete.
        ArraySet<Pair<Integer, String>> mixed = running(10600, "com.android.deskclock");
        mixed.add(new Pair<>(1010700, "com.example.private"));
        repository.update(32, ROSE, ROSE_UID, mixed);
        repository.update(33, ROSE, ROSE_UID, running(10500, "com.android.calculator2"));
        handler.drain();
        AutomatedApps filtered = repository.automatedAppsForDevice(32, ROSE_UID);
        expect(!filtered.isComplete() && Arrays.asList(filtered.getPackageNames()).equals(List.of("com.android.deskclock")),
                "another user's activity is omitted, never named");
        expect(repository.automatedAppsForDevice(33, ROSE_UID).isComplete(), "the other session stays complete");
        expect(repository.automatedAppsForDevice(32, ROSE_UID_USER10) == null && repository.automatedAppsForDevice(32, OTHER_UID) == null,
                "no other user or owner reads either session");
        expect(Arrays.stream(repository.ownedAutomatedDevices(ROSE_UID_USER10).getIntArray("deviceIds")).count() == 0,
                "Rose in another user owns none of them");
    }

    public static void main(String[] args) {
        admission();
        names();
        inFlight();
        sessionLimit();
        ids();
        lifecycle();
        System.out.println("MultiSessionContractTest: " + checks + " checks passed");
    }
}
