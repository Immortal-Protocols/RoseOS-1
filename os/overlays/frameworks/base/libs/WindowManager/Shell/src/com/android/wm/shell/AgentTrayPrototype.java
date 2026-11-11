/*
 * Copyright 2026
 * SPDX-License-Identifier: Apache-2.0
 */
package com.android.wm.shell;

import android.app.ActivityManager;
import android.app.KeyguardManager;
import android.content.BroadcastReceiver;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.ServiceConnection;
import android.database.ContentObserver;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.Rect;
import android.graphics.RectF;
import android.hardware.display.DisplayManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Message;
import android.os.Messenger;
import android.os.RemoteException;
import android.os.SystemClock;
import android.os.SystemProperties;
import android.os.UserHandle;
import android.provider.Settings;
import android.util.Log;
import android.view.Display;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.ViewConfiguration;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.view.WindowMetrics;
import com.android.wm.shell.common.DisplayInsetsController;

/** Emulator status-bar experiment. Never changes application or display-area bounds. */
public final class AgentTrayPrototype implements DisplayManager.DisplayListener {
    private static final String TAG = "AgentStatusWave";
    private static AgentTrayPrototype instance;
    /**
     * F08: the Rose raised edge's switch, read for REGISTER's arg2. Builds set it only with rose-edge-wmshell.patch
     * (rose-edge-build-make.patch), and the edge's Shell controller reads the same switch. It is not checked by class
     * name: SystemUI's R8 keep rule covers only this package, not com.android.wm.shell.roseedge.
     */
    private static final String EDGE_PROPERTY = "persist.wm.debug.rose_edge";
    private int sentChin;
    private final AgentStatusControls controls = new AgentStatusControls();
    private boolean active;
    private long lastCommandAt;

    /** A status-bar touch consumer that runs before the tray (the Rose notification shade, 1G). */
    public interface StatusBarDragHandler {
        /** True to take this touch; the status bar then cancels its own gesture. */
        boolean onStatusBarTouch(MotionEvent event);
    }

    private static volatile StatusBarDragHandler dragHandler;

    /** Set by SystemUI's RoseShadeStartable; the shade lives in SystemUI, which this library cannot reference. */
    public static void setStatusBarDragHandler(StatusBarDragHandler handler) {
        dragHandler = handler;
    }

    /** Called by the native status window; the decorative overlay stays non-touchable. */
    public static boolean onStatusBarTouch(MotionEvent event) {
        final StatusBarDragHandler handler = dragHandler;
        if (handler != null && handler.onStatusBarTouch(event)) {
            // The pull is the shade's; a tray tap that began with it is abandoned.
            if (instance != null) instance.controls.cancel();
            return true;
        }
        return instance != null && instance.handleTouch(event);
    }

