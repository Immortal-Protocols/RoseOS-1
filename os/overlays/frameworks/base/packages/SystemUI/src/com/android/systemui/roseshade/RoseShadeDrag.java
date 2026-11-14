/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.systemui.roseshade;

/**
 * The status-bar pull that opens the Rose shade (1G), as pure logic for the regression tests.
 *
 * <p>A touch on the status bar is the status bar's until it moves down past the touch slop; from then on it is the
 * shade's, which follows the finger. Release opens the shade when it is a third of the way down or flung down;
 * otherwise the shade goes back. A sideways or upward move is never the shade's, so taps and the tray's controls
 * keep working. {@link Swipe} is the sideways swipe that dismisses a card.
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

    /**
     * The sideways swipe that dismisses a notification card in the Rose shade (1G), as pure logic for the tests.
     *
     * <p>A touch on a card is the card's own (a tap opens it, a vertical move scrolls the list) until it moves
     * sideways past the touch slop and more sideways than vertically; from then on the card follows the finger.
     * Release dismisses when the card is two fifths of the way across or flung that way; otherwise it goes back. A
     * card that cannot be dismissed never starts following.
     */
    public static final class Swipe {
        public static final float DISMISS_FRACTION = 0.4f;
        /** Pixels per second sideways that dismiss however short the swipe. */
        public static final float FLING_VELOCITY = 1200f;

        public enum Result { NONE, TRACK, DISMISS, RETURN }

        private final float mSlop;
        private float mDownX;
        private float mDownY;
        private boolean mDown;
        private boolean mTracking;
        private float mOffset;
        private float mWidth;

        public Swipe(float slop) {
            mSlop = slop;
        }

        public void down(float x, float y) {
            mDown = true;
            mTracking = false;
            mDownX = x;
            mDownY = y;
            mOffset = 0;
        }

        /** NONE while the card keeps the touch; TRACK once (and while) the card follows it sideways. */
        public Result move(float x, float y, float width, boolean dismissible) {
            if (!mDown || !dismissible) return Result.NONE;
            final float dx = x - mDownX;
            if (!mTracking) {
                if (Math.abs(dx) <= mSlop || Math.abs(y - mDownY) >= Math.abs(dx)) return Result.NONE;
                mTracking = true;
            }
            mWidth = width;
            mOffset = dx;
            return Result.TRACK;
        }

        /** DISMISS or RETURN when the card had the touch, else NONE. velocityX is positive to the right. */
        public Result up(float velocityX) {
            final boolean tracking = mTracking;
            mDown = false;
            mTracking = false;
            if (!tracking) return Result.NONE;
            final boolean far = mWidth > 0 && Math.abs(mOffset) >= mWidth * DISMISS_FRACTION;
            // A fling counts only in the direction the card is already going.
            final boolean flung = Math.abs(velocityX) >= FLING_VELOCITY
                    && Math.signum(velocityX) == Math.signum(mOffset);
            return far || flung ? Result.DISMISS : Result.RETURN;
        }

        public Result cancel() {
            final boolean tracking = mTracking;
            mDown = false;
            mTracking = false;
            return tracking ? Result.RETURN : Result.NONE;
        }

        public boolean tracking() {
            return mTracking;
        }

        /** How far the card follows the finger, in pixels (negative to the left). */
        public float offset() {
            return mOffset;
        }
    }
}
