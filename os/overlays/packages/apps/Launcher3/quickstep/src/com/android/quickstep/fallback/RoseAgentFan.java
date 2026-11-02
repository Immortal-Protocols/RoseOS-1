/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import android.animation.ValueAnimator;
import android.content.Context;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.accessibility.AccessibilityEvent;
import android.widget.HorizontalScrollView;

/**
 * RoseOS (#410): the switcher's Rose carousel as the Oxblood HiFi's centred fan with depth (RoseFan), scrolling on its
 * own, independent of Launcher3's human-task carousel. It only lays out and moves the cards RoseAgentStrip gives it:
 * it never reads the platform, never decides which cards exist, and never captures or opens anything.
 *
 * It always settles exactly on a card: a release without a fling centres the nearest card, and a fling moves one card
 * in its direction, so a fling never skips work the person has not seen. Left and right keys move one card, clamped
 * with no wrap. Accessibility focus on a card centres it. With animations off (Settings > Accessibility > Remove
 * animations, the system's reduced motion) every move is immediate; the fan itself is static geometry and stays.
 */
final class RoseAgentFan extends HorizontalScrollView {
    private final float density, dpi;
    private boolean flung;
    /** The card to centre once laid out: set on entry (RoseFan.initial) and after the cards change (RoseFan.keep). */
    private int pendingIndex = -1;
    private boolean placed;

    RoseAgentFan(Context context) {
        super(context);
        density = context.getResources().getDisplayMetrics().density;
        dpi = context.getResources().getDisplayMetrics().densityDpi;
        setHorizontalScrollBarEnabled(false);
        setOverScrollMode(OVER_SCROLL_NEVER);
        setFillViewport(false);
    }

    /** On every entry: the next cards are placed afresh (RoseFan.initial), not where they were left last time. */
    void unplace() {
        placed = false;
        pendingIndex = -1;
    }

    boolean placed() { return placed; }

    private int pitch() { return Math.round(RoseFan.CARD_WIDTH_DP * density); }

    private ViewGroup cards() { return getChildCount() > 0 ? (ViewGroup) getChildAt(0) : null; }

    private int count() {
        ViewGroup cards = cards();
        return cards == null ? 0 : cards.getChildCount();
    }

    /** The card at the centre now. */
    int centred() { return RoseFan.nearest(getScrollX(), pitch(), count()); }

    /** Centres card index without animation, now if laid out, else at the next layout. */
    void place(int index) {
        placed = true;
        pendingIndex = index;
        if (getWidth() > 0 && !isLayoutRequested()) applyPending();
    }

    private void applyPending() {
        if (pendingIndex < 0) return;
        int index = RoseFan.step(pendingIndex, 0, count());
        pendingIndex = -1;
        scrollTo(RoseFan.scrollFor(index, pitch()), 0);
        fan();
    }

    /** Settles on a card: a smooth move, or an immediate one when the system has animations off. */
    private void settle(int index) {
        int x = RoseFan.scrollFor(RoseFan.step(index, 0, count()), pitch());
        if (ValueAnimator.areAnimatorsEnabled()) smoothScrollTo(x, 0);
        else scrollTo(x, 0);
    }

    @Override
    protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
        // Side padding that lets the first and last cards reach the centre (RoseFan.sidePadding), set before the cards
        // are measured so the scroll range that placement clamps to already includes it.
        ViewGroup cards = cards();
        if (cards != null) {
            int side = RoseFan.sidePadding(MeasureSpec.getSize(widthMeasureSpec), pitch());
            if (cards.getPaddingLeft() != side || cards.getPaddingRight() != side) {
                cards.setPadding(side, cards.getPaddingTop(), side, cards.getPaddingBottom());
            }
        }
        super.onMeasure(widthMeasureSpec, heightMeasureSpec);
    }

    @Override
    protected void onLayout(boolean changed, int l, int t, int r, int b) {
        super.onLayout(changed, l, t, r, b);
        applyPending();
        fan();
    }

    @Override
    protected void onScrollChanged(int x, int y, int oldX, int oldY) {
        super.onScrollChanged(x, y, oldX, oldY);
        fan();
    }

    /** Applies RoseFan to every card from its position now: offset, turn, shrink and depth (draw and touch order). */
    private void fan() {
        ViewGroup cards = cards();
        if (cards == null || getWidth() <= 0) return;
        float centre = getScrollX() + getWidth() / 2f;
        for (int i = 0; i < cards.getChildCount(); i++) {
            View card = cards.getChildAt(i);
            float k = RoseFan.offset(card.getLeft() + card.getWidth() / 2f, centre, Math.max(1, card.getWidth()));
            card.setCameraDistance(RoseFan.cameraDistance(RoseFan.PERSPECTIVE_DP * density, dpi));
            card.setTranslationX(RoseFan.shiftDp(k) * density);
            card.setRotationY(RoseFan.turnDeg(k));
            float s = RoseFan.scale(k);
            card.setScaleX(s);
            card.setScaleY(s);
            // Z orders drawing and touch dispatch (ViewGroup sorts children by Z), so the centre card is on top.
            card.setTranslationZ(RoseFan.depth(k) * density / 2f);
        }
    }

    @Override
    public void fling(int velocityX) {
        // One card in the fling's direction, from the card nearest now; never the platform's free momentum.
        flung = true;
        settle(centred() + (velocityX > 0 ? 1 : velocityX < 0 ? -1 : 0));
    }

    @Override
    public boolean onTouchEvent(MotionEvent event) {
        int action = event.getActionMasked();
        if (action == MotionEvent.ACTION_DOWN) flung = false;
        boolean handled = super.onTouchEvent(event);
        // A drag that ends without a fling (or is cancelled, e.g. by the system gesture) settles on the nearest card.
        if ((action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) && !flung) settle(centred());
        return handled;
    }

    @Override
    public boolean executeKeyEvent(KeyEvent event) {
        if (event.getAction() == KeyEvent.ACTION_DOWN) {
            int code = event.getKeyCode();
            int delta = code == KeyEvent.KEYCODE_DPAD_LEFT ? -1 : code == KeyEvent.KEYCODE_DPAD_RIGHT ? 1 : 0;
            if (getLayoutDirection() == LAYOUT_DIRECTION_RTL) delta = -delta;
            if (delta != 0 && count() > 0) {
                int target = RoseFan.step(centred(), delta, count());
                settle(target);
                ViewGroup cards = cards();
                if (cards != null && cards.hasFocus()) cards.getChildAt(target).requestFocus();
                return true;
            }
        }
        return super.executeKeyEvent(event);
    }

    /**
     * Accessibility focus moved to card (its delegate reports TYPE_VIEW_ACCESSIBILITY_FOCUSED): centre it, so the card
     * a screen reader is on is the one in front. A card that is not one of this carousel's own is ignored.
     */
    void focused(View card) {
        ViewGroup cards = cards();
        int index = cards == null ? -1 : cards.indexOfChild(card);
        if (index >= 0 && index != centred()) settle(index);
    }

    /** The delegate that reports a card's accessibility focus to focused(). */
    AccessibilityDelegate centreOnFocus() {
        return new AccessibilityDelegate() {
            @Override
            public void sendAccessibilityEvent(View host, int eventType) {
                super.sendAccessibilityEvent(host, eventType);
                if (eventType == AccessibilityEvent.TYPE_VIEW_ACCESSIBILITY_FOCUSED) focused(host);
            }
        };
    }
}
