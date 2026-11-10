/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package dev.pixel.rose.keyboard;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.HapticFeedbackConstants;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowInsets;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityNodeProvider;

import java.util.ArrayList;
import java.util.List;

/**
 * RoseOS (1M, F10): the Rose keyboard as drawn, per the selected design's HANDOFF (818a032). From the top: a 44dp
 * suggestion strip (three slots and the hide control), or, while the app has a selection, the edit strip (Cut, Copy,
 * Paste | Select all | Undo, Redo); then four 46dp key rows with 8dp row gaps and 6dp key gaps, inside 8dp padding:
 * 268dp in all. No number row and no microphone key: voice and microphone-input mute stay on the Rose chin. The 24dp
 * rail under the space row is the system gesture area (Home and the chin), drawn by the system, never by this view.
 * While the Rose chin is raised it provides a systemOverlays() inset (agreed with OS Core); whatever of it reaches past
 * the navigation bar's (34dp under the 58dp chin) is added below the space row, so the keys ride up onto the chin's
 * concave top. Lowered, or where the keyboard already sits above the chin, that is 0 and nothing is added.
 *
 * Touch: a key commits on release, wherever the finger ends (sliding to another key before release types that key);
 * with two thumbs the earlier key commits when the next one lands. A press shows a preview above the key. Holding
 * delete repeats, then deletes whole words after about a second; a swipe left from delete removes a word. Dragging
 * the space bar moves the caret (the keys dim). A swipe down on the strip, or the hide control, hides the keyboard.
 * Every key and strip slot is a virtual accessibility node with a label.
 */
public final class RoseKeyboardView extends View {
    /** What the service does for the view. */
    public interface Listener {
        void onKey(String id);

        void onSuggestion(String text);

        void onEditAction(int androidId);

        void onCaretMove(int delta);

        void onHide();

        void onDeleteWord();
    }

    // Geometry (dp, at the 384dp design width).
    static final float STRIP_DP = 44, KEY_DP = 46, ROW_GAP_DP = 8, KEY_GAP_DP = 6, PAD_DP = 8;
    static final float TOTAL_DP = STRIP_DP + 4 * KEY_DP + 3 * ROW_GAP_DP + 2 * PAD_DP;  // 268
    static final float HIDE_DP = 44, CORNER_DP = 8;
    // Visual tokens.
    static final int KEY_TOP = 0xFF3A2A2E, KEY_BOTTOM = 0xFF2C1F22, FUNCTION = 0xFF1E1417, BOARD_TOP = 0xFF1D1114,
            BOARD_BOTTOM = 0xFF150C0F, ACTION_TOP = 0xFFC8304A, ACTION_BOTTOM = 0xFF97182E, TEXT = 0xFFF2E6D9,
            CORRECTION = 0xFFE04A62;
    static final long REPEAT_START_MS = 400, REPEAT_MS = 60, WORD_REPEAT_AFTER_MS = 1000;

    /** One laid-out key. */
    static final class Key {
        final String id;
        final RectF bounds = new RectF();
        final boolean function;

        Key(String id) {
            this.id = id;
            function = id.length() > 1;
        }
    }

    private final Listener listener;
    private final float density;
    private final Paint board = new Paint(), keyPaint = new Paint(Paint.ANTI_ALIAS_FLAG),
            fnPaint = new Paint(Paint.ANTI_ALIAS_FLAG), actionPaint = new Paint(Paint.ANTI_ALIAS_FLAG),
            label = new Paint(Paint.ANTI_ALIAS_FLAG), small = new Paint(Paint.ANTI_ALIAS_FLAG),
            strip = new Paint(Paint.ANTI_ALIAS_FLAG), underline = new Paint(Paint.ANTI_ALIAS_FLAG),
            glyph = new Paint(Paint.ANTI_ALIAS_FLAG), preview = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final List<Key> keys = new ArrayList<>();
    private final RectF[] slots = {new RectF(), new RectF(), new RectF()};
    private final RectF hide = new RectF();
    private final List<RectF> editRects = new ArrayList<>();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final android.graphics.Matrix rowShift = new android.graphics.Matrix();

