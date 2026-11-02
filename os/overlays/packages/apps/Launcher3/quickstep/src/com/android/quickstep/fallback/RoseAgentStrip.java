/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import static com.android.launcher3.util.Executors.UI_HELPER_EXECUTOR;

import android.animation.ObjectAnimator;
import android.animation.ValueAnimator;
import android.app.ActivityManager;
import android.app.ActivityTaskManager;
import android.app.KeyguardManager;
import android.companion.virtual.VirtualDevice;
import android.companion.virtual.VirtualDeviceManager;
import android.companion.virtual.computercontrol.AutomatedPackageListener;
import android.content.ComponentName;
import android.content.Context;
import android.content.ContextWrapper;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.drawable.GradientDrawable;
import android.os.Process;
import android.os.RemoteException;
import android.os.UserHandle;
import android.util.AttributeSet;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.widget.HorizontalScrollView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.window.TaskSnapshot;
import android.window.TaskSnapshotManager;
import com.android.launcher3.R;
import com.android.quickstep.RecentsActivity;

import java.lang.ref.WeakReference;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * RoseOS (#156): the agent row of the fallback overview, above Launcher3's own human-task carousel and scrolling
 * independently of it. Cards are Rose Agent's real running work (RoseAgentWork). Selecting one opens Rose's viewer of
 * that work only while it is the agent screen's current task. Uses only what this recents component already holds:
 * the platform lets the configured recents component read all tasks (ActivityTaskManagerService.isGetTasksAllowed)
 * and receive its complete snapshots of which owner each ComputerControl session device belongs to
 * (VirtualDeviceManagerService.checkCallerIsRecentsOrHomeRoleHolder). Rose's session display stays private: it is
 * never listed to Launcher3 and nothing here makes it public. No permission, no provider read, no invented card.
 */
public final class RoseAgentStrip extends LinearLayout {
    private static final String TAG = "RoseAgentStrip";
    private static final ComponentName VIEWER =
            new ComponentName(RoseAgentWork.CONTROLLER, RoseAgentWork.CONTROLLER + ".AgentWorkspaceActivity");

    private final TextView link, status;
    private final View connector;
    private final HorizontalScrollView scroller;
    private final LinearLayout cards;
    private ObjectAnimator pulse;
    private int generation, selection;
    /** The frames on screen now, all from the read that built the cards; released when replaced or hidden. */
    private Map<Integer, Bitmap> shownFrames = new HashMap<>();
    /** Re-reads the platform while a live view is on screen; never decides membership or attribution. */
    private final Runnable previewTick = this::refresh;

    public RoseAgentStrip(Context context, AttributeSet attrs) {
        super(context, attrs);
        setOrientation(VERTICAL);
        int pad = dp(12);
        setPadding(pad, pad, pad, 0);
        // The HiFi link between the carousels: a connector that is live only while Rose is actually working, and
        // the count of that work. Nothing in it is invented: both come from the platform read (RoseAgentWork).
        LinearLayout row = new LinearLayout(context);
        row.setOrientation(HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        connector = new View(context);
        GradientDrawable wire = new GradientDrawable();
        wire.setCornerRadius(dp(2));
        wire.setColor(0xFFFFFFFF);
        connector.setBackground(wire);
        connector.setAlpha(IDLE);
        LayoutParams wireLp = new LayoutParams(dp(28), dp(3));
        wireLp.setMarginEnd(dp(8));
        row.addView(connector, wireLp);
        link = text("", 13);
        link.setAccessibilityLiveRegion(ACCESSIBILITY_LIVE_REGION_POLITE);
        row.addView(link);
        addView(row);
        scroller = new HorizontalScrollView(context);
        scroller.setHorizontalScrollBarEnabled(false);
        cards = new LinearLayout(context);
        cards.setOrientation(HORIZONTAL);
        scroller.addView(cards);
        addView(scroller);
        status = text("", 13);
        status.setAccessibilityLiveRegion(ACCESSIBILITY_LIVE_REGION_POLITE);
        addView(status);
        setContentDescription("Rose's background apps");
        // Registered as early as the overview exists, so its report is in before the row is first shown. A refusal
        // never fails the overview: read() retries it and the row names that step.
        Sessions sessions = Sessions.of(context);
        sessions.watch(this);
        try {
            sessions.register();
        } catch (RuntimeException e) {
            // Named by read() at Step.OWNERSHIP.
        }
    }

    /**
     * The platform's session-device ownership, held for this process. VirtualDeviceManager
     * .registerAutomatedPackageListener (allowed only for the recents component or the HOME role holder) sends a
     * complete snapshot of this user's ComputerControl session devices running apps on registration, even when empty,
     * and again on every change (onAutomatedDevicesChanged). RoseAgentWork.Ownership applies them: the first valid one
     * per registration, then only higher generations; a malformed one is never partly applied. The package reports
     * (onAutomatedPackagesChanged) are not used for attribution. Registered once, as Launcher3's own
     * AutomationRepositoryImpl is, retried on the next read if it failed, with a fresh baseline each time. Any applied
     * change refreshes the visible rows. Session displays once seen are remembered (ids are never reused), so a task
     * left on a released one is held as unconfirmed instead of looking like another app's display.
     */
    private static final class Sessions implements AutomatedPackageListener {
        private static Sessions instance;
        private final Context context;
        private final RoseAgentWork.Ownership ownership = new RoseAgentWork.Ownership();
        private final Set<Integer> seenSessionDisplays = new HashSet<>();
        private final List<WeakReference<RoseAgentStrip>> strips = new ArrayList<>();
        private boolean registered;
        /** Counts every applied change, so a read can tell that one arrived while it was reading the platform. */
        private long generation;

        private Sessions(Context context) { this.context = context; }

        static synchronized Sessions of(Context context) {
            if (instance == null) instance = new Sessions(context.getApplicationContext());
            return instance;
        }

        /** Throws when the platform refuses or has no virtual device service; read() names that step. */
        synchronized void register() {
            if (registered) return;
            VirtualDeviceManager vdm = context.getSystemService(VirtualDeviceManager.class);
            if (vdm == null) throw new IllegalStateException();
            ownership.reset();
            generation++;
            vdm.registerAutomatedPackageListener(Runnable::run, this);
            registered = true;
        }

        synchronized void watch(RoseAgentStrip strip) { strips.add(new WeakReference<>(strip)); }

        @Override
        public void onAutomatedPackagesChanged(String automatingPackage, List<String> automatedPackages, UserHandle user) {
            // Never used for attribution (ownership comes only from onAutomatedDevicesChanged). An app starting or
            // stopping on a session device that already runs apps changes no device snapshot, so this only re-reads
            // the visible rows, which then decide from the platform's own state.
            refreshStrips();
        }

        @Override
        public void onAutomatedDevicesChanged(long snapshotGeneration, int[] deviceIds, String[] ownerPackages,
                int[] ownerUids) {
            synchronized (this) {
                if (!ownership.accept(snapshotGeneration, deviceIds, ownerPackages, ownerUids)) return;
                generation++;
            }
            refreshStrips();
        }

        private void refreshStrips() {
            List<RoseAgentStrip> visible = new ArrayList<>();
            synchronized (this) {
                for (int i = strips.size() - 1; i >= 0; i--) {
                    RoseAgentStrip strip = strips.get(i).get();
                    if (strip == null) strips.remove(i);
                    else visible.add(strip);
                }
            }
            for (RoseAgentStrip strip : visible) {
                strip.post(() -> { if (strip.getWindowVisibility() == VISIBLE) strip.refresh(); });
            }
        }

        /**
         * The same dispatch's devices with their current apps, front or hidden: the platform's one membership source
         * (#156 parity), applied by the same generation rule. Membership never comes from task history.
         */
        @Override
        public void onAutomatedDeviceAppsChanged(long snapshotGeneration, int[] deviceIds, String[] ownerPackages,
                int[] ownerUids, boolean[] deviceComplete, int[] appDeviceIds, String[] appPackageNames) {
            synchronized (this) {
                if (!ownership.acceptApps(snapshotGeneration, deviceIds, ownerPackages, ownerUids, deviceComplete,
                        appDeviceIds, appPackageNames)) {
                    return;
                }
                generation++;
            }
            refreshStrips();
        }

        synchronized RoseAgentWork.Owners owners() { return ownership.snapshot(); }

        /** The session displays seen before and not listed now; remembers the ones listed now. */
        synchronized List<Integer> released(Set<Integer> current) {
            List<Integer> gone = new ArrayList<>();
            for (int id : seenSessionDisplays) if (!current.contains(id)) gone.add(id);
            seenSessionDisplays.addAll(current);
            return gone;
        }

        synchronized long generation() { return generation; }
    }

    private static final float IDLE = 0.35f;
    private static final int TASK_LIMIT = 64;
    /** How often a visible live view is captured again: at most once a second, like the controller's own preview. */
    private static final long PREVIEW_INTERVAL_MS = 1000;
    private static final int PREVIEW_HEIGHT_DP = 64;

    /**
     * The row's height when this context belongs to the fallback overview (RecentsActivity, whose layout holds the
     * row), else 0: OverviewActionsView subtracts it so the action buttons sit below the row. The in-launcher overview
     * is unchanged.
     */
    public static int reservedBelow(Context context) {
        for (Context c = context; c != null; c = c instanceof ContextWrapper ? ((ContextWrapper) c).getBaseContext() : null) {
            if (c instanceof RecentsActivity) {
                return c.getResources().getDimensionPixelSize(R.dimen.rose_agent_strip_height);
            }
            if (!(c instanceof ContextWrapper)) break;
        }
        return 0;
    }

    private final View.OnLayoutChangeListener relayout = (v, l, t, r, b, ol, ot, orr, ob) -> anchor();

    @Override
    protected void onAttachedToWindow() {
        super.onAttachedToWindow();
        View parent = (View) getParent();
        parent.addOnLayoutChangeListener(relayout);
        View buttons = parent.findViewById(R.id.action_buttons);
        if (buttons != null) buttons.addOnLayoutChangeListener(relayout);
    }

    @Override
    protected void onDetachedFromWindow() {
        View parent = (View) getParent();
        if (parent != null) {
            parent.removeOnLayoutChangeListener(relayout);
            View buttons = parent.findViewById(R.id.action_buttons);
            if (buttons != null) buttons.removeOnLayoutChangeListener(relayout);
        }
        removeCallbacks(previewTick);
        generation++;
        clearCards();
        super.onDetachedFromWindow();
    }

    /**
     * Below the human cards and above the action buttons (the selected HiFi order): the row's bottom edge goes to the
     * buttons' laid-out top, measured in the window, so it follows wherever Launcher3 placed them. Without visible
     * buttons it stays at the bottom of its parent, where the layout put it. Only the row moves; no card or task does.
     */
    private void anchor() {
        View parent = (View) getParent();
        if (parent == null) return;
        View buttons = parent.findViewById(R.id.action_buttons);
        float shift = 0;
        if (buttons != null && buttons.isShown() && buttons.getHeight() > 0) {
            int[] at = new int[2], origin = new int[2];
            buttons.getLocationInWindow(at);
            parent.getLocationInWindow(origin);
            shift = (at[1] - origin[1]) - getBottom();
        }
        if (getTranslationY() != shift) setTranslationY(shift);
    }

    /**
     * Each entry starts from the first agent card (the HiFi agent carousel is placed again on every entry) and reads
     * the platform afresh; leaving stops the connector. Nothing here stops, finishes or moves any task.
     */
    @Override
    protected void onWindowVisibilityChanged(int visibility) {
        super.onWindowVisibilityChanged(visibility);
        if (visibility == VISIBLE) {
            scroller.scrollTo(0, 0);
            refresh();
        } else {
            // Leaving: no capture continues, no pending read lands, and no frame stays to be shown again on return.
            removeCallbacks(previewTick);
            generation++;
            clearCards();
            setWorking(false);
        }
    }

    /** The connector pulses only while work is in progress; idle and hidden it is still. */
    private void setWorking(boolean working) {
        if (working) {
            if (pulse == null) {
                pulse = ObjectAnimator.ofFloat(connector, "alpha", IDLE, 1f);
                pulse.setDuration(900);
                pulse.setRepeatMode(ValueAnimator.REVERSE);
                pulse.setRepeatCount(ValueAnimator.INFINITE);
                pulse.start();
            }
        } else {
            if (pulse != null) pulse.cancel();
            pulse = null;
            connector.setAlpha(IDLE);
        }
    }

    /** Reads the platform state off the UI thread; the newest read wins. */
    void refresh() {
        int token = ++generation;
        removeCallbacks(previewTick);
        Context context = getContext();
        int framePx = dp(PREVIEW_HEIGHT_DP);
        UI_HELPER_EXECUTOR.execute(() -> {
            boolean locked = locked(context);
            // Locked: nothing of Rose's screens is listed (the common #156/#225 contract), so nothing is read either.
            RoseAgentWork next = locked ? RoseAgentWork.locked() : read(context);
            Map<Integer, Bitmap> frames = capture(next, locked, framePx);
            post(() -> {
                if (token == generation) show(next, frames, locked);
                else release(frames);
            });
        });
    }

    /** Fail closed: a lock state that cannot be read counts as locked. */
    private static boolean locked(Context context) {
        try {
            KeyguardManager keyguard = context.getSystemService(KeyguardManager.class);
            return keyguard == null || keyguard.isKeyguardLocked();
        } catch (RuntimeException e) {
            return true;
        }
    }

    /**
     * Fresh frames for this read's own cards only: Rose's items whose preview is CAPTURE (the front task of a session
     * display, phone unlocked). Each is TaskSnapshotManager.takeTaskSnapshot(taskId, updateCache false, low
     * resolution): the platform's real current content, not stored in the recents cache, with secure windows
     * redacted, and nothing foregrounded or sent to the agent. A task with no frame has none: nothing older is reused.
     */
    private static Map<Integer, Bitmap> capture(RoseAgentWork work, boolean locked, int framePx) {
        Map<Integer, Bitmap> frames = new HashMap<>();
        if (work.state != RoseAgentWork.State.AVAILABLE) return frames;
        for (RoseAgentWork.Task task : work.items) {
            if (task.taskId == RoseAgentWork.NO_TASK) continue; // a member without its own task: nothing to capture
            if (RoseAgentWork.preview(task, locked) != RoseAgentWork.Preview.CAPTURE) continue;
            Bitmap frame = frame(task.taskId, framePx);
            if (frame != null) frames.put(task.taskId, frame);
        }
        return frames;
    }

    private static Bitmap frame(int taskId, int framePx) {
        TaskSnapshot snapshot = null;
        try {
            snapshot = TaskSnapshotManager.getInstance().takeTaskSnapshot(taskId, false /* updateCache */,
                    true /* lowResolution */);
            if (snapshot == null || !snapshot.isBufferValid()) return null;
            Bitmap wrapped = snapshot.wrapToBitmap(snapshot.getColorSpace());
            if (wrapped == null || wrapped.getHeight() <= 0) return null;
            Bitmap copy = wrapped.copy(Bitmap.Config.ARGB_8888, false);
            wrapped.recycle();
            if (copy == null) return null;
            int width = Math.max(1, Math.round(copy.getWidth() * (framePx / (float) copy.getHeight())));
            Bitmap scaled = Bitmap.createScaledBitmap(copy, width, framePx, true);
            if (scaled != copy) copy.recycle();
            return scaled;
        } catch (RemoteException | RuntimeException e) {
            Log.w(TAG, "Live view unavailable (" + e.getClass().getSimpleName() + ")");
            return null;
        } finally {
            if (snapshot != null) snapshot.closeBuffer();
        }
    }

    private static void release(Map<Integer, Bitmap> frames) {
        for (Bitmap frame : frames.values()) frame.recycle();
        frames.clear();
    }

    private void clearCards() {
        cards.removeAllViews();
        release(shownFrames);
    }

    /**
     * The platform's running work, filtered to the controller's own automated work and the current user before any
     * label is looked up (RoseAgentWork.of, then labelled). A label that cannot be read shows the package name; only a
     * failed controller, automation, task or display read makes the row unavailable, named by its step (no exception
     * text shown or kept). DisplayManager.getDisplays() is not used: it never lists Rose's private session display to
     * Launcher3 (Display.hasAccess), so it made the row say "No background work" while Rose worked.
     */
    private static RoseAgentWork read(Context context) {
        RoseAgentWork.Step step = RoseAgentWork.Step.CONTROLLER;
        try {
            // This overview's own user: the fallback overview runs in the foreground user's Launcher3. Not
            // ActivityManager.getCurrentUser(), which needs INTERACT_ACROSS_USERS(_FULL) (UserController
            // .checkGetCurrentUserPermissions) that Launcher3 does not hold, so it always threw here and the row read
            // "Rose's work could not be read (controller)". Every lookup below then stays within this user.
            int user = Process.myUserHandle().getIdentifier();
            PackageManager pm = context.getPackageManager();
            int controllerUid;
            try {
                controllerUid = pm.getPackageUidAsUser(RoseAgentWork.CONTROLLER, user);
            } catch (PackageManager.NameNotFoundException e) {
                controllerUid = -1;
            }
            step = RoseAgentWork.Step.OWNERSHIP;
            Sessions platform = Sessions.of(context);
            platform.register();
            long generation = platform.generation();
            RoseAgentWork.Owners owners = platform.owners();
            step = RoseAgentWork.Step.TASKS;
            List<RoseAgentWork.Task> tasks = new ArrayList<>();
            List<Integer> seenDisplays = new ArrayList<>();
            // getTasks lists tasks front to back, so the first task met on a display is its current screen. A full
            // list may be truncated, so the row could not say it shows all of Rose's work.
            List<ActivityManager.RunningTaskInfo> running = ActivityTaskManager.getInstance().getTasks(TASK_LIMIT);
            boolean complete = running.size() < TASK_LIMIT;
            for (ActivityManager.RunningTaskInfo t : running) {
                if (t.topActivity == null) continue;
                String pkg = t.topActivity.getPackageName();
                boolean top = !seenDisplays.contains(t.displayId);
                seenDisplays.add(t.displayId);
                tasks.add(new RoseAgentWork.Task(t.taskId, t.displayId, t.userId, pkg, pkg, top));
            }
            // The displays of ComputerControl session devices, read after the tasks so every live session display a
            // task is on is listed: only the platform's session path creates a virtual device with
            // DEVICE_PROFILE_COMPUTER_CONTROL (VirtualDeviceManagerService), and getVirtualDevices() and
            // getDisplayIds() need no permission. Another app's virtual display (streaming, casting) is not one.
            step = RoseAgentWork.Step.DISPLAYS;
            VirtualDeviceManager vdm = context.getSystemService(VirtualDeviceManager.class);
            Map<Integer, Integer> sessionDevices = new HashMap<>();
            for (VirtualDevice device : vdm.getVirtualDevices()) {
                if (device.getDeviceProfile() != VirtualDevice.DEVICE_PROFILE_COMPUTER_CONTROL) continue;
                for (int id : device.getDisplayIds()) if (id > 0) sessionDevices.put(id, device.getDeviceId());
            }
            List<Integer> released = platform.released(sessionDevices.keySet());
            // A snapshot applied during these reads means they are not one state; that snapshot refreshes the row.
            if (platform.generation() != generation) return RoseAgentWork.unconfirmed();
            return RoseAgentWork.of(controllerUid, user, owners, sessionDevices, released, tasks, complete).labelled(task ->
                    pm.getApplicationLabel(pm.getApplicationInfoAsUser(task.packageName, 0, task.userId)).toString());
        } catch (RuntimeException e) {
            Log.w(TAG, "Agent work unavailable at " + step + " (" + e.getClass().getSimpleName() + ")");
            return RoseAgentWork.failed(step);
        }
    }

    /** Cards only from this read, each with the frame captured for it in this read or the reason it has none. */
    private void show(RoseAgentWork next, Map<Integer, Bitmap> frames, boolean locked) {
        clearCards();
        shownFrames = frames;
        status.setText(next.reason == null ? "" : next.reason);
        link.setText(next.linkLabel());
        setWorking(next.working() > 0 && getWindowVisibility() == VISIBLE);
        for (RoseAgentWork.Task task : next.items) {
            LinearLayout card = new LinearLayout(getContext());
            card.setOrientation(VERTICAL);
            card.setGravity(Gravity.CENTER_HORIZONTAL);
            card.setMinimumWidth(dp(120));
            card.setMinimumHeight(dp(96));
            card.setPadding(dp(12), dp(8), dp(12), dp(8));
            Bitmap frame = frames.get(task.taskId);
            String why = RoseAgentWork.previewText(RoseAgentWork.preview(task, locked), frame != null);
            View body;
            if (why == null) {
                ImageView live = new ImageView(getContext());
                live.setImageBitmap(frame);
                live.setAdjustViewBounds(true);
                body = live;
            } else {
                TextView none = text(why, 12);
                none.setGravity(Gravity.CENTER);
                body = none;
            }
            card.addView(body, new LayoutParams(LayoutParams.WRAP_CONTENT, dp(PREVIEW_HEIGHT_DP)));
            TextView name = text(task.label, 15);
            name.setGravity(Gravity.CENTER);
            card.addView(name);
            GradientDrawable shape = new GradientDrawable();
            shape.setCornerRadius(dp(18));
            // The card Rose is working in now is marked; background apps stay plain.
            shape.setColor(task.topOfDisplay ? 0x55FFFFFF : 0x33FFFFFF);
            if (task.topOfDisplay) shape.setStroke(dp(2), 0xFFFFFFFF);
            card.setBackground(shape);
            LayoutParams lp = new LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT);
            lp.setMarginEnd(dp(10));
            card.setContentDescription(task.label + (task.topOfDisplay ? ", Rose is working in it now" : ", in Rose's background")
                    + (why == null ? ", live view" : ", " + why));
            card.setOnClickListener(v -> select(task));
            cards.addView(card, lp);
        }
        // Capture again only while a live view is on screen; the row keeps re-reading membership at the same time.
        if (getWindowVisibility() == VISIBLE && next.hasLiveView(locked)) postDelayed(previewTick, PREVIEW_INTERVAL_MS);
    }

    /**
     * The tap re-reads the platform off the UI thread; only the newest tap acts. A valid selection sends the
     * controller's viewer a selection request (display and package), which it binds to its own live session or
     * refuses; it is never an unbound viewer, and never carries an Android task id as a Rose id.
     */
    private void select(RoseAgentWork.Task card) {
        int token = ++selection;
        Context context = getContext();
        int framePx = dp(PREVIEW_HEIGHT_DP);
        UI_HELPER_EXECUTOR.execute(() -> {
            RoseAgentWork platform = read(context);
            boolean locked = locked(context);
            RoseAgentWork fresh = locked ? RoseAgentWork.locked() : platform;
            Map<Integer, Bitmap> frames = capture(fresh, locked, framePx);
            post(() -> {
                if (token != selection) {
                    release(frames);
                    return;
                }
                removeCallbacks(previewTick);
                show(fresh, frames, locked);
                RoseAgentWork.Open result = RoseAgentWork.decide(card, fresh);
                String notice = RoseAgentWork.describe(result, card.label);
                if (notice != null) {
                    status.setText(notice);
                    return;
                }
                try {
                    context.startActivityAsUser(new Intent().setComponent(VIEWER)
                            .putExtra(RoseAgentWork.EXTRA_SELECT_DISPLAY, card.displayId)
                            .putExtra(RoseAgentWork.EXTRA_SELECT_PACKAGE, card.packageName)
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), UserHandle.of(card.userId));
                } catch (RuntimeException e) {
                    Log.w(TAG, "Viewer unavailable", e);
                    status.setText("Rose's viewer could not be opened");
                }
            });
        });
    }

    private TextView text(CharSequence value, int sp) {
        TextView view = new TextView(getContext());
        view.setText(value);
        view.setTextSize(sp);
        view.setTextColor(0xFFFFFFFF);
        return view;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
