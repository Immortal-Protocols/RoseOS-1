/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.systemui.roseshade;

import java.util.List;

/**
 * The Rose shade's model (1G) against Designer's F09 Refinement v1 board (sha256 5ebc5f0c…42daf1): grouped,
 * expanded, empty, quick settings and reply states, and draft keeping across the keyboard and the chin; and the F09
 * interaction handoff: back order, dismissal with Undo and Keep draft / Discard, app-acknowledged reply states and
 * the kept scroll place.
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

        // Handoff: back closes the reply, then expanded settings, then the shade.
        m = board();
        m.startReply("alex");
        expect("no reply in a closed shade", null, m.replying());
        m.open();
        m.startReply("cal");
        expect("no reply where the app offers none", null, m.replying());
        m.startReply("alex");
        expect("reply open", "alex", m.replying());
        expect("keyboard first", RoseShadeModel.Back.HIDE_KEYBOARD, m.back(true));
        expect("the reply stays open with the keyboard hidden", "alex", m.replying());
        expect("then the reply closes", RoseShadeModel.Back.CLOSE_REPLY, m.back(false));
        expect("reply closed", null, m.replying());
        expect("then the shade", RoseShadeModel.Back.CLOSE_SHADE, m.back(false));
        m.open();
        m.startReply("alex");
        m.toggleQuickSettings();
        expect("quick settings fold the reply away", null, m.replying());
        expect("so back folds quick settings next", RoseShadeModel.Back.COLLAPSE_QUICK_SETTINGS, m.back(false));
        // A further pull that began in the header expands quick settings and never folds them.
        m.expandQuickSettings();
        expect("pull in the header expands", RoseShadeModel.Mode.QUICK_SETTINGS, m.mode());
        m.expandQuickSettings();
        expect("a second pull keeps them expanded", RoseShadeModel.Mode.QUICK_SETTINGS, m.mode());
        m.close();
        m.expandQuickSettings();
        expect("no pull expansion in a closed shade", RoseShadeModel.Mode.CLOSED, m.mode());
        m.open();
        m.startReply("alex");
        m.remove("alex");
        expect("a removed notification closes its reply", null, m.replying());

        // Handoff: dismiss only dismissible notifications, with Undo; ongoing and protected ones stay.
        m = board();
        m.post(new RoseShadeModel.Item("nav", "com.maps", "Maps", "Navigating", "Turn left", NOW - 2 * MIN,
                null, false, false));
        m.open();
        final java.util.Set<String> clean = new java.util.HashSet<>();
        expect("ongoing stays", RoseShadeModel.Dismiss.REFUSED, m.dismiss("nav", clean));
        expect("ongoing still shown", 5, m.count());
        expect("nothing to undo", null, m.undoLabel());
        expect("a card is dismissible", true, m.canDismiss("cal"));
        expect("dismiss hides with Undo", RoseShadeModel.Dismiss.HIDDEN, m.dismiss("cal", clean));
        expect("hidden from the list", 4, m.count());
        expect("undo label", "Notification dismissed", m.undoLabel());
        expect("nothing cancelled while Undo shows", 0, m.takeCancels().size());
        expect("undo", true, m.undo());
        expect("back after Undo", 5, m.count());
        expect("Calendar back", true, m.entries().stream().anyMatch(e -> e.id.equals("cal")));
        expect("undo twice does nothing", false, m.undo());
        // A collapsed group goes as a whole; a row of an expanded group goes alone.
        expect("collapsed group dismisses both", java.util.Arrays.asList("alex", "sam"),
                m.dismissibleKeys("group:com.messages"));
        m.toggleExpanded("group:com.messages");
        expect("an expanded group is not swiped as a whole", 0, m.dismissibleKeys("group:com.messages").size());
        expect("its row is", java.util.Arrays.asList("sam"), m.dismissibleKeys("sam"));
        final int batch = m.undoBatch();
        m.dismiss("sam", clean);
        expect("one Messages left becomes a card while Undo shows", "Messages", m.entries().stream()
                .filter(e -> e.appLabel.equals("Messages")).findFirst().get().header());
        m.dismiss("rose", clean);
        expect("a second dismissal makes the first final", java.util.Arrays.asList("sam"), m.takeCancels());
        expect("cancels are taken once", 0, m.takeCancels().size());
        m.commit(batch);
        expect("a stale timer changes nothing", "Notification dismissed", m.undoLabel());
        m.commit(m.undoBatch());
        expect("the Undo timer makes it final", java.util.Arrays.asList("rose"), m.takeCancels());
        expect("Undo gone", null, m.undoLabel());
        m.dismiss("cal", clean);
        m.close();
        expect("closing makes a dismissal final", java.util.Arrays.asList("cal"), m.takeCancels());
        m.remove("cal");
        m.remove("rose");
        m.remove("sam");
        expect("the system's removal leaves the rest", 2, m.count());

        // Handoff: a dirty reply asks Keep draft / Discard before the actual dismissal.
        m = board();
        m.open();
        final java.util.Set<String> dirty = new java.util.HashSet<>(java.util.Arrays.asList("alex"));
        expect("dirty asks first", RoseShadeModel.Dismiss.CONFIRM_DRAFT, m.dismiss("group:com.messages", dirty));
        expect("asking on the group", "group:com.messages", m.confirming());
        expect("nothing hidden while asking", 4, m.count());
        m.keepDraft();
        expect("keep draft dismisses nothing", 4, m.count());
        expect("question gone", null, m.confirming());
        m.dismiss("group:com.messages", dirty);
        expect("back answers Keep draft", RoseShadeModel.Back.KEEP_DRAFT, m.back(false));
        expect("still there after back", 4, m.count());
        m.dismiss("group:com.messages", dirty);
        expect("discard goes ahead", RoseShadeModel.Dismiss.HIDDEN, m.discardDraft());
        expect("both hidden", 2, m.count());
        expect("undo after discard", true, m.undo());
        expect("undo brings the group back", "Messages · 2", m.entries().get(1).header());
        m.dismiss("group:com.messages", dirty);
        m.toggleExpanded("group:com.messages");
        expect("expanding the group answers Keep draft", null, m.confirming());
        m.toggleExpanded("group:com.messages");
        m.dismiss("group:com.messages", dirty);
        m.close();
        expect("closing answers Keep draft", null, m.confirming());
        expect("and dismisses nothing", 0, m.takeCancels().size());

        // Handoff: the list keeps its place across updates and reopening.
        m = board();
        m.open();
        expect("top by default", null, m.scrollAnchor());
        m.scrolled("cal", 40);
        m.close();
        m.open();
        expect("kept across reopening", "cal", m.scrollAnchor().entryId);
        expect("offset kept", 40, m.scrollAnchor().offset);
        m.post(new RoseShadeModel.Item("new", "com.mail", "Mail", "Invoice", "", NOW, null, false));
        expect("kept when a new one arrives above", "cal", m.scrollAnchor().entryId);
        m.scrolled("group:com.messages", 10);
        m.remove("sam");
        expect("folded into its app's entry", "alex", m.scrollAnchor().entryId);
        expect("at that entry's top", 0, m.scrollAnchor().offset);
        m.scrolled("alex", 5);
        m.post(new RoseShadeModel.Item("sam", "com.messages", "Messages", "Sam", "Again", NOW, null, true));
        expect("a card that became a group keeps its app's place", "group:com.messages",
                m.scrollAnchor().entryId);
        m.toggleQuickSettings();
        expect("no anchor in quick settings", null, m.scrollAnchor());
        m.toggleQuickSettings();
        m.scrolled("rose", 0);
        m.remove("rose");
        expect("gone app goes to the top", null, m.scrollAnchor());

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

        expect("a typed draft is dirty", true, r.get("alex").dirty());
        expect("a blank draft is not", false, r.get("nobody").dirty());
        expect("send label", "Send", RoseShadeReplies.sendLabel(r.get("alex").status));
        expect("explicit send returns the exact text", "Three works for me.", r.send("alex"));
        final int first = r.get("alex").attempt;
        expect("pending", RoseShadeReplies.Status.PENDING, r.get("alex").status);
        expect("pending line", "Sending…", RoseShadeReplies.statusLine(RoseShadeReplies.Status.PENDING));
        expect("no double send while pending", null, r.send("alex"));
        r.edit("alex", "changed", 0, 0);
        expect("the pending text cannot change", "Three works for me.", r.get("alex").text);
        r.failed("alex", first);
        expect("failed keeps the draft", "Three works for me.", r.get("alex").text);
        expect("failed keeps the selection", 5, r.get("alex").selectionEnd);
        expect("failed", RoseShadeReplies.Status.FAILED, r.get("alex").status);
        expect("failed line", "Couldn't send · Retry", RoseShadeReplies.statusLine(RoseShadeReplies.Status.FAILED));
        expect("retry label", "Retry", RoseShadeReplies.sendLabel(RoseShadeReplies.Status.FAILED));
        expect("still dirty after a failure", true, r.get("alex").dirty());
        r.delivered("alex", first);
        expect("a late delivery cannot overturn the failure", RoseShadeReplies.Status.FAILED, r.get("alex").status);
        r.acknowledged("alex");
        expect("an app update is no acknowledgment of a failed send", RoseShadeReplies.Status.FAILED,
                r.get("alex").status);

        // Handoff: the app's acknowledgment, not the hand-over, makes it Sent.
        expect("retry sends the same text", "Three works for me.", r.send("alex"));
        final int second = r.get("alex").attempt;
        r.failed("alex", first);
        expect("a result for the earlier try changes nothing", RoseShadeReplies.Status.PENDING,
                r.get("alex").status);
        r.delivered("alex", second);
        expect("handed over is still sending", RoseShadeReplies.Status.PENDING, r.get("alex").status);
        expect("no double send while waiting for the app", null, r.send("alex"));
        r.acknowledged("alex");
        expect("sent once the app acknowledges", RoseShadeReplies.Status.SENT, r.get("alex").status);
        expect("sent line", "Sent", RoseShadeReplies.statusLine(RoseShadeReplies.Status.SENT));
        expect("field cleared after the acknowledgment", "", r.get("alex").text);
        expect("a sent reply is not dirty", false, r.get("alex").dirty());
        r.failed("alex", second);
        expect("a late failure cannot overturn the acknowledgment", RoseShadeReplies.Status.SENT,
                r.get("alex").status);
        expect("nothing to resend", null, r.send("alex"));
        r.edit("alex", "One more", 8, 8);
        expect("typing again starts a new draft", RoseShadeReplies.Status.EDITING, r.get("alex").status);

        // An acknowledgment that races ahead of the hand-over result counts once the hand-over lands.
        r.send("alex");
        final int third = r.get("alex").attempt;
        r.acknowledged("alex");
        expect("not sent before the hand-over", RoseShadeReplies.Status.PENDING, r.get("alex").status);
        r.delivered("alex", third);
        expect("sent once handed over", RoseShadeReplies.Status.SENT, r.get("alex").status);
        r.edit("alex", "Still there?", 3, 7);
        r.send("alex");
        final int raced = r.get("alex").attempt;
        r.acknowledged("alex");
        r.failed("alex", raced);
        expect("a refused hand-over is a failure whatever the app posted", RoseShadeReplies.Status.FAILED,
                r.get("alex").status);

        // No acknowledgment in time: neither Sent nor failed; the draft stays for an explicit retry.
        r.send("alex");
        final int quiet = r.get("alex").attempt;
        r.timedOut("alex", quiet);
        expect("no timeout before the hand-over", RoseShadeReplies.Status.PENDING, r.get("alex").status);
        r.delivered("alex", quiet);
        r.timedOut("alex", quiet - 1);
        expect("a stale timer changes nothing", RoseShadeReplies.Status.PENDING, r.get("alex").status);
        r.timedOut("alex", quiet);
        expect("unconfirmed", RoseShadeReplies.Status.UNCONFIRMED, r.get("alex").status);
        expect("unconfirmed line", "Not confirmed · Retry",
                RoseShadeReplies.statusLine(RoseShadeReplies.Status.UNCONFIRMED));
        expect("unconfirmed keeps the draft", "Still there?", r.get("alex").text);
        expect("unconfirmed keeps the selection", 3, r.get("alex").selectionStart);
        expect("retry offered", true, r.canSend("alex"));
        r.acknowledged("alex");
        expect("a late acknowledgment is the truth", RoseShadeReplies.Status.SENT, r.get("alex").status);
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

        // The sideways dismiss swipe: taps and scrolling stay the card's and the list's; a protected card never moves.
        RoseShadeDrag.Swipe s = new RoseShadeDrag.Swipe(10);
        s.down(100, 100);
        expect("within slop the card keeps it", RoseShadeDrag.Swipe.Result.NONE, s.move(108, 100, 1000, true));
        expect("a tap is no swipe", RoseShadeDrag.Swipe.Result.NONE, s.up(0));
        s.down(100, 100);
        expect("a vertical move scrolls", RoseShadeDrag.Swipe.Result.NONE, s.move(130, 160, 1000, true));
        s.cancel();
        s.down(100, 100);
        expect("a protected card never follows", RoseShadeDrag.Swipe.Result.NONE, s.move(400, 100, 1000, false));
        expect("so release does nothing", RoseShadeDrag.Swipe.Result.NONE, s.up(5000));
        s.down(100, 100);
        expect("sideways follows", RoseShadeDrag.Swipe.Result.TRACK, s.move(160, 110, 1000, true));
        expect("offset follows the finger", 60f, s.offset());
        expect("a short slow swipe goes back", RoseShadeDrag.Swipe.Result.RETURN, s.up(100));
        s.down(500, 100);
        s.move(50, 100, 1000, true);
        expect("two fifths across to the left dismisses", RoseShadeDrag.Swipe.Result.DISMISS, s.up(0));
        s.down(100, 100);
        s.move(160, 100, 1000, true);
        expect("a fling dismisses", RoseShadeDrag.Swipe.Result.DISMISS, s.up(2000));
        s.down(100, 100);
        s.move(160, 100, 1000, true);
        expect("a fling back the other way returns", RoseShadeDrag.Swipe.Result.RETURN, s.up(-2000));
        s.down(100, 100);
        s.move(160, 100, 1000, true);
        expect("cancel returns", RoseShadeDrag.Swipe.Result.RETURN, s.cancel());

        System.out.println("RoseShadeTest: " + checks + " checks passed");
    }
}
