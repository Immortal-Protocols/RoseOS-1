package dev.pixel.rose.keyboard;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import android.content.Context;
import android.os.SystemClock;
import android.text.InputType;
import android.view.MotionEvent;
import android.view.View;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;
import android.widget.EditText;
import java.util.ArrayList;
import java.util.List;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

/**
 * 1M: the production Rose keyboard (KeyboardSession, RoseKeyboardView, compiled unchanged from the overlay) on
 * Robolectric's SDK 35 runtime, against a real EditText and its real InputConnection: the edits the app receives,
 * the selection it ends with, field kinds (passwords, multi-line, actions), and the view's layout and touch handling
 * at the design's 384dp width, xxhdpi. Framework behaviour on a host; not a device and not the Android build.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w384dp-h832dp-xxhdpi")
public class RoseKeyboardNativeTest {
    Context context;
    EditText field;
    InputConnection ic;
    KeyboardSession session;
    boolean hidden;
    final List<Integer> actions = new ArrayList<>();

    @Before
    public void setUp() {
        context = RuntimeEnvironment.getApplication();
    }

    /** A field of the given input type and IME action, with its own InputConnection, and a started session. */
    void open(int inputType, int imeOptions, String text) {
        field = new EditText(context);
        field.setInputType(inputType);
        field.setImeOptions(imeOptions);
        field.setText(text);
        field.setSelection(text.length());
        field.setOnEditorActionListener((v, actionId, event) -> {
            actions.add(actionId);
            return true;
        });
        EditorInfo info = new EditorInfo();
        ic = field.onCreateInputConnection(info);
        session = new KeyboardSession(() -> ic, () -> hidden = true);
        session.start(info);
    }

    void open(String text) {
        open(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES, EditorInfo.IME_ACTION_SEND, text);
    }

    /** One key, then the selection report the app sends after it. */
    void key(String id) {
        session.onKey(id);
        session.selectionChanged(field.getSelectionStart(), field.getSelectionEnd());
    }

    void typeStr(String s) {
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            key(c == ' ' ? "space" : String.valueOf(c));
            SystemClock.sleep(1000);  // the prototype's checks type a second apart (no double-space period)
        }
    }

    String text() {
        return field.getText().toString();
    }

    @Test
    public void typingReachesTheAppWithAutoCapitalsAndCorrections() {
        open("");
        typeStr("teh garden ");
        assertEquals("The garden ", text());
        assertEquals("the caret follows the text", text().length(), field.getSelectionStart());
        key("bksp");
        assertEquals("one backspace removes one character", "The garden", text());
    }

    @Test
    public void oneBackspaceRightAfterACorrectionRestoresWhatWasTyped() {
        open("");
        typeStr("see teh ");
        assertEquals("See the ", text());
        key("bksp");
        assertEquals("See teh", text());
        key("space");
        assertEquals("the reverted word is not corrected again", "See teh ", text());
    }

    @Test
    public void doubleSpaceEndsTheSentence() {
        open("");
        typeStr("hi");
        session.onKey("space");
        session.selectionChanged(field.getSelectionStart(), field.getSelectionEnd());
        session.onKey("space");
        assertEquals("Hi. ", text());
    }

    @Test
    public void editsLandAtTheCaretEvenInsideText() {
        open("aa");
        field.setSelection(1);
        session.selectionChanged(1, 1);
        key("a");
        assertEquals("aaa", text());
        assertEquals("inserted at the caret, not after the repeat", 2, field.getSelectionStart());
        key("bksp");
        assertEquals("aa", text());
        assertEquals(1, field.getSelectionStart());
    }

    @Test
    public void anEmojiIsDeletedWhole() {
        open("hi 👋");
        key("bksp");
        assertEquals("hi ", text());
    }

    @Test
    public void typingReplacesTheAppsSelection() {
        open("Garden at ten");
        field.setSelection(7, 9);
        session.selectionChanged(7, 9);
        key("x");
        assertEquals("Garden x ten", text());
    }

    @Test
    public void passwordsGetNoCorrectionsSuggestionsOrCapitals() {
        open(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD, EditorInfo.IME_ACTION_DONE, "");
        typeStr("teh ");
        assertEquals("teh ", text());
        assertTrue(RoseKeys.suggestions(session.modelForTest()).isEmpty());
    }

    @Test
    public void theActionKeyFiresTheAppsActionAndNeverClearsTheField() {
        open("");
        typeStr("hi dont");
        key("action");
        assertEquals("the word before the caret is corrected first", "Hi don't", text());
        assertEquals(List.of(EditorInfo.IME_ACTION_SEND), actions);
    }

    @Test
    public void multiLineReturnIsANewLineWithACapitalAfterIt() {
        open(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE, EditorInfo.IME_ACTION_NONE, "");
        typeStr("list");
        key("action");
        typeStr("milk");
        assertEquals("List\nMilk", text());
        assertTrue("no editor action", actions.isEmpty());
    }

    @Test
    public void numberFieldsOpenOnTheSymbolsPage() {
        open(InputType.TYPE_CLASS_NUMBER, EditorInfo.IME_ACTION_DONE, "");
        assertEquals(RoseKeys.SYM1, session.modelForTest().page);
        key("4");
        key("2");
        assertEquals("42", text());
    }

    @Test
    public void suggestionsArePickedIntoTheField() {
        open("");
        typeStr("see you at th");
        List<RoseKeys.Suggestion> sg = RoseKeys.suggestions(session.modelForTest());
        assertEquals("the", sg.get(1).text);
        session.onSuggestion(sg.get(1).text);
        assertEquals("See you at the ", text());
    }

    @Test
    public void aCursorMoveByTheUserEndsThePendingRevert() {
        open("");
        typeStr("teh ");
        assertEquals("The ", text());
        field.setSelection(0);
        session.selectionChanged(0, 0);
        field.setSelection(4);
        session.selectionChanged(4, 4);
        key("bksp");
        assertEquals("no revert after the user moved the cursor", "The", text());
    }

    @Test
    public void hideNeverTouchesTheField() {
        open("draft");
        session.onHide();
        assertTrue(hidden);
        assertEquals("draft", text());
    }

    // ---------- the view ----------

    RoseKeyboardView laidOut() {
        open("");
        RoseKeyboardView view = new RoseKeyboardView(context, session);
        session.attach(view);
        int width = Math.round(384 * context.getResources().getDisplayMetrics().density);
        view.measure(View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED));
        view.layout(0, 0, view.getMeasuredWidth(), view.getMeasuredHeight());
        return view;
    }

    RoseKeyboardView.Key keyNamed(RoseKeyboardView view, String id) {
        for (RoseKeyboardView.Key k : view.keysForTest()) if (k.id.equals(id)) return k;
        throw new AssertionError("no key " + id);
    }

    void tap(RoseKeyboardView view, String id) {
        RoseKeyboardView.Key k = keyNamed(view, id);
        long t = SystemClock.uptimeMillis();
        view.dispatchTouchEvent(MotionEvent.obtain(t, t, MotionEvent.ACTION_DOWN, k.bounds.centerX(), k.bounds.centerY(), 0));
        view.dispatchTouchEvent(MotionEvent.obtain(t, t + 50, MotionEvent.ACTION_UP, k.bounds.centerX(), k.bounds.centerY(), 0));
        session.selectionChanged(field.getSelectionStart(), field.getSelectionEnd());
    }

    @Test
    public void theViewIs268dpWithTheDesignsRowsAndNoMicOrNumberRow() {
        RoseKeyboardView view = laidOut();
        float d = context.getResources().getDisplayMetrics().density;
        assertEquals(Math.round(268 * d), view.getMeasuredHeight());
        List<RoseKeyboardView.Key> keys = view.keysForTest();
        assertEquals("10 + 9 + 9 + 5 keys", 33, keys.size());
        for (RoseKeyboardView.Key k : keys) {
            assertFalse("no microphone key", k.id.toLowerCase().contains("mic"));
            assertEquals("46dp keys", 46 * d, k.bounds.height(), 0.5f);
        }
        assertEquals("the top row is letters", "q", keys.get(0).id);
        RoseKeyboardView.Key space = keyNamed(view, "space");
        assertTrue("space is the widest key", space.bounds.width() > keyNamed(view, "action").bounds.width() * 3);
    }

    @Test
    public void tapsOnKeysTypeIntoTheField() {
        RoseKeyboardView view = laidOut();
        for (String id : new String[] {"h", "i", "space"}) tap(view, id);
        assertEquals("Hi ", text());
        tap(view, "?123");
        tap(view, "4");
        tap(view, "ABC");
        tap(view, "a");
        assertEquals("Hi 4a", text());
    }

    @Test
    public void slidingToAnotherKeyBeforeReleaseTypesThatKey() {
        RoseKeyboardView view = laidOut();
        RoseKeyboardView.Key q = keyNamed(view, "q"), w = keyNamed(view, "w");
        long t = SystemClock.uptimeMillis();
        view.dispatchTouchEvent(MotionEvent.obtain(t, t, MotionEvent.ACTION_DOWN, q.bounds.centerX(), q.bounds.centerY(), 0));
        view.dispatchTouchEvent(MotionEvent.obtain(t, t + 20, MotionEvent.ACTION_MOVE, w.bounds.centerX(), w.bounds.centerY(), 0));
        view.dispatchTouchEvent(MotionEvent.obtain(t, t + 40, MotionEvent.ACTION_UP, w.bounds.centerX(), w.bounds.centerY(), 0));
        assertEquals("W", text());
    }

    @Test
    public void everyKeyIsALabelledAccessibilityNode() {
        RoseKeyboardView view = laidOut();
        AccessibilityNodeInfo root = view.getAccessibilityNodeProvider().createAccessibilityNodeInfo(View.NO_ID);
        assertTrue(root.getChildCount() >= 34);  // 33 keys and hide
        int shiftIndex = view.keysForTest().indexOf(keyNamed(view, "shift"));
        AccessibilityNodeInfo shift = view.getAccessibilityNodeProvider().createAccessibilityNodeInfo(shiftIndex);
        assertEquals("Shift on", shift.getContentDescription().toString());  // auto-capital at the start
        int bksp = view.keysForTest().indexOf(keyNamed(view, "bksp"));
        assertEquals("Delete", view.getAccessibilityNodeProvider().createAccessibilityNodeInfo(bksp)
                .getContentDescription().toString());
        int a = view.keysForTest().indexOf(keyNamed(view, "a"));
        assertTrue(view.getAccessibilityNodeProvider().performAction(a, AccessibilityNodeInfo.ACTION_CLICK, null));
        assertEquals("A", text());
    }
}
