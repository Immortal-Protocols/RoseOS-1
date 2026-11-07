package dev.pixel.rose.keyboard;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.text.InputType;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;
import android.widget.EditText;
import java.io.File;
import java.io.FileOutputStream;
import java.security.MessageDigest;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;

/**
 * 1M: the production RoseKeyboardView drawn by Android's own native graphics (Robolectric NATIVE mode) at the
 * design's 384dp width, xxhdpi: the letters page in a Send field, mid-word with suggestions, and the symbols page.
 * Each render is written to target/render (render.out) with its sha256, for review against the design's sheet.
 */
@RunWith(RobolectricTestRunner.class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = 35, qualifiers = "w384dp-h832dp-xxhdpi")
public class RoseKeyboardRenderNativeTest {
    Bitmap render(String name, String typed, boolean symbols) throws Exception {
        Context context = RuntimeEnvironment.getApplication();
        EditText field = new EditText(context);
        field.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        field.setImeOptions(EditorInfo.IME_ACTION_SEND);
        EditorInfo info = new EditorInfo();
        InputConnection ic = field.onCreateInputConnection(info);
        KeyboardSession session = new KeyboardSession(() -> ic, () -> {});
        RoseKeyboardView view = new RoseKeyboardView(context, session);
        session.attach(view);
        session.start(info);
        for (char c : typed.toCharArray()) {
            session.onKey(c == ' ' ? "space" : String.valueOf(c));
            session.selectionChanged(field.getSelectionStart(), field.getSelectionEnd());
        }
        if (symbols) session.onKey("?123");
        int width = Math.round(384 * context.getResources().getDisplayMetrics().density);
        view.measure(View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED));
        view.layout(0, 0, view.getMeasuredWidth(), view.getMeasuredHeight());
        Bitmap bitmap = Bitmap.createBitmap(view.getMeasuredWidth(), view.getMeasuredHeight(), Bitmap.Config.ARGB_8888);
        view.draw(new Canvas(bitmap));
        File dir = new File(System.getProperty("render.out", "target/render"));
        dir.mkdirs();
        File out = new File(dir, name + ".png");
        try (FileOutputStream stream = new FileOutputStream(out)) {
            bitmap.compress(Bitmap.CompressFormat.PNG, 100, stream);
        }
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(java.nio.file.Files.readAllBytes(out.toPath()));
        StringBuilder hex = new StringBuilder();
        for (byte b : digest) hex.append(String.format("%02x", b));
        System.out.println("RENDER " + name + " " + bitmap.getWidth() + "x" + bitmap.getHeight() + " sha256=" + hex);
        return bitmap;
    }

    @Test
    public void lettersSuggestionsAndSymbolsRender() throws Exception {
        Bitmap letters = render("rose-keyboard-letters", "", false);
        float d = RuntimeEnvironment.getApplication().getResources().getDisplayMetrics().density;
        assertEquals(Math.round(268 * d), letters.getHeight());
        // The board's top colour (#1D1114) at the top-left corner.
        assertEquals(0xFF1D1114, letters.getPixel(1, 1));
        Bitmap typing = render("rose-keyboard-typing", "see you at th", false);
        assertTrue(typing.getWidth() > 0);
        render("rose-keyboard-symbols", "", true);
    }
}
