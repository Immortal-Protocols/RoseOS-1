/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.widget.Toast;

/** November 10 plan 1A: open the existing conversation task directly, without a Home focus transition. */
public final class RoseEdgeEntry {
    static final String PACKAGE = "dev.pixel.voice";
    static final String COMPONENT = PACKAGE + ".RoseTrayEntry";

    private RoseEdgeEntry() {}

    /** The alias is guarded by INTERNAL_SYSTEM_WINDOW, which Launcher3 already holds. No task id or return extra. */
    public static Intent intent() {
        return new Intent().setClassName(PACKAGE, COMPONENT).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
    }

    /** Older/missing/disabled app or denied entry: stay in the source app, never fall back through Home. */
    public static void open(Context context) {
        try {
            context.startActivity(intent());
        } catch (ActivityNotFoundException | SecurityException unavailable) {
            Toast.makeText(context, "Rose could not open. Check for a RoseOS update.", Toast.LENGTH_LONG).show();
        }
    }
}
