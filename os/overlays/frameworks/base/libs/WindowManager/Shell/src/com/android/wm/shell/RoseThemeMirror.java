/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.wm.shell;

import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Plan 1E: the Rose theme SystemUI draws, as pure logic for the regression tests.
 *
 * <p>Rose Agent owns the choice: it sends the theme id on the trusted tray link (STATE key {@link RoseTheme#TRAY_KEY}).
 * SystemUI applies it to its own Rose surfaces (the status-bar tray, the chin mic, the shade) and mirrors it to
 * Settings.Global {@link RoseTheme#SETTING_KEY} with SystemUI's existing WRITE_SECURE_SETTINGS, so Launcher3 (chin,
 * switcher) and the Rose keyboard, which have no link to Rose Agent, read and observe the same value; it survives a
 * restart and is there before the first unlock. SystemUI's own surfaces read the setting too, so there is one value.
 *
 * <p>Truthful: SystemUI acknowledges the theme it actually shows, after its surfaces were told and the setting was
 * written; when the write fails nothing changes and the acknowledgment names the theme still shown. An unknown id
 * (a newer Rose) changes nothing. No new permission, export or trust surface: only the existing link and setting.
 */
public final class RoseThemeMirror {
    /** Side effects, performed by AgentTrayPrototype on the main thread. */
    interface Port {
        /** Settings.Global.getString(SETTING_KEY), or null. */
        String read();
        /** Settings.Global.putString(SETTING_KEY, id); false when it was not written. */
        boolean write(String id);
        /** Tells Rose Agent over the tray link which theme SystemUI now shows. */
        void acknowledge(String id);
    }

    private static volatile RoseThemeMirror sInstance;
    private static final CopyOnWriteArrayList<Runnable> sListeners = new CopyOnWriteArrayList<>();

    private final Port port;
    private volatile String theme = RoseTheme.DEFAULT;
    private String acknowledged;

    RoseThemeMirror(Port port) { this.port = port; }

    /** The theme SystemUI's Rose surfaces draw now; DEFAULT before SystemUI started. */
    public static String current() {
        RoseThemeMirror m = sInstance;
        return m == null ? RoseTheme.DEFAULT : m.theme;
    }

    /** Runs on the main thread whenever current() changes. */
    public static void listen(Runnable listener) { sListeners.addIfAbsent(listener); }

    public static void unlisten(Runnable listener) { sListeners.remove(listener); }

    /** SystemUI started: adopt the persisted value, so surfaces draw the last theme before Rose answers. */
    void start() {
        sInstance = this;
        theme = RoseTheme.normalize(port.read());
    }

    String theme() { return theme; }

    /** A STATE bundle arrived; id is its theme value (null when this Rose sends none). */
    void fromRose(String id) {
        if (id == null) return;
        if (RoseTheme.known(id) && !id.equals(theme)) {
            if (!id.equals(port.read()) && !port.write(id)) {
                acknowledge();
                return;
            }
            show(id);
        }
        acknowledge();
    }

    /** The setting changed (Rose through SystemUI, or adb); SystemUI follows it. */
    void settingChanged() {
        String id = RoseTheme.normalize(port.read());
        if (!id.equals(theme)) show(id);
    }

    /** The tray link resubscribed: acknowledge again on the next STATE, so Rose never keeps a stale answer. */
    void resubscribed() { acknowledged = null; }

    private void show(String id) {
        theme = id;
        for (Runnable r : sListeners) r.run();
    }

    private void acknowledge() {
        if (theme.equals(acknowledged)) return;
        acknowledged = theme;
        port.acknowledge(theme);
    }
}
