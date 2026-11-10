/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Insets;
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
import android.os.Binder;
import android.os.SystemProperties;
import android.provider.Settings;
import android.util.SparseArray;
import android.view.Display;
import android.view.Gravity;
import android.view.HapticFeedbackConstants;
import android.view.InsetsFrameProvider;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewConfiguration;
import android.view.ViewTreeObserver;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityNodeProvider;
import android.view.inputmethod.InputMethodInfo;
import android.view.inputmethod.InputMethodManager;

/**
 * RoseOS (#410 E2c): the raised edge's window, Launcher3's half of the system edge. It is shown while a bottom swipe
 * over an app reveals it (RoseEdgeState peek: it rises from the bottom with the finger and dims once the switcher stage
 * takes over), stays while RoseEdgeState says the display's edge is up, and is removed when it lowers or a drag that
 * revealed it ends without raising it. It draws Home's design contract (homeEdge: plate, hairline,
 * the Rose and switcher wells, the pearl and the switcher glyph; RoseEdgeLayout) in a TYPE_NAVIGATION_BAR_PANEL window
 * along the bottom of the display, above the shortened app (Launcher3 holds INTERNAL_SYSTEM_WINDOW; no new permission).
 *
 * The microphone-input slot belongs to SystemUI (Chief's option (a), agreed with OS Core on #410): it is outside the
 * window's touchable region, and its screen bounds go to Shell through RoseEdgeState.setMicSlot, where SystemUI's
 * existing trusted tray control draws and handles it once the shell has raised the edge. Until that control covers it
 * (while a drag reveals or reverses the tray, and between a release and the shell's onRaised), this window draws the
 * mic button in the slot with the other buttons, in the control's own "status unavailable" look (its well and glyph
 * at UNKNOWN_ALPHA), so the mic rises and falls with the tray without claiming muted or unmuted (1A). SystemUI's opaque
 * control, added later over the slot, hides it. Like that control, the stand-in shows only while the Rose status tray
 * is enabled (TRAY_PROPERTY): with no control to come, a mic would be a button that does nothing. This window never
 * reads or changes microphone state.
 *
 * Taps: Rose lowers the edge and asks the host to open Rose; the switcher lowers it and asks for the switcher. A swipe
 * down of more than RoseEdgeGesture.RAISE_DP lowers it. A drag up on the open tray goes on toward the switcher: past
 * RoseEdgeGesture.TRAY_TO_SWITCHER_DP the switcher well lights with a haptic and a release opens the switcher (back
 * below TRAY_TO_SWITCHER_EXIT_DP, it does not). A swipe that starts in the bottom
 * system area still belongs to the system gesture (Launcher3's input consumer, RoseEdgeSwipe). Main thread only.
 *
 * Insets (with OS Core): once the shell has really shortened the app (RoseEdgeState onResized), the window provides a
 * systemOverlays() inset of the lift (RoseEdgeGesture.raisedInsetPx, 58dp) along its bottom, so windows that still
 * reach the bottom of the display, the keyboard and the shade, can stand clear of the raised edge. Never before the
 * shell's onRaised: until then the app is full height and an app padding by systemBars() would lift twice. Never
 * navigationBars(): the focused app controls those, and an immersive app would hide the edge. The shortened app does
 * not overlap the source frame, so it gets none. The inset goes with the window when the edge lowers. The window
 * declares its one provider when it is added, at size 0, and a relayout only resizes it: the window manager refuses a
 * relayout that adds, removes or re-identifies a provider ("Insets amount can not be changed after the window is
 * added", WindowManagerService.relayoutWindow), which would crash this process on every raise.
 */
public final class RoseEdgeWindow implements RoseEdgeState.Listener {
    /** What the host (TouchInteractionService, via rose-edge-launcher3.patch) does for the edge's two own controls. */
    public interface Actions {
        void openRose(int displayId);

        void showSwitcher(int displayId);
    }

    /** Rose Agent's package: Home (#410, oxblood-contract.json homeEdge.controls.rose.destination.raisedEdge). */
    public static final String ROSE_PACKAGE = "dev.pixel.voice";

