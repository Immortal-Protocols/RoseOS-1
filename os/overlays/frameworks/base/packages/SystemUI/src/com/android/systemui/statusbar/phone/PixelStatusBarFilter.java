/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.systemui.statusbar.phone;

import android.app.ActivityManager;
import android.app.KeyguardManager;
import android.content.Context;
import android.os.SystemProperties;
import android.view.View;
import android.view.ViewGroup;
import com.android.systemui.res.R;
import com.android.systemui.statusbar.StatusIconDisplayable;
import java.util.WeakHashMap;
import java.util.Map;

/** Keeps the stock controllers and accessibility state; filters only their unlocked drawing. */
final class PixelStatusBarFilter {
    private static final Map<View, Float> hidden = new WeakHashMap<>();
    private static final Map<View, Boolean> watched = new WeakHashMap<>();
    static void draw(View root, Runnable drawStock) {
        if (!watched.containsKey(root)) {
            watched.put(root, true);
            // Modern signal binders update child RenderNode alpha without redrawing
            // the parent. Apply before every frame, not only parent dispatchDraw.
            root.getViewTreeObserver().addOnPreDrawListener(() -> {
                apply(root);
                return true;
            });
        }
        apply(root);
        drawStock.run();
    }
    private static void apply(View root) {
        Context context = root.getContext();
        if (!SystemProperties.getBoolean("persist.wm.debug.pixel_agent_tray", false)
                || ActivityManager.getCurrentUser() != 0
                || context.getSystemService(KeyguardManager.class).isKeyguardLocked()) {
            for (Map.Entry<View, Float> item : hidden.entrySet()) item.getKey().setAlpha(item.getValue());
            hidden.clear();
            return;
        }
        hide(root.findViewById(R.id.clock), hidden);
        hide(root.findViewById(R.id.notification_icon_area), hidden);
        hide(root.findViewById(R.id.notification_lights_out), hidden);
        hide(root.findViewById(R.id.user_switcher_container), hidden);
        int operator = context.getResources().getIdentifier("operator_name", "id", context.getPackageName());
        if (operator != 0) hide(root.findViewById(operator), hidden);
        int battery = context.getResources().getIdentifier("battery", "id", context.getPackageName());
        if (battery != 0) hide(root.findViewById(battery), hidden);
        View icons = root.findViewById(R.id.statusIcons);
        View systemIcons = root.findViewById(R.id.system_icons);
        if (systemIcons instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) systemIcons;
            for (int i = 0; i < group.getChildCount(); i++)
                if (group.getChildAt(i) != icons) hide(group.getChildAt(i), hidden);
        }
        hideStatusIcons(root);
    }
    private static void hideStatusIcons(View view) {
        if (view instanceof StatusIconDisplayable) {
            String slot = ((StatusIconDisplayable) view).getSlot();
            boolean keep = slot != null && (slot.contains("privacy") || slot.contains("camera")
                    || slot.contains("microphone") || slot.contains("location")
                    || slot.contains("vpn") || slot.contains("record"));
            if (!keep) hide(view, hidden);
        }
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) hideStatusIcons(group.getChildAt(i));
        }
    }
    private static void hide(View view, Map<View, Float> hidden) {
        if (view != null) { hidden.putIfAbsent(view, view.getAlpha()); view.setAlpha(0); }
    }
}