    private boolean handleTouch(MotionEvent event) {
        if (view == null || voice == null || screenOff || link.user() != 0
                || context.getSystemService(KeyguardManager.class).isKeyguardLocked()) {
            controls.cancel();
            return false;
        }
        int[] location = new int[2];
        view.getLocationOnScreen(location);
        float x = event.getRawX() - location[0], y = event.getRawY() - location[1];
        int target = y >= 0 && y < view.getHeight() ? view.targetAt(x) : 0;
        int command = controls.touch(event.getActionMasked(), event.getPointerCount(),
                x, y, event.getEventTime(), target, active, ViewConfiguration.get(context).getScaledTouchSlop());
        if (command == 0) return false;
        long now = SystemClock.elapsedRealtime();
        if (now - lastCommandAt >= 600) {
            lastCommandAt = now;
            if (!send(command)) link.disconnected();
            Log.i(TAG, "Status tap command=" + command);
        }
        return true;
    }
    private final Context context;
    private final Handler handler = new Handler(Looper.getMainLooper());
    // One pending retry at most; screen, user and binding events would otherwise each start a chain.
    private final Runnable retryBind = () -> this.link.retry(ActivityManager.getCurrentUser());
    private final WindowManager windows;
    private Messenger voice;
    private boolean listening, muted, connected, screenOff;
    private float input, output;
    private long observed;
    private Waves view;
    private final Messenger reply = new Messenger(new Handler(Looper.getMainLooper(), message -> {
        // A reply queued before screen-off unsubscribed must not repopulate the suspended tray.
        if (!this.link.subscribed() || !trusted(message.sendingUid)) return true;
        // 1C: a return to the task the shell recorded (RoseEdgeController), answered once on this same link.
        if (message.what == AgentTrayLink.Return.RETURN_TO) {
            AgentTrayLink.Return.returnTo(message.getData().getLong("token"), message.arg1,
                    (attempt, result) -> handler.post(() -> sendReturnResult(attempt, result)));
            return true;
        }
        // The shell starts a return as it arrives, so a cancel never precedes it: there is nothing to drop.
        if (message.what == AgentTrayLink.Return.CANCEL) return true;
        if (message.what != 100) return true;
        Bundle data = message.getData();
        if (data.getInt("protocolVersion") != 1) { mirrorBackgroundActivity(null); return true; }
        // Plan 1E: Rose Agent's theme choice; SystemUI applies, mirrors and acknowledges it (RoseThemeMirror).
        theme.fromRose(data.getString(RoseTheme.TRAY_KEY));
        mirrorBackgroundActivity(data.getString("backgroundActivity"));
        listening = data.getBoolean("listening");
        muted = data.getBoolean("muted");
        if (view != null) view.describe();
        connected = data.getBoolean("connected");
        active = AgentStatusControls.active(connected, listening, data.getString("status"));
        input = data.getFloat("inputLevel", 0);
        output = data.getFloat("outputLevel", 0);
        observed = data.getLong("audioObservedAt", 0);
        if (view != null) view.invalidate();
        return true;
    }));
    private final ServiceConnection connection = new ServiceConnection() {
        @Override public void onServiceConnected(ComponentName name, IBinder binder) {
            voice = new Messenger(binder);
            link.connected();
        }
        @Override public void onServiceDisconnected(ComponentName name) { link.disconnected(); }
        @Override public void onBindingDied(ComponentName name) { link.bindingDied(); }
        @Override public void onNullBinding(ComponentName name) { link.bindingDied(); }
    };
    // Plan 1E: the Rose theme SystemUI draws, mirrored to Settings.Global for Launcher3 and the keyboard.
    private final RoseThemeMirror theme = new RoseThemeMirror(new RoseThemeMirror.Port() {
        @Override public String read() {
            return Settings.Global.getString(context.getContentResolver(), RoseTheme.SETTING_KEY);
        }
        @Override public boolean write(String id) {
            try { return Settings.Global.putString(context.getContentResolver(), RoseTheme.SETTING_KEY, id); }
            catch (RuntimeException e) {
                Log.w(TAG, "Rose theme setting not written", e);
                return false;
            }
        }
        @Override public void acknowledge(String id) { sendThemeShown(id); }
    });
    // Subscribed only while the screen is on (OS-TRAY-LIFECYCLE-001); see AgentTrayLink.
    private final AgentTrayLink link = new AgentTrayLink(new AgentTrayLink.Port() {
        @Override public boolean bind(int user) {
            try {
                return context.bindServiceAsUser(new Intent().setComponent(new ComponentName(
                        "dev.pixel.voice", "dev.pixel.voice.AgentTrayService")), connection,
                        Context.BIND_AUTO_CREATE, UserHandle.of(user));
            } catch (RuntimeException e) {
                Log.w(TAG, "Voice binding unavailable", e);
                return false;
            }
        }
        @Override public void unbind() {
            try { context.unbindService(connection); }
            catch (IllegalArgumentException e) { Log.w(TAG, "Voice binding already released", e); }
        }
        @Override public boolean send(int what) { return AgentTrayPrototype.this.send(what); }
        @Override public void scheduleRetry() {
            handler.removeCallbacks(retryBind);
            handler.postDelayed(retryBind, 3000);
        }
        @Override public void cancelRetry() { handler.removeCallbacks(retryBind); }
        @Override public void resetState() { clearState(); }
        @Override public void forgetService() { voice = null; }
    });