    /**
     * The intent the host starts for the Rose control, as Home's contract gives it: when Rose Agent holds HOME, its
     * existing launch intent (the icon tap: ACTION_MAIN + CATEGORY_LAUNCHER, explicit, FLAG_ACTIVITY_NEW_TASK), which
     * Home shows as Rose Home with the conversation over it; otherwise the plain HOME intent. No new extra, action or
     * exported entry, and never FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY (Home reads that as a relaunch). Visibility of the
     * two packages comes from Launcher3's existing QUERY_ALL_PACKAGES.
     */
    public static Intent openRoseIntent(PackageManager pm) {
        Intent home = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        ResolveInfo holder = pm.resolveActivity(home, PackageManager.MATCH_DEFAULT_ONLY);
        if (holder == null || holder.activityInfo == null || !ROSE_PACKAGE.equals(holder.activityInfo.packageName)) {
            return home;
        }
        Intent launch = pm.getLaunchIntentForPackage(ROSE_PACKAGE);
        if (launch == null) return home;
        return launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
    }

    private final Context mContext;
    private final Actions mActions;
    private final SparseArray<EdgeView> mShown = new SparseArray<>();
    /** SystemUI's mic control (RoseEdgeMicSlot.Control): its well (OxbloodEdge.Well.mic) and unknown-state alpha. */
    static final float MIC_WELL_DP = 32, MIC_WELL_RADIUS_DP = 9;
    static final int MIC_UNKNOWN_ALPHA = 110;
    /** The Rose status tray switch SystemUI's mic control is gated on (RoseEdgeMicSlot, default off). */
    static final String TRAY_PROPERTY = "persist.wm.debug.pixel_agent_tray";
    /** Identifies this window's inset source to the window manager (as the taskbar's insets owner does). */
    private final Binder mInsetsOwner = new Binder();
    /** Plan 1E: the theme the edge draws (RoseThemeSetting, SystemUI's mirror); every shown edge follows a change. */
    private String mTheme = RoseTheme.DEFAULT;
    private final Runnable mThemeChanged = this::onThemeChanged;

    public RoseEdgeWindow(Context context, Actions actions) {
        mContext = context.getApplicationContext() != null ? context.getApplicationContext() : context;
        mActions = actions;
    }

    /** Starts drawing raised edges: from now on the edge may rise (RoseEdgeState.hasListener). */
    public void start() {
        mTheme = RoseThemeSetting.current(mContext);
        RoseThemeSetting.listen(mContext, mThemeChanged);
        RoseEdgeState.addListener(this);
        KeyboardDefault.applyOnce(mContext);
    }

    /**
     * RoseOS (1M): the Rose keyboard (os/overlays/packages/apps/RoseKeyboard), whose rail and chin the edge shares, is
     * made the user's keyboard once, the first time the edge's host starts with it installed: added to the enabled
     * input methods and selected. A choice the user makes afterwards stands; this never runs again for that user (a
     * marker in Launcher3's device-protected preferences). Only a system-installed copy is selected, the stock
     * keyboard stays installed and enabled as the fallback, and nothing is done while the keyboard is absent. It uses
     * Launcher3's existing WRITE_SECURE_SETTINGS (QuickStep's manifest); no permission is added.
     */
    static final class KeyboardDefault {
        static final String PACKAGE = "dev.pixel.rose.keyboard";
        static final String ID = PACKAGE + "/.RoseKeyboardService";
        static final String PREFS = "rose_keyboard", DONE = "selected_once";

        private KeyboardDefault() {}

        /** Returns true when it selected the keyboard now. */
        static boolean applyOnce(Context context) {
            try {
                Context storage = context.createDeviceProtectedStorageContext();
                SharedPreferences prefs = storage.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
                if (prefs.getBoolean(DONE, false) || !installedAsSystem(context)) return false;
                String enabled = Settings.Secure.getString(context.getContentResolver(),
                        Settings.Secure.ENABLED_INPUT_METHODS);
                Settings.Secure.putString(context.getContentResolver(), Settings.Secure.ENABLED_INPUT_METHODS,
                        withEnabled(enabled, ID));
                Settings.Secure.putString(context.getContentResolver(), Settings.Secure.DEFAULT_INPUT_METHOD, ID);
                prefs.edit().putBoolean(DONE, true).apply();
                return true;
            } catch (RuntimeException e) {
                // Never let a keyboard default stop the edge: the user can still pick the keyboard in Settings.
                return false;
            }
        }

