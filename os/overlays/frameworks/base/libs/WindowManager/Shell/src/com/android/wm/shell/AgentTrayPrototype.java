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
import android.graphics.Path;
import android.graphics.Rect;
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

    /** Called by the native status window; the decorative overlay stays non-touchable. */
    public static boolean onStatusBarTouch(MotionEvent event) {
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
                x, y, event.getEventTime(), target, active, muted,
                ViewConfiguration.get(context).getScaledTouchSlop());
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
        if (message.what != 100 || !this.link.subscribed() || !trusted(message.sendingUid)) return true;
        Bundle data = message.getData();
        if (data.getInt("protocolVersion") != 1) return true;
        listening = data.getBoolean("listening");
        muted = data.getBoolean("muted");
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

    private void clearState() {
        connected = listening = muted = active = false;
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
        message.replyTo = reply;
        try { voice.send(message); return true; } catch (RemoteException e) { return false; }
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
        Waves() {
            super(context);
            setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);
            setContentDescription("Agent voice glow left, microphone voice glow right");
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
            // The service sends STATE only on change, so a Connecting glow drawn once would freeze; animate
            // for the whole active span, the same span the glow is drawn for.
            if (active) postInvalidateOnAnimation();
        }
    }

    @Override public void onDisplayAdded(int id) { }
    @Override public void onDisplayRemoved(int id) { }
    @Override public void onDisplayChanged(int id) { if (id == Display.DEFAULT_DISPLAY) reconcile(); }
}
