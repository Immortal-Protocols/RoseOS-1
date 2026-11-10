/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package dev.pixel.rose.keyboard;

import android.content.ContentResolver;
import android.content.Context;
import android.database.ContentObserver;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;

/**
 * RoseOS (1E): the theme the keyboard draws. SystemUI writes the selected theme id to the world-readable
 * Settings.Global key {@link RoseTheme#SETTING_KEY}; the keyboard only reads it (no permission, no storage of its own)
 * and watches it so an open keyboard recolours when the theme changes. Anything unreadable falls back to the default.
 */
final class RoseKeyboardTheme {
    private final Context context;
    private final Runnable onChange;
    private ContentObserver observer;

    RoseKeyboardTheme(Context context, Runnable onChange) {
        this.context = context;
        this.onChange = onChange;
    }

    /** The current theme id, normalized; the default when the setting is unset or unreadable. */
    static String current(Context context) {
        try {
            ContentResolver cr = context.getContentResolver();
            return RoseTheme.normalize(Settings.Global.getString(cr, RoseTheme.SETTING_KEY));
        } catch (RuntimeException e) {
            return RoseTheme.DEFAULT;
        }
    }

    /** Starts watching the setting; onChange runs on the main thread. Safe to call more than once. */
    void register() {
        if (observer != null) return;
        ContentObserver o = new ContentObserver(new Handler(Looper.getMainLooper())) {
            @Override
            public void onChange(boolean selfChange) {
                onChange.run();
            }
        };
        try {
            Uri uri = Settings.Global.getUriFor(RoseTheme.SETTING_KEY);
            context.getContentResolver().registerContentObserver(uri, false, o);
            observer = o;
        } catch (RuntimeException e) {
            // Unwatchable: the keyboard still re-reads the theme each time it is attached.
        }
    }

    void unregister() {
        if (observer == null) return;
        try {
            context.getContentResolver().unregisterContentObserver(observer);
        } catch (RuntimeException e) {
            // Already gone.
        }
        observer = null;
    }
}
