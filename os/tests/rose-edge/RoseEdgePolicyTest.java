package com.android.wm.shell.roseedge;

import static com.android.wm.shell.roseedge.RoseEdgePolicy.*;

import java.util.Arrays;

/**
 * RoseOS (#410): runs the patched production RoseEdgePolicy (os/patches/rose-edge-wmshell.patch) through the raise
 * refusals, the lift bound, single ownership, every lowering rule and the microphone slot (option a: bottom band,
 * 32-64dp, mute or unmute only, tap and spacing, owned by the current raise) agreed with OS Product. Pure Java: no
 * Android, no device.
 */
public final class RoseEdgePolicyTest {
    static int checks;

    static void expect(boolean ok, String what) { checks++; if (!ok) throw new AssertionError(what); }

    static final int W = 1080, H = 2424;
    static final float DENSITY = 2.625f;  // 420 dpi
    static final int LIFT = Math.round(58 * DENSITY);

    static Conditions ok() { return new Conditions(true, H, DENSITY, ROTATION_0, false, false, false); }

    static TaskState app(int taskId) { return new TaskState(taskId, DEFAULT_DISPLAY, true, true, true, true, false); }

    static void refusals() {
        TaskState mail = app(7);
        expect(refusal(DEFAULT_DISPLAY, 7, LIFT, ok(), mail, false) == 0, "the top resizeable fullscreen app is raised");

        expect(refusal(DEFAULT_DISPLAY, 7, LIFT, new Conditions(false, H, DENSITY, ROTATION_0, false, false, false),
                mail, false) == REFUSED_DISABLED, "default off: the flag gates everything first");
        expect(refusal(2, 7, LIFT, ok(), mail, false) == REFUSED_DISPLAY, "only the default display");

        // Lift bound: positive, at most a quarter of the height and at most 160dp.
        int max = maxLiftPx(H, DENSITY);
        expect(max == Math.min(H / 4, Math.round(160 * DENSITY)), "cap is min(H/4, 160dp)");
        expect(refusal(DEFAULT_DISPLAY, 7, 0, ok(), mail, false) == REFUSED_LIFT, "zero lift");
        expect(refusal(DEFAULT_DISPLAY, 7, -5, ok(), mail, false) == REFUSED_LIFT, "negative lift");
        expect(refusal(DEFAULT_DISPLAY, 7, max, ok(), mail, false) == 0, "the cap itself is allowed");
        expect(refusal(DEFAULT_DISPLAY, 7, max + 1, ok(), mail, false) == REFUSED_LIFT, "never past the cap");
        expect(maxLiftPx(800, 3f) == 200, "a short display caps at a quarter of its height");

        expect(refusal(DEFAULT_DISPLAY, 7, LIFT, new Conditions(true, H, DENSITY, ROTATION_0, true, false, false),
                mail, false) == REFUSED_LOCKED, "keyguard");
        expect(refusal(DEFAULT_DISPLAY, 7, LIFT, ok(), mail, true) == REFUSED_BUSY, "something in flight or owned");
        expect(refusal(DEFAULT_DISPLAY, 7, LIFT, new Conditions(true, H, DENSITY, ROTATION_0, false, true, false),
                mail, false) == 0, "IME showing: the chin rises over the open keyboard (F10 rule 1)");
        expect(refusal(DEFAULT_DISPLAY, 7, LIFT, new Conditions(true, H, DENSITY, ROTATION_0, false, true, false),
                mail, true) == REFUSED_BUSY, "IME showing and something in flight: still busy");
        expect(refusal(DEFAULT_DISPLAY, 7, LIFT, new Conditions(true, H, DENSITY, ROTATION_0, true, true, false),
                mail, false) == REFUSED_LOCKED, "IME showing over the keyguard: still locked");
        expect(refusal(DEFAULT_DISPLAY, 7, LIFT, new Conditions(true, H, DENSITY, 1, false, false, false),
                mail, false) == REFUSED_ROTATION, "natural rotation only");
        expect(refusal(DEFAULT_DISPLAY, 7, LIFT, new Conditions(true, H, DENSITY, ROTATION_0, false, false, true),
                mail, false) == REFUSED_MULTI_WINDOW, "no split, PiP or freeform visible");

        expect(refusal(DEFAULT_DISPLAY, 7, LIFT, ok(), null, false) == REFUSED_NOT_TOP_TASK, "no focused task");
        expect(refusal(DEFAULT_DISPLAY, 8, LIFT, ok(), mail, false) == REFUSED_NOT_TOP_TASK, "not the focused task");
        expect(refusal(DEFAULT_DISPLAY, 7, LIFT, ok(), new TaskState(7, DEFAULT_DISPLAY, false, true, true, true, false),
                false) == REFUSED_NOT_TOP_TASK, "not visible");
        expect(refusal(DEFAULT_DISPLAY, 7, LIFT, ok(), new TaskState(7, DEFAULT_DISPLAY, true, false, true, true, false),
                false) == REFUSED_NOT_TOP_TASK, "not fullscreen");
        expect(refusal(DEFAULT_DISPLAY, 7, LIFT, ok(), new TaskState(7, DEFAULT_DISPLAY, true, true, false, true, false),
                false) == REFUSED_NOT_TOP_TASK, "Home, recents and other non-standard tasks are never raised over");
        expect(refusal(DEFAULT_DISPLAY, 7, LIFT, ok(), new TaskState(7, 2, true, true, true, true, false), false)
                == REFUSED_NOT_TOP_TASK, "a task on another display");
        // A non-resizeable or size-compat app would be letterboxed or scaled: no raise, never a scaled substitute.
        expect(refusal(DEFAULT_DISPLAY, 7, LIFT, ok(), new TaskState(7, DEFAULT_DISPLAY, true, true, true, false, false),
                false) == REFUSED_NOT_RESIZEABLE, "not resizeable");
        expect(refusal(DEFAULT_DISPLAY, 7, LIFT, ok(), new TaskState(7, DEFAULT_DISPLAY, true, true, true, true, true),
                false) == REFUSED_NOT_RESIZEABLE, "in size compat");
    }

