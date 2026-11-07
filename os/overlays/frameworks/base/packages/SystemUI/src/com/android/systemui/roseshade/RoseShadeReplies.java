/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.systemui.roseshade;

import java.util.HashMap;
import java.util.Map;

/**
 * Inline replies in the Rose shade (1G), as pure logic for the regression tests.
 *
 * <p>Each notification keeps its own draft: the text and the selection. The draft survives the keyboard hiding or
 * showing, the Rose chin rising or lowering, and the shade closing and reopening; nothing but an explicit Send (the
 * Send button or the keyboard's Send action) sends it. The states are truthful:
 * <ul>
 *   <li>{@link Status#PENDING} from Send until the app's reply intent has been delivered or refused;</li>
 *   <li>{@link Status#SENT} once Android has delivered the reply to the app ("Sent": the shade cannot know whether
 *       the app's own message went out, so it never claims more);</li>
 *   <li>{@link Status#FAILED} when the app's reply intent is gone (cancelled); the draft is kept for another try.</li>
 * </ul>
 * A draft goes away only when its reply was delivered or its notification was removed.
 */
public final class RoseShadeReplies {
    public enum Status { EDITING, PENDING, SENT, FAILED }

    /** One notification's reply. */
    public static final class Draft {
        public final String text;
        public final int selectionStart;
        public final int selectionEnd;
        public final Status status;

        Draft(String text, int selectionStart, int selectionEnd, Status status) {
            this.text = text;
            this.selectionStart = selectionStart;
            this.selectionEnd = selectionEnd;
            this.status = status;
        }
    }

    private static final Draft NONE = new Draft("", 0, 0, Status.EDITING);
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
                Status.EDITING));
    }

    /** Whether Send can be pressed: there is text and nothing is already pending. */
    public boolean canSend(String key) {
        final Draft draft = get(key);
        return draft.status != Status.PENDING && !draft.text.trim().isEmpty();
    }

    /**
     * The owner pressed Send. Returns the exact text to deliver, or null when nothing may be sent. The draft is
     * kept, unchanged, until the delivery result arrives.
     */
    public String send(String key) {
        if (!canSend(key)) return null;
        final Draft draft = get(key);
        mDrafts.put(key, new Draft(draft.text, draft.selectionStart, draft.selectionEnd, Status.PENDING));
        return draft.text;
    }

    /** Android delivered the reply to the app: show Sent and clear the field. */
    public void delivered(String key) {
        if (get(key).status != Status.PENDING) return;
        mDrafts.put(key, new Draft("", 0, 0, Status.SENT));
    }

    /** The app's reply intent was refused: keep the text and its selection so the owner can send again. */
    public void failed(String key) {
        final Draft draft = get(key);
        if (draft.status != Status.PENDING) return;
        mDrafts.put(key, new Draft(draft.text, draft.selectionStart, draft.selectionEnd, Status.FAILED));
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
            case FAILED: return "Not sent. Tap Send to try again.";
            default: return null;
        }
    }

    private static int clamp(int value, int length) {
        return Math.max(0, Math.min(value, length));
    }
}
