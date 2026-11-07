/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package dev.pixel.rose.keyboard;

import android.os.SystemClock;
import android.text.InputType;
import android.view.KeyCharacterMap;
import android.view.KeyEvent;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;

/**
 * RoseOS (1M, F10): one field's keyboard session, the logic of RoseKeyboardService without the service. Each key runs
 * one step of the design's own text model (RoseKeys) on a mirror of the text around the app's cursor and applies the
 * difference to the app's field (FieldEdit). The model's state between keys (shift, page, the last autocorrection and
 * its one-backspace revert, double-space timing) lives here; the text is always the app's. Hiding never sends and
 * never changes the field: the draft is the app's and stays where it is.
 *
 * Fields: the action key takes its label from EditorInfo.imeOptions (Send is the ruby arrow); a multi-line field, or
 * one that asks for no action, gets Return (a new line). Passwords, e-mail addresses, URLs, and fields that ask for
 * no suggestions get no autocorrect, no suggestions and no auto-capitals; number, phone and date fields open on the
 * symbols page. Cut, Copy, Paste, Select all, Undo and Redo are the app's own (performContextMenuAction): this
 * keyboard never reads the clipboard.
 */
public final class KeyboardSession implements RoseKeyboardView.Listener {
    /** The app's current input connection (InputMethodService.getCurrentInputConnection), or null. */
    public interface Connection {
        InputConnection get();
    }

    /** What the service does besides editing. */
    public interface Host {
        void hide();
    }

    /** Text mirrored on each side of the cursor: enough for the model's rules (words, sentence ends). */
    static final int CONTEXT = 512;

    private final Connection connection;
    private final Host host;
    private RoseKeyboardView view;
    private RoseKeys.State model = RoseKeys.initial();
    private int action = EditorInfo.IME_ACTION_NONE;
    private boolean multiline;
    private int selStart = -1, selEnd = -1;
    /** The text this keyboard's last edit left around the cursor; anything else means the user or app moved it. */
    private String expected;

    public KeyboardSession(Connection connection, Host host) {
        this.connection = connection;
        this.host = host;
    }

    public void attach(RoseKeyboardView view) {
        this.view = view;
        if (view != null) view.setAction(actionLabel(action), action == EditorInfo.IME_ACTION_SEND);
        refresh();
    }

    private InputConnection ic() {
        return connection.get();
    }

    /** A field gained the keyboard. */
    public void start(EditorInfo info) {
        int type = info.inputType;
        int cls = type & InputType.TYPE_MASK_CLASS;
        int variation = type & InputType.TYPE_MASK_VARIATION;
        boolean text = cls == InputType.TYPE_CLASS_TEXT;
        boolean plain = !text || isPassword(cls, variation) || variation == InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS
                || variation == InputType.TYPE_TEXT_VARIATION_WEB_EMAIL_ADDRESS
                || variation == InputType.TYPE_TEXT_VARIATION_URI
                || (type & InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS) != 0;
        boolean multiline = text && (type & InputType.TYPE_TEXT_FLAG_MULTI_LINE) != 0;
        int requested = info.imeOptions & EditorInfo.IME_MASK_ACTION;
        boolean noAction = (info.imeOptions & EditorInfo.IME_FLAG_NO_ENTER_ACTION) != 0
                || requested == EditorInfo.IME_ACTION_NONE || requested == EditorInfo.IME_ACTION_UNSPECIFIED;
        this.multiline = multiline;
        action = noAction || multiline ? EditorInfo.IME_ACTION_NONE : requested;
        model = RoseKeys.initial("", multiline);
        model.correctOn = !plain;
        model.autoCapOn = !plain;
        model.page = text ? RoseKeys.LETTERS : RoseKeys.SYM1;
        selStart = info.initialSelStart;
        selEnd = info.initialSelEnd;
        mirror();
        expected = around(model);
        RoseKeys.autoCap(model);
        if (view != null) view.setAction(actionLabel(action), action == EditorInfo.IME_ACTION_SEND);
        refresh();
    }

    static boolean isPassword(int cls, int variation) {
        return cls == InputType.TYPE_CLASS_TEXT && (variation == InputType.TYPE_TEXT_VARIATION_PASSWORD
                || variation == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
                || variation == InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD)
                || cls == InputType.TYPE_CLASS_NUMBER && variation == InputType.TYPE_NUMBER_VARIATION_PASSWORD;
    }

    static String actionLabel(int action) {
        switch (action) {
            case EditorInfo.IME_ACTION_SEND: return "Send";
            case EditorInfo.IME_ACTION_GO: return "Go";
            case EditorInfo.IME_ACTION_SEARCH: return "Search";
            case EditorInfo.IME_ACTION_NEXT: return "Next";
            case EditorInfo.IME_ACTION_PREVIOUS: return "Back";
            case EditorInfo.IME_ACTION_DONE: return "Done";
            default: return "Return";
        }
    }

    /** The app's selection changed (InputMethodService.onUpdateSelection). */
    public void selectionChanged(int newSelStart, int newSelEnd) {
        selStart = newSelStart;
        selEnd = newSelEnd;
        mirror();
        String now = around(model);
        if (!now.equals(expected)) {
            // The user or the app changed the field or moved the cursor: the model's pending revert and typing run
            // end, as the model's setSel does. Our own edits leave exactly the expected text and cursor.
            RoseKeys.setSel(model, model.a, model.b);
            expected = now;
        }
        refresh();
    }