    static void geometry() {
        expect(Arrays.equals(raisedBounds(W, H, LIFT), new int[] {0, 0, W, H - LIFT}),
                "full width, top unchanged, only the bottom moves up");
    }

    static void ownership() {
        State s = new State();
        Object launcher = new Object(), stale = new Object();
        expect(!s.raised() && !s.busyFor(launcher), "nothing raised: free");
        s.setInFlight(true);
        expect(s.busyFor(launcher), "a raise or lower in flight blocks the next");
        s.setInFlight(false);
        s.raise(launcher, DEFAULT_DISPLAY, 7, LIFT);
        expect(s.raised() && s.owner() == launcher && s.taskId() == 7 && s.liftPx() == LIFT, "raised and owned");
        expect(s.busyFor(stale), "another binder cannot raise over it");
        expect(!s.busyFor(launcher), "the owner may raise again (a new lift or task is judged afresh)");
        expect(s.onOwnerGone(stale) == KEEP, "another binder's death changes nothing");
        expect(s.onOwnerGone(launcher) == LOWERED_CALLER_DIED, "the owner's death lowers");
        expect(s.clear() == launcher && !s.raised() && s.clear() == null, "clear returns the owner once");
    }

    static void lowering() {
        Object launcher = new Object();
        State s = new State();
        // Nothing raised: no event lowers anything.
        expect(s.onFocusChanged(app(9), false) == KEEP && s.onKeyguard(true) == KEEP && s.onDisplayChanged(0) == KEEP
                && s.onImeVisibility(0, true) == KEEP && s.onUserChanged() == KEEP && s.onTaskVanished(7) == KEEP
                && s.onLowerRequested(0) == KEEP, "lowered: events are no-ops");

        s.raise(launcher, DEFAULT_DISPLAY, 7, LIFT);
        expect(s.onFocusChanged(app(7), false) == KEEP, "the same task keeps focus");
        expect(s.onFocusChanged(app(9), false) == LOWERED_TASK, "another app in front");
        expect(s.onFocusChanged(new TaskState(1, DEFAULT_DISPLAY, true, true, false, true, false), true)
                == LOWERED_HOME, "Home or recents in front");
        expect(s.onFocusChanged(new TaskState(9, 2, true, true, true, true, false), false) == KEEP,
                "another display's focus is not ours");
        expect(s.onFocusChanged(null, false) == KEEP, "no info: wait for a real event");

        // Its own resize keeps it raised; leaving fullscreen, visibility, resizeability or entering compat lowers.
        expect(s.onTaskInfoChanged(app(7)) == KEEP, "new bounds alone change nothing");
        expect(s.onTaskInfoChanged(new TaskState(7, 0, false, true, true, true, false)) == LOWERED_TASK, "hidden");
        expect(s.onTaskInfoChanged(new TaskState(7, 0, true, false, true, true, false)) == LOWERED_TASK, "multi-window");
        expect(s.onTaskInfoChanged(new TaskState(7, 0, true, true, true, true, true)) == LOWERED_TASK, "size compat");
        expect(s.onTaskInfoChanged(new TaskState(9, 0, false, false, true, true, false)) == KEEP, "another task");
        expect(s.onTaskVanished(9) == KEEP && s.onTaskVanished(7) == LOWERED_TASK, "the raised task vanished");

        expect(s.onKeyguard(false) == KEEP && s.onKeyguard(true) == LOWERED_KEYGUARD, "keyguard, occluded or not");
        expect(s.onDisplayChanged(2) == KEEP && s.onDisplayChanged(0) == LOWERED_DISPLAY,
                "rotation, size or density change of the raised display");
        expect(s.onImeVisibility(0, false) == KEEP && s.onImeVisibility(2, true) == KEEP
                && s.onImeVisibility(0, true) == KEEP && s.raised(),
                "the keyboard showing or hiding never lowers the chin (F10 rules 1-2)");
        expect(LOWERED_IME == 5, "LOWERED_IME keeps its wire number, though it is no longer sent");
        expect(s.onUserChanged() == LOWERED_USER, "user switch");
        expect(s.onLowerRequested(2) == KEEP && s.onLowerRequested(0) == LOWERED_REQUESTED, "explicit lower");
    }

