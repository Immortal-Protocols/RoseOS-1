/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package dev.pixel.rose.keyboard;

import static org.junit.Assert.fail;

import android.content.Context;
import android.graphics.Insets;
import android.os.SystemClock;
import android.text.InputType;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowInsets;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityNodeProvider;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;
import android.widget.EditText;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

/**
 * 1A: the keyboard never throws, whatever the user and the app do. Seeded random runs drive the production
 * KeyboardSession and RoseKeyboardView against a real EditText: every key on every page, suggestions, the edit strip,
 * caret drags, word deletes, the app replacing text or moving the selection under the keyboard (also between a key
 * and its selection report), field restarts of every kind, relayouts at other widths, insets, touches (one and two
 * fingers, off the keys) and accessibility queries. A failure names its seed and step.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w384dp-h832dp-xxhdpi")
public class RoseKeyboardFuzzNativeTest {
    static final int SEEDS = 120, STEPS = 300;
    static final int[] TYPES = {
        InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES,
        InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE,
        InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD,
        InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS,
        InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI,
        InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS,
        InputType.TYPE_CLASS_NUMBER,
        InputType.TYPE_CLASS_PHONE,
        InputType.TYPE_CLASS_DATETIME,
        InputType.TYPE_NULL,
    };
    static final int[] OPTIONS = {
        EditorInfo.IME_ACTION_SEND, EditorInfo.IME_ACTION_DONE, EditorInfo.IME_ACTION_GO, EditorInfo.IME_ACTION_SEARCH,
        EditorInfo.IME_ACTION_NEXT, EditorInfo.IME_ACTION_UNSPECIFIED, EditorInfo.IME_FLAG_NO_ENTER_ACTION,
    };
    static final String[] TEXTS = {"", "hello", "Hello world. ", "teh  ", "a\nb\nc", "😀 ok", "x😀", "café au lait",
        "I'm here", "   ", "ill see"};
    static final int[] EDIT_IDS = {android.R.id.cut, android.R.id.copy, android.R.id.paste, android.R.id.selectAll,
        android.R.id.undo, android.R.id.redo};

    final Context context = RuntimeEnvironment.getApplication();
    EditText field;
    InputConnection ic;
    KeyboardSession session;
    RoseKeyboardView view;
    final List<String> trail = new ArrayList<>();

    void open(Random r) {
        field = new EditText(context);
        field.setInputType(TYPES[r.nextInt(TYPES.length)]);
        field.setImeOptions(OPTIONS[r.nextInt(OPTIONS.length)]);
        String text = TEXTS[r.nextInt(TEXTS.length)];
        field.setText(text);
        field.setSelection(r.nextInt(text.length() + 1));
        field.setOnEditorActionListener((v, actionId, event) -> true);
        EditorInfo info = new EditorInfo();
        ic = field.onCreateInputConnection(info);
        if (session == null || r.nextBoolean()) {
            session = new KeyboardSession(() -> ic, () -> { });
            view = new RoseKeyboardView(context, session);
            session.attach(view);
        }
        session.start(info);
        layout(r, 384);
        trail.add("open type=" + field.getInputType() + " opts=" + field.getImeOptions() + " text=" + quote(text)
                + " sel=" + field.getSelectionStart());
    }

    void layout(Random r, int widthDp) {
        float d = context.getResources().getDisplayMetrics().density;
        view.measure(View.MeasureSpec.makeMeasureSpec(Math.round(widthDp * d), View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED));
        view.layout(0, 0, view.getMeasuredWidth(), view.getMeasuredHeight());
    }

    void report() {
        session.selectionChanged(field.getSelectionStart(), field.getSelectionEnd());
    }

    static String quote(String s) {
        return "\"" + s.replace("\n", "\\n") + "\"";
    }

    MotionEvent event(int action, float x, float y) {
        long t = SystemClock.uptimeMillis();
        return MotionEvent.obtain(t, t, action, x, y, 0);
    }

    MotionEvent twoFingers(int action, float x0, float y0, float x1, float y1) {
        long t = SystemClock.uptimeMillis();
        MotionEvent.PointerProperties[] props = {new MotionEvent.PointerProperties(),
            new MotionEvent.PointerProperties()};
        props[0].id = 0;
        props[1].id = 1;
        MotionEvent.PointerCoords[] coords = {new MotionEvent.PointerCoords(), new MotionEvent.PointerCoords()};
        coords[0].x = x0;
        coords[0].y = y0;
        coords[1].x = x1;
        coords[1].y = y1;
        return MotionEvent.obtain(t, t, action, 2, props, coords, 0, 0, 1, 1, 0, 0, 0, 0);
    }

    void step(Random r) {
        int what = r.nextInt(100);
        List<RoseKeyboardView.Key> keys = view.keysForTest();
        if (what < 40 && !keys.isEmpty()) {
            String id = keys.get(r.nextInt(keys.size())).id;
            trail.add("key " + id);
            session.onKey(id);
            if (r.nextInt(10) > 0) report();
        } else if (what < 48) {
            List<RoseKeys.Suggestion> s = RoseKeys.suggestions(session.modelForTest());
            String pick = s.isEmpty() || r.nextInt(4) == 0 ? TEXTS[r.nextInt(TEXTS.length)] : s.get(r.nextInt(s.size())).text;
            trail.add("suggestion " + quote(pick));
            session.onSuggestion(pick);
            report();
        } else if (what < 53) {
            int id = EDIT_IDS[r.nextInt(EDIT_IDS.length)];
            trail.add("edit " + id);
            session.onEditAction(id);
            report();
        } else if (what < 58) {
            int delta = r.nextInt(41) - 20;
            trail.add("caret " + delta);
            session.onCaretMove(delta);
            report();
        } else if (what < 61) {
            trail.add("deleteWord");
            session.onDeleteWord();
            report();
        } else if (what < 68) {
            // The app changes the text under the keyboard; sometimes the selection report comes late or never.
            int len = field.length(), a = r.nextInt(len + 1), b = a + r.nextInt(len - a + 1);
            String with = TEXTS[r.nextInt(TEXTS.length)];
            trail.add("app replace " + a + ".." + b + " with " + quote(with));
            field.getText().replace(a, b, with);
            if (r.nextBoolean()) report();
        } else if (what < 75) {
            int len = field.length(), a = r.nextInt(len + 1), b = r.nextInt(len + 1);
            trail.add("app select " + a + ".." + b);
            field.setSelection(Math.min(a, b), Math.max(a, b));
            if (r.nextInt(5) > 0) report();
        } else if (what < 78) {
            trail.add("stale report");
            session.selectionChanged(r.nextInt(field.length() + 5) - 2, r.nextInt(field.length() + 5) - 2);
        } else if (what < 82) {
            open(r);
        } else if (what < 85) {
            int w = new int[] {320, 360, 384, 412, 600, 0}[r.nextInt(6)];
            trail.add("layout " + w);
            layout(r, w);
        } else if (what < 87) {
            float d = context.getResources().getDisplayMetrics().density;
            int overlays = Math.round(new int[] {0, 34, 58}[r.nextInt(3)] * d);
            int nav = Math.round(new int[] {0, 24}[r.nextInt(2)] * d);
            trail.add("insets " + overlays + "/" + nav);
            view.dispatchApplyWindowInsets(new WindowInsets.Builder()
                    .setInsets(WindowInsets.Type.systemOverlays(), Insets.of(0, 0, 0, overlays))
                    .setInsets(WindowInsets.Type.navigationBars(), Insets.of(0, 0, 0, nav)).build());
            layout(r, 384);
        } else if (what < 95) {
            float x = r.nextFloat() * (view.getWidth() + 40) - 20, y = r.nextFloat() * (view.getHeight() + 40) - 20;
            float x2 = r.nextFloat() * view.getWidth(), y2 = r.nextFloat() * view.getHeight();
            int kind = r.nextInt(4);
            trail.add("touch " + kind + " " + (int) x + "," + (int) y + " -> " + (int) x2 + "," + (int) y2);
            if (kind == 3) {
                view.dispatchTouchEvent(event(MotionEvent.ACTION_DOWN, x, y));
                view.dispatchTouchEvent(twoFingers(MotionEvent.ACTION_POINTER_DOWN
                        | (1 << MotionEvent.ACTION_POINTER_INDEX_SHIFT), x, y, x2, y2));
                view.dispatchTouchEvent(twoFingers(MotionEvent.ACTION_POINTER_UP, x, y, x2, y2));
                view.dispatchTouchEvent(event(MotionEvent.ACTION_UP, x2, y2));
            } else {
                view.dispatchTouchEvent(event(MotionEvent.ACTION_DOWN, x, y));
                if (kind >= 1) view.dispatchTouchEvent(event(MotionEvent.ACTION_MOVE, x2, y2));
                view.dispatchTouchEvent(event(kind == 2 ? MotionEvent.ACTION_CANCEL : MotionEvent.ACTION_UP, x2, y2));
            }
            report();
        } else {
            AccessibilityNodeProvider p = view.getAccessibilityNodeProvider();
            int id = r.nextInt(80) - 2;
            trail.add("a11y " + id);
            if (p != null) {
                p.createAccessibilityNodeInfo(id == -2 ? AccessibilityNodeProvider.HOST_VIEW_ID : id);
                p.performAction(id, r.nextBoolean() ? AccessibilityNodeInfo.ACTION_CLICK
                        : AccessibilityNodeInfo.ACTION_ACCESSIBILITY_FOCUS, null);
                report();
            }
        }
        // Long-press repeat and other delayed work run between steps.
        org.robolectric.shadows.ShadowLooper.idleMainLooper(r.nextInt(3) * 600L);
    }

    @Test
    public void noSequenceOfUserOrAppActionsThrows() {
        for (int seed = 0; seed < SEEDS; seed++) {
            Random r = new Random(seed);
            trail.clear();
            session = null;
            int i = 0;
            try {
                open(r);
                for (i = 0; i < STEPS; i++) step(r);
            } catch (Throwable t) {
                StringWriter trace = new StringWriter();
                t.printStackTrace(new PrintWriter(trace));
                List<String> last = trail.subList(Math.max(0, trail.size() - 12), trail.size());
                fail("seed " + seed + " step " + i + ": " + t + "\nlast actions:\n  " + String.join("\n  ", last)
                        + "\n" + trace.toString().substring(0, Math.min(3000, trace.toString().length())));
            }
        }
    }
}
