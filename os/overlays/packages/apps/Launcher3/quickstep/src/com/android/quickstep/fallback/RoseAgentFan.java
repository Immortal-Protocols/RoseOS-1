/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import android.animation.ValueAnimator;
import android.content.Context;
import android.os.Bundle;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.HorizontalScrollView;

/**
 * RoseOS (#410): the switcher's Rose carousel as the Oxblood HiFi's centred fan with depth (RoseFan), scrolling on its
 * own, independent of Launcher3's human-task carousel. It only lays out and moves the cards RoseAgentStrip gives it:
 * it never reads the platform, never decides which cards exist, and never captures or opens anything.
 *
 * It always settles exactly on a card: a release or cancel without a fling centres the nearest card, and a fling moves
 * one card in its direction, so a fling never skips work the person has not seen. Every other way to scroll also moves
 * exactly one card: left/right, page and space keys, a mouse wheel or rotary input, and a screen reader's scroll
 * actions; at either end the key is not taken, so focus can leave the row. Keyboard or accessibility focus on a card
 * centres it. Positions come from where the cards actually are, so it holds right to left. With animations off
 * (Settings > Accessibility > Remove animations, the system's reduced motion) every move is immediate; the fan itself
 * is static geometry and stays.
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

    private boolean rtl() { return getLayoutDirection() == LAYOUT_DIRECTION_RTL; }

    private ViewGroup cards() { return getChildCount() > 0 ? (ViewGroup) getChildAt(0) : null; }

    private int count() {
        ViewGroup cards = cards();
        return cards == null ? 0 : cards.getChildCount();
    }

    /** Each card's centre in scroll content coordinates, where it is laid out now. */
    private float[] centres() {
        ViewGroup cards = cards();
        float[] out = new float[count()];
        for (int i = 0; i < out.length; i++) {
            View card = cards.getChildAt(i);
            out[i] = cards.getLeft() + card.getLeft() + card.getWidth() / 2f;
        }
        return out;
    }

    /** The card at the centre now. */
    int centred() { return RoseFan.nearest(centres(), getScrollX() + getWidth() / 2f); }

    /** The scroll that centres card index. */
    private int scrollFor(int index) {
        float[] centres = centres();
        if (centres.length == 0) return 0;
        return RoseFan.scrollToCentre(centres[RoseFan.step(index, 0, centres.length)], getWidth());
    }

    /** Centres card index without animation, now if laid out, else at the next layout. */
    void place(int index) {
        placed = true;
        pendingIndex = index;
        if (getWidth() > 0 && !isLayoutRequested()) applyPending();
    }

    private void applyPending() {
        if (pendingIndex < 0) return;
        int index = pendingIndex;
        pendingIndex = -1;
        scrollTo(scrollFor(index), 0);
        fan();
    }

    /** Settles on a card: a smooth move, or an immediate one when the system has animations off. */
    private void settle(int index) {
        int x = scrollFor(index);
        if (ValueAnimator.areAnimatorsEnabled()) smoothScrollTo(x, 0);
        else scrollTo(x, 0);
    }

    /** Moves by orderStep cards from the focused card (or the centred one); false when there is nowhere to go. */
    private boolean move(int orderStep) {
        if (count() == 0 || orderStep == 0) return false;
        ViewGroup cards = cards();
        View focused = cards.getFocusedChild();
        int from = focused != null ? cards.indexOfChild(focused) : centred();
        int target = RoseFan.step(from, orderStep, count());
        if (target == from) return false;
        settle(target);
        if (focused != null) cards.getChildAt(target).requestFocus();
        return true;
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
        settle(centred() + RoseFan.orderStep(velocityX > 0 ? 1 : velocityX < 0 ? -1 : 0, rtl()));
    }

    @Override
    public boolean onInterceptTouchEvent(MotionEvent event) {
        // A gesture that starts on a card reaches this view's onTouchEvent only once the drag is taken, without its
        // ACTION_DOWN: start it unflung here too, or a later slow drag would not settle.
        if (event.getActionMasked() == MotionEvent.ACTION_DOWN) flung = false;
        return super.onInterceptTouchEvent(event);
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
        int code = event.getKeyCode();
        int step;
        switch (code) {
            case KeyEvent.KEYCODE_DPAD_LEFT: step = RoseFan.orderStep(-1, rtl()); break;
            case KeyEvent.KEYCODE_DPAD_RIGHT: step = RoseFan.orderStep(1, rtl()); break;
            case KeyEvent.KEYCODE_PAGE_UP: case KeyEvent.KEYCODE_MOVE_HOME: step = -1; break;
            case KeyEvent.KEYCODE_PAGE_DOWN: case KeyEvent.KEYCODE_MOVE_END: step = 1; break;
            case KeyEvent.KEYCODE_SPACE: step = event.isShiftPressed() ? -1 : 1; break;
            default: return super.executeKeyEvent(event);
        }
        // Never the platform's page or full scroll, which stop between cards; at either end the key is not taken.
        return event.getAction() == KeyEvent.ACTION_DOWN && move(step);
    }

    @Override
    public boolean onGenericMotionEvent(MotionEvent event) {
        if (event.getAction() == MotionEvent.ACTION_SCROLL) {
            float h = event.getAxisValue(MotionEvent.AXIS_HSCROLL);
            if (h == 0 && event.isFromSource(InputDevice.SOURCE_CLASS_POINTER)
                    && (event.getMetaState() & KeyEvent.META_SHIFT_ON) != 0) {
                h = -event.getAxisValue(MotionEvent.AXIS_VSCROLL);
            }
            if (h == 0) h = event.getAxisValue(MotionEvent.AXIS_SCROLL);
            // One card per scroll event (a wheel notch or a rotary detent), never a free scroll between cards; the event
            // is taken even at an end, so the row is never left between cards.
            if (h != 0) {
                move(RoseFan.orderStep(h > 0 ? 1 : -1, rtl()));
                return true;
            }
        }
        return super.onGenericMotionEvent(event);
    }

    @Override
    public boolean performAccessibilityAction(int action, Bundle arguments) {
        // A screen reader's scroll moves one card, never a page that stops between cards.
        if (action == AccessibilityNodeInfo.ACTION_SCROLL_FORWARD) return move(1);
        if (action == AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD) return move(-1);
        if (action == android.R.id.accessibilityActionScrollRight) return move(RoseFan.orderStep(1, rtl()));
        if (action == android.R.id.accessibilityActionScrollLeft) return move(RoseFan.orderStep(-1, rtl()));
        return super.performAccessibilityAction(action, arguments);
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

    /** Keyboard focus moving to a card centres it too. */
    OnFocusChangeListener centreOnKeyFocus() {
        return (card, hasFocus) -> { if (hasFocus) focused(card); };
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