    static void micSlot() {
        int px = Math.round(44 * DENSITY), band = maxLiftPx(H, DENSITY);
        int cx = W / 2, top = H - Math.round(29 * DENSITY) - px / 2;
        expect(validMicSlot(cx - px / 2, top, cx + px / 2, top + px, W, H, DENSITY), "44dp slot in the edge band");
        expect(!validMicSlot(cx - px / 2, H - band - 1, cx + px / 2, H - band - 1 + px, W, H, DENSITY),
                "never above the bottom band");
        expect(validMicSlot(cx - px / 2, H - band, cx + px / 2, H - band + px, W, H, DENSITY), "the band's top edge");
        int min = Math.round(32 * DENSITY), max = Math.round(64 * DENSITY);
        expect(validMicSlot(0, H - min, min, H, W, H, DENSITY) && !validMicSlot(0, H - min + 1, min, H, W, H, DENSITY),
                "32dp is the smallest side");
        expect(validMicSlot(0, H - max, max, H, W, H, DENSITY) && !validMicSlot(0, H - max - 1, max + 1, H, W, H, DENSITY),
                "64dp is the largest side");
        expect(!validMicSlot(-1, H - px, px - 1, H, W, H, DENSITY) && !validMicSlot(W - px + 1, H - px, W + 1, H, W, H, DENSITY)
                && !validMicSlot(0, H - px + 1, px, H + 1, W, H, DENSITY), "never off the display");
        expect(!validMicSlot(0, H - px, px * 3, H, W, H, DENSITY), "never a wide strip");
        // Mute only: never START (3) or END (6).
        expect(micCommand(false) == MIC_MUTE && micCommand(true) == MIC_UNMUTE && MIC_MUTE == 4 && MIC_UNMUTE == 5,
                "a tap mutes or unmutes Rose's input, and nothing else");
        expect(micCommandAllowed(1000, 0) && !micCommandAllowed(1500, 1000) && micCommandAllowed(1600, 1000),
                "commands spaced like the tray's");
        MicTap tap = new MicTap();
        tap.down(10, 10, 0);
        expect(tap.up(12, 11, 300, 8, true), "a short tap");
        tap.down(10, 10, 0);
        expect(!tap.up(10, 10, 401, 8, true), "a long press is not a tap");
        tap.down(10, 10, 0);
        tap.move(30, 10, 8);
        expect(!tap.up(10, 10, 100, 8, true), "a drag is not a tap");
        tap.down(10, 10, 0);
        expect(!tap.up(10, 10, 100, 8, false), "released outside the control");
        tap.down(10, 10, 0);
        tap.cancel();
        expect(!tap.up(10, 10, 100, 8, true), "cancelled");
        expect(!tap.up(10, 10, 100, 8, true), "no down, no tap");
        // A slot belongs to the current raise and goes with it.
        State s = new State();
        expect(!s.acceptsMicSlot(0), "no raise, no slot");
        s.raise(new Object(), DEFAULT_DISPLAY, 7, LIFT);
        expect(s.acceptsMicSlot(0) && !s.acceptsMicSlot(2), "only the raised display");
        s.setMicSlot(new int[] {1, 2, 3, 4});
        s.clear();
        expect(s.micSlot() == null && !s.acceptsMicSlot(0), "every lower drops it; Launcher3 re-sends after onRaised");
    }

    public static void main(String[] args) {
        refusals();
        geometry();
        ownership();
        lowering();
        micSlot();
        System.out.println("RoseEdgePolicyTest: " + checks + " checks passed");
    }
}