    AgentTrayPrototype(Context source, RootTaskDisplayAreaOrganizer unused,
            DisplayInsetsController unusedInsets) {
        instance = this;
        // 1C: the shell's return target goes to Rose Agent whenever it changes.
        AgentTrayLink.Return.listen(() -> handler.post(this::sendReturnTarget));
        context = source.createWindowContext(WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                null);
        windows = context.getSystemService(WindowManager.class);
        screenOff = !context.getSystemService(android.os.PowerManager.class).isInteractive();
        context.getSystemService(DisplayManager.class).registerDisplayListener(this, handler);
        context.getSystemService(KeyguardManager.class).addKeyguardLockedStateListener(
                context.getMainExecutor(), locked -> reconcile());
        IntentFilter filter = new IntentFilter();
        filter.addAction(Intent.ACTION_SCREEN_OFF);
        filter.addAction(Intent.ACTION_SCREEN_ON);
        filter.addAction(Intent.ACTION_USER_PRESENT);
        filter.addAction(Intent.ACTION_USER_SWITCHED);
        context.registerReceiver(new BroadcastReceiver() {
            @Override public void onReceive(Context c, Intent intent) {
                String action = intent.getAction();
                if (Intent.ACTION_SCREEN_OFF.equals(action)) {
                    screenOff = true;
                    link.screenOff();
                } else if (Intent.ACTION_SCREEN_ON.equals(action)
                        || Intent.ACTION_USER_PRESENT.equals(action)) {
                    screenOff = false;
                    link.screenOn(ActivityManager.getCurrentUser());
                } else if (Intent.ACTION_USER_SWITCHED.equals(action)) {
                    link.userSwitched(ActivityManager.getCurrentUser());
                } else {
                    return;
                }
                reconcile();
            }
        }, filter, Context.RECEIVER_EXPORTED);
        // F08: the chin's availability follows gesture navigation and the edge switch while subscribed.
        context.getContentResolver().registerContentObserverAsUser(
                Settings.Secure.getUriFor(Settings.Secure.NAVIGATION_MODE), false,
                new ContentObserver(handler) {
                    @Override public void onChange(boolean selfChange) { chinMaybeChanged(); }
                }, UserHandle.SYSTEM);
        SystemProperties.addChangeCallback(() -> handler.post(this::chinMaybeChanged));
        // Plan 1E: the last theme is drawn before Rose answers, and an outside change of the setting is followed.
        theme.start();
        mirrorBackgroundActivity(null);
        RoseThemeMirror.listen(() -> { if (view != null) view.invalidate(); });
        context.getContentResolver().registerContentObserver(
                Settings.Global.getUriFor(RoseTheme.SETTING_KEY), false,
                new ContentObserver(handler) {
                    @Override public void onChange(boolean selfChange) { theme.settingChanged(); }
                });
        handler.post(() -> {
            if (!screenOff) link.screenOn(ActivityManager.getCurrentUser());
            reconcile();
        });
    }

    private boolean trusted(int uid) {
        try {
            return voice != null && uid == context.getPackageManager().getPackageUidAsUser(
                    "dev.pixel.voice", link.user());
        } catch (android.content.pm.PackageManager.NameNotFoundException e) { return false; }
    }

    // Same trusted Settings.Global writer as the theme. Deadline is producer-owned, never renewed here.
    private void mirrorBackgroundActivity(String value) {
        try {
            int boot = Settings.Global.getInt(context.getContentResolver(), Settings.Global.BOOT_COUNT, -1);
            String next = AgentStatusControls.backgroundActivity(link.user() == 0 ? value : null,
                    boot, System.nanoTime() / 1000000);
            String key = "rose_background_activity_v1";
            if (!next.equals(Settings.Global.getString(context.getContentResolver(), key))) {
                Settings.Global.putString(context.getContentResolver(), key, next);
            }
        } catch (RuntimeException e) {
            // A failed write cannot prolong an old receipt: readers expire its original deadline.
            Log.w(TAG, "Rose activity setting not written", e);
        }
    }