        /** The system's input method list holds the keyboard, from a system-installed package. */
        static boolean installedAsSystem(Context context) {
            InputMethodManager imm = context.getSystemService(InputMethodManager.class);
            if (imm == null) return false;
            for (InputMethodInfo imi : imm.getInputMethodList()) {
                if (!ID.equals(imi.getId())) continue;
                ApplicationInfo app = imi.getServiceInfo() == null ? null : imi.getServiceInfo().applicationInfo;
                return app != null && (app.flags & ApplicationInfo.FLAG_SYSTEM) != 0;
            }
            return false;
        }

        /** ENABLED_INPUT_METHODS ("id;subtype:id2...") with id added once, every existing entry kept. */
        static String withEnabled(String enabled, String id) {
            if (enabled == null || enabled.isEmpty()) return id;
            for (String entry : enabled.split(":")) {
                String entryId = entry.split(";", 2)[0];
                if (entryId.equals(id)) return enabled;
            }
            return enabled + ":" + id;
        }
    }

    /** Stops: removes every shown edge and stops listening, so the edge can no longer rise. */
    public void stop() {
        RoseThemeSetting.unlisten(mThemeChanged);
        RoseEdgeState.removeListener(this);
        for (int i = mShown.size() - 1; i >= 0; i--) remove(mShown.keyAt(i));
    }

    /** Plan 1E: the mirrored theme changed: each shown edge redraws in it (colour only). */
    private void onThemeChanged() {
        String next = RoseThemeSetting.current(mContext);
        if (next.equals(mTheme)) return;
        mTheme = next;
        for (int i = 0; i < mShown.size(); i++) mShown.valueAt(i).setTheme(next);
    }

    @Override
    public void onRaisedChanged(int displayId, boolean raised) {
        if (raised) {
            EdgeView view = show(displayId);
            if (view != null) view.setPeek(1, 0, RoseEdgeGesture.TRAY);
        } else {
            remove(displayId);
        }
    }

    @Override
    public void onResized(int displayId) {
        EdgeView view = mShown.get(displayId);
        if (view == null || !(view.getLayoutParams() instanceof WindowManager.LayoutParams)) return;
        WindowManager.LayoutParams lp = (WindowManager.LayoutParams) view.getLayoutParams();
        int lift = RoseEdgeGesture.raisedInsetPx(view.mDensity);
        if (insetPx(lp) == lift) return;
        lp.providedInsets = chinInsets(lift);  // the same provider (owner, index, type) as at add: only its size changes
        view.getContext().getSystemService(WindowManager.class).updateViewLayout(view, lp);
    }

    /**
     * The window's one inset provider, bottomPx tall: a systemOverlays() source along its bottom, identified by this
     * window's owner, index 0 and type, exactly as when it was added.
     */
    InsetsFrameProvider[] chinInsets(int bottomPx) {
        return new InsetsFrameProvider[] {new InsetsFrameProvider(mInsetsOwner, 0, WindowInsets.Type.systemOverlays())
                .setInsetsSize(Insets.of(0, 0, 0, bottomPx))};
    }

    /** The bottom systemOverlays() inset these layout params provide, or 0 (tests read it). */
    static int insetPx(WindowManager.LayoutParams lp) {
        if (lp == null || lp.providedInsets == null) return 0;
        for (InsetsFrameProvider provider : lp.providedInsets) {
            if (provider != null && provider.getType() == WindowInsets.Type.systemOverlays()
                    && provider.getInsetsSize() != null) {
                return provider.getInsetsSize().bottom;
            }
        }
        return 0;
    }

    @Override
    public void onPeek(int displayId, float reveal, float approach, int stage) {
        if (reveal < 0) {
            // The drag ended: the tray stays only if the edge rose (then it is shown fully).
            EdgeView view = mShown.get(displayId);
            if (!RoseEdgeState.isRaised(displayId)) remove(displayId);
            else if (view != null) view.setPeek(1, 0, RoseEdgeGesture.TRAY);
            return;
        }
        EdgeView view = show(displayId);
        if (view != null) view.setPeek(reveal, approach, stage);
    }

