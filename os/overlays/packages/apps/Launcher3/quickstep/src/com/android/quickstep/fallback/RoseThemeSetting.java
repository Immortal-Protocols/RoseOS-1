/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import android.content.ContentResolver;
import android.content.Context;
import android.database.ContentObserver;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;

import java.util.ArrayList;
import java.util.List;

/**
 * Plan 1E: the Rose theme Launcher3 draws its Rose surfaces in (the raised edge, the switcher's agent row and field).
 * Launcher3 has no link to Rose Agent: SystemUI mirrors the theme it shows to Settings.Global RoseTheme.SETTING_KEY,
 * and this reads it. Read only; nothing here writes a setting or needs a permission. An unset, unknown or unreadable
 * value is RoseTheme.DEFAULT (Oxblood), so a missing mirror leaves today's look. Main thread only.
 */
final class RoseThemeSetting {
    private RoseThemeSetting() {}

    /** The callbacks run when the setting changes; the observer runs them on the main thread. */
    private static final List<Runnable> LISTENERS = new ArrayList<>();
    /** The one observer for this process, kept strongly for its lifetime once registered. */
    private static ContentObserver sObserver;

    /** The theme id to draw now: the mirrored id when known, otherwise RoseTheme.DEFAULT. */
    static String current(Context context) {
        try {
            ContentResolver resolver = context.getContentResolver();
            return RoseTheme.normalize(resolver == null ? null : Settings.Global.getString(resolver, RoseTheme.SETTING_KEY));
        } catch (RuntimeException e) {
            return RoseTheme.DEFAULT;
        }
    }

    /**
     * Runs listener on the main thread whenever the mirrored theme changes. Adding the same listener again does
     * nothing; the observer is registered once for the process, on the application context.
     */
    static void listen(Context context, Runnable listener) {
        if (listener == null) return;
        if (!LISTENERS.contains(listener)) LISTENERS.add(listener);
        if (sObserver != null) return;
        Context app = context.getApplicationContext() != null ? context.getApplicationContext() : context;
        ContentObserver observer = new ContentObserver(new Handler(Looper.getMainLooper())) {
            @Override
            public void onChange(boolean selfChange) {
                for (Runnable r : new ArrayList<>(LISTENERS)) r.run();
            }
        };
        try {
            Uri uri = Settings.Global.getUriFor(RoseTheme.SETTING_KEY);
            app.getContentResolver().registerContentObserver(uri, false, observer);
            sObserver = observer;
        } catch (RuntimeException e) {
            // Unobservable: surfaces still read current() whenever they are built, so the next one shows the theme.
        }
    }

    /** Stops running listener; the process observer stays registered for the next surface. */
    static void unlisten(Runnable listener) {
        LISTENERS.remove(listener);
    }

    // Plan 1E: Glacier's raster field, decoded once per process off the UI thread and shared by every strip.
    private static volatile android.graphics.Bitmap sField;
    private static volatile boolean sFieldLoading, sFieldFailed;

    /**
     * The decoded Glacier field, or null while it loads (or if it cannot be loaded). The first call starts one
     * background decode; ready runs on the main thread when it finishes, so the caller redraws. Never blocks.
     */
    static android.graphics.Bitmap glacierField(Context context, Runnable ready) {
        android.graphics.Bitmap field = sField;
        if (field != null || sFieldFailed || sFieldLoading) return field;
        sFieldLoading = true;
        final Context app = context.getApplicationContext() != null ? context.getApplicationContext() : context;
        final Handler main = new Handler(Looper.getMainLooper());
        new Thread(() -> {
            android.graphics.Bitmap decoded = null;
            try {
                int id = app.getResources().getIdentifier("rose_glacier_field", "drawable", app.getPackageName());
                android.graphics.BitmapFactory.Options options = new android.graphics.BitmapFactory.Options();
                options.inScaled = false;
                decoded = id == 0 ? null : android.graphics.BitmapFactory.decodeResource(app.getResources(), id, options);
            } catch (RuntimeException | OutOfMemoryError e) {
                decoded = null;
            }
            sField = decoded;
            sFieldFailed = decoded == null;
            sFieldLoading = false;
            if (ready != null) main.post(ready);
        }, "rose-glacier-field").start();
        return null;
    }
}
