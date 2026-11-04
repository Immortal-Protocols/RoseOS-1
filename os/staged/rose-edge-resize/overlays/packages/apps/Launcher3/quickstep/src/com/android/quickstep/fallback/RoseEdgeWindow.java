/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import android.content.Context;
import android.content.Intent;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.RadialGradient;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Region;
import android.graphics.Shader;
import android.hardware.display.DisplayManager;
import android.util.SparseArray;
import android.view.Display;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewConfiguration;
import android.view.ViewTreeObserver;
import android.view.WindowManager;

/**
 * RoseOS (#410 E2c): the raised edge's window, Launcher3's half of the system edge. It is shown while RoseEdgeState
 * says a display's edge is up and removed when it lowers. It draws Home's design contract (homeEdge: plate, hairline,
 * the Rose and switcher wells, the pearl and the switcher glyph; RoseEdgeLayout) in a TYPE_NAVIGATION_BAR_PANEL window
 * along the bottom of the display, above the shortened app (Launcher3 holds INTERNAL_SYSTEM_WINDOW; no new permission).
 *
 * The microphone-input slot is a hole (Chief's option (a), agreed with OS Core on #410): nothing is drawn there, it is
 * outside the window's touchable region, and its screen bounds go to Shell through RoseEdgeState.setMicSlot, where
 * SystemUI's existing trusted tray control draws and handles it. This window never reads or changes microphone state.
 *
 * Taps: Rose lowers the edge and asks the host to open Rose; the switcher lowers it and asks for the switcher. A swipe
 * down of more than RoseEdgeGesture.RAISE_DP lowers it. A swipe up belongs to the system gesture (Launcher3's input
 * consumer), not to this window. Main thread only.
 */
public final class RoseEdgeWindow implements RoseEdgeState.Listener {
    /** What the host (TouchInteractionService, via rose-edge-launcher3.patch) does for the edge's two own controls. */
    public interface Actions {
        void openRose(int displayId);

        void showSwitcher(int displayId);
    }

    /**
     * The Rose control's Home start (proposed to Home on #410; Home owns the meaning): an ordinary HOME intent that Home
     * reads as untrusted UI routing, opening its own Rose conversation. Nothing else crosses to Home.
     */
    public static final String EXTRA_DESTINATION = "dev.pixel.voice.extra.ROSE_EDGE_DESTINATION";
    public static final String DESTINATION_ROSE = "rose";

