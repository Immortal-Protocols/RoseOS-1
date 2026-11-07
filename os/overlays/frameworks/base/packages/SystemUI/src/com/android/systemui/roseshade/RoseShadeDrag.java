/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.systemui.roseshade;

/**
 * The status-bar pull that opens the Rose shade (1G), as pure logic for the regression tests.
 *
 * <p>A touch on the status bar is the status bar's until it moves down past the touch slop; from then on it is the
 * shade's, which follows the finger. Release opens the shade when it is a third of the way down or flung down;
 * otherwise the shade goes back. A sideways or upward move is never the shade's, so taps and the tray's controls
 * keep working.
 */
public final class RoseShadeDrag {
    public static final float OPEN_FRACTION = 1f / 3f;
    /** Pixels per second downward that open the shade however short the pull. */
    public static final float FLING_VELOCITY = 1200f;

    public enum Result { NONE, TRACK, OPEN, CANCEL }

    private final float mSlop;
    private float mDownX;
    private float mDownY;
    private boolean mDown;
    private boolean mTracking;
    private float mProgress;

    public RoseShadeDrag(float slop) {
        mSlop = slop;
    }

    public void down(float x, float y) {
        mDown = true;
        mTracking = false;
        mDownX = x;
        mDownY = y;
        mProgress = 0;
    }

    /** NONE while the status bar keeps the touch; TRACK once (and while) the shade follows it. */
    public Result move(float x, float y, float panelHeight) {
        if (!mDown) return Result.NONE;
        final float dy = y - mDownY;
        if (!mTracking) {
            if (dy <= mSlop || Math.abs(x - mDownX) > dy) return Result.NONE;
            mTracking = true;
        }
        mProgress = panelHeight <= 0 ? 0 : Math.max(0f, Math.min(1f, dy / panelHeight));
        return Result.TRACK;
    }

    /** OPEN or CANCEL when the shade had the touch, else NONE. velocityY is positive downward. */
    public Result up(float velocityY) {
        final boolean tracking = mTracking;
        mDown = false;
        mTracking = false;
        if (!tracking) return Result.NONE;
        return mProgress >= OPEN_FRACTION || velocityY >= FLING_VELOCITY ? Result.OPEN : Result.CANCEL;
    }

    public Result cancel() {
        final boolean tracking = mTracking;
        mDown = false;
        mTracking = false;
        return tracking ? Result.CANCEL : Result.NONE;
    }

    public boolean tracking() {
        return mTracking;
    }

    /** How far the shade has followed the finger, 0..1. */
    public float progress() {
        return mProgress;
    }
}