    // State the service sets.
    private String page = RoseKeys.LETTERS, shift = RoseKeys.OFF, actionLabel = "Return";
    private boolean actionIsSend, editStrip;
    private List<RoseKeys.Suggestion> suggestions = new ArrayList<>();

    // Touch state: per pointer, the key it is on.
    private final android.util.SparseArray<Key> down = new android.util.SparseArray<>();
    private final android.util.SparseArray<float[]> downAt = new android.util.SparseArray<>();
    private int spacePointer = -1;
    private float spaceAnchorX;
    private boolean caretMoving;
    /** The pointer that went down on the strip (a tap or a swipe down there), or -1; only its own release acts. */
    private int stripPointer = -1;
    private float stripDownY;
    private long deleteDownAt;
    private boolean deleteRepeated;

    static final String[] EDIT_LABELS = {"Cut", "Copy", "Paste", "Select all", "Undo", "Redo"};
    static final int[] EDIT_IDS = {android.R.id.cut, android.R.id.copy, android.R.id.paste, android.R.id.selectAll,
            android.R.id.undo, android.R.id.redo};

    public RoseKeyboardView(Context context, Listener listener) {
        super(context);
        this.listener = listener;
        density = context.getResources().getDisplayMetrics().density;
        keyPaint.setStyle(Paint.Style.FILL);
        fnPaint.setColor(FUNCTION);
        label.setColor(TEXT);
        label.setTextAlign(Paint.Align.CENTER);
        label.setTextSize(22 * density);
        small.setColor(TEXT);
        small.setTextAlign(Paint.Align.CENTER);
        small.setTextSize(14 * density);
        strip.setColor(TEXT);
        strip.setTextAlign(Paint.Align.CENTER);
        strip.setTextSize(16 * density);
        underline.setColor(CORRECTION);
        underline.setStrokeWidth(2 * density);
        glyph.setColor(TEXT);
        glyph.setStyle(Paint.Style.STROKE);
        glyph.setStrokeWidth(2 * density);
        glyph.setStrokeCap(Paint.Cap.ROUND);
        glyph.setStrokeJoin(Paint.Join.ROUND);
        preview.setColor(KEY_TOP);
        setContentDescription("Rose keyboard");
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);
    }

    // ---------- state from the service ----------

    public void setPage(String page, String shift) {
        boolean relayout = !page.equals(this.page);
        this.page = page;
        this.shift = shift;
        if (relayout) layoutKeys(getWidth());
        invalidate();
    }

    public void setAction(String label, boolean send) {
        actionLabel = label;
        actionIsSend = send;
        invalidate();
    }

    public void setSuggestions(List<RoseKeys.Suggestion> list) {
        suggestions = list == null ? new ArrayList<>() : list;
        invalidate();
    }

    /** While the app has a selection the strip shows the edit actions instead of suggestions. */
    public void setEditStrip(boolean on) {
        if (editStrip == on) return;
        editStrip = on;
        layoutStrip(getWidth());
        invalidate();
    }

    // ---------- layout ----------

    @Override
    protected void onMeasure(int widthSpec, int heightSpec) {
        setMeasuredDimension(MeasureSpec.getSize(widthSpec), Math.round(TOTAL_DP * density) + chinLiftPx);
    }

    /** Below the keys: the raised chin's inset beyond the navigation bar's, or 0. */
    private int chinLiftPx;

    @Override
    public WindowInsets onApplyWindowInsets(WindowInsets insets) {
        // Both bottoms from the window's own insets, before any parent in InputMethodService's frame consumed one:
        // the subtraction needs navigationBars() and systemOverlays() measured against the same frame.
        WindowInsets root = getRootWindowInsets();
        int lift = chinLiftPx(root != null ? root : insets);
        if (lift != chinLiftPx) {
            chinLiftPx = lift;
            requestLayout();
        }
        return super.onApplyWindowInsets(insets);
    }

    /** max(0, systemOverlays().bottom - navigationBars().bottom): what of the raised chin the keys must clear. */
    static int chinLiftPx(WindowInsets insets) {
        if (insets == null) return 0;
        return Math.max(0, insets.getInsets(WindowInsets.Type.systemOverlays()).bottom
                - insets.getInsets(WindowInsets.Type.navigationBars()).bottom);
    }

    @Override
    protected void onSizeChanged(int w, int h, int oldw, int oldh) {
        layoutKeys(w);
        layoutStrip(w);
        board.setShader(new LinearGradient(0, 0, 0, h, BOARD_TOP, BOARD_BOTTOM, Shader.TileMode.CLAMP));
        keyPaint.setShader(new LinearGradient(0, 0, 0, KEY_DP * density, KEY_TOP, KEY_BOTTOM, Shader.TileMode.CLAMP));
        actionPaint.setShader(new LinearGradient(0, 0, 0, KEY_DP * density, ACTION_TOP, ACTION_BOTTOM,
                Shader.TileMode.CLAMP));
    }

    /** Width units per key: 1 for a letter; shift, delete, the page keys and the action key 1.5; space 5. */
    static float units(String id) {
        switch (id) {
            case "shift": case "bksp": case "?123": case "ABC": case "=\\<": case "action": return 1.5f;
            case "space": return 5f;
            default: return 1f;
        }
    }

    void layoutKeys(int width) {
        keys.clear();
        if (width <= 0) return;
        float pad = PAD_DP * density, gap = KEY_GAP_DP * density, keyH = KEY_DP * density;
        float unit = (width - 2 * pad - 9 * gap) / 10f + gap;  // one key plus its gap
        float top = STRIP_DP * density + pad;
        List<List<String>> rows = RoseKeys.LAYOUTS.get(page);
        for (int r = 0; r < rows.size(); r++) {
            List<String> row = rows.get(r);
            float total = 0;
            for (String id : row) total += units(id);
            float x = pad + (10 - total) * unit / 2f;  // a shorter row is centred (the home row)
            for (String id : row) {
                Key k = new Key(id);
                float w = units(id) * unit - gap;
                k.bounds.set(x, top, x + w, top + keyH);
                keys.add(k);
                x += units(id) * unit;
            }
            top += keyH + ROW_GAP_DP * density;
        }
    }

    void layoutStrip(int width) {
        float h = STRIP_DP * density, hideW = HIDE_DP * density;
        hide.set(width - hideW, 0, width, h);
        float w = (width - hideW) / 3f;
        for (int i = 0; i < 3; i++) slots[i].set(i * w, 0, (i + 1) * w, h);
        editRects.clear();
        // Content-width edit actions, grouped Cut Copy Paste | Select all | Undo Redo, 44dp hit areas.
        float x = 8 * density;
        for (int i = 0; i < EDIT_LABELS.length; i++) {
            float tw = small.measureText(EDIT_LABELS[i]) + 20 * density;
            if (i == 3 || i == 4) x += 10 * density;
            editRects.add(new RectF(x, 0, x + tw, h));
            x += tw;
        }
    }

    Key keyAt(float x, float y) {
        Key best = null;
        float bestD = Float.MAX_VALUE;
        if (y < STRIP_DP * density) return null;
        for (Key k : keys) {
            if (k.bounds.contains(x, y)) return k;
            // Gaps belong to the nearest key, so a touch between keys still types.
            float dx = Math.max(0, Math.max(k.bounds.left - x, x - k.bounds.right));
            float dy = Math.max(0, Math.max(k.bounds.top - y, y - k.bounds.bottom));
            float d = dx * dx + dy * dy;
            if (d < bestD) {
                bestD = d;
                best = k;
            }
        }
        return bestD <= Math.pow(KEY_GAP_DP * density, 2) * 4 ? best : null;
    }

    // ---------- drawing ----------

    String keyLabel(String id) {
        switch (id) {
            case "space": return "";
            case "action": return actionIsSend ? "" : actionLabel;
            case "shift": case "bksp": return "";
            default:
                return RoseKeys.LETTERS.equals(page) && id.length() == 1 && !RoseKeys.OFF.equals(shift)
                        ? id.toUpperCase(java.util.Locale.ROOT) : id;
        }
    }

    @Override
    protected void onDraw(Canvas canvas) {
        canvas.drawRect(0, 0, getWidth(), getHeight(), board);
        drawStrip(canvas);
        float r = CORNER_DP * density;
        int dim = caretMoving ? 0x66 : 0xFF;
        for (Key k : keys) {
            boolean pressed = indexOf(k) >= 0;
            Paint fill = "action".equals(k.id) ? actionPaint : k.function && !"space".equals(k.id) ? fnPaint : keyPaint;
            fill.setAlpha(pressed ? 0xCC : dim);
            // Each key's own top-to-bottom gradient (the design's key tokens), whatever its row.
            if (fill.getShader() != null) {
                rowShift.setTranslate(0, k.bounds.top);
                fill.getShader().setLocalMatrix(rowShift);
            }
            canvas.drawRoundRect(k.bounds, r, r, fill);
            fill.setAlpha(0xFF);
            label.setAlpha(dim);
            small.setAlpha(dim);
            glyph.setAlpha(dim);
            String text = keyLabel(k.id);
            float cy = k.bounds.centerY();
            if ("shift".equals(k.id)) drawShift(canvas, k.bounds);
            else if ("bksp".equals(k.id)) drawDelete(canvas, k.bounds);
            else if ("action".equals(k.id) && actionIsSend) drawSend(canvas, k.bounds);
            else if (text.length() > 1) canvas.drawText(text, k.bounds.centerX(), cy + small.getTextSize() / 3, small);
            else if (!text.isEmpty()) canvas.drawText(text, k.bounds.centerX(), cy + label.getTextSize() / 3, label);
        }
        label.setAlpha(0xFF);
        small.setAlpha(0xFF);
        glyph.setAlpha(0xFF);
        // The key preview pops above each pressed character key.
        for (int i = 0; i < down.size(); i++) {
            Key k = down.valueAt(i);
            if (k == null || k.function) continue;
            RectF p = new RectF(k.bounds.left - 4 * density, k.bounds.top - KEY_DP * density - 10 * density,
                    k.bounds.right + 4 * density, k.bounds.top - 6 * density);
            p.offset(0, Math.max(0, -p.top));
            canvas.drawRoundRect(p, r, r, preview);
            canvas.drawText(keyLabel(k.id), p.centerX(), p.centerY() + label.getTextSize() / 3, label);
        }
    }

    private void drawStrip(Canvas canvas) {
        float h = STRIP_DP * density;
        if (editStrip) {
            for (int i = 0; i < editRects.size(); i++) {
                RectF e = editRects.get(i);
                canvas.drawText(EDIT_LABELS[i], e.centerX(), h / 2 + small.getTextSize() / 3, small);
            }
        } else {
            for (int i = 0; i < suggestions.size() && i < 3; i++) {
                RoseKeys.Suggestion sg = suggestions.get(i);
                // Slot order: typed (quoted) | best (bold, middle) | next; a lone word sits in the middle.
                int slot = suggestions.size() == 1 ? 1 : i;
                RectF b = slots[slot];
                boolean typed = "typed".equals(sg.kind);
                strip.setTypeface(slot == 1 ? Typeface.DEFAULT_BOLD : Typeface.DEFAULT);
                String t = typed ? "“" + sg.text + "”" : sg.text;
                float y = h / 2 + strip.getTextSize() / 3;
                canvas.drawText(t, b.centerX(), y, strip);
                if ("correct".equals(sg.kind)) {
                    float half = strip.measureText(t) / 2;
                    canvas.drawLine(b.centerX() - half, y + 4 * density, b.centerX() + half, y + 4 * density, underline);
                }
            }
        }
        // Hide (⌄) at the strip's right end.
        float cx = hide.centerX(), cy = hide.centerY(), s = 6 * density;
        Path chevron = new Path();
        chevron.moveTo(cx - s, cy - s / 2);
        chevron.lineTo(cx, cy + s / 2);
        chevron.lineTo(cx + s, cy - s / 2);
        canvas.drawPath(chevron, glyph);
    }

    private void drawShift(Canvas canvas, RectF b) {
        float cx = b.centerX(), cy = b.centerY(), s = 8 * density;
        Path arrow = new Path();
        arrow.moveTo(cx, cy - s);
        arrow.lineTo(cx + s, cy);
        arrow.lineTo(cx + s / 2, cy);
        arrow.lineTo(cx + s / 2, cy + s);
        arrow.lineTo(cx - s / 2, cy + s);
        arrow.lineTo(cx - s / 2, cy);
        arrow.lineTo(cx - s, cy);
        arrow.close();
        Paint p = new Paint(glyph);
        if (!RoseKeys.OFF.equals(shift)) p.setStyle(Paint.Style.FILL_AND_STROKE);
        canvas.drawPath(arrow, p);
        if (RoseKeys.LOCK.equals(shift)) canvas.drawLine(cx - s / 2, cy + s + 4 * density, cx + s / 2, cy + s + 4 * density, glyph);
    }

    private void drawDelete(Canvas canvas, RectF b) {
        float cx = b.centerX(), cy = b.centerY(), s = 7 * density;
        Path p = new Path();
        p.moveTo(cx - 2 * s, cy);
        p.lineTo(cx - s, cy - s);
        p.lineTo(cx + 1.5f * s, cy - s);
        p.lineTo(cx + 1.5f * s, cy + s);
        p.lineTo(cx - s, cy + s);
        p.close();
        canvas.drawPath(p, glyph);
        canvas.drawLine(cx - s / 3, cy - s / 2, cx + s * 0.8f, cy + s / 2, glyph);
        canvas.drawLine(cx + s * 0.8f, cy - s / 2, cx - s / 3, cy + s / 2, glyph);
    }

    private void drawSend(Canvas canvas, RectF b) {
        float cx = b.centerX(), cy = b.centerY(), s = 8 * density;
        canvas.drawLine(cx - s, cy, cx + s, cy, glyph);
        canvas.drawLine(cx + s, cy, cx + s / 3, cy - s * 0.7f, glyph);
        canvas.drawLine(cx + s, cy, cx + s / 3, cy + s * 0.7f, glyph);
    }

    private int indexOf(Key k) {
        for (int i = 0; i < down.size(); i++) if (down.valueAt(i) == k) return i;
        return -1;
    }

    // ---------- touch ----------

    private final Runnable deleteRepeat = new Runnable() {
        @Override
        public void run() {
            deleteRepeated = true;
            if (SystemClock.uptimeMillis() - deleteDownAt > WORD_REPEAT_AFTER_MS) listener.onDeleteWord();
            else listener.onKey("bksp");
            handler.postDelayed(this, REPEAT_MS);
        }
    };

    @Override
    public boolean onTouchEvent(MotionEvent e) {
        int action = e.getActionMasked();
        int index = e.getActionIndex();
        int id = e.getPointerId(index);
        float x = e.getX(index), y = e.getY(index);
        switch (action) {
            case MotionEvent.ACTION_DOWN:
            case MotionEvent.ACTION_POINTER_DOWN: {
                // A new gesture's pointer ids start again at 0: a strip finger from an unfinished one is gone.
                if (action == MotionEvent.ACTION_DOWN) stripPointer = -1;
                if (y < STRIP_DP * density) {
                    stripPointer = id;
                    stripDownY = y;
                    return true;
                }
                // Rollover: the earlier key commits as the next one lands.
                for (int i = down.size() - 1; i >= 0; i--) {
                    Key earlier = down.valueAt(i);
                    if (earlier != null && !"space".equals(earlier.id) && !"bksp".equals(earlier.id)) {
                        commit(earlier);
                        down.removeAt(i);
                    }
                }
                Key k = keyAt(x, y);
                down.put(id, k);
                downAt.put(id, new float[] {x, y});
                if (k != null) {
                    performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP);
                    sendHover(k);
                    if ("bksp".equals(k.id)) {
                        deleteDownAt = SystemClock.uptimeMillis();
                        deleteRepeated = false;
                        handler.postDelayed(deleteRepeat, REPEAT_START_MS);
                    } else if ("space".equals(k.id)) {
                        spacePointer = id;
                        spaceAnchorX = x;
                        caretMoving = false;
                    }
                }
                invalidate();
                return true;
            }
            case MotionEvent.ACTION_MOVE: {
                for (int i = 0; i < e.getPointerCount(); i++) {
                    int pid = e.getPointerId(i);
                    float px = e.getX(i), py = e.getY(i);
                    Key k = down.get(pid);
                    if (pid == spacePointer) {
                        float step = 10 * density;
                        if (!caretMoving && Math.abs(px - spaceAnchorX) > 2 * step) caretMoving = true;
                        if (caretMoving) {
                            int delta = (int) ((px - spaceAnchorX) / step);
                            if (delta != 0) {
                                listener.onCaretMove(delta);
                                spaceAnchorX += delta * step;
                            }
                            invalidate();
                        }
                        continue;
                    }
                    if (k != null && "bksp".equals(k.id)) continue;
                    // Slide to another key before release types that key.
                    Key now = keyAt(px, py);
                    if (now != null && now != k && down.indexOfKey(pid) >= 0) {
                        down.put(pid, now);
                        invalidate();
                    }
                }
                return true;
            }
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_POINTER_UP: {
                if (id == stripPointer) {
                    // Only the strip's own finger taps or swipes it: a key finger lifting meanwhile types its key.
                    stripPointer = -1;
                    if (y - stripDownY > 24 * density) listener.onHide();
                    else if (Math.abs(y - stripDownY) < 12 * density) tapStrip(x);
                    return true;
                }
                Key k = down.get(id);
                float[] at = downAt.get(id);
                down.remove(id);
                downAt.remove(id);
                if (k != null) {
                    if ("bksp".equals(k.id)) {
                        handler.removeCallbacks(deleteRepeat);
                        // A swipe left from delete removes a word.
                        if (at != null && at[0] - x > 40 * density) listener.onDeleteWord();
                        else if (!deleteRepeated) commit(k);
                    } else if ("space".equals(k.id) && id == spacePointer) {
                        if (!caretMoving) commit(k);
                        spacePointer = -1;
                        caretMoving = false;
                    } else {
                        commit(k);
                    }
                }
                invalidate();
                return true;
            }
            case MotionEvent.ACTION_CANCEL: {
                handler.removeCallbacks(deleteRepeat);
                down.clear();
                downAt.clear();
                spacePointer = -1;
                caretMoving = false;
                stripPointer = -1;
                invalidate();
                return true;
            }
            default:
                return true;
        }
    }

    private void commit(Key k) {
        listener.onKey(k.id);
    }

    private void tapStrip(float x) {
        if (hide.contains(x, STRIP_DP * density / 2)) {
            listener.onHide();
            return;
        }
        if (editStrip) {
            for (int i = 0; i < editRects.size(); i++) {
                if (x >= editRects.get(i).left && x < editRects.get(i).right) {
                    listener.onEditAction(EDIT_IDS[i]);
                    return;
                }
            }
            return;
        }
        for (int i = 0; i < suggestions.size() && i < 3; i++) {
            int slot = suggestions.size() == 1 ? 1 : i;
            if (x >= slots[slot].left && x < slots[slot].right) {
                listener.onSuggestion(suggestions.get(i).text);
                return;
            }
        }
    }

    @Override
    protected void onDetachedFromWindow() {
        handler.removeCallbacks(deleteRepeat);
        super.onDetachedFromWindow();
    }

    // ---------- accessibility ----------

    /** Spoken labels; a character key speaks its character. */
    String spoken(String id) {
        switch (id) {
            case "shift": return RoseKeys.LOCK.equals(shift) ? "Caps lock on" : RoseKeys.ONCE.equals(shift) ? "Shift on" : "Shift";
            case "bksp": return "Delete";
            case "space": return "Space";
            case "action": return actionIsSend ? "Send" : actionLabel;
            case "?123": return "Symbols";
            case "ABC": return "Letters";
            case "=\\<": return "More symbols";
            default: return keyLabel(id);
        }
    }

    private static final int HIDE_ID = 1000, SLOT_ID = 2000, EDIT_ID = 3000;
    private int hovered = View.NO_ID;

    private void sendHover(Key k) {
        int vid = keys.indexOf(k);
        if (vid < 0) return;
        AccessibilityEvent ev = new AccessibilityEvent(AccessibilityEvent.TYPE_VIEW_HOVER_ENTER);
        ev.setSource(this, vid);
        ev.setPackageName(getContext().getPackageName());
        ev.setClassName(android.widget.Button.class.getName());
        ev.getText().add(spoken(k.id));
        if (getParent() != null) getParent().requestSendAccessibilityEvent(this, ev);
    }

    @Override
    public boolean onHoverEvent(MotionEvent e) {
        Key k = keyAt(e.getX(), e.getY());
        int vid = k == null ? View.NO_ID : keys.indexOf(k);
        if (vid != hovered) {
            hovered = vid;
            if (k != null) sendHover(k);
        }
        if (e.getActionMasked() == MotionEvent.ACTION_HOVER_EXIT && k != null) {
            // Explore by touch: lifting the finger on a key types it.
            listener.onKey(k.id);
        }
        return true;
    }

    private final AccessibilityNodeProvider nodes = new AccessibilityNodeProvider() {
        @Override
        public AccessibilityNodeInfo createAccessibilityNodeInfo(int vid) {
            if (vid == View.NO_ID) {
                AccessibilityNodeInfo root = new AccessibilityNodeInfo(RoseKeyboardView.this);
                onInitializeAccessibilityNodeInfo(root);
                for (int i = 0; i < keys.size(); i++) root.addChild(RoseKeyboardView.this, i);
                root.addChild(RoseKeyboardView.this, HIDE_ID);
                if (editStrip) {
                    for (int i = 0; i < EDIT_LABELS.length; i++) root.addChild(RoseKeyboardView.this, EDIT_ID + i);
                } else {
                    for (int i = 0; i < Math.min(3, suggestions.size()); i++) root.addChild(RoseKeyboardView.this, SLOT_ID + i);
                }
                return root;
            }
            RectF b;
            String text;
            if (vid == HIDE_ID) {
                b = hide;
                text = "Hide keyboard";
            } else if (vid >= EDIT_ID && vid < EDIT_ID + EDIT_LABELS.length && editStrip) {
                b = editRects.get(vid - EDIT_ID);
                text = EDIT_LABELS[vid - EDIT_ID];
            } else if (vid >= SLOT_ID && vid < SLOT_ID + 3 && !editStrip && vid - SLOT_ID < suggestions.size()) {
                b = slots[suggestions.size() == 1 ? 1 : vid - SLOT_ID];
                text = "Suggestion " + suggestions.get(vid - SLOT_ID).text;
            } else if (vid >= 0 && vid < keys.size()) {
                b = keys.get(vid).bounds;
                text = spoken(keys.get(vid).id);
            } else {
                return null;
            }
            AccessibilityNodeInfo node = new AccessibilityNodeInfo(RoseKeyboardView.this, vid);
            node.setParent(RoseKeyboardView.this);
            node.setPackageName(getContext().getPackageName());
            node.setClassName(android.widget.Button.class.getName());
            node.setContentDescription(text);
            Rect r = new Rect();
            b.roundOut(r);
            int[] loc = new int[2];
            getLocationOnScreen(loc);
            r.offset(loc[0], loc[1]);
            node.setBoundsInScreen(r);
            node.setEnabled(true);
            node.setVisibleToUser(true);
            node.setClickable(true);
            node.addAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_CLICK);
            return node;
        }

        @Override
        public boolean performAction(int vid, int action, Bundle args) {
            if (vid == View.NO_ID) return performAccessibilityAction(action, args);
            if (action != AccessibilityNodeInfo.ACTION_CLICK) return false;
            if (vid == HIDE_ID) listener.onHide();
            else if (vid >= EDIT_ID && vid < EDIT_ID + EDIT_LABELS.length) listener.onEditAction(EDIT_IDS[vid - EDIT_ID]);
            else if (vid >= SLOT_ID && vid < SLOT_ID + 3 && vid - SLOT_ID < suggestions.size()) {
                listener.onSuggestion(suggestions.get(vid - SLOT_ID).text);
            } else if (vid >= 0 && vid < keys.size()) listener.onKey(keys.get(vid).id);
            else return false;
            return true;
        }
    };

    @Override
    public AccessibilityNodeProvider getAccessibilityNodeProvider() {
        return nodes;
    }

    /** Tests read the layout. */
    List<Key> keysForTest() {
        return keys;
    }
}