    /** The view shown on a display, or null (tests and the host read it). */
    EdgeView shownOn(int displayId) {
        return mShown.get(displayId);
    }

    private EdgeView show(int displayId) {
        EdgeView shown = mShown.get(displayId);
        if (shown != null) return shown;
        Display display = mContext.getSystemService(DisplayManager.class).getDisplay(displayId);
        if (display == null) return null;
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
        // Declared now, empty; onResized only resizes it (a provider added on relayout is refused by the system).
        lp.providedInsets = chinInsets(0);
        window.getSystemService(WindowManager.class).addView(view, lp);
        mShown.put(displayId, view);
        return view;
    }

    private void remove(int displayId) {
        EdgeView view = mShown.get(displayId);
        if (view == null) return;
        mShown.remove(displayId);
        // The shell drops the slot with the lower; clearing it here keeps a re-raise from reusing stale geometry.
        RoseEdgeState.setMicSlot(displayId, null, 0, 0, view.mDensity);
        view.getContext().getSystemService(WindowManager.class).removeViewImmediate(view);
    }

    /**
     * Home's edge (homeEdge). The microphone slot is untouchable here (its touches are SystemUI's control's); this
     * view draws the mic there only in the "status unavailable" look, under SystemUI's control once that is shown.
     */
    final class EdgeView extends View implements ViewTreeObserver.OnComputeInternalInsetsListener {
        final int mDisplayId;
        final float mDensity;
        private final Paint mPlate = new Paint(Paint.ANTI_ALIAS_FLAG), mSheen = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint mHairline = new Paint(Paint.ANTI_ALIAS_FLAG), mWell = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint mRing = new Paint(Paint.ANTI_ALIAS_FLAG), mPearl = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint mGlyph = new Paint(Paint.ANTI_ALIAS_FLAG), mFront = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint mArmed = new Paint(Paint.ANTI_ALIAS_FLAG), mMicPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
        /** SystemUI's mic control exists on this build (TRAY_PROPERTY): only then is its stand-in drawn. */
        private final boolean mMicStandIn = SystemProperties.getBoolean(TRAY_PROPERTY, false);
        private final Path mOutline = new Path();
        private final int mSlop;
        private float mDownY = Float.NaN;
        private int mDownTarget = -1;
        /** A drag up on the open tray has passed TRAY_TO_SWITCHER_DP (and not come back below its exit). */
        private boolean mSwitcherArmed;
        /** Plan 1E: the theme this edge draws in; Oxblood is today's colours exactly (RoseTheme.map is identity). */
        private String mTheme = RoseEdgeWindow.this.mTheme;

        /**
         * The tray as a drag reveals it: it rises from below with reveal, and fades as the drag approaches the
         * switcher, then dims once the switcher stage has visibly taken over.
         */
        void setPeek(float reveal, float approach, int stage) {
            float shown = Math.max(0f, Math.min(1f, reveal));
            float height = getHeight() > 0 ? getHeight() : RoseEdgeLayout.windowHeightPx(mDensity, 0);
            setTranslationY((1 - shown) * height);
            setAlpha(stage == RoseEdgeGesture.SWITCHER ? 0.35f : 1f - 0.4f * Math.max(0f, Math.min(1f, approach)));
        }

        EdgeView(Context context, int displayId) {
            super(context);
            mDisplayId = displayId;
            mDensity = context.getResources().getDisplayMetrics().density;
            mSlop = ViewConfiguration.get(context).getScaledTouchSlop();
            mHairline.setStyle(Paint.Style.STROKE);
            mHairline.setStrokeWidth(1 * mDensity);
            mHairline.setShadowLayer(10 * mDensity, 0, 4 * mDensity, 0x73000000);
            mRing.setStyle(Paint.Style.STROKE);
            mRing.setStrokeWidth(1 * mDensity);
            mGlyph.setStyle(Paint.Style.STROKE);
            mGlyph.setStrokeWidth(2 * mDensity);
            mGlyph.setStrokeJoin(Paint.Join.ROUND);
            colour();
            setContentDescription("System tray");
        }

