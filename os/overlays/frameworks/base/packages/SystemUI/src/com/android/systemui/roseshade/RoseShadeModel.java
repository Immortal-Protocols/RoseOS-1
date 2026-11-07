/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.systemui.roseshade;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
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

        public Item(String key, String packageName, String appLabel, String title, String text, long postedAt,
                String primaryAction, boolean canReply) {
            this.key = key;
            this.packageName = packageName;
            this.appLabel = appLabel;
            this.title = title;
            this.text = text;
            this.postedAt = postedAt;
            this.primaryAction = primaryAction;
            this.canReply = canReply;
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
    public enum Back { HIDE_KEYBOARD, COLLAPSE_QUICK_SETTINGS, CLOSE_SHADE, NOTHING }

    private final Map<String, Item> mItems = new LinkedHashMap<>();
    private final Set<String> mExpanded = new HashSet<>();
    private Mode mMode = Mode.CLOSED;

    public void post(Item item) {
        mItems.put(item.key, item);
    }

    public void remove(String key) {
        mItems.remove(key);
    }

    public int count() {
        return mItems.size();
    }

    public boolean isEmpty() {
        return mItems.isEmpty();
    }

    /** "2 notifications" in the quick settings view; null when there are none. */
    public String countLabel() {
        if (mItems.isEmpty()) return null;
        return mItems.size() == 1 ? "1 notification" : mItems.size() + " notifications";
    }

    public Mode mode() {
        return mMode;
    }

    public void open() {
        if (mMode == Mode.CLOSED) mMode = Mode.OPEN;
    }

    public void close() {
        mMode = Mode.CLOSED;
    }

    public void toggleQuickSettings() {
        if (mMode == Mode.OPEN) mMode = Mode.QUICK_SETTINGS;
        else if (mMode == Mode.QUICK_SETTINGS) mMode = Mode.OPEN;
    }

    /** Expands or collapses a group; a card has nothing to expand. */
    public void toggleExpanded(String entryId) {
        if (!mExpanded.remove(entryId)) mExpanded.add(entryId);
    }

    /** Back hides the keyboard first, then folds quick settings, then closes; it never sends or discards a reply. */
    public Back back(boolean keyboardShowing) {
        if (mMode == Mode.CLOSED) return Back.NOTHING;
        if (keyboardShowing) return Back.HIDE_KEYBOARD;
        if (mMode == Mode.QUICK_SETTINGS) {
            mMode = Mode.OPEN;
            return Back.COLLAPSE_QUICK_SETTINGS;
        }
        mMode = Mode.CLOSED;
        return Back.CLOSE_SHADE;
    }

    /**
     * The list: one entry per app, groups for two or more notifications, newest entry first. Each group's items
     * are newest first. Empty when there is nothing to show (the shade then draws the empty state).
     */
    public List<Entry> entries() {
        final Map<String, List<Item>> byApp = new LinkedHashMap<>();
        for (Item item : mItems.values()) {
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
