/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package dev.pixel.rose.keyboard;

/**
 * RoseOS (1M): one model step applied to the app's real field, relative to the app's cursor. The IME mirrors the text
 * around the cursor (before, selected, after) into a RoseKeys.State; after an operation the model's text differs from
 * the mirror in one contiguous range, and that range always contains the old caret or selection (the diff is anchored
 * there, so repeated characters such as "aa" can never move an edit to the wrong side of the caret). The app then
 * deletes `before` characters before the cursor (or selection) and `after` characters after it, and commits `text`,
 * which replaces any selection and leaves the caret after it, exactly where every model step leaves its caret. No
 * absolute offsets are used, so a selection report that arrives late from the app can never misplace an edit. Pure:
 * no Android types; KeyboardSession applies it through an InputConnection.
 */
public final class FieldEdit {
    /** Characters to delete before the old caret or selection, and after it; then the text to commit. */
    public final int before, after;
    public final String text;

    FieldEdit(int before, int after, String text) {
        this.before = before;
        this.after = after;
        this.text = text;
    }

    /** No text change at all (the old selection, if any, is kept). */
    public boolean none() {
        return before == 0 && after == 0 && text.isEmpty();
    }

    /**
     * The edit that turns the mirror `old` (selection [oldLo, oldHi]) into the model's result `now`, whose caret is
     * `caret`. Committing leaves the caret right after the committed text, so the kept tail is exactly the text after
     * the model's caret (every model step edits only at or before the cursor), and the kept head never reaches past
     * either caret. The replaced range therefore covers [oldLo, oldHi] and the caret lands where the model put it,
     * even among repeated characters; it never splits a surrogate pair.
     */
    public static FieldEdit diff(String old, int oldLo, int oldHi, String now, int caret) {
        int suffix = now.length() - caret;
        if (suffix < 0 || suffix > old.length() - oldHi || !old.endsWith(now.substring(caret))) {
            // Not an edit at the cursor (no model step makes one): replace the whole mirror, caret at its end.
            return new FieldEdit(oldLo, old.length() - oldHi, now);
        }
        int prefix = 0, maxPrefix = Math.min(oldLo, caret);
        while (prefix < maxPrefix && old.charAt(prefix) == now.charAt(prefix)) prefix++;
        if (prefix > 0 && Character.isHighSurrogate(old.charAt(prefix - 1))) prefix--;
        return new FieldEdit(oldLo - prefix, old.length() - suffix - oldHi, now.substring(prefix, caret));
    }
}