        /** The edge's flat colours in its theme (RoseTheme.map: identity in Oxblood). */
        private void colour() {
            mHairline.setColor(RoseTheme.map(mTheme, 0xA6D65468));
            mRing.setColor(RoseTheme.map(mTheme, 0x66ECCDBE));
            mGlyph.setColor(RoseTheme.map(mTheme, 0xFFEADBCD));
            mFront.setColor(RoseTheme.map(mTheme, 0xFF331019));
            mArmed.setColor(RoseTheme.map(mTheme, 0x66FF4A5E));
        }

        /** Plan 1E: draws in this theme from now on: colours and shaders are rebuilt, nothing else changes. */
        void setTheme(String theme) {
            if (theme.equals(mTheme)) return;
            mTheme = theme;
            colour();
            if (getWidth() > 0) shade();
            invalidate();
        }

        Rect control(int which) {
            return RoseEdgeLayout.control(which, getWidth(), mDensity, 0);
        }

        /** The two wells as virtual buttons for accessibility; the mic slot is SystemUI's control, with its own node. */
        private final AccessibilityNodeProvider mNodes = new AccessibilityNodeProvider() {
            @Override
            public AccessibilityNodeInfo createAccessibilityNodeInfo(int virtualViewId) {
                if (virtualViewId == View.NO_ID) {
                    AccessibilityNodeInfo root = AccessibilityNodeInfo.obtain(EdgeView.this);
                    onInitializeAccessibilityNodeInfo(root);
                    root.addChild(EdgeView.this, RoseEdgeLayout.ROSE);
                    root.addChild(EdgeView.this, RoseEdgeLayout.SWITCHER);
                    return root;
                }
                if (virtualViewId != RoseEdgeLayout.ROSE && virtualViewId != RoseEdgeLayout.SWITCHER) return null;
                AccessibilityNodeInfo node = AccessibilityNodeInfo.obtain(EdgeView.this, virtualViewId);
                node.setParent(EdgeView.this);
                node.setPackageName(getContext().getPackageName());
                node.setClassName(android.widget.Button.class.getName());
                node.setContentDescription(virtualViewId == RoseEdgeLayout.ROSE ? "Open Rose" : "Open system switcher");
                Rect bounds = control(virtualViewId);
                node.setBoundsInParent(bounds);
                int[] onScreen = new int[2];
                getLocationOnScreen(onScreen);
                bounds.offset(onScreen[0], onScreen[1]);
                node.setBoundsInScreen(bounds);
                node.setEnabled(true);
                node.setVisibleToUser(true);
                node.setClickable(true);
                node.addAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_CLICK);
                return node;
            }

            @Override
            public boolean performAction(int virtualViewId, int action, android.os.Bundle arguments) {
                if (virtualViewId == View.NO_ID) return performAccessibilityAction(action, arguments);
                if (action != AccessibilityNodeInfo.ACTION_CLICK
                        || (virtualViewId != RoseEdgeLayout.ROSE && virtualViewId != RoseEdgeLayout.SWITCHER)) {
                    return false;
                }
                activate(virtualViewId);
                return true;
            }
        };

        @Override
        public AccessibilityNodeProvider getAccessibilityNodeProvider() {
            return mNodes;
        }

        /**
         * Lowers the edge, then runs the control's action (taps and accessibility clicks alike). Only while raised, so
         * a raise gives at most one launch: Home asks for one Rose start per tap (its conversation stacks).
         */
        void activate(int which) {
            if (!RoseEdgeState.isRaised(mDisplayId)) return;
            RoseEdgeState.lower(mDisplayId);
            if (which == RoseEdgeLayout.ROSE) mActions.openRose(mDisplayId);
            else mActions.showSwitcher(mDisplayId);
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
            float flat = RoseEdgeLayout.FLAT_TOP_DP * mDensity;
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
            shade();
            Display display = getDisplay();
            android.util.DisplayMetrics metrics = new android.util.DisplayMetrics();
            if (display != null) display.getRealMetrics(metrics);
            // The window is bottom-gravity, full width and exactly as tall as this view (its own layout params), so
            // its top on screen is the display height less that height.
            Rect slot = RoseEdgeLayout.micSlotOnScreen(getWidth(), mDensity, 0, metrics.heightPixels - getHeight());
            RoseEdgeState.setMicSlot(mDisplayId, slot, metrics.widthPixels, metrics.heightPixels, mDensity);
        }

