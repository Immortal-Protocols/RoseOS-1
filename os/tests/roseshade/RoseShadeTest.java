/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.systemui.roseshade;

import java.util.List;

/**
 * The Rose shade's model (1G) against Designer's F09 Refinement v1 board (sha256 5ebc5f0c…42daf1): grouped,
 * expanded, empty, quick settings and reply states, and draft keeping across the keyboard and the chin.
 * Pure JVM: no Android runtime, SystemUI or device.
 */
public final class RoseShadeTest {
    static int checks;

    static void expect(String name, Object expected, Object actual) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError(name + ": expected " + expected + ", got " + actual);
        }
        checks++;
    }

    static final long NOW = 10L * 60 * 60 * 1000;
    static final long MIN = 60_000;

    /** Board panel 01: Rose (now), Messages · 2 (Alex now, Sam 5m), Calendar (in 20m, posted earlier). */
    static RoseShadeModel board() {
        RoseShadeModel m = new RoseShadeModel();
        m.post(new RoseShadeModel.Item("cal", "com.android.calendar", "Calendar", "Design review", "In 20 minutes",
                NOW - 30 * MIN, "Open", false));
        m.post(new RoseShadeModel.Item("sam", "com.messages", "Messages", "Sam", "Shared a photo", NOW - 5 * MIN,
                null, true));
        m.post(new RoseShadeModel.Item("alex", "com.messages", "Messages", "Alex", "Can we meet at three?",
                NOW - 1 * MIN, "Reply", true));
        m.post(new RoseShadeModel.Item("rose", "dev.pixel.voice", "Rose", "Needs your review", "Draft reply is ready",
                NOW, "Review", false));
        return m;
    }

    public static void main(String[] args) {
        // 01 Grouped: newest entry first, one app's notifications grouped and collapsed, cards keep their action.
        RoseShadeModel m = board();
        m.open();
        List<RoseShadeModel.Entry> entries = m.entries();
        expect("three entries", 3, entries.size());
        expect("Rose first (newest)", "Rose", entries.get(0).header());
        expect("Messages grouped with its count", "Messages · 2", entries.get(1).header());
        expect("group collapsed by default", false, entries.get(1).expanded);
        expect("group items newest first", "Alex", entries.get(1).items.get(0).title);
        expect("Calendar card last", "Calendar", entries.get(2).header());
        expect("a card is not a group", false, entries.get(2).isGroup());
        expect("card keeps its primary action", "Open", entries.get(2).items.get(0).primaryAction);

        // 02 Expanded: a group expands and collapses; a card has nothing to expand.
        m.toggleExpanded(entries.get(1).id);
        expect("group expanded", true, m.entries().get(1).expanded);
        m.toggleExpanded(entries.get(2).id);
        expect("a card never shows as expanded", false, m.entries().get(2).expanded);
        m.toggleExpanded(entries.get(1).id);
        expect("group collapsed again", false, m.entries().get(1).expanded);

        // Relative times as on the board.
        expect("now", "now", RoseShadeModel.age(NOW, NOW));
        expect("5m ago", "5m ago", RoseShadeModel.age(NOW - 5 * MIN, NOW));
        expect("2h ago", "2h ago", RoseShadeModel.age(NOW - 120 * MIN, NOW));
        expect("a clock skew never shows as future", "now", RoseShadeModel.age(NOW + 5 * MIN, NOW));

        // 03 Empty: nothing to list; the shade draws the useful empty state, and quick settings has no count row.
        RoseShadeModel empty = new RoseShadeModel();
        expect("empty list", 0, empty.entries().size());
        expect("empty", true, empty.isEmpty());
        expect("empty title", "No notifications", RoseShadeModel.EMPTY_TITLE);
        expect("empty body", "You're all caught up.", RoseShadeModel.EMPTY_BODY);
        expect("no count row when empty", null, empty.countLabel());
        // A group that loses a notification becomes a card; removing all of them empties the shade.
        m.remove("sam");
        expect("one Messages left becomes a card", "Messages", m.entries().get(1).header());
        m.remove("alex");
        m.remove("rose");
        m.remove("cal");
        expect("all removed is empty", true, m.isEmpty());

        // 04 Quick settings: the mode toggles; notifications fold into a count row.
        m = board();
        expect("closed at rest", RoseShadeModel.Mode.CLOSED, m.mode());
        m.toggleQuickSettings();
        expect("quick settings needs an open shade", RoseShadeModel.Mode.CLOSED, m.mode());
        m.open();
        m.toggleQuickSettings();
        expect("quick settings open", RoseShadeModel.Mode.QUICK_SETTINGS, m.mode());
        expect("count row", "4 notifications", m.countLabel());
        m.remove("rose");
        m.remove("cal");
        m.remove("sam");
        expect("singular count", "1 notification", m.countLabel());

        // Back: keyboard first, then quick settings, then the shade; never a send or a discard.
        m = board();
        m.open();
        m.toggleQuickSettings();
        expect("back hides the keyboard first", RoseShadeModel.Back.HIDE_KEYBOARD, m.back(true));
        expect("shade still in quick settings", RoseShadeModel.Mode.QUICK_SETTINGS, m.mode());
        expect("then folds quick settings", RoseShadeModel.Back.COLLAPSE_QUICK_SETTINGS, m.back(false));
        expect("then closes", RoseShadeModel.Back.CLOSE_SHADE, m.back(false));
        expect("closed", RoseShadeModel.Mode.CLOSED, m.mode());
        expect("nothing when closed", RoseShadeModel.Back.NOTHING, m.back(false));

        // Quick settings tiles: the board's four, no microphone; a state shows only once the system reports it.
        RoseQuickSettings qs = new RoseQuickSettings();
        expect("four tiles", 4, RoseQuickSettings.TILES.size());
        for (RoseQuickSettings.Tile tile : RoseQuickSettings.TILES) {
            if (tile.label.toLowerCase().contains("mic") || tile.label.toLowerCase().contains("mute")) {
                throw new AssertionError("the shade must not duplicate the chin's microphone control: " + tile);
            }
        }
        expect("order", "Wi-Fi", RoseQuickSettings.TILES.get(0).label);
        expect("unknown state shows nothing", "", qs.stateLabel(RoseQuickSettings.Tile.WIFI));
        expect("no toggle before the state is known", null, qs.requestToggle(RoseQuickSettings.Tile.WIFI));
        qs.report(RoseQuickSettings.Tile.WIFI, true);
        expect("Wi-Fi On", "On", qs.stateLabel(RoseQuickSettings.Tile.WIFI));
        expect("a tap requests Off", Boolean.FALSE, qs.requestToggle(RoseQuickSettings.Tile.WIFI));
        expect("the tile waits for the system", "On", qs.stateLabel(RoseQuickSettings.Tile.WIFI));
        qs.report(RoseQuickSettings.Tile.WIFI, false);
        expect("then shows Off", "Off", qs.stateLabel(RoseQuickSettings.Tile.WIFI));
        expect("brightness unknown", -1f, qs.brightness());
        qs.reportBrightness(1.4f);
        expect("brightness clamped", 1f, qs.brightness());

        // 05/06 Reply: the draft and its selection survive the keyboard and the chin; only Send sends.
        RoseShadeReplies r = new RoseShadeReplies();
        expect("empty draft", "", r.get("alex").text);
        expect("cannot send nothing", false, r.canSend("alex"));
        expect("blank is nothing", null, r.send("alex"));
        r.edit("alex", "Three works for me.", 0, 5);
        expect("selection kept", 5, r.get("alex").selectionEnd);
        r.inputSurfaceChanged();  // keyboard hidden
        r.inputSurfaceChanged();  // chin raised (input muted) and lowered
        expect("text survives keyboard and chin", "Three works for me.", r.get("alex").text);
        expect("selection survives", 0, r.get("alex").selectionStart);
        expect("still editing, never sent by hiding", RoseShadeReplies.Status.EDITING, r.get("alex").status);
        expect("no status line while editing", null, RoseShadeReplies.statusLine(r.get("alex").status));
        r.edit("bob", "x", 9, -3);
        expect("selection clamped and ordered", 0, r.get("bob").selectionStart);
        expect("selection clamped end", 1, r.get("bob").selectionEnd);
        // Drafts are per notification.
        expect("drafts are independent", "x", r.get("bob").text);

        expect("explicit send returns the exact text", "Three works for me.", r.send("alex"));
        expect("pending", RoseShadeReplies.Status.PENDING, r.get("alex").status);
        expect("pending line", "Sending…", RoseShadeReplies.statusLine(RoseShadeReplies.Status.PENDING));
        expect("no double send while pending", null, r.send("alex"));
        r.edit("alex", "changed", 0, 0);
        expect("the pending text cannot change", "Three works for me.", r.get("alex").text);
        r.failed("alex");
        expect("failed keeps the draft", "Three works for me.", r.get("alex").text);
        expect("failed", RoseShadeReplies.Status.FAILED, r.get("alex").status);
        expect("failed line", "Not sent. Tap Send to try again.",
                RoseShadeReplies.statusLine(RoseShadeReplies.Status.FAILED));
        r.delivered("alex");
        expect("a late delivery cannot overturn the failure", RoseShadeReplies.Status.FAILED, r.get("alex").status);
        expect("retry sends the same text", "Three works for me.", r.send("alex"));
        r.delivered("alex");
        expect("sent", RoseShadeReplies.Status.SENT, r.get("alex").status);
        expect("sent line", "Sent", RoseShadeReplies.statusLine(RoseShadeReplies.Status.SENT));
        expect("field cleared after delivery", "", r.get("alex").text);
        r.failed("alex");
        expect("a late failure cannot overturn delivery", RoseShadeReplies.Status.SENT, r.get("alex").status);
        r.edit("alex", "One more", 8, 8);
        expect("typing again starts a new draft", RoseShadeReplies.Status.EDITING, r.get("alex").status);
        r.removed("alex");
        expect("removed notification drops its draft", "", r.get("alex").text);

        // The status-bar pull: the status bar keeps taps and sideways moves; the shade takes a downward pull.
        RoseShadeDrag d = new RoseShadeDrag(10);
        d.down(100, 5);
        expect("within slop the status bar keeps it", RoseShadeDrag.Result.NONE, d.move(100, 12, 900));
        expect("a tap stays the status bar's", RoseShadeDrag.Result.NONE, d.up(0));
        d.down(100, 5);
        expect("sideways is never the shade's", RoseShadeDrag.Result.NONE, d.move(160, 30, 900));
        expect("upward is never the shade's", RoseShadeDrag.Result.NONE, d.move(100, -40, 900));
        d.cancel();
        d.down(100, 5);
        expect("a downward pull is the shade's", RoseShadeDrag.Result.TRACK, d.move(105, 100, 900));
        expect("progress follows the finger", true, Math.abs(d.progress() - 95f / 900f) < 1e-4);
        expect("then keeps tracking even sideways", RoseShadeDrag.Result.TRACK, d.move(300, 120, 900));
        expect("a short slow pull goes back", RoseShadeDrag.Result.CANCEL, d.up(200));
        d.down(100, 5);
        d.move(100, 60, 900);
        expect("a short fling opens", RoseShadeDrag.Result.OPEN, d.up(1500));
        d.down(100, 5);
        d.move(100, 400, 900);
        expect("a third of the way opens", RoseShadeDrag.Result.OPEN, d.up(0));
        d.down(100, 5);
        d.move(100, 2000, 900);
        expect("progress is clamped", 1f, d.progress());
        expect("cancel while tracking goes back", RoseShadeDrag.Result.CANCEL, d.cancel());
        expect("cancel when idle does nothing", RoseShadeDrag.Result.NONE, d.cancel());

        System.out.println("RoseShadeTest: " + checks + " checks passed");
    }
}
