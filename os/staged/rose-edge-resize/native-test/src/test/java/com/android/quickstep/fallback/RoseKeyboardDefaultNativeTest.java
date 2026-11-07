package com.android.quickstep.fallback;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.content.Context;
import android.content.pm.ApplicationInfo;
import android.content.pm.ResolveInfo;
import android.content.pm.ServiceInfo;
import android.provider.Settings;
import android.view.inputmethod.InputMethodInfo;
import android.view.inputmethod.InputMethodManager;
import java.util.ArrayList;
import java.util.List;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

/**
 * 1M: the production RoseEdgeWindow.KeyboardDefault (compiled unchanged from the Launcher3 overlay) on Robolectric's
 * SDK 35 runtime with real Settings.Secure and the framework's InputMethodManager list: the Rose keyboard becomes the
 * selected keyboard exactly once and only when it is a system-installed input method; the stock keyboard stays enabled;
 * a later choice by the user stands.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public class RoseKeyboardDefaultNativeTest {
    static final String LATIN = "com.android.inputmethod.latin/.LatinIME";
    Context context;

    @Before
    public void setUp() {
        context = RuntimeEnvironment.getApplication();
        Settings.Secure.putString(context.getContentResolver(), Settings.Secure.ENABLED_INPUT_METHODS, LATIN);
        Settings.Secure.putString(context.getContentResolver(), Settings.Secure.DEFAULT_INPUT_METHOD, LATIN);
    }

    static InputMethodInfo ime(String pkg, String cls, boolean system) {
        ServiceInfo service = new ServiceInfo();
        service.packageName = pkg;
        service.name = cls;
        service.applicationInfo = new ApplicationInfo();
        service.applicationInfo.packageName = pkg;
        service.applicationInfo.flags = system ? ApplicationInfo.FLAG_SYSTEM : 0;
        ResolveInfo resolve = new ResolveInfo();
        resolve.serviceInfo = service;
        return new InputMethodInfo(resolve, false, null, null, 0, false);
    }

    void installed(InputMethodInfo... imes) {
        List<InputMethodInfo> list = new ArrayList<>();
        for (InputMethodInfo i : imes) list.add(i);
        shadowOf(context.getSystemService(InputMethodManager.class)).setInputMethodInfoList(list);
    }

    String secure(String key) {
        return Settings.Secure.getString(context.getContentResolver(), key);
    }

    @Test
    public void theRoseKeyboardIsSelectedOnceAndTheStockKeyboardStaysEnabled() {
        installed(ime("com.android.inputmethod.latin", "com.android.inputmethod.latin.LatinIME", true),
                ime("dev.pixel.rose.keyboard", "dev.pixel.rose.keyboard.RoseKeyboardService", true));
        assertEquals(RoseEdgeWindow.KeyboardDefault.ID, "dev.pixel.rose.keyboard/.RoseKeyboardService");
        assertTrue(RoseEdgeWindow.KeyboardDefault.applyOnce(context));
        assertEquals(RoseEdgeWindow.KeyboardDefault.ID, secure(Settings.Secure.DEFAULT_INPUT_METHOD));
        assertEquals(LATIN + ":" + RoseEdgeWindow.KeyboardDefault.ID, secure(Settings.Secure.ENABLED_INPUT_METHODS));
        // The user picks the stock keyboard afterwards: that choice stands.
        Settings.Secure.putString(context.getContentResolver(), Settings.Secure.DEFAULT_INPUT_METHOD, LATIN);
        assertFalse("never again", RoseEdgeWindow.KeyboardDefault.applyOnce(context));
        assertEquals(LATIN, secure(Settings.Secure.DEFAULT_INPUT_METHOD));
    }

    @Test
    public void nothingHappensWithoutASystemRoseKeyboard() {
        installed(ime("com.android.inputmethod.latin", "com.android.inputmethod.latin.LatinIME", true));
        assertFalse(RoseEdgeWindow.KeyboardDefault.applyOnce(context));
        assertEquals(LATIN, secure(Settings.Secure.DEFAULT_INPUT_METHOD));
        // A user-installed package with the same name is never selected.
        installed(ime("dev.pixel.rose.keyboard", "dev.pixel.rose.keyboard.RoseKeyboardService", false));
        assertFalse(RoseEdgeWindow.KeyboardDefault.applyOnce(context));
        assertEquals(LATIN, secure(Settings.Secure.DEFAULT_INPUT_METHOD));
        // Once the system keyboard is there, the first start selects it.
        installed(ime("dev.pixel.rose.keyboard", "dev.pixel.rose.keyboard.RoseKeyboardService", true));
        assertTrue(RoseEdgeWindow.KeyboardDefault.applyOnce(context));
    }

    @Test
    public void theEnabledListKeepsEveryEntryAndAddsTheKeyboardOnce() {
        String id = RoseEdgeWindow.KeyboardDefault.ID;
        assertEquals(id, RoseEdgeWindow.KeyboardDefault.withEnabled(null, id));
        assertEquals(id, RoseEdgeWindow.KeyboardDefault.withEnabled("", id));
        assertEquals(LATIN + ";12345:" + id, RoseEdgeWindow.KeyboardDefault.withEnabled(LATIN + ";12345", id));
        String both = LATIN + ":" + id + ";678";
        assertEquals("already enabled, with a subtype", both, RoseEdgeWindow.KeyboardDefault.withEnabled(both, id));
    }
}