        /** The plate, sheen, well and pearl shaders for the current size, in the edge's theme. */
        private void shade() {
            float h = RoseEdgeLayout.EDGE_HEIGHT_DP * mDensity;
            mPlate.setShader(new LinearGradient(0, 0, 0, h, RoseTheme.map(mTheme,
                    new int[] {0xFF4A141A, 0xFF4A141A, 0xFF3C0E13, 0xFF330C10}),
                    new float[] {0, 34 / 92f, 46 / 92f, 1}, Shader.TileMode.CLAMP));
            mSheen.setShader(new LinearGradient(0, 0, 0, h * 0.4f, RoseTheme.map(mTheme, 0x12FF96A5),
                    RoseTheme.map(mTheme, 0x00FF96A5), Shader.TileMode.CLAMP));
            mWell.setShader(new LinearGradient(0, 0, 0, RoseEdgeLayout.WELL_HEIGHT_DP * mDensity,
                    RoseTheme.map(mTheme, 0x47140407), RoseTheme.map(mTheme, 0x1F46121C), Shader.TileMode.CLAMP));
            Rect rose = control(RoseEdgeLayout.ROSE);
            // pearl.ruby: radial, centred at (34%, 28%) of the 16dp pearl, radius the distance to its farthest corner.
            float pr = 8 * mDensity, gx = rose.exactCenterX() - pr + 0.34f * 2 * pr;
            float gy = rose.exactCenterY() - pr + 0.28f * 2 * pr;
            float far = (float) Math.hypot((1 - 0.34f) * 2 * pr, (1 - 0.28f) * 2 * pr);
            mPearl.setShader(new RadialGradient(gx, gy, far, RoseTheme.map(mTheme,
                    new int[] {0xFFFFE6E8, 0xFFFFE6E8, 0xFFE8606F, 0xFFC43048, 0xFF8A1428, 0xFF4A0612}),
                    new float[] {0, 0.07f, 0.2f, 0.46f, 0.76f, 1}, Shader.TileMode.CLAMP));
        }

