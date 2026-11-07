/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.systemui.roseshade;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * The Rose shade's quick settings (1G), as pure logic for the regression tests: four tiles and brightness. The
 * compact row shows the tiles as icons; the quick settings view shows them labelled ("Wi-Fi", "On") in two
 * columns, with brightness and a settings shortcut. There is deliberately no microphone tile: the Rose chin holds
 * the only microphone-input control.
 */
public final class RoseQuickSettings {
    public enum Tile {
        WIFI("Wi-Fi"), BLUETOOTH("Bluetooth"), FLASHLIGHT("Flashlight"), DO_NOT_DISTURB("Do not disturb");

        public final String label;

        Tile(String label) {
            this.label = label;
        }
    }

    /** The tiles, in the board's order. */
    public static final List<Tile> TILES = Collections.unmodifiableList(Arrays.asList(Tile.values()));

    private final boolean[] mOn = new boolean[Tile.values().length];
    private final boolean[] mKnown = new boolean[Tile.values().length];
    private float mBrightness = -1;

    /** The system reported a tile's state. Until then the tile shows neither On nor Off. */
    public void report(Tile tile, boolean on) {
        mOn[tile.ordinal()] = on;
        mKnown[tile.ordinal()] = true;
    }

    public boolean isOn(Tile tile) {
        return mKnown[tile.ordinal()] && mOn[tile.ordinal()];
    }

    /** "On" or "Off", or "" before the system has reported the state. */
    public String stateLabel(Tile tile) {
        if (!mKnown[tile.ordinal()]) return "";
        return mOn[tile.ordinal()] ? "On" : "Off";
    }

    /**
     * A tap asks the system for the opposite state; the tile changes only when the system reports it, so a refused
     * change never shows as done. Returns the requested state, or null before the state is known.
     */
    public Boolean requestToggle(Tile tile) {
        if (!mKnown[tile.ordinal()]) return null;
        return !mOn[tile.ordinal()];
    }

    /** Brightness as the slider's 0..1 position, or -1 before the system reported it. */
    public float brightness() {
        return mBrightness;
    }

    public void reportBrightness(float value) {
        mBrightness = Math.max(0f, Math.min(1f, value));
    }
}
