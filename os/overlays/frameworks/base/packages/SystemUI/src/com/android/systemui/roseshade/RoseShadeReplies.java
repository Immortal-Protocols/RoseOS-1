/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.systemui.roseshade;

import java.util.HashMap;
import java.util.Map;

/**
 * Inline replies in the Rose shade (1G), as pure logic for the regression tests.
 *
 * <p>Each notification keeps its own draft: the text and the selection. The draft survives the keyboard hiding or
 * showing, the Rose chin rising or lowering, and the shade closing and reopening; nothing but an explicit Send (the
 * Send button or the keyboard's Send action) sends it. The states are truthful, and the app's own acknowledgment
 * decides them (F09 interaction handoff):
 * <ul>
 *   <li>{@link Status#PENDING} ("Sending…") from Send until the app has both received the reply and acknowledged
 *       it, by posting its notification again or removing it, as Android's direct-reply contract asks;</li>
 *   <li>{@link Status#SENT} once both happened;</li>
 *   <li>{@link Status#FAILED} ("Couldn't send · Retry") when the app's reply intent was refused or is gone;</li>
 *   <li>{@link Status#UNCONFIRMED} ("Not confirmed · Retry") when the app received the reply but never
 *       acknowledged it in time: the shade cannot know whether it went out, so it claims neither.</li>
 * </ul>
 * A failed or unconfirmed reply keeps its text and selection for another explicit try. A draft goes away only once
 * the app acknowledged it or the notification was removed.
 */
public final class RoseShadeReplies {
    public enum Status { EDITING, PENDING, SENT, FAILED, UNCONFIRMED }

    /** One notification's reply. */
    public static final class Draft {
        public final String text;
        public final int selectionStart;
        public final int selectionEnd;
        public final Status status;
        /** Which Send this state belongs to, so a late result for an earlier Send changes nothing. */
        public final int attempt;
        /** While pending: the app received the reply. */
        final boolean delivered;
        /** While pending: the app acknowledged the reply. */
        final boolean acknowledged;

        Draft(String text, int selectionStart, int selectionEnd, Status status, int attempt, boolean delivered,
                boolean acknowledged) {
            this.text = text;
            this.selectionStart = selectionStart;
            this.selectionEnd = selectionEnd;
            this.status = status;
            this.attempt = attempt;
            this.delivered = delivered;
            this.acknowledged = acknowledged;
        }

        Draft with(Status status, boolean delivered, boolean acknowledged) {
            return new Draft(text, selectionStart, selectionEnd, status, attempt, delivered, acknowledged);
        }

        /** Whether the field holds something the owner would lose by dismissing the notification. */
        public boolean dirty() {
            return !text.trim().isEmpty() && status != Status.SENT;
        }
    }

    private static final Draft NONE = new Draft("", 0, 0, Status.EDITING, 0, false, false);
    private final Map<String, Draft> mDrafts = new HashMap<>();

    public Draft get(String key) {
        final Draft draft = mDrafts.get(key);
        return draft == null ? NONE : draft;
    }

    /** The owner typed or moved the selection. Ignored while a send is pending, so the sent text cannot change. */
    public void edit(String key, String text, int selectionStart, int selectionEnd) {
        final Draft draft = get(key);
        if (draft.status == Status.PENDING) return;
        final int length = text == null ? 0 : text.length();
        final int start = clamp(selectionStart, length);
        final int end = clamp(selectionEnd, length);
        mDrafts.put(key, new Draft(text == null ? "" : text, Math.min(start, end), Math.max(start, end),
                Status.EDITING, draft.attempt, false, false));
    }

    /** Whether Send can be pressed: there is text and nothing is already pending. */
    public boolean canSend(String key) {
        final Draft draft = get(key);
        return draft.status != Status.PENDING && draft.status != Status.SENT && !draft.text.trim().isEmpty();
    }

    /**
     * The owner pressed Send (or Retry). Returns the exact text to deliver, or null when nothing may be sent, which
     * prevents a duplicate while one is pending. The draft is kept, unchanged, until the app acknowledges it.
     */
    public String send(String key) {
        if (!canSend(key)) return null;
        final Draft draft = get(key);
        mDrafts.put(key, new Draft(draft.text, draft.selectionStart, draft.selectionEnd, Status.PENDING,
                draft.attempt + 1, false, false));
        return draft.text;
    }

    /** Android handed the reply to the app. Still "Sending…" until the app acknowledges it. */
    public void delivered(String key, int attempt) {
        final Draft draft = pending(key, attempt);
        if (draft == null) return;
        mDrafts.put(key, draft.acknowledged ? sent(draft) : draft.with(Status.PENDING, true, false));
    }

    /**
     * The app posted this notification again, its direct-reply acknowledgment. Counts only once the reply was
     * handed over; an update that arrives first is remembered until it was.
     */
    public void acknowledged(String key) {
        final Draft draft = get(key);
        if (draft.status == Status.UNCONFIRMED) {
            mDrafts.put(key, sent(draft));
        } else if (draft.status == Status.PENDING) {
            mDrafts.put(key, draft.delivered ? sent(draft) : draft.with(Status.PENDING, false, true));
        }
    }

    /** The app's reply intent was refused: keep the text and its selection so the owner can send again. */
    public void failed(String key, int attempt) {
        final Draft draft = pending(key, attempt);
        if (draft == null) return;
        mDrafts.put(key, draft.with(Status.FAILED, false, false));
    }

    /** The app received the reply but did not acknowledge it in time: claim neither success nor failure. */
    public void timedOut(String key, int attempt) {
        final Draft draft = pending(key, attempt);
        if (draft == null || !draft.delivered) return;
        mDrafts.put(key, draft.with(Status.UNCONFIRMED, false, false));
    }

    /** The keyboard hid or showed, or the chin rose or lowered: nothing about the reply changes. */
    public void inputSurfaceChanged() {}

    /** The notification is gone; so is its draft. */
    public void removed(String key) {
        mDrafts.remove(key);
    }

    /** The line shown under the field, or null while editing. */
    public static String statusLine(Status status) {
        switch (status) {
            case PENDING: return "Sending…";
            case SENT: return "Sent";
            case FAILED: return "Couldn't send · Retry";
            case UNCONFIRMED: return "Not confirmed · Retry";
            default: return null;
        }
    }

    /** The send button's label: Retry after a failed or unconfirmed try. */
    public static String sendLabel(Status status) {
        return status == Status.FAILED || status == Status.UNCONFIRMED ? "Retry" : "Send";
    }

    private Draft pending(String key, int attempt) {
        final Draft draft = get(key);
        return draft.status == Status.PENDING && draft.attempt == attempt ? draft : null;
    }

    private static Draft sent(Draft draft) {
        return new Draft("", 0, 0, Status.SENT, draft.attempt, false, false);
    }

    private static int clamp(int value, int length) {
        return Math.max(0, Math.min(value, length));
    }
}