    /**
     * The text next to the cursor, as a comparison key: the last WINDOW characters before it, the selection, and the
     * first WINDOW after it. Fixed-size, so the mirror's own window sliding in a long field never looks like a change.
     */
    static String around(RoseKeys.State s) {
        int lo = Math.min(s.a, s.b), hi = Math.max(s.a, s.b);
        return s.text.substring(Math.max(0, lo - WINDOW), lo) + "\u0000" + s.text.substring(lo, hi) + "\u0000"
                + s.text.substring(hi, Math.min(s.text.length(), hi + WINDOW));
    }

    static final int WINDOW = 64;

    /** The text around the cursor into the model (before, selected, after). */
    private void mirror() {
        InputConnection ic = ic();
        CharSequence before = ic == null ? null : ic.getTextBeforeCursor(CONTEXT, 0);
        CharSequence selected = ic == null ? null : ic.getSelectedText(0);
        CharSequence after = ic == null ? null : ic.getTextAfterCursor(CONTEXT, 0);
        String b = before == null ? "" : before.toString(), s = selected == null ? "" : selected.toString(),
                a = after == null ? "" : after.toString();
        model.text = b + s + a;
        model.a = b.length();
        model.b = b.length() + s.length();
    }

    /**
     * Runs one model step against the app's field and applies the difference relative to the cursor (FieldEdit):
     * delete around the cursor or selection, then commit the new text, in one batch edit.
     */
    private void edit(Runnable step) {
        InputConnection ic = ic();
        if (ic == null) return;
        mirror();
        String old = model.text;
        int lo = Math.min(model.a, model.b), hi = Math.max(model.a, model.b);
        step.run();
        FieldEdit e = FieldEdit.diff(old, lo, hi, model.text, model.a);
        if (!e.none()) {
            ic.beginBatchEdit();
            if (e.before > 0 || e.after > 0) ic.deleteSurroundingText(e.before, e.after);
            ic.commitText(e.text, 1);
            ic.endBatchEdit();
        }
        expected = around(model);
        refresh();
    }

    /** A single-line field with no action: Return is the Enter key, which the app interprets. */
    private void sendEnter(InputConnection ic) {
        long t = SystemClock.uptimeMillis();
        ic.sendKeyEvent(new KeyEvent(t, t, KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_ENTER, 0, 0,
                KeyCharacterMap.VIRTUAL_KEYBOARD, 0, KeyEvent.FLAG_SOFT_KEYBOARD | KeyEvent.FLAG_KEEP_TOUCH_MODE));
        ic.sendKeyEvent(new KeyEvent(t, t, KeyEvent.ACTION_UP, KeyEvent.KEYCODE_ENTER, 0, 0,
                KeyCharacterMap.VIRTUAL_KEYBOARD, 0, KeyEvent.FLAG_SOFT_KEYBOARD | KeyEvent.FLAG_KEEP_TOUCH_MODE));
    }

    private void refresh() {
        if (view == null) return;
        view.setPage(model.page, model.shift);
        view.setEditStrip(selStart >= 0 && selStart != selEnd);
        view.setSuggestions(RoseKeys.suggestions(model));
    }

    // ---------- RoseKeyboardView.Listener ----------

    @Override
    public void onKey(String id) {
        long now = SystemClock.uptimeMillis();
        switch (id) {
            case "shift":
                RoseKeys.shiftTap(model, now);
                refresh();
                return;
            case "?123":
                RoseKeys.setPage(model, RoseKeys.SYM1);
                refresh();
                return;
            case "=\\<":
                RoseKeys.setPage(model, RoseKeys.SYM2);
                refresh();
                return;
            case "ABC":
                mirror();
                RoseKeys.setPage(model, RoseKeys.LETTERS);
                refresh();
                return;
            case "space":
                edit(() -> RoseKeys.space(model, now));
                return;
            case "bksp":
                edit(() -> RoseKeys.backspace(model));
                return;
            case "action": {
                if (multiline) {
                    edit(() -> RoseKeys.enter(model));  // a new line, after any correction
                    return;
                }
                // Never the model's single-line "send": the field's text is the app's to keep or clear.
                edit(() -> RoseKeys.beforeAction(model));
                InputConnection ic = ic();
                if (ic == null) return;
                if (action == EditorInfo.IME_ACTION_NONE) sendEnter(ic);
                else ic.performEditorAction(action);
                return;
            }
            default:
                edit(() -> RoseKeys.type(model, id));
        }
    }

    @Override
    public void onSuggestion(String text) {
        edit(() -> RoseKeys.pick(model, text));
    }

    @Override
    public void onEditAction(int androidId) {
        InputConnection ic = ic();
        if (ic != null) ic.performContextMenuAction(androidId);
    }

    @Override
    public void onCaretMove(int delta) {
        InputConnection ic = ic();
        if (ic == null || selStart < 0) return;
        int to = Math.max(0, Math.min(selStart, selEnd) + delta);
        ic.setSelection(to, to);  // the user moves the cursor: selectionChanged then resets the model's run
    }

    @Override
    public void onHide() {
        host.hide();
    }

    @Override
    public void onDeleteWord() {
        edit(() -> RoseKeys.deleteWord(model));
    }

    /** Tests read the model. */
    RoseKeys.State modelForTest() {
        return model;
    }
}
