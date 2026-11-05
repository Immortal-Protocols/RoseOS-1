/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.wm.shell;

/** Status-bar tap mapping for the SystemUI tray; pure logic, no Android runtime. */
public final class AgentStatusControlsTest {
    // MotionEvent actions and the AgentTrayService commands they map to.
    static final int DOWN = 0, UP = 1, MOVE = 2, CANCEL = 3;
    static final int START = 3, MUTE = 4, UNMUTE = 5, END = 6;
    static final int AGENT = 1, MIC = 2, NONE = 0, SLOP = 8;
    static int checks;

    static void expect(String name, int expected, int actual) {
        if (expected != actual) throw new AssertionError(name + ": expected " + expected + ", got " + actual);
        checks++;
    }

    static int tap(AgentStatusControls c, int down, int up, long heldMs, boolean active, boolean muted) {
        c.touch(DOWN, 1, 10, 5, 1000, down, active, muted, SLOP);
        return c.touch(UP, 1, 10, 5, 1000 + heldMs, up, active, muted, SLOP);
    }

    public static void main(String[] args) {
        // v1 STATE as AgentTrayService sends it (ConversationState.tray*): active is Connecting through End.
        expect("Connecting is active", 1, AgentStatusControls.active(false, false, "Connecting") ? 1 : 0);
        expect("Listening is active", 1, AgentStatusControls.active(true, true, "Listening") ? 1 : 0);
        expect("Muted is active", 1, AgentStatusControls.active(true, false, "Muted") ? 1 : 0);
        expect("Ready is idle", 0, AgentStatusControls.active(false, false, "Ready") ? 1 : 0);
        expect("Disconnected is idle", 0, AgentStatusControls.active(false, false, "Disconnected") ? 1 : 0);
        expect("missing status is idle", 0, AgentStatusControls.active(false, false, null) ? 1 : 0);

        AgentStatusControls c = new AgentStatusControls();
        expect("idle agent tap starts", START, tap(c, AGENT, AGENT, 100, false, false));
        expect("idle mic tap starts", START, tap(c, MIC, MIC, 100, false, false));
        expect("active agent tap ends", END, tap(c, AGENT, AGENT, 100, true, false));
        expect("active mic tap mutes", MUTE, tap(c, MIC, MIC, 100, true, false));
        expect("muted mic tap unmutes", UNMUTE, tap(c, MIC, MIC, 100, true, true));
        expect("tap outside both glows", 0, tap(c, NONE, NONE, 100, true, false));
        expect("down and up on different glows", 0, tap(c, AGENT, MIC, 100, true, false));
        expect("long press is not a tap", 0, tap(c, MIC, MIC, 401, true, false));
        expect("400 ms is still a tap", MUTE, tap(c, MIC, MIC, 400, true, false));

        c.touch(DOWN, 1, 10, 5, 1000, MIC, true, false, SLOP);
        c.touch(MOVE, 1, 10 + SLOP + 1, 5, 1010, MIC, true, false, SLOP);
        expect("drag past slop (to the shade) is not a tap", 0,
                c.touch(UP, 1, 10, 5, 1020, MIC, true, false, SLOP));

        c.touch(DOWN, 1, 10, 5, 1000, MIC, true, false, SLOP);
        c.touch(MOVE, 1, 10 + SLOP, 5 + SLOP, 1010, MIC, true, false, SLOP);
        expect("movement within slop still taps", MUTE, c.touch(UP, 1, 10, 5, 1020, MIC, true, false, SLOP));

        c.touch(DOWN, 1, 10, 5, 1000, AGENT, true, false, SLOP);
        expect("second pointer cancels", 0, c.touch(MOVE, 2, 10, 5, 1010, AGENT, true, false, SLOP));
        expect("up after multi-touch cancel", 0, c.touch(UP, 1, 10, 5, 1020, AGENT, true, false, SLOP));

        c.touch(DOWN, 1, 10, 5, 1000, AGENT, true, false, SLOP);
        expect("cancel event", 0, c.touch(CANCEL, 1, 10, 5, 1010, AGENT, true, false, SLOP));
        expect("up after cancel", 0, c.touch(UP, 1, 10, 5, 1020, AGENT, true, false, SLOP));

        c.touch(DOWN, 1, 10, 5, 1000, AGENT, true, false, SLOP);
        c.cancel();  // keyguard, screen-off or lost binding while the finger is down
        expect("up after external cancel", 0, c.touch(UP, 1, 10, 5, 1020, AGENT, true, false, SLOP));
        expect("stray up without down", 0, c.touch(UP, 1, 10, 5, 1030, AGENT, true, false, SLOP));
        // F08: the chin is available only with the edge switch, user 0 and gesture navigation.
        expect("chin available", 1, AgentStatusControls.chinArg(true, 0, 2));
        expect("switch off", 0, AgentStatusControls.chinArg(false, 0, 2));
        expect("secondary user", 0, AgentStatusControls.chinArg(true, 10, 2));
        expect("three-button navigation", 0, AgentStatusControls.chinArg(true, 0, 0));
        expect("two-button navigation", 0, AgentStatusControls.chinArg(true, 0, 1));
        System.out.println("AgentStatusControlsTest: " + checks + " checks passed");
    }
}
