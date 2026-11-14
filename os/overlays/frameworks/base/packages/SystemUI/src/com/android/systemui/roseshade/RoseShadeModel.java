/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.systemui.roseshade;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The Rose notification shade (1G, Designer's F09 Refinement v1), as pure logic for the regression tests.
 *
 * <p>The shade enters from the top and shows, in order, the compact quick settings row, the brightness slider and
 * the owner's notifications. Several notifications from one app form a group ("Messages · 2"). A collapsed group
 * shows one line per notification; an expanded group shows each one with its actions. One notification is a card
 * with its primary action. With no notifications the shade says so ("No notifications", "You're all caught up.").
 * Expanding quick settings turns the row into labelled tiles and folds the notifications into one count row.
 *
 * <p>The F09 interaction handoff adds: back closes the reply, then expanded quick settings, then the shade; only a
 * dismissible notification can be dismissed, with Undo, and one with an unsent draft first asks Keep draft or
 * Discard; ongoing and protected notifications stay; the list keeps its place across updates and reopening.
 *
 * <p>The shade has no microphone control: the Rose chin holds the only one.
 */
public final class RoseShadeModel {
    public static final String EMPTY_TITLE = "No notifications";
    public static final String EMPTY_BODY = "You're all caught up.";

    /** One posted notification, as much of it as the shade shows. */
    public static final class Item {
        public final String key;
        public final String packageName;
        public final String appLabel;
        /** The person or headline ("Alex", "Needs your review"). */
        public final String title;
        public final String text;
        public final long postedAt;
        /** The notification's own first action label, or null ("Review", "Open"). */
        public final String primaryAction;
        /** Whether the notification offers an inline reply (a RemoteInput action). */
        public final boolean canReply;
        /** Whether the owner may dismiss it: not ongoing, not protected from clearing, not a running service. */
        public final boolean dismissible;

        public Item(String key, String packageName, String appLabel, String title, String text, long postedAt,
                String primaryAction, boolean canReply) {
            this(key, packageName, appLabel, title, text, postedAt, primaryAction, canReply, true);
        }

        public Item(String key, String packageName, String appLabel, String title, String text, long postedAt,
                String primaryAction, boolean canReply, boolean dismissible) {
            this.key = key;
            this.packageName = packageName;
            this.appLabel = appLabel;
            this.title = title;
            this.text = text;
            this.postedAt = postedAt;
            this.primaryAction = primaryAction;
            this.canReply = canReply;
            this.dismissible = dismissible;
        }
    }

    /** One row of the list: a single card, or a group of one app's notifications. */
    public static final class Entry {
        public final String id;
        public final String appLabel;
        /** Newest first; one element for a card. */
        public final List<Item> items;
        public final boolean expanded;

        Entry(String id, String appLabel, List<Item> items, boolean expanded) {
            this.id = id;
            this.appLabel = appLabel;
            this.items = items;
            this.expanded = expanded;
        }

        public boolean isGroup() {
            return items.size() > 1;
        }

        /** "Messages · 2" for a group, the app label for a card. */
        public String header() {
            return isGroup() ? appLabel + " · " + items.size() : appLabel;
        }

        long newest() {
            return items.get(0).postedAt;
        }
    }

    public enum Mode { CLOSED, OPEN, QUICK_SETTINGS }

    /** What the back gesture does now. */
    public enum Back { HIDE_KEYBOARD, KEEP_DRAFT, CLOSE_REPLY, COLLAPSE_QUICK_SETTINGS, CLOSE_SHADE, NOTHING }

    /** What a dismissal did. */
    public enum Dismiss {
        /** Nothing there may be dismissed (ongoing or protected); the card stays. */
        REFUSED,
        /** An unsent draft would be lost: the card asks Keep draft or Discard first. */
        CONFIRM_DRAFT,
        /** Hidden, with Undo; the notification is cancelled only once Undo is gone. */
        HIDDEN
    }

    /** Where the list was scrolled: the entry at the top of the view and how far into it. */
    public static final class ScrollAnchor {
        public final String entryId;
        public final int offset;

        ScrollAnchor(String entryId, int offset) {
            this.entryId = entryId;
            this.offset = offset;
        }
    }

    public static final String KEEP_DRAFT = "Keep draft";
    public static final String DISCARD = "Discard";
    public static final String CONFIRM_DRAFT_LINE = "Dismissing deletes your unsent reply.";
    public static final String UNDO = "Undo";

    private final Map<String, Item> mItems = new LinkedHashMap<>();
    private final Set<String> mExpanded = new HashSet<>();
    private Mode mMode = Mode.CLOSED;
    private String mReplying;
    private String mConfirming;
    private final Set<String> mHidden = new LinkedHashSet<>();
    private final List<String> mToCancel = new ArrayList<>();
    private int mUndoBatch;
    private ScrollAnchor mScroll;

    public void post(Item item) {
        mItems.put(item.key, item);
    }

    public void remove(String key) {
        mItems.remove(key);
        mHidden.remove(key);
        mToCancel.remove(key);
        if (key.equals(mReplying)) mReplying = null;
        if (key.equals(mConfirming)) mConfirming = null;
    }

    /** The notifications shown (a dismissal waiting on Undo is not). */
    public int count() {
        return mItems.size() - mHidden.size();
    }

    public boolean isEmpty() {
        return count() == 0;
    }

    /** "2 notifications" in the quick settings view; null when there are none. */
    public String countLabel() {
        final int count = count();
        if (count == 0) return null;
        return count == 1 ? "1 notification" : count + " notifications";
    }

    public Mode mode() {
        return mMode;
    }

    public void open() {
        if (mMode == Mode.CLOSED) mMode = Mode.OPEN;
    }

    /**
     * Closes the shade. The open reply folds (its draft is kept by RoseShadeReplies), a Keep draft / Discard
     * question is answered Keep draft, and a dismissal waiting on Undo becomes final. The scroll place is kept.
     */
    public void close() {
        mMode = Mode.CLOSED;
        mReplying = null;
        mConfirming = null;
        commit(mUndoBatch);
    }

    /** The header chevron or a tile's long press: opens or folds the labelled quick settings. */
    public void toggleQuickSettings() {
        if (mMode == Mode.OPEN) expandQuickSettings();
        else if (mMode == Mode.QUICK_SETTINGS) mMode = Mode.OPEN;
    }

    /** A further pull that began in the header: opens the labelled quick settings, never folds them. */
    public void expandQuickSettings() {
        if (mMode != Mode.OPEN) return;
        mMode = Mode.QUICK_SETTINGS;
        // The notifications fold into a count row; the reply folds with them (its draft is kept).
        mReplying = null;
        mConfirming = null;
    }

    // ---- Replies ----

    /** Reply opens this notification's draft (one at a time). */
    public void startReply(String key) {
        if (mMode == Mode.OPEN && mItems.containsKey(key) && mItems.get(key).canReply) mReplying = key;
    }

    /** The notification whose reply field is open, or null. */
    public String replying() {
        return mReplying;
    }

    /** Expands or collapses a group; a card has nothing to expand. */
    public void toggleExpanded(String entryId) {
        if (!mExpanded.remove(entryId)) mExpanded.add(entryId);
        // A question about the whole group no longer fits its rows (or the reverse): the draft is kept.
        mConfirming = null;
    }

    /**
     * Back hides the keyboard first, then closes the reply (answering a Keep draft / Discard question with Keep
     * draft), then folds quick settings, then closes the shade. It never sends, discards or dismisses anything.
     */
    public Back back(boolean keyboardShowing) {
        if (mMode == Mode.CLOSED) return Back.NOTHING;
        if (keyboardShowing) return Back.HIDE_KEYBOARD;
        if (mConfirming != null) {
            mConfirming = null;
            return Back.KEEP_DRAFT;
        }
        if (mReplying != null) {
            mReplying = null;
            return Back.CLOSE_REPLY;
        }
        if (mMode == Mode.QUICK_SETTINGS) {
            mMode = Mode.OPEN;
            return Back.COLLAPSE_QUICK_SETTINGS;
        }
        mMode = Mode.CLOSED;
        return Back.CLOSE_SHADE;
    }

    // ---- Dismissal ----

    /** The notifications a dismissal of this card, group or group row would cancel: only dismissible ones. */
    public List<String> dismissibleKeys(String id) {
        final List<String> keys = new ArrayList<>();
        for (Entry entry : entries()) {
            for (Item item : entry.items) {
                // A collapsed group goes as a whole; a row inside an expanded group goes alone.
                final boolean named = item.key.equals(id) || (entry.id.equals(id) && !entry.expanded);
                if (named && item.dismissible) keys.add(item.key);
            }
        }
        return keys;
    }

    public boolean canDismiss(String id) {
        return !dismissibleKeys(id).isEmpty();
    }

    /**
     * The owner swiped a card, a collapsed group or a row away. {@code dirty} names the notifications whose reply
     * holds unsent text. Ongoing and protected notifications stay; nothing is cancelled until Undo is gone.
     */
    public Dismiss dismiss(String id, Set<String> dirty) {
        final List<String> keys = dismissibleKeys(id);
        if (keys.isEmpty()) return Dismiss.REFUSED;
        for (String key : keys) {
            if (dirty.contains(key)) {
                mConfirming = id;
                return Dismiss.CONFIRM_DRAFT;
            }
        }
        hide(keys);
        return Dismiss.HIDDEN;
    }

    /** The card, group or row asking Keep draft / Discard, or null. */
    public String confirming() {
        return mConfirming;
    }

    /** Keep draft: nothing is dismissed. */
    public void keepDraft() {
        mConfirming = null;
    }

    /** Discard: the dismissal goes ahead, with Undo (the draft goes only with the notification). */
    public Dismiss discardDraft() {
        final String id = mConfirming;
        mConfirming = null;
        if (id == null) return Dismiss.REFUSED;
        final List<String> keys = dismissibleKeys(id);
        if (keys.isEmpty()) return Dismiss.REFUSED;
        hide(keys);
        return Dismiss.HIDDEN;
    }

    private void hide(List<String> keys) {
        // One Undo at a time: an earlier dismissal becomes final.
        commit(mUndoBatch);
        mHidden.addAll(keys);
        if (mReplying != null && mHidden.contains(mReplying)) mReplying = null;
        mUndoBatch++;
    }

    /** The current Undo, so a timer that outlived it changes nothing. */
    public int undoBatch() {
        return mUndoBatch;
    }

    /** "Notification dismissed" while Undo is offered, or null. */
    public String undoLabel() {
        if (mHidden.isEmpty()) return null;
        return mHidden.size() == 1 ? "Notification dismissed" : mHidden.size() + " notifications dismissed";
    }

    /** Undo: the dismissed notifications come back as they were, drafts included. */
    public boolean undo() {
        if (mHidden.isEmpty()) return false;
        mHidden.clear();
        return true;
    }

    /** Undo timed out (or another dismissal or closing replaced it): the dismissal becomes final. */
    public void commit(int batch) {
        if (batch != mUndoBatch || mHidden.isEmpty()) return;
        mToCancel.addAll(mHidden);
        mHidden.clear();
    }

    /** The notifications to cancel now, each once. */
    public List<String> takeCancels() {
        final List<String> keys = new ArrayList<>(mToCancel);
        mToCancel.clear();
        return keys;
    }

    // ---- Scroll ----

    /** The list scrolled: remember the top entry and the offset into it. */
    public void scrolled(String entryId, int offset) {
        mScroll = entryId == null ? null : new ScrollAnchor(entryId, Math.max(0, offset));
    }

    /** Where to put the list back after an update or a reopening; null for the top. */
    public ScrollAnchor scrollAnchor() {
        if (mScroll == null || mMode == Mode.QUICK_SETTINGS) return null;
        for (Entry entry : entries()) {
            if (entry.id.equals(mScroll.entryId)) return mScroll;
        }
        // The entry is gone (dismissed, or a card that became a group): its app's entry, if still there.
        for (Entry entry : entries()) {
            for (Item item : entry.items) {
                if (mScroll.entryId.equals(item.key) || mScroll.entryId.equals("group:" + item.packageName)) {
                    return new ScrollAnchor(entry.id, 0);
                }
            }
        }
        return null;
    }

    /**
     * The list: one entry per app, groups for two or more notifications, newest entry first. Each group's items
     * are newest first. Empty when there is nothing to show (the shade then draws the empty state).
     */
    public List<Entry> entries() {
        final Map<String, List<Item>> byApp = new LinkedHashMap<>();
        for (Item item : mItems.values()) {
            if (mHidden.contains(item.key)) continue;
            List<Item> list = byApp.get(item.packageName);
            if (list == null) {
                list = new ArrayList<>();
                byApp.put(item.packageName, list);
            }
            list.add(item);
        }
        final List<Entry> entries = new ArrayList<>();
        for (Map.Entry<String, List<Item>> app : byApp.entrySet()) {
            final List<Item> items = app.getValue();
            Collections.sort(items, (a, b) -> Long.compare(b.postedAt, a.postedAt));
            final String id = items.size() > 1 ? "group:" + app.getKey() : items.get(0).key;
            entries.add(new Entry(id, items.get(0).appLabel, Collections.unmodifiableList(items),
                    items.size() > 1 && mExpanded.contains(id)));
        }
        Collections.sort(entries, (a, b) -> Long.compare(b.newest(), a.newest()));
        return entries;
    }

    /** "now", "5m ago", "2h ago", "3d ago": the board's relative times. */
    public static String age(long postedAt, long now) {
        final long minutes = Math.max(0, now - postedAt) / 60_000;
        if (minutes < 1) return "now";
        if (minutes < 60) return minutes + "m ago";
        if (minutes < 24 * 60) return (minutes / 60) + "h ago";
        return (minutes / (24 * 60)) + "d ago";
    }
}