    private void clearState() {
        mirrorBackgroundActivity(null);
        connected = listening = muted = active = false;
        if (view != null) view.describe();
        controls.cancel();
        input = output = 0;
        observed = 0;
        if (view != null) view.invalidate();
    }

    private boolean send(int action) {
        if (voice == null) return false;
        Message message = Message.obtain(null, action);
        // F08: Rose Agent hides its own mute only where the chin can stand in for it.
        if (action == AgentTrayLink.REGISTER) message.arg2 = sentChin = chinArg();
        // Plan 1E: a new subscription is acknowledged afresh, so Rose never keeps a stale theme answer.
        if (action == AgentTrayLink.REGISTER) theme.resubscribed();
        message.replyTo = reply;
        try { voice.send(message); } catch (RemoteException e) { return false; }
        // 1C: each REGISTER is followed by the current return target, so a new subscriber never holds a stale one.
        if (action == AgentTrayLink.REGISTER) handler.post(this::sendReturnTarget);
        return true;
    }

    /** 1C: RETURN_TARGET, the shell's current record (token 0 when there is none), while subscribed. */
    private void sendReturnTarget() {
        if (voice == null || !link.subscribed()) return;
        AgentTrayLink.Return.Target target = AgentTrayLink.Return.target();
        Message message = Message.obtain(null, AgentTrayLink.Return.TARGET);
        Bundle data = new Bundle();
        data.putInt("protocolVersion", 1);
        data.putLong("token", target.token);
        data.putString("package", target.packageName);
        data.putInt("userId", target.userId);
        data.putString("label", target.label);
        message.setData(data);
        message.replyTo = reply;
        try { voice.send(message); } catch (RemoteException e) { link.disconnected(); }
    }

    /** 1C: RETURN_RESULT, the one answer to a RETURN_TO (arg1 its attempt, arg2 RETURNED, UNAVAILABLE or REFUSED). */
    private void sendReturnResult(int attempt, int result) {
        if (voice == null) return;
        Message message = Message.obtain(null, AgentTrayLink.Return.RESULT, attempt, result);
        message.replyTo = reply;
        try { voice.send(message); } catch (RemoteException e) { link.disconnected(); }
    }

    /** Plan 1E: THEME_SHOWN, the theme SystemUI's Rose surfaces now draw. */
    private void sendThemeShown(String id) {
        if (voice == null || !link.subscribed()) return;
        Message message = Message.obtain(null, AgentTrayLink.THEME_SHOWN);
        Bundle data = new Bundle();
        data.putString(RoseTheme.TRAY_KEY, id);
        message.setData(data);
        message.replyTo = reply;
        try { voice.send(message); } catch (RemoteException e) { link.disconnected(); }
    }

    /** F08: REGISTER's arg2, read fresh for each REGISTER. */
    private int chinArg() {
        return AgentStatusControls.chinArg(SystemProperties.getBoolean(EDGE_PROPERTY, false), link.user(),
                Settings.Secure.getIntForUser(context.getContentResolver(), Settings.Secure.NAVIGATION_MODE, 0,
                        UserHandle.USER_SYSTEM));
    }

    /** Navigation mode or a property changed: REGISTER again only when the chin's availability did. */
    private void chinMaybeChanged() {
        if (chinArg() != sentChin) link.refresh();
    }

    private int dp(float value) {
        return Math.round(value * context.getResources().getDisplayMetrics().density);
    }

