/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.systemui.roseshade;

import android.app.ActivityManager;
import android.app.ActivityOptions;
import android.app.KeyguardManager;
import android.app.Notification;
import android.app.PendingIntent;
import android.app.RemoteInput;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.graphics.PixelFormat;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.SystemProperties;
import android.os.UserHandle;
import android.provider.Settings;
import android.service.notification.StatusBarNotification;
import android.text.Editable;
import android.text.InputType;
import android.text.TextUtils;
import android.text.TextWatcher;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.VelocityTracker;
import android.view.View;
import android.view.ViewConfiguration;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.view.animation.DecelerateInterpolator;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.TextView;
import android.window.OnBackInvokedCallback;
import android.window.OnBackInvokedDispatcher;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * The Rose notification shade (1G, Designer's F09 Refinement v1): a focusable SystemUI window that enters from
 * the top over the owner's screen. It shows the compact quick settings row, brightness and the owner's
 * notifications, grouped per app, with inline replies.
 *
 * <p>All the rules live in pure, tested classes: {@link RoseShadeModel} (list, modes, back),
 * {@link RoseShadeReplies} (drafts and truthful send states), {@link RoseQuickSettings} (tiles) and
 * {@link RoseShadeDrag} (the status-bar pull). This class only draws them and talks to Android.
 *
 * <p>Boundaries: the shade is pulled from the status bar through AgentTrayPrototype's status-bar hook; the lock
 * screen keeps the stock shade. The reply field is a single-line editor with the keyboard's Send action; the
 * keyboard (OS Product) and the Rose chin keep their own space through the window insets. The shade has no
 * microphone control: the chin holds the only one.
 */
final class RoseShadeController {
    private static final String TAG = "RoseShade";
    /** Off switch for the shade; on unless a build or the owner sets it false. */
    static final String ENABLED_PROPERTY = "persist.wm.debug.rose_shade";
    private static final String ROSE_PACKAGE = "dev.pixel.voice";

    // Oxblood palette, as on the board.
    private static final int PANEL = 0xF2141012;
    private static final int CARD = 0xFF221B1D;
    private static final int TILE = 0xFF2A2224;
    private static final int RUBY = 0xFF7A1F2B;
    private static final int RUBY_LIGHT = 0xFFE07A88;
    private static final int TEXT = 0xFFF4EEEE;
    private static final int TEXT_2 = 0xFFB9AEB0;
    private static final int SCRIM = 0x8C000000;
    private static final long ENTER_MS = 240;

    private final Context mContext;
    private final Handler mHandler = new Handler(Looper.getMainLooper());
    private final Handler mSendHandler;
    private final WindowManager mWindows;
    private final RoseShadeModel mModel = new RoseShadeModel();
    private final RoseShadeReplies mReplies = new RoseShadeReplies();
    private final RoseQuickSettings mQuickSettings = new RoseQuickSettings();
    private final RoseShadeSystem mSystem;
    private final RoseShadeDrag mDrag;
    private final Map<String, StatusBarNotification> mNotifications = new HashMap<>();
    private final Map<String, ReplyRow> mReplyRows = new HashMap<>();
    private VelocityTracker mVelocity;

    private FrameLayout mRoot;
    private View mScrim;
    private LinearLayout mPanel;
    private LinearLayout mQuickArea;
    private LinearLayout mList;
    private ScrollView mScroll;
    private boolean mAttached;
    private boolean mImeVisible;
    private String mReplyingKey;
    private OnBackInvokedCallback mBack;

    RoseShadeController(Context context) {
        mContext = context.createWindowContext(WindowManager.LayoutParams.TYPE_STATUS_BAR_ADDITIONAL, null);
        mWindows = mContext.getSystemService(WindowManager.class);
        final HandlerThread sender = new HandlerThread("RoseShadeReply");
        sender.start();
        mSendHandler = new Handler(sender.getLooper());
        mSystem = new RoseShadeSystem(mContext, mHandler, mQuickSettings, this::render);
        mDrag = new RoseShadeDrag(ViewConfiguration.get(mContext).getScaledTouchSlop());
        final IntentFilter filter = new IntentFilter();
        filter.addAction(Intent.ACTION_CLOSE_SYSTEM_DIALOGS);
        filter.addAction(Intent.ACTION_SCREEN_OFF);
        mContext.registerReceiver(new BroadcastReceiver() {
            @Override
            public void onReceive(Context c, Intent intent) {
                // Home, recents or the screen going off close the shade; drafts are kept.
                if (mAttached) close();
            }
        }, filter, null, mHandler, Context.RECEIVER_EXPORTED);
    }

    static boolean enabled() {
        return SystemProperties.getBoolean(ENABLED_PROPERTY, true);
    }

    // ---- Notifications (from SystemUI's NotificationListener, on the main thread) ----

    void onPosted(StatusBarNotification sbn) {
        if (!shows(sbn)) {
            onRemoved(sbn.getKey());
            return;
        }
        mNotifications.put(sbn.getKey(), sbn);
        mModel.post(item(sbn));
        render();
    }

    void onRemoved(String key) {
        if (mNotifications.remove(key) == null) return;
        mModel.remove(key);
        mReplies.removed(key);
        final ReplyRow row = mReplyRows.remove(key);
        if (row != null) detach(row.view);
        if (key.equals(mReplyingKey)) mReplyingKey = null;
        render();
    }

    private static boolean shows(StatusBarNotification sbn) {
        final Notification n = sbn.getNotification();
        if ((n.flags & Notification.FLAG_GROUP_SUMMARY) != 0) return false;
        final int user = sbn.getUserId();
        return user == UserHandle.ALL.getIdentifier() || user == ActivityManager.getCurrentUser();
    }

    private RoseShadeModel.Item item(StatusBarNotification sbn) {
        final Notification n = sbn.getNotification();
        final Bundle extras = n.extras;
        final CharSequence title = extras.getCharSequence(Notification.EXTRA_TITLE);
        CharSequence text = extras.getCharSequence(Notification.EXTRA_TEXT);
        if (TextUtils.isEmpty(text)) text = extras.getCharSequence(Notification.EXTRA_BIG_TEXT);
        String primary = null;
        boolean canReply = false;
        if (n.actions != null) {
            for (Notification.Action action : n.actions) {
                if (replyInputs(action) != null) canReply = true;
                else if (primary == null && action.title != null) primary = action.title.toString();
            }
        }
        if (primary == null && n.contentIntent != null) primary = "Open";
        return new RoseShadeModel.Item(sbn.getKey(), sbn.getPackageName(), appLabel(sbn),
                title == null ? "" : title.toString(), text == null ? "" : text.toString(), sbn.getPostTime(),
                primary, canReply);
    }

    private String appLabel(StatusBarNotification sbn) {
        if (ROSE_PACKAGE.equals(sbn.getPackageName())) return "Rose";
        try {
            final PackageManager pm = mContext.getPackageManager();
            final ApplicationInfo info = pm.getApplicationInfoAsUser(sbn.getPackageName(), 0,
                    sbn.getUser().getIdentifier() < 0 ? ActivityManager.getCurrentUser()
                            : sbn.getUser().getIdentifier());
            return pm.getApplicationLabel(info).toString();
        } catch (PackageManager.NameNotFoundException e) {
            return sbn.getPackageName();
        }
    }

    private static RemoteInput[] replyInputs(Notification.Action action) {
        final RemoteInput[] inputs = action.getRemoteInputs();
        if (inputs == null) return null;
        for (RemoteInput input : inputs) {
            if (input.getAllowFreeFormInput()) return inputs;
        }
        return null;
    }

    // ---- The status-bar pull (AgentTrayPrototype's hook, main thread) ----

    /** True when the shade takes this status-bar touch; the status bar then cancels its own gesture. */
    boolean onStatusBarTouch(MotionEvent event) {
        if (!enabled() || ActivityManager.getCurrentUser() != 0
                || mContext.getSystemService(KeyguardManager.class).isKeyguardLocked()) {
            // The lock screen and other users keep the stock shade.
            mDrag.cancel();
            return false;
        }
        if (mVelocity == null) mVelocity = VelocityTracker.obtain();
        mVelocity.addMovement(event);
        final float height = pullHeight();
        switch (event.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                mDrag.down(event.getRawX(), event.getRawY());
                return false;
            case MotionEvent.ACTION_MOVE:
                if (mDrag.move(event.getRawX(), event.getRawY(), height) != RoseShadeDrag.Result.TRACK) {
                    return false;
                }
                follow(mDrag.progress());
                return true;
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL: {
                mVelocity.computeCurrentVelocity(1000);
                final float vy = mVelocity.getYVelocity();
                mVelocity.recycle();
                mVelocity = null;
                final RoseShadeDrag.Result result = event.getActionMasked() == MotionEvent.ACTION_UP
                        ? mDrag.up(vy) : mDrag.cancel();
                if (result == RoseShadeDrag.Result.OPEN) settleOpen();
                else if (result == RoseShadeDrag.Result.CANCEL) close();
                return result != RoseShadeDrag.Result.NONE;
            }
            default:
                return mDrag.tracking();
        }
    }

    private float pullHeight() {
        return mContext.getResources().getDisplayMetrics().heightPixels / 2f;
    }

    // ---- Window ----

    private void attach() {
        if (mAttached) return;
        buildViews();
        final WindowManager.LayoutParams params = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.TYPE_STATUS_BAR_ADDITIONAL,
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
                        | WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
                PixelFormat.TRANSLUCENT);
        params.softInputMode = WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE
                | WindowManager.LayoutParams.SOFT_INPUT_STATE_UNCHANGED;
        params.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS;
        params.setFitInsetsTypes(0);
        params.gravity = Gravity.TOP;
        params.setTitle("RoseShade");
        mWindows.addView(mRoot, params);
        mAttached = true;
        mModel.open();
        mSystem.start();
        render();
    }

    private void follow(float progress) {
        attach();
        final float height = Math.max(mPanel.getHeight(), pullHeight());
        mPanel.setTranslationY(-(1f - progress) * height);
        mScrim.setAlpha(progress);
    }

    private void settleOpen() {
        attach();
        mPanel.animate().translationY(0).setDuration(ENTER_MS).setInterpolator(new DecelerateInterpolator())
                .start();
        mScrim.animate().alpha(1f).setDuration(ENTER_MS).start();
    }

    /** Closes the shade; drafts stay in {@link RoseShadeReplies} for the next opening. */
    void close() {
        if (!mAttached) return;
        mModel.close();
        hideKeyboard();
        mSystem.stop();
        // This window's views and callback leave with it; a reopening during the exit builds its own.
        final FrameLayout root = mRoot;
        final OnBackInvokedCallback back = mBack;
        final Map<String, ReplyRow> rows = new HashMap<>(mReplyRows);
        mReplyRows.clear();
        mBack = null;
        mReplyingKey = null;
        final float height = Math.max(mPanel.getHeight(), pullHeight());
        mScrim.animate().alpha(0f).setDuration(ENTER_MS).start();
        mPanel.animate().translationY(-height).setDuration(ENTER_MS).withEndAction(() -> {
            if (back != null) {
                final OnBackInvokedDispatcher dispatcher = root.findOnBackInvokedDispatcher();
                if (dispatcher != null) dispatcher.unregisterOnBackInvokedCallback(back);
            }
            for (ReplyRow row : rows.values()) detach(row.view);
            mWindows.removeViewImmediate(root);
        }).start();
        mAttached = false;
    }

    private void back() {
        final RoseShadeModel.Back back = mModel.back(mImeVisible);
        if (back == RoseShadeModel.Back.HIDE_KEYBOARD) hideKeyboard();
        else if (back == RoseShadeModel.Back.COLLAPSE_QUICK_SETTINGS) render();
        else if (back == RoseShadeModel.Back.CLOSE_SHADE) close();
    }

    private void hideKeyboard() {
        if (mRoot == null) return;
        final View focus = mRoot.findFocus();
        mContext.getSystemService(InputMethodManager.class).hideSoftInputFromWindow(mRoot.getWindowToken(), 0);
        if (focus != null) focus.clearFocus();
    }

    // ---- Views ----

    private int dp(float value) {
        return Math.round(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value,
                mContext.getResources().getDisplayMetrics()));
    }

    private static GradientDrawable round(int color, float radius) {
        final GradientDrawable d = new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(radius);
        return d;
    }

    private TextView text(CharSequence value, float sp, int color, boolean bold) {
        final TextView t = new TextView(mContext);
        t.setText(value);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        if (bold) t.setTypeface(Typeface.create(Typeface.DEFAULT, Typeface.BOLD));
        return t;
    }

    private ImageView icon(RoseShadeIcons.Glyph glyph, int color, int sizeDp) {
        final ImageView v = new ImageView(mContext);
        v.setImageDrawable(new RoseShadeIcons(glyph, color, dp(1.8f)));
        v.setLayoutParams(new LinearLayout.LayoutParams(dp(sizeDp), dp(sizeDp)));
        return v;
    }

    private TextView pill(String label, boolean primary, View.OnClickListener click) {
        final TextView b = text(label, 14, TEXT, false);
        b.setGravity(Gravity.CENTER);
        b.setPadding(dp(18), dp(8), dp(18), dp(8));
        final GradientDrawable bg = round(primary ? RUBY : 0x00000000, dp(18));
        if (!primary) bg.setStroke(dp(1), 0x66F4EEEE);
        b.setBackground(bg);
        b.setOnClickListener(click);
        return b;
    }

    private void buildViews() {
        mRoot = new FrameLayout(mContext) {
            @Override
            public boolean dispatchKeyEvent(KeyEvent event) {
                if (event.getKeyCode() == KeyEvent.KEYCODE_BACK) {
                    if (event.getAction() == KeyEvent.ACTION_UP) back();
                    return true;
                }
                return super.dispatchKeyEvent(event);
            }

            @Override
            protected void onAttachedToWindow() {
                super.onAttachedToWindow();
                final OnBackInvokedDispatcher dispatcher = findOnBackInvokedDispatcher();
                if (dispatcher != null) {
                    mBack = RoseShadeController.this::back;
                    dispatcher.registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT, mBack);
                }
            }
        };
        mRoot.setFocusableInTouchMode(true);
        mScrim = new View(mContext);
        mScrim.setBackgroundColor(SCRIM);
        mScrim.setAlpha(0f);
        mScrim.setOnClickListener(v -> close());
        mRoot.addView(mScrim, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));

        mPanel = new LinearLayout(mContext);
        mPanel.setOrientation(LinearLayout.VERTICAL);
        mPanel.setBackground(round(PANEL, dp(28)));
        mPanel.setPadding(dp(18), dp(12), dp(18), dp(18));
        mPanel.setClickable(true);  // taps inside never reach the scrim
        final FrameLayout.LayoutParams panel = new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP);
        panel.setMargins(dp(8), 0, dp(8), dp(8));
        mRoot.addView(mPanel, panel);
        mPanel.setTranslationY(-pullHeight());

        final LinearLayout header = new LinearLayout(mContext);
        header.setGravity(Gravity.CENTER_VERTICAL);
        final TextView title = text("Notifications", 28, TEXT, false);
        header.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        // Opens or folds the labelled quick settings view (board panel 04); a tile long press does the same.
        final ImageView more = icon(RoseShadeIcons.Glyph.CHEVRON_DOWN, TEXT_2, 28);
        more.setContentDescription("Quick settings");
        more.setOnClickListener(v -> {
            mModel.toggleQuickSettings();
            render();
        });
        header.addView(more);
        mPanel.addView(header);

        mQuickArea = new LinearLayout(mContext);
        mQuickArea.setOrientation(LinearLayout.VERTICAL);
        final LinearLayout.LayoutParams quick = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        quick.topMargin = dp(14);
        mPanel.addView(mQuickArea, quick);

        mScroll = new ScrollView(mContext);
        mScroll.setVerticalScrollBarEnabled(false);
        mList = new LinearLayout(mContext);
        mList.setOrientation(LinearLayout.VERTICAL);
        mScroll.addView(mList, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        final LinearLayout.LayoutParams list = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                0, 1);
        list.topMargin = dp(6);
        mPanel.addView(mScroll, list);

        // The status bar above, and the keyboard or the navigation area (with the chin) below, keep their space.
        mRoot.setOnApplyWindowInsetsListener((v, insets) -> {
            final int top = insets.getInsets(WindowInsets.Type.statusBars()
                    | WindowInsets.Type.displayCutout()).top;
            final int bottom = insets.getInsets(WindowInsets.Type.ime()
                    | WindowInsets.Type.navigationBars()).bottom;
            mImeVisible = insets.isVisible(WindowInsets.Type.ime());
            final FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) mPanel.getLayoutParams();
            lp.topMargin = top + dp(4);
            lp.bottomMargin = bottom + dp(8);
            mPanel.setLayoutParams(lp);
            // Keyboard or chin changes never touch a draft (RoseShadeReplies.inputSurfaceChanged).
            mReplies.inputSurfaceChanged();
            return WindowInsets.CONSUMED;
        });
    }

    private void render() {
        if (!mAttached || mPanel == null) return;
        // A rebuild must not take the keyboard away from the reply being typed.
        final View focus = mRoot.findFocus();
        String typing = null;
        for (ReplyRow row : mReplyRows.values()) {
            if (row.field == focus) typing = row.key;
        }
        renderList();
        final ReplyRow row = typing == null ? null : mReplyRows.get(typing);
        if (row != null && row.view.getParent() != null && !row.field.hasFocus()) {
            row.field.requestFocus();
            if (mImeVisible) {
                mContext.getSystemService(InputMethodManager.class).showSoftInput(row.field, 0);
            }
        }
    }

    private void renderList() {
        renderQuickSettings();
        mList.removeAllViews();
        if (mModel.mode() == RoseShadeModel.Mode.QUICK_SETTINGS) {
            final String count = mModel.countLabel();
            if (count != null) {
                final LinearLayout row = row(RoseShadeIcons.Glyph.BUBBLE, count, RoseShadeIcons.Glyph.CHEVRON_DOWN);
                row.setOnClickListener(v -> {
                    mModel.toggleQuickSettings();
                    render();
                });
                mList.addView(row, cardParams());
            }
            return;
        }
        final List<RoseShadeModel.Entry> entries = mModel.entries();
        if (entries.isEmpty()) {
            final LinearLayout empty = new LinearLayout(mContext);
            empty.setOrientation(LinearLayout.VERTICAL);
            empty.setGravity(Gravity.CENTER);
            empty.setPadding(0, dp(56), 0, dp(56));
            final TextView title = text(RoseShadeModel.EMPTY_TITLE, 24, TEXT, false);
            title.setGravity(Gravity.CENTER);
            final TextView body = text(RoseShadeModel.EMPTY_BODY, 16, TEXT_2, false);
            body.setGravity(Gravity.CENTER);
            empty.addView(title);
            empty.addView(body);
            mList.addView(empty);
            return;
        }
        final long now = System.currentTimeMillis();
        for (RoseShadeModel.Entry entry : entries) mList.addView(card(entry, now), cardParams());
    }

    private LinearLayout.LayoutParams cardParams() {
        final LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(10);
        return lp;
    }

    private LinearLayout row(RoseShadeIcons.Glyph lead, String label, RoseShadeIcons.Glyph trail) {
        final LinearLayout row = new LinearLayout(mContext);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setBackground(round(CARD, dp(22)));
        row.setPadding(dp(16), dp(14), dp(16), dp(14));
        row.addView(icon(lead, TEXT, 22));
        final TextView t = text(label, 16, TEXT, false);
        t.setPadding(dp(12), 0, 0, 0);
        row.addView(t, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        if (trail != null) row.addView(icon(trail, TEXT, 20));
        return row;
    }

    private void renderQuickSettings() {
        mQuickArea.removeAllViews();
        final boolean expanded = mModel.mode() == RoseShadeModel.Mode.QUICK_SETTINGS;
        final RoseShadeIcons.Glyph[] glyphs = {RoseShadeIcons.Glyph.WIFI, RoseShadeIcons.Glyph.BLUETOOTH,
                RoseShadeIcons.Glyph.FLASHLIGHT, RoseShadeIcons.Glyph.MOON};
        if (!expanded) {
            final LinearLayout tiles = new LinearLayout(mContext);
            for (RoseQuickSettings.Tile tile : RoseQuickSettings.TILES) {
                final FrameLayout t = new FrameLayout(mContext);
                t.setBackground(round(mQuickSettings.isOn(tile) ? RUBY : TILE, dp(18)));
                t.setContentDescription(tile.label + " " + mQuickSettings.stateLabel(tile));
                final ImageView i = icon(glyphs[tile.ordinal()], TEXT, 24);
                t.addView(i, new FrameLayout.LayoutParams(dp(24), dp(24), Gravity.CENTER));
                t.setOnClickListener(v -> mSystem.toggle(tile));
                // A long press opens the labelled quick settings view (board panel 04).
                t.setOnLongClickListener(v -> {
                    mModel.toggleQuickSettings();
                    render();
                    return true;
                });
                final LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, dp(48), 1);
                lp.setMargins(dp(4), 0, dp(4), 0);
                tiles.addView(t, lp);
            }
            mQuickArea.addView(tiles);
        } else {
            for (int r = 0; r < 2; r++) {
                final LinearLayout line = new LinearLayout(mContext);
                for (int c = 0; c < 2; c++) {
                    final RoseQuickSettings.Tile tile = RoseQuickSettings.TILES.get(r * 2 + c);
                    final LinearLayout t = new LinearLayout(mContext);
                    t.setGravity(Gravity.CENTER_VERTICAL);
                    t.setPadding(dp(14), dp(14), dp(14), dp(14));
                    t.setBackground(round(mQuickSettings.isOn(tile) ? RUBY : TILE, dp(20)));
                    t.addView(icon(glyphs[tile.ordinal()], TEXT, 24));
                    final LinearLayout labels = new LinearLayout(mContext);
                    labels.setOrientation(LinearLayout.VERTICAL);
                    labels.setPadding(dp(12), 0, 0, 0);
                    labels.addView(text(tile.label, 15, TEXT, false));
                    labels.addView(text(mQuickSettings.stateLabel(tile), 13, TEXT_2, false));
                    t.addView(labels);
                    t.setOnClickListener(v -> mSystem.toggle(tile));
                    final LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, dp(72), 1);
                    lp.setMargins(dp(4), dp(4), dp(4), dp(4));
                    line.addView(t, lp);
                }
                mQuickArea.addView(line);
            }
        }
        final LinearLayout brightness = new LinearLayout(mContext);
        brightness.setGravity(Gravity.CENTER_VERTICAL);
        brightness.setBackground(round(CARD, dp(20)));
        brightness.setPadding(dp(14), dp(6), dp(14), dp(6));
        brightness.addView(icon(RoseShadeIcons.Glyph.SUN, TEXT, 22));
        final SeekBar slider = new SeekBar(mContext);
        slider.setMax(1000);
        final float level = mQuickSettings.brightness();
        slider.setEnabled(level >= 0);
        slider.setProgress(level < 0 ? 0 : Math.round(level * 1000));
        slider.setContentDescription("Brightness");
        slider.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override
            public void onProgressChanged(SeekBar bar, int progress, boolean fromUser) {
                if (fromUser) mSystem.setBrightness(progress / 1000f);
            }

            @Override
            public void onStartTrackingTouch(SeekBar bar) {}

            @Override
            public void onStopTrackingTouch(SeekBar bar) {}
        });
        brightness.addView(slider, new LinearLayout.LayoutParams(0, dp(40), 1));
        if (expanded) {
            final ImageView settings = icon(RoseShadeIcons.Glyph.GEAR, TEXT, 24);
            settings.setContentDescription("Settings");
            settings.setOnClickListener(v -> {
                // The shade runs only for the owner (user 0), SystemUI's own user.
                mContext.startActivity(new Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                close();
            });
            brightness.addView(settings);
        }
        final LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(10);
        mQuickArea.addView(brightness, lp);
    }

    private View card(RoseShadeModel.Entry entry, long now) {
        final LinearLayout card = new LinearLayout(mContext);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(round(CARD, dp(22)));
        card.setPadding(dp(16), dp(14), dp(16), dp(14));

        final LinearLayout head = new LinearLayout(mContext);
        head.setGravity(Gravity.CENTER_VERTICAL);
        final RoseShadeModel.Item newest = entry.items.get(0);
        if (ROSE_PACKAGE.equals(newest.packageName)) {
            final View dot = new View(mContext);
            dot.setBackground(round(RUBY_LIGHT, dp(9)));
            head.addView(dot, new LinearLayout.LayoutParams(dp(18), dp(18)));
        } else if (entry.isGroup()) {
            head.addView(icon(RoseShadeIcons.Glyph.BUBBLE, TEXT, 22));
        }
        final TextView label = text(entry.header(), 15, TEXT, false);
        label.setPadding(dp(10), 0, 0, 0);
        head.addView(label, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        if (entry.isGroup()) {
            head.addView(icon(entry.expanded ? RoseShadeIcons.Glyph.CHEVRON_UP : RoseShadeIcons.Glyph.CHEVRON_DOWN,
                    TEXT, 20));
            head.setOnClickListener(v -> {
                mModel.toggleExpanded(entry.id);
                render();
            });
        } else {
            head.addView(text(RoseShadeModel.age(newest.postedAt, now), 13, TEXT_2, false));
        }
        card.addView(head);

        if (!entry.isGroup()) {
            card.addView(text(newest.title, 17, TEXT, false));
            if (!newest.text.isEmpty()) card.addView(text(newest.text, 14, TEXT_2, false));
            card.setOnClickListener(v -> open(newest.key));
            addActions(card, newest);
        } else if (!entry.expanded) {
            for (int i = 0; i < Math.min(3, entry.items.size()); i++) {
                final RoseShadeModel.Item item = entry.items.get(i);
                final LinearLayout line = new LinearLayout(mContext);
                line.setPadding(0, dp(6), 0, 0);
                line.addView(text(item.title, 14, TEXT, true));
                final TextView body = text("  " + item.text, 14, TEXT_2, false);
                body.setSingleLine(true);
                body.setEllipsize(TextUtils.TruncateAt.END);
                line.addView(body);
                card.addView(line);
            }
            card.setOnClickListener(v -> {
                mModel.toggleExpanded(entry.id);
                render();
            });
        } else {
            for (RoseShadeModel.Item item : entry.items) {
                final LinearLayout one = new LinearLayout(mContext);
                one.setOrientation(LinearLayout.VERTICAL);
                one.setPadding(0, dp(12), 0, 0);
                final LinearLayout top = new LinearLayout(mContext);
                top.setGravity(Gravity.CENTER_VERTICAL);
                top.addView(icon(RoseShadeIcons.Glyph.PERSON, TEXT, 28));
                final LinearLayout words = new LinearLayout(mContext);
                words.setOrientation(LinearLayout.VERTICAL);
                words.setPadding(dp(12), 0, 0, 0);
                words.addView(text(item.title, 16, TEXT, false));
                words.addView(text(item.text, 14, TEXT_2, false));
                top.addView(words, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
                top.addView(text(RoseShadeModel.age(item.postedAt, now), 13, TEXT_2, false));
                top.setOnClickListener(v -> open(item.key));
                one.addView(top);
                addActions(one, item);
                card.addView(one);
            }
        }
        return card;
    }

    /** The notification's own action, Reply when it offers an inline reply, and the reply row while in use. */
    private void addActions(LinearLayout parent, RoseShadeModel.Item item) {
        final LinearLayout actions = new LinearLayout(mContext);
        actions.setPadding(0, dp(10), 0, 0);
        boolean any = false;
        if (item.canReply) {
            actions.addView(pill("Reply", true, v -> startReply(item.key)), actionParams());
            any = true;
        }
        // Beside Reply, the app's own action (e.g. "Mark read"); without Reply, the card's action or "Open".
        final boolean showPrimary = item.primaryAction != null
                && (!item.canReply || !"Open".equals(item.primaryAction));
        if (showPrimary) {
            actions.addView(pill(item.primaryAction, !item.canReply, v -> runAction(item.key)), actionParams());
            any = true;
        }
        if (any) parent.addView(actions);
        final RoseShadeReplies.Draft draft = mReplies.get(item.key);
        if (item.key.equals(mReplyingKey) || !draft.text.isEmpty()
                || draft.status != RoseShadeReplies.Status.EDITING) {
            final ReplyRow row = replyRow(item.key);
            row.sync();
            detach(row.view);
            parent.addView(row.view);
        }
    }

    private LinearLayout.LayoutParams actionParams() {
        final LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        lp.setMargins(0, 0, dp(10), 0);
        return lp;
    }

    private static void detach(View view) {
        final ViewGroup parent = (ViewGroup) view.getParent();
        if (parent != null) parent.removeView(view);
    }

    // ---- Actions and replies ----

    private Bundle allowStart() {
        return ActivityOptions.makeBasic().setPendingIntentBackgroundActivityStartMode(
                ActivityOptions.MODE_BACKGROUND_ACTIVITY_START_ALLOWED).toBundle();
    }

    /** Opens the notification's app (its content intent) and closes the shade. */
    private void open(String key) {
        final StatusBarNotification sbn = mNotifications.get(key);
        if (sbn == null || sbn.getNotification().contentIntent == null) return;
        try {
            sbn.getNotification().contentIntent.send(mContext, 0, null, null, null, null, allowStart());
            close();
        } catch (PendingIntent.CanceledException e) {
            Log.w(TAG, "Content intent cancelled for " + key);
        }
    }

    /** The notification's own first non-reply action, or its content intent when it has none. */
    private void runAction(String key) {
        final StatusBarNotification sbn = mNotifications.get(key);
        if (sbn == null) return;
        final Notification n = sbn.getNotification();
        if (n.actions != null) {
            for (Notification.Action action : n.actions) {
                if (replyInputs(action) == null && action.actionIntent != null) {
                    try {
                        action.actionIntent.send(mContext, 0, null, null, null, null, allowStart());
                        if (action.actionIntent.isActivity()) close();
                    } catch (PendingIntent.CanceledException e) {
                        Log.w(TAG, "Action cancelled for " + key);
                    }
                    return;
                }
            }
        }
        open(key);
    }

    private void startReply(String key) {
        mReplyingKey = key;
        render();
        final ReplyRow row = mReplyRows.get(key);
        if (row == null) return;
        row.field.requestFocus();
        mContext.getSystemService(InputMethodManager.class).showSoftInput(row.field, 0);
    }

    /** The only path that sends: the Send button or the keyboard's Send action. */
    private void send(String key) {
        final String text = mReplies.send(key);
        if (text == null) return;
        render();
        final StatusBarNotification sbn = mNotifications.get(key);
        Notification.Action reply = null;
        if (sbn != null && sbn.getNotification().actions != null) {
            for (Notification.Action action : sbn.getNotification().actions) {
                if (replyInputs(action) != null) {
                    reply = action;
                    break;
                }
            }
        }
        if (reply == null || reply.actionIntent == null) {
            mReplies.failed(key);
            render();
            return;
        }
        final Notification.Action action = reply;
        mSendHandler.post(() -> {
            boolean delivered;
            try {
                final RemoteInput[] inputs = replyInputs(action);
                final Bundle results = new Bundle();
                for (RemoteInput input : inputs) {
                    if (input.getAllowFreeFormInput()) {
                        results.putCharSequence(input.getResultKey(), text);
                        break;
                    }
                }
                final Intent fill = new Intent().addFlags(Intent.FLAG_RECEIVER_FOREGROUND);
                RemoteInput.addResultsToIntent(inputs, fill, results);
                RemoteInput.setResultsSource(fill, RemoteInput.SOURCE_FREE_FORM_INPUT);
                action.actionIntent.send(mContext, 0, fill, null, null, null, allowStart());
                delivered = true;
            } catch (PendingIntent.CanceledException | RuntimeException e) {
                Log.w(TAG, "Reply not delivered for " + key, e);
                delivered = false;
            }
            final boolean ok = delivered;
            mHandler.post(() -> {
                if (ok) mReplies.delivered(key);
                else mReplies.failed(key);
                render();
            });
        });
    }

    private ReplyRow replyRow(String key) {
        ReplyRow row = mReplyRows.get(key);
        if (row == null) {
            row = new ReplyRow(key);
            mReplyRows.put(key, row);
        }
        return row;
    }

    /** One notification's reply field, kept across renders so the keyboard and the draft stay put. */
    private final class ReplyRow {
        final String key;
        final LinearLayout view;
        final EditText field;
        final TextView sendButton;
        final TextView status;
        private boolean mSyncing;

        ReplyRow(String key) {
            this.key = key;
            view = new LinearLayout(mContext);
            view.setOrientation(LinearLayout.VERTICAL);
            view.setPadding(0, dp(10), 0, 0);
            final LinearLayout line = new LinearLayout(mContext);
            line.setGravity(Gravity.CENTER_VERTICAL);
            field = new EditText(mContext) {
                @Override
                protected void onSelectionChanged(int start, int end) {
                    super.onSelectionChanged(start, end);
                    if (!mSyncing && getText() != null) {
                        mReplies.edit(ReplyRow.this.key, getText().toString(), start, end);
                    }
                }
            };
            field.setSingleLine(true);
            field.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
                    | InputType.TYPE_TEXT_FLAG_AUTO_CORRECT);
            field.setImeOptions(EditorInfo.IME_ACTION_SEND);
            field.setTextColor(TEXT);
            field.setHintTextColor(TEXT_2);
            field.setHint("Reply");
            final GradientDrawable bg = round(0x00000000, dp(18));
            bg.setStroke(dp(1), 0x66F4EEEE);
            field.setBackground(bg);
            field.setPadding(dp(14), dp(8), dp(14), dp(8));
            field.addTextChangedListener(new TextWatcher() {
                @Override
                public void beforeTextChanged(CharSequence s, int start, int count, int after) {}

                @Override
                public void onTextChanged(CharSequence s, int start, int before, int count) {}

                @Override
                public void afterTextChanged(Editable s) {
                    if (mSyncing) return;
                    mReplies.edit(ReplyRow.this.key, s.toString(), field.getSelectionStart(),
                            field.getSelectionEnd());
                    updateControls();
                }
            });
            field.setOnEditorActionListener((v, actionId, event) -> {
                if (actionId == EditorInfo.IME_ACTION_SEND) {
                    send(ReplyRow.this.key);
                    return true;
                }
                return false;
            });
            line.addView(field, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
            sendButton = pill("Send", true, v -> send(ReplyRow.this.key));
            final LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            sp.leftMargin = dp(10);
            line.addView(sendButton, sp);
            view.addView(line);
            status = text("", 13, TEXT_2, false);
            status.setPadding(dp(4), dp(6), 0, 0);
            view.addView(status);
        }

        /** Puts the kept draft (text and selection) and its truthful state back into the views. */
        void sync() {
            final RoseShadeReplies.Draft draft = mReplies.get(key);
            mSyncing = true;
            if (!draft.text.contentEquals(field.getText())) field.setText(draft.text);
            final int length = field.getText().length();
            field.setSelection(Math.min(draft.selectionStart, length), Math.min(draft.selectionEnd, length));
            mSyncing = false;
            updateControls();
        }

        void updateControls() {
            final RoseShadeReplies.Draft draft = mReplies.get(key);
            field.setEnabled(draft.status != RoseShadeReplies.Status.PENDING);
            sendButton.setEnabled(mReplies.canSend(key));
            sendButton.setAlpha(mReplies.canSend(key) ? 1f : 0.45f);
            final String line = RoseShadeReplies.statusLine(draft.status);
            status.setText(line == null ? "" : line);
            status.setVisibility(line == null ? View.GONE : View.VISIBLE);
            status.setTextColor(draft.status == RoseShadeReplies.Status.FAILED ? RUBY_LIGHT : TEXT_2);
        }
    }
}