    /** The intent the host starts for the Rose control. */
    public static Intent openRoseIntent() {
        return new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).putExtra(EXTRA_DESTINATION, DESTINATION_ROSE);
    }

    private final Context mContext;
    private final Actions mActions;
    private final SparseArray<EdgeView> mShown = new SparseArray<>();

    public RoseEdgeWindow(Context context, Actions actions) {
        mContext = context.getApplicationContext() != null ? context.getApplicationContext() : context;
        mActions = actions;
    }

    /** Starts drawing raised edges: from now on the edge may rise (RoseEdgeState.hasListener). */
    public void start() {
        RoseEdgeState.addListener(this);
    }

    /** Stops: removes every shown edge and stops listening, so the edge can no longer rise. */
    public void stop() {
        RoseEdgeState.removeListener(this);
        for (int i = mShown.size() - 1; i >= 0; i--) remove(mShown.keyAt(i));
    }

    @Override
    public void onRaisedChanged(int displayId, boolean raised) {
        if (raised) show(displayId);
        else remove(displayId);
    }

    /** The view shown on a display, or null (tests and the host read it). */
    EdgeView shownOn(int displayId) {
        return mShown.get(displayId);
    }

    private void show(int displayId) {
        if (mShown.get(displayId) != null) return;
        Display display = mContext.getSystemService(DisplayManager.class).getDisplay(displayId);
        if (display == null) return;
        Context window = mContext.createWindowContext(display, WindowManager.LayoutParams.TYPE_NAVIGATION_BAR_PANEL,
                null);
        EdgeView view = new EdgeView(window, displayId);
        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(WindowManager.LayoutParams.MATCH_PARENT,
                RoseEdgeLayout.windowHeightPx(view.mDensity, 0), WindowManager.LayoutParams.TYPE_NAVIGATION_BAR_PANEL,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT);
        lp.gravity = Gravity.BOTTOM;
        lp.setTitle("RoseEdge");
        lp.setFitInsetsTypes(0);
        window.getSystemService(WindowManager.class).addView(view, lp);
        mShown.put(displayId, view);
    }

    private void remove(int displayId) {
        EdgeView view = mShown.get(displayId);
        if (view == null) return;
        mShown.remove(displayId);
        // The shell drops the slot with the lower; clearing it here keeps a re-raise from reusing stale geometry.
        RoseEdgeState.setMicSlot(displayId, null, 0, 0, view.mDensity);
        view.getContext().getSystemService(WindowManager.class).removeViewImmediate(view);
    }

    /** Home's edge (homeEdge), with the microphone slot left as a transparent, untouchable hole. */
    final class EdgeView extends View implements ViewTreeObserver.OnComputeInternalInsetsListener {
        final int mDisplayId;
        final float mDensity;
        private final Paint mPlate = new Paint(Paint.ANTI_ALIAS_FLAG), mSheen = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint mHairline = new Paint(Paint.ANTI_ALIAS_FLAG), mWell = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint mRing = new Paint(Paint.ANTI_ALIAS_FLAG), mPearl = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint mGlyph = new Paint(Paint.ANTI_ALIAS_FLAG), mFront = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Path mOutline = new Path();
        private final int mSlop;
        private float mDownY = Float.NaN;
        private int mDownTarget = -1;

        EdgeView(Context context, int displayId) {
            super(context);
            mDisplayId = displayId;
            mDensity = context.getResources().getDisplayMetrics().density;
            mSlop = ViewConfiguration.get(context).getScaledTouchSlop();
            mHairline.setStyle(Paint.Style.STROKE);
            mHairline.setStrokeWidth(1 * mDensity);
            mHairline.setColor(0xA6D65468);
            mHairline.setShadowLayer(10 * mDensity, 0, 4 * mDensity, 0x73000000);
            mRing.setStyle(Paint.Style.STROKE);
            mRing.setStrokeWidth(1 * mDensity);
            mRing.setColor(0x66ECCDBE);
            mGlyph.setStyle(Paint.Style.STROKE);
            mGlyph.setStrokeWidth(2 * mDensity);
            mGlyph.setStrokeJoin(Paint.Join.ROUND);
            mGlyph.setColor(0xFFEADBCD);
            mFront.setColor(0xFF331019);
            setContentDescription("System tray");
        }

        Rect control(int which) {
            return RoseEdgeLayout.control(which, getWidth(), mDensity, 0);
        }

        @Override
        protected void onAttachedToWindow() {
            super.onAttachedToWindow();
            getViewTreeObserver().addOnComputeInternalInsetsListener(this);
        }

        @Override
        protected void onDetachedFromWindow() {
            getViewTreeObserver().removeOnComputeInternalInsetsListener(this);
            super.onDetachedFromWindow();
        }

        @Override
        protected void onLayout(boolean changed, int left, int top, int right, int bottom) {
            super.onLayout(changed, left, top, right, bottom);
            float h = RoseEdgeLayout.EDGE_HEIGHT_DP * mDensity, flat = RoseEdgeLayout.FLAT_TOP_DP * mDensity;
            float r = RoseEdgeLayout.FILLET_DP * mDensity, w = getWidth();
            // homeEdge.outline: M0,0 arc r34 (centre 34,0) to 34,34; L W-34,34; arc r34 (centre W-34,0) to W,0; L W,H.
            mOutline.reset();
            mOutline.moveTo(0, 0);
            mOutline.arcTo(new RectF(0, -r, 2 * r, r), 180, -90, false);
            mOutline.lineTo(w - r, flat);
            mOutline.arcTo(new RectF(w - 2 * r, -r, w, r), 90, -90, false);
            mOutline.lineTo(w, getHeight());
            mOutline.lineTo(0, getHeight());
            mOutline.close();
            mPlate.setShader(new LinearGradient(0, 0, 0, h, new int[] {0xFF4A141A, 0xFF4A141A, 0xFF3C0E13, 0xFF330C10},
                    new float[] {0, 34 / 92f, 46 / 92f, 1}, Shader.TileMode.CLAMP));
            mSheen.setShader(new LinearGradient(0, 0, 0, h * 0.4f, 0x12FF96A5, 0x00FF96A5, Shader.TileMode.CLAMP));
            mWell.setShader(new LinearGradient(0, 0, 0, RoseEdgeLayout.WELL_HEIGHT_DP * mDensity, 0x47140407, 0x1F46121C,
                    Shader.TileMode.CLAMP));
            Rect rose = control(RoseEdgeLayout.ROSE);
            // pearl.ruby: radial, centred at (34%, 28%) of the 16dp pearl, radius the distance to its farthest corner.
            float pr = 8 * mDensity, gx = rose.exactCenterX() - pr + 0.34f * 2 * pr;
            float gy = rose.exactCenterY() - pr + 0.28f * 2 * pr;
            float far = (float) Math.hypot((1 - 0.34f) * 2 * pr, (1 - 0.28f) * 2 * pr);
            mPearl.setShader(new RadialGradient(gx, gy, far,
                    new int[] {0xFFFFE6E8, 0xFFFFE6E8, 0xFFE8606F, 0xFFC43048, 0xFF8A1428, 0xFF4A0612},
                    new float[] {0, 0.07f, 0.2f, 0.46f, 0.76f, 1}, Shader.TileMode.CLAMP));
            Display display = getDisplay();
            android.util.DisplayMetrics metrics = new android.util.DisplayMetrics();
            if (display != null) display.getRealMetrics(metrics);
            // The window is bottom-gravity, full width and exactly as tall as this view (its own layout params), so
            // its top on screen is the display height less that height.
            Rect slot = RoseEdgeLayout.micSlotOnScreen(getWidth(), mDensity, 0, metrics.heightPixels - getHeight());
            RoseEdgeState.setMicSlot(mDisplayId, slot, metrics.widthPixels, metrics.heightPixels, mDensity);
        }

        @Override
        protected void onDraw(Canvas canvas) {
            canvas.save();
            // The microphone slot stays empty: SystemUI's tray control is there.
            canvas.clipOutRect(control(RoseEdgeLayout.MIC));
            canvas.drawPath(mOutline, mPlate);
            canvas.drawPath(mOutline, mSheen);
            canvas.drawPath(mOutline, mHairline);
            for (int which : new int[] {RoseEdgeLayout.ROSE, RoseEdgeLayout.SWITCHER}) {
                RectF well = new RectF(control(which));
                float inset = (well.height() - RoseEdgeLayout.WELL_HEIGHT_DP * mDensity) / 2f;
                well.inset(0, inset);
                float radius = RoseEdgeLayout.WELL_RADIUS_DP * mDensity;
                canvas.save();
                canvas.translate(0, well.top);
                well.offset(0, -well.top);
                canvas.drawRoundRect(well, radius, radius, mWell);
                canvas.drawRoundRect(well, radius, radius, mRing);
                canvas.restore();
            }
            Rect rose = control(RoseEdgeLayout.ROSE);
            canvas.drawCircle(rose.exactCenterX(), rose.exactCenterY(), 8 * mDensity, mPearl);
            drawSwitcherGlyph(canvas, control(RoseEdgeLayout.SWITCHER));
            canvas.restore();
        }

        /** homeEdge.switcherGlyph: four cards in a 64 x 28 box, viewBox (2, 3, 60, 20). */
        private void drawSwitcherGlyph(Canvas canvas, Rect box) {
            float sx = 64 * mDensity / 60f, sy = 28 * mDensity / 20f, s = Math.min(sx, sy);
            float left = box.exactCenterX() - 30 * s, top = box.exactCenterY() - 10 * s;
            canvas.save();
            canvas.translate(left - 2 * s, top - 3 * s);
            canvas.scale(s, s);
            float stroke = mGlyph.getStrokeWidth();
            mGlyph.setStrokeWidth(2);
            for (float x : new float[] {0, 32}) {
                Path back = card(x, 11), front = card(x, 5);
                // switcherGlyph.rubyEdge: the right back card's stroke runs ink to ruby, user space x 34 to 62.
                mGlyph.setShader(x == 0 ? null : new LinearGradient(34, 0, 62, 0, new int[] {0xFFEADBCD, 0xFFEADBCD,
                        0xFFFF4A5E, 0xFFFF4A5E}, new float[] {0, 0.5f, 0.8f, 1}, Shader.TileMode.CLAMP));
                canvas.drawPath(back, mGlyph);
                mGlyph.setShader(null);
                canvas.drawPath(front, mFront);
                canvas.drawPath(front, mGlyph);
            }
            mGlyph.setStrokeWidth(stroke);
            canvas.restore();
        }

        /** "M10 y h18 l-6 10 H4 z" shifted by x. */
        private Path card(float x, float y) {
            Path p = new Path();
            p.moveTo(10 + x, y);
            p.lineTo(28 + x, y);
            p.lineTo(22 + x, y + 10);
            p.lineTo(4 + x, y + 10);
            p.close();
            return p;
        }

        @Override
        public void onComputeInternalInsets(ViewTreeObserver.InternalInsetsInfo info) {
            // Touchable: the whole window except the microphone slot, which belongs to Shell's tray control.
            info.setTouchableInsets(ViewTreeObserver.InternalInsetsInfo.TOUCHABLE_INSETS_REGION);
            info.touchableRegion.set(0, 0, getWidth(), getHeight());
            info.touchableRegion.op(control(RoseEdgeLayout.MIC), Region.Op.DIFFERENCE);
        }

        /** Which control a window point hits, or -1. The slot never counts: its touches are not this window's. */
        int hit(float x, float y) {
            for (int which : new int[] {RoseEdgeLayout.ROSE, RoseEdgeLayout.SWITCHER}) {
                if (control(which).contains(Math.round(x), Math.round(y))) return which;
            }
            return -1;
        }

        @Override
        public boolean onTouchEvent(MotionEvent e) {
            if (control(RoseEdgeLayout.MIC).contains(Math.round(e.getX()), Math.round(e.getY()))
                    && e.getActionMasked() == MotionEvent.ACTION_DOWN) {
                return false;
            }
            switch (e.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    mDownY = e.getY();
                    mDownTarget = hit(e.getX(), e.getY());
                    return true;
                case MotionEvent.ACTION_UP: {
                    float dy = e.getY() - mDownY;
                    int target = Math.abs(dy) <= mSlop ? hit(e.getX(), e.getY()) : -1;
                    if (dy > RoseEdgeGesture.RAISE_DP * mDensity) {
                        RoseEdgeState.lower(mDisplayId);
                    } else if (target >= 0 && target == mDownTarget) {
                        RoseEdgeState.lower(mDisplayId);
                        if (target == RoseEdgeLayout.ROSE) mActions.openRose(mDisplayId);
                        else mActions.showSwitcher(mDisplayId);
                    }
                    mDownTarget = -1;
                    return true;
                }
                case MotionEvent.ACTION_CANCEL:
                    mDownTarget = -1;
                    return true;
                default:
                    return true;
            }
        }
    }
}