    private void reconcile() {
        boolean visible = !screenOff && link.user() == 0
                && !context.getSystemService(KeyguardManager.class).isKeyguardLocked();
        if (!visible) {
            if (view != null) windows.removeViewImmediate(view);
            view = null;
            return;
        }
        WindowMetrics metrics = windows.getMaximumWindowMetrics();
        int height = metrics.getWindowInsets().getInsetsIgnoringVisibility(
                WindowInsets.Type.statusBars()).top;
        if (metrics.getWindowInsets().getDisplayCutout() != null) {
            height = Math.max(height, metrics.getWindowInsets().getDisplayCutout().getSafeInsetTop());
        }
        WindowManager.LayoutParams params = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT, Math.max(dp(24), height),
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                android.graphics.PixelFormat.TRANSLUCENT);
        // Below native status/notification windows so privacy chips and system
        // dialogs always cover our decorative indicators, never the reverse.
        params.privateFlags |= WindowManager.LayoutParams.PRIVATE_FLAG_TRUSTED_OVERLAY;
        params.gravity = Gravity.TOP;
        params.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS;
        params.setFitInsetsTypes(0);
        params.setTitle("Pixel agent status waves");
        if (view == null) { view = new Waves(); windows.addView(view, params); }
        else windows.updateViewLayout(view, params);
        Log.i(TAG, "Status waves height=" + params.height + "; app bounds untouched");
    }

    private final class Waves extends View {
        final StatusVoiceGlow agentGlow = new StatusVoiceGlow();
        final StatusVoiceGlow micGlow = new StatusVoiceGlow();
        long rendered;
        float leftStart, rightStart, glowWidth;
        int targetAt(float x) {
            if (x >= leftStart - dp(4) && x <= leftStart + glowWidth + dp(4)) return 1;
            if (x >= rightStart - dp(4) && x <= rightStart + glowWidth + dp(4)) return 2;
            return 0;
        }
        // The Rose chin's muted look (RoseEdgeMicSlot): slashed mic, ruby ring, the same inks.
        static final int MUTED_INK = 0xffff8c9a;
        static final int MUTED_RING = 0xbfff5a6e;
        static final int MUTED_FILL = 0x47140407;
        final Paint micOffPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
        final Path micOffPath = new Path();
        Waves() {
            super(context);
            setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);
            describe();
        }
        void describe() {
            setContentDescription(muted ? "Microphone muted"
                    : "Agent voice glow left, microphone voice glow right");
        }
        /**
         * Keyboard handoff rule 4: while Rose's microphone input is muted, the status bar shows "Mic off" with
         * the chin's slashed mic and ruby outline, so the state stays visible after the chin lowers. It is an
         * indicator only: the chin holds the microphone control.
         */
        void drawMicOff(Canvas canvas, float left) {
            final float h = Math.min(getHeight() - dp(4), dp(20));
            final float top = (getHeight() - h) / 2f;
            micOffPaint.reset();
            micOffPaint.setAntiAlias(true);
            micOffPaint.setTextSize(dp(11));
            micOffPaint.setFakeBoldText(true);
            final String label = "Mic off";
            final float glyph = h - dp(4);
            final float width = dp(6) + glyph + dp(4) + micOffPaint.measureText(label) + dp(8);
            final RectF pill = new RectF(left, top, left + width, top + h);
            // Plan 1E: muted keeps its warm meaning in every theme (RoseTheme.warm).
            final String t = RoseThemeMirror.current();
            micOffPaint.setColor(RoseTheme.warm(t, MUTED_FILL));
            canvas.drawRoundRect(pill, h / 2, h / 2, micOffPaint);
            micOffPaint.setStyle(Paint.Style.STROKE);
            micOffPaint.setStrokeWidth(dp(1));
            micOffPaint.setColor(RoseTheme.warm(t, MUTED_RING));
            canvas.drawRoundRect(pill, h / 2, h / 2, micOffPaint);
            // The chin's 24-unit mic glyph, scaled to the pill, with its slash.
            final int save = canvas.save();
            final float s = glyph / 24f;
            canvas.translate(left + dp(6), top + (h - glyph) / 2f);
            canvas.scale(s, s);
            micOffPaint.setStyle(Paint.Style.FILL);
            micOffPaint.setColor((RoseTheme.warm(t, MUTED_INK) & 0x00ffffff) | (Math.round(.55f * 255) << 24));
            canvas.drawRoundRect(new RectF(8.6f, 2.5f, 15.4f, 15f), 3.4f, 3.4f, micOffPaint);
            micOffPaint.setStyle(Paint.Style.STROKE);
            micOffPaint.setStrokeWidth(1.9f);
            micOffPaint.setStrokeCap(Paint.Cap.ROUND);
            canvas.drawArc(new RectF(5.6f, 4.4f, 18.4f, 17.2f), 0, 180, false, micOffPaint);
            micOffPath.reset();
            micOffPath.moveTo(12, 17.4f);
            micOffPath.lineTo(12, 20.6f);
            micOffPath.moveTo(8.8f, 20.8f);
            micOffPath.lineTo(15.2f, 20.8f);
            canvas.drawPath(micOffPath, micOffPaint);
            micOffPath.reset();
            micOffPath.moveTo(4.5f, 3.5f);
            micOffPath.lineTo(19.5f, 20.5f);
            micOffPaint.setColor(RoseTheme.warm(t, MUTED_INK));
            canvas.drawPath(micOffPath, micOffPaint);
            canvas.restoreToCount(save);
            micOffPaint.setStyle(Paint.Style.FILL);
            micOffPaint.setColor(RoseTheme.warm(t, MUTED_INK));
            final Paint.FontMetrics m = micOffPaint.getFontMetrics();
            canvas.drawText(label, left + dp(6) + glyph + dp(4), top + h / 2f - (m.ascent + m.descent) / 2f,
                    micOffPaint);
        }
        @Override protected void onDraw(Canvas canvas) {
            long now = SystemClock.elapsedRealtime();
            float elapsed = rendered == 0 ? 16 : Math.min(100, now - rendered);
            rendered = now;
            boolean fresh = now - observed < 1200;
            float center = getWidth() / 2f;
            float holeLeft = center - dp(12), holeRight = center + dp(12);
            WindowInsets insets = getRootWindowInsets();
            if (insets != null && insets.getDisplayCutout() != null) {
                for (Rect rect : insets.getDisplayCutout().getBoundingRects()) {
                    if (rect.top < getHeight() && rect.centerX() > getWidth() / 4
                            && rect.centerX() < getWidth() * 3 / 4) {
                        holeLeft = rect.left;
                        holeRight = rect.right;
                    }
                }
            }
            rightStart = holeRight + dp(6);
            glowWidth = Math.min(holeLeft-dp(6), getWidth()-rightStart);
            leftStart = 0;
            // At rest nothing is drawn (#224): the idle grey lines read as screen artifacts and are not in
            // the selected HiFi design. Only an active session shows its glows.
            if (active) {
                agentGlow.draw(canvas,leftStart,0,glowWidth,getHeight(),
                        fresh ? output : 0,now/1000f,elapsed,true);
            } else agentGlow.clear();
            if (active && listening && !muted) {
                micGlow.draw(canvas,rightStart,0,glowWidth,getHeight(),
                        fresh ? input : 0,now/1000f+1.7f,elapsed,false);
            } else micGlow.clear();
            // While muted, at rest or in a session: the "Mic off" indicator where the mic glow would be.
            if (muted) drawMicOff(canvas, rightStart + dp(4));
            // The service sends STATE only on change, so a Connecting glow drawn once would freeze; animate
            // for the whole active span, the same span the glow is drawn for.
            if (active) postInvalidateOnAnimation();
        }
    }

    @Override public void onDisplayAdded(int id) { }
    @Override public void onDisplayRemoved(int id) { }
    @Override public void onDisplayChanged(int id) { if (id == Display.DEFAULT_DISPLAY) reconcile(); }
}