        @Override
        protected void onDraw(Canvas canvas) {
            canvas.save();
            // The plate runs through the microphone slot, as SystemUI's opaque control continues it there.
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
                if (which == RoseEdgeLayout.SWITCHER && mSwitcherArmed) {
                    // Armed: a drag up on the tray will open the switcher on release.
                    canvas.drawRoundRect(well, radius, radius, mArmed);
                }
                canvas.drawRoundRect(well, radius, radius, mRing);
                canvas.restore();
            }
            Rect rose = control(RoseEdgeLayout.ROSE);
            canvas.drawCircle(rose.exactCenterX(), rose.exactCenterY(), 8 * mDensity, mPearl);
            drawSwitcherGlyph(canvas, control(RoseEdgeLayout.SWITCHER));
            if (mMicStandIn) drawMicUnavailable(canvas, control(RoseEdgeLayout.MIC));
            canvas.restore();
        }

        /**
         * The mic button as SystemUI's control draws it before it knows the state (RoseEdgeMicSlot.Control with mKnown
         * false: OxbloodEdge.Well.mic and the unmuted MicGlyph at UNKNOWN_ALPHA), so it reads as "status unavailable",
         * never as muted or unmuted. SystemUI's control covers it once the edge is raised.
         */
        private void drawMicUnavailable(Canvas canvas, Rect slot) {
            float cx = slot.exactCenterX(), cy = slot.exactCenterY(), d = mDensity;
            int layer = canvas.saveLayerAlpha(slot.left, slot.top, slot.right, slot.bottom, MIC_UNKNOWN_ALPHA);
            float h = MIC_WELL_DP * d, r = MIC_WELL_RADIUS_DP * d;
            RectF well = new RectF(cx - h / 2, cy - h / 2, cx + h / 2, cy + h / 2);
            mMicPaint.reset();
            mMicPaint.setAntiAlias(true);
            mMicPaint.setShader(new LinearGradient(0, well.top, 0, well.bottom, RoseTheme.map(mTheme, 0x47140407),
                    RoseTheme.map(mTheme, 0x1F46121C), Shader.TileMode.CLAMP));
            canvas.drawRoundRect(well, r, r, mMicPaint);
            mMicPaint.setShader(null);
            mMicPaint.setStyle(Paint.Style.STROKE);
            float ring = Math.max(1f, d), half = ring / 2;
            mMicPaint.setStrokeWidth(ring);
            mMicPaint.setColor(RoseTheme.map(mTheme, 0x66ECCDBE));
            canvas.drawRoundRect(well.left + half, well.top + half, well.right - half, well.bottom - half, r - half,
                    r - half, mMicPaint);
            // OxbloodEdge.MicGlyph, unmuted ink, in its 24-unit box scaled to 20dp.
            float s = 20 * d / 24f;
            canvas.translate(cx - 12 * s, cy - 12 * s);
            canvas.scale(s, s);
            mMicPaint.reset();
            mMicPaint.setAntiAlias(true);
            mMicPaint.setColor(RoseTheme.map(mTheme, 0xFFE6D4C3));
            canvas.drawRoundRect(new RectF(8.6f, 2.5f, 15.4f, 15f), 3.4f, 3.4f, mMicPaint);
            mMicPaint.setStyle(Paint.Style.STROKE);
            mMicPaint.setStrokeWidth(1.9f);
            mMicPaint.setStrokeCap(Paint.Cap.ROUND);
            canvas.drawArc(new RectF(5.6f, 4.4f, 18.4f, 17.2f), 0, 180, false, mMicPaint);
            canvas.drawLine(12, 17.4f, 12, 20.6f, mMicPaint);
            canvas.drawLine(8.8f, 20.8f, 15.2f, 20.8f, mMicPaint);
            canvas.restoreToCount(layer);
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
                mGlyph.setShader(x == 0 ? null : new LinearGradient(34, 0, 62, 0, RoseTheme.map(mTheme,
                        new int[] {0xFFEADBCD, 0xFFEADBCD, 0xFFFF4A5E, 0xFFFF4A5E}), new float[] {0, 0.5f, 0.8f, 1},
                        Shader.TileMode.CLAMP));
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
                    mSwitcherArmed = false;
                    return true;
                case MotionEvent.ACTION_MOVE: {
                    // A drag up on the open tray goes on toward the switcher, visibly: the switcher well lights, with
                    // a haptic, once armed. (The window is only the tray's height, so the tray itself stays put.)
                    float up = (mDownY - e.getY()) / mDensity;
                    boolean armed = up >= RoseEdgeGesture.TRAY_TO_SWITCHER_DP
                            || (mSwitcherArmed && up >= RoseEdgeGesture.TRAY_TO_SWITCHER_EXIT_DP);
                    if (armed != mSwitcherArmed) {
                        mSwitcherArmed = armed;
                        if (armed) performHapticFeedback(HapticFeedbackConstants.GESTURE_THRESHOLD_ACTIVATE);
                        invalidate();
                    }
                    return true;
                }
                case MotionEvent.ACTION_UP: {
                    float dy = e.getY() - mDownY;
                    int target = Math.abs(dy) <= mSlop ? hit(e.getX(), e.getY()) : -1;
                    if (mSwitcherArmed) {
                        activate(RoseEdgeLayout.SWITCHER);
                    } else if (dy > RoseEdgeGesture.RAISE_DP * mDensity) {
                        RoseEdgeState.lower(mDisplayId);
                    } else if (target >= 0 && target == mDownTarget) {
                        activate(target);
                    }
                    mSwitcherArmed = false;
                    mDownTarget = -1;
                    invalidate();
                    return true;
                }
                case MotionEvent.ACTION_CANCEL:
                    mDownTarget = -1;
                    mSwitcherArmed = false;
                    invalidate();
                    return true;
                default:
                    return true;
            }
        }
    }
}
