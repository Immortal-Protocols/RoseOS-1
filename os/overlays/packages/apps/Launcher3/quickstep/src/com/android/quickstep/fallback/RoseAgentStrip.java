/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import static com.android.launcher3.util.Executors.UI_HELPER_EXECUTOR;

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
import android.content.res.Resources;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.os.Process;
import android.os.RemoteException;
import android.os.UserHandle;
import android.text.TextUtils;
import android.util.AttributeSet;
import android.util.DisplayMetrics;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.window.TaskSnapshot;
import android.window.TaskSnapshotManager;
import com.android.launcher3.R;
import com.android.launcher3.util.SystemUiController;
import com.android.launcher3.views.ActivityContext;
import com.android.launcher3.views.ScrimView;
import com.android.quickstep.RecentsActivity;

import java.lang.ref.WeakReference;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * RoseOS (#156): the agent row of the fallback overview, below Launcher3's own human-task carousel and scrolling
 * independently of it. Cards are Rose Agent's real running work (RoseAgentWork). Selecting one opens Rose's viewer of
 * that work only while it is the agent screen's current task. #410: the row is the Oxblood HiFi's Rose carousel, a
 * centred fan with depth (RoseAgentFan, RoseFan) beside a static ruby pearl, with Oxblood card materials from the
 * shared contract; the fan only places the cards this row reads, and nothing about membership, capture or selection
 * changes. Uses only what this recents component already holds:
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
    private final RoseAgentFan scroller;
    private final LinearLayout cards;
    private int generation, selection;
    /** The frames on screen now, all from the read that built the cards; released when replaced or hidden. */
    private Map<Integer, Bitmap> shownFrames = new HashMap<>();
    /** The identities (display, user, package) of the cards on screen now, in order. */
    private List<String> shownKeys = new ArrayList<>();
    /** Re-reads the platform while a live view is on screen; never decides membership or attribution. */
    private final Runnable previewTick = this::refresh;
    /** Plan 1E: the theme the row draws (RoseThemeSetting, SystemUI's mirror); the pearl it set on the link. */
    private String theme;
    /** Plan 1E: each card's Oxblood ring (current or neutral), so a theme change recolours it in place. */
    private final java.util.Map<View, Integer> rings = new java.util.WeakHashMap<>();
    private final GradientDrawable pearl;
    private final Runnable themeChanged = this::onThemeChanged;

    public RoseAgentStrip(Context context, AttributeSet attrs) {
        super(context, attrs);
        theme = RoseThemeSetting.current(context);
        field.setTheme(context, theme);
        field.setOnRaster(() -> { if (scrim != null) scrim.invalidate(); });
        setOrientation(VERTICAL);
        int pad = dp(12);
        setPadding(pad, pad, pad, 0);
        // The HiFi link between the carousels: Rose's ruby pearl (Oxblood, oxblood.css:342, static: it shows no mic or
        // call state, which Launcher3 does not have), lit only while Rose is actually working, and the count of that
        // work. Nothing in it is invented: both come from the platform read (RoseAgentWork).
        LinearLayout row = new LinearLayout(context);
        row.setOrientation(HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        connector = new View(context);
        pearl = new GradientDrawable();
        pearl.setShape(GradientDrawable.OVAL);
        pearl.setGradientType(GradientDrawable.RADIAL_GRADIENT);
        pearl.setGradientCenter(0.34f, 0.28f);
        pearl.setGradientRadius(PEARL_RADIUS_DP * getResources().getDisplayMetrics().density);
        pearl.setColors(RoseTheme.map(theme, PEARL_COLORS), PEARL_STOPS);
        connector.setBackground(pearl);
        connector.setAlpha(IDLE);
        connector.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
        LayoutParams pearlLp = new LayoutParams(dp(PEARL_DP), dp(PEARL_DP));
        pearlLp.setMarginEnd(dp(8));
        row.addView(connector, pearlLp);
        link = text("", LINK_SP);
        link.setSingleLine(true);
        link.setEllipsize(TextUtils.TruncateAt.END);
        link.setAccessibilityLiveRegion(ACCESSIBILITY_LIVE_REGION_POLITE);
        row.addView(link);
        row.setMinimumHeight(dp(LINK_ROW_DP));
        addView(row, new LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT));
        scroller = new RoseAgentFan(context);
        cards = new LinearLayout(context);
        cards.setOrientation(HORIZONTAL);
        cards.setGravity(Gravity.CENTER_VERTICAL);
        scroller.addView(cards);
        addView(scroller, new LayoutParams(LayoutParams.MATCH_PARENT, dp(RoseFan.CARD_HEIGHT_DP)));
        status = text("", STATUS_SP);
        // Room for STATUS_LINES lines is part of the row's measured height (rowHeight), at every font scale.
        status.setMaxLines(RoseFan.STATUS_LINES);
        status.setEllipsize(TextUtils.TruncateAt.END);
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
     * change refreshes the visible rows.
     */
    private static final class Sessions implements AutomatedPackageListener {
        private static Sessions instance;
        private final Context context;
        private final RoseAgentWork.Ownership ownership = new RoseAgentWork.Ownership();
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

        synchronized long generation() { return generation; }
    }

    private static final float IDLE = 0.35f;
    private static final int TASK_LIMIT = 64;
    /** How often a visible live view is captured again: at most once a second, like the controller's own preview. */
    private static final long PREVIEW_INTERVAL_MS = 1000;
    private static final int LINK_ROW_DP = 22, PEARL_DP = 18, LINK_SP = 13, STATUS_SP = 13;
    /**
     * Oxblood materials: Launcher3's own copy of the shared contract (qa/oxblood/oxblood-contract.json, written by Home;
     * test_rose_switcher compares these key for key): manager.card.fill and .neutralRing, ink.primary and
     * .secondary, and the ruby pearl (pearl.ruby.stops, pearl.sizes.switcher). The contract's running edge is only for
     * work that is authoritatively running, which Launcher3 cannot see, so it is never drawn here: the card that is the
     * current screen of Rose's display is marked with the ink.secondary ring instead.
     */
    static final int CARD_FILL = 0xFF1A0A0D, CARD_RING = 0x29ECD5C6, CURRENT_RING = 0xFFD2B9AB, TEXT = 0xFFF2E6D9,
            TEXT_MUTED = 0xFFD2B9AB;
    static final int[] PEARL_COLORS = {0xFFFFE6E8, 0xFFE8606F, 0xFFC43048, 0xFF8A1428, 0xFF4A0612};
    static final float[] PEARL_STOPS = {0.07f, 0.20f, 0.46f, 0.76f, 1f};
    /** The ruby gradient's radius: from its centre (34%, 28%) to the farthest corner of the 18 dp pearl. */
    private static final float PEARL_RADIUS_DP = (float) Math.hypot(0.66 * PEARL_DP, 0.72 * PEARL_DP);

    /**
     * The row's height: the claimed default (R.dimen.rose_agent_strip_height), or more when the person's font scale
     * needs it for the count and two lines of status (RoseFan.rowHeightPx). TypedValue applies the platform's own sp
     * conversion, nonlinear at large scales. FallbackActivityInterface claims this height below the task cards, and
     * the row measures itself to it, so the status is never clipped and the human cards make room.
     */
    public static int rowHeight(Resources resources) {
        DisplayMetrics metrics = resources.getDisplayMetrics();
        return RoseFan.rowHeightPx(metrics.density,
                TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, LINK_SP, metrics),
                TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, STATUS_SP, metrics),
                resources.getDimensionPixelSize(R.dimen.rose_agent_strip_height));
    }

    /**
     * The row's height when this context belongs to the fallback overview (RecentsActivity, whose layout holds the
     * row), else 0: OverviewActionsView subtracts it so the action buttons sit below the row. The in-launcher overview
     * is unchanged.
     */
    public static int reservedBelow(Context context) {
        for (Context c = context; c != null; c = c instanceof ContextWrapper ? ((ContextWrapper) c).getBaseContext() : null) {
            if (c instanceof RecentsActivity) {
                return rowHeight(c.getResources());
            }
            if (!(c instanceof ContextWrapper)) break;
        }
        return 0;
    }

    /** The layout's fixed height is the default; the row takes rowHeight, the same height the overview claimed. */
    @Override
    protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
        super.onMeasure(widthMeasureSpec, MeasureSpec.makeMeasureSpec(rowHeight(getResources()), MeasureSpec.EXACTLY));
    }

    private final View.OnLayoutChangeListener relayout = (v, l, t, r, b, ol, ot, orr, ob) -> anchor();

    /**
     * #410: the switcher's Oxblood field (RoseOxbloodField, the contract's fields.rose) drawn on the fallback overview's
     * own scrim through its public drawing-controller seam, after the scrim's background colour and at that colour's
     * current opacity. So the field follows every overview state the scrim already animates (opaque in the overview,
     * transparent while an app is in front or going Home) without any new state logic. Nothing else in the fallback
     * overview uses this seam (only the in-launcher All Apps does, on its own scrim). Only in the RecentsActivity
     * overview, where RoseOverviewOxblood sets the scrim's Oxblood base: Launcher3's in-window recents (other displays,
     * or its in-window flag) inflate the same layout but keep the stock scrim, so the field is not registered there.
     */
    private final RoseOxbloodField field = new RoseOxbloodField();
    private ScrimView scrim;
    private final ScrimView.ScrimDrawingController fieldOnScrim = (canvas, scale, bottomOffset) -> {
        ScrimView on = scrim;
        if (on == null) return;
        field.drawAsScrim(canvas, on.getWidth(), on.getHeight(), Color.alpha(on.getBackgroundColor()));
        lightBars(on);
    };
    private boolean lightBarsSet;

    /**
     * Plan 1E, Winds: the stock scrim picks the bar icons from its own (dark) colour, but a light theme's field is what
     * is drawn on it, so while the scrim is opaque (the stock rule: view and colour above 0.9) a light theme asks for
     * dark icons. A dark theme leaves the stock choice alone; leaving a light theme restores the stock dark-scrim state.
     */
    private void lightBars(ScrimView on) {
        boolean light = RoseTheme.light(theme);
        if (!light && !lightBarsSet) return;
        boolean opaque = on.getVisibility() == VISIBLE && on.getAlpha() > 0.9f
                && Color.alpha(on.getBackgroundColor()) / 255f > 0.9f;
        if (light && !opaque) return;
        SystemUiController bars = ActivityContext.lookupContext(on.getContext()).getSystemUiController();
        if (light) bars.updateUiState(SystemUiController.UI_STATE_SCRIM_VIEW, true);
        else if (opaque) bars.updateUiState(SystemUiController.UI_STATE_SCRIM_VIEW, false);
        else bars.updateUiState(SystemUiController.UI_STATE_SCRIM_VIEW, 0);
        lightBarsSet = light;
    }

    @Override
    protected void onAttachedToWindow() {
        super.onAttachedToWindow();
        View parent = (View) getParent();
        parent.addOnLayoutChangeListener(relayout);
        View buttons = parent.findViewById(R.id.action_buttons);
        if (buttons != null) buttons.addOnLayoutChangeListener(relayout);
        View found = parent.findViewById(R.id.scrim_view);
        if (found instanceof ScrimView && reservedBelow(getContext()) > 0) {
            scrim = (ScrimView) found;
            scrim.setDrawingController(fieldOnScrim);
        }
        RoseThemeSetting.listen(getContext(), themeChanged);
        // A change while detached is picked up now.
        onThemeChanged();
    }

    /**
     * Plan 1E: the mirrored theme changed (or the row was attached). Colour only: the pearl, the link and status ink,
     * each card's plate and ink and the field are recoloured in place in the new theme. The platform is not re-read and
     * nothing is captured: nothing about membership, capture, selection or layout changes.
     */
    private void onThemeChanged() {
        String next = RoseThemeSetting.current(getContext());
        if (next.equals(theme)) return;
        theme = next;
        pearl.setColors(RoseTheme.map(theme, PEARL_COLORS), PEARL_STOPS);
        link.setTextColor(RoseTheme.map(theme, TEXT));
        status.setTextColor(RoseTheme.map(theme, TEXT));
        for (int i = 0; i < cards.getChildCount(); i++) {
            View card = cards.getChildAt(i);
            if (card.getBackground() instanceof GradientDrawable) {
                ((GradientDrawable) card.getBackground()).setColor(RoseTheme.map(theme, CARD_FILL));
            }
            Integer ring = rings.get(card);
            if (ring != null && card.getForeground() instanceof GradientDrawable) {
                ((GradientDrawable) card.getForeground()).setStroke(Math.max(1, dp(1)), RoseTheme.map(theme, ring));
            }
            if (!(card instanceof LinearLayout)) continue;
            LinearLayout content = (LinearLayout) card;
            int last = content.getChildCount() - 1;
            for (int j = 0; j <= last; j++) {
                // bind(): the reason a card has no live view comes first (muted ink); the name is last (TEXT).
                if (content.getChildAt(j) instanceof TextView) {
                    ((TextView) content.getChildAt(j)).setTextColor(RoseTheme.map(theme, j == last ? TEXT : TEXT_MUTED));
                }
            }
        }
        field.setTheme(getContext(), theme);
        if (scrim != null) {
            if (!RoseTheme.light(theme)) lightBars(scrim);
            scrim.invalidate();
        }
    }

    @Override
    protected void onDetachedFromWindow() {
        View parent = (View) getParent();
        if (parent != null) {
            parent.removeOnLayoutChangeListener(relayout);
            View buttons = parent.findViewById(R.id.action_buttons);
            if (buttons != null) buttons.removeOnLayoutChangeListener(relayout);
        }
        if (scrim != null) {
            scrim.setDrawingController(null);
            scrim = null;
        }
        RoseThemeSetting.unlisten(themeChanged);
        removeCallbacks(previewTick);
        generation++;
        selection++;
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
     * Each entry places the carousel afresh (RoseFan.initial: the second card when there are three or more, before its
     * first frame) and reads the platform afresh; leaving dims the pearl. Nothing here stops, finishes or moves a task.
     */
    @Override
    protected void onWindowVisibilityChanged(int visibility) {
        super.onWindowVisibilityChanged(visibility);
        if (visibility == VISIBLE) {
            scroller.unplace();
            refresh();
        } else {
            // Leaving: no capture continues, no pending read or tap lands, and no frame stays to be shown on return.
            removeCallbacks(previewTick);
            generation++;
            selection++;
            clearCards();
            setWorking(false);
        }
    }

    /**
     * The pearl is lit only while work is in progress; idle and hidden it is dim. It never animates (the Oxblood
     * switcher's pearl is static, oxblood.css:321), so there is nothing to stop under reduced motion.
     */
    private void setWorking(boolean working) {
        connector.setAlpha(working ? 1f : IDLE);
    }

    /** Reads the platform state off the UI thread; the newest read wins. */
    void refresh() {
        int token = ++generation;
        removeCallbacks(previewTick);
        Context context = getContext();
        int framePx = dp(RoseFan.CARD_WIDTH_DP);
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
            if (task.taskId == RoseAgentWork.NO_TASK) continue; // a member without a visible task: nothing to capture
            if (RoseAgentWork.preview(task, locked) != RoseAgentWork.Preview.CAPTURE) continue;
            Bitmap frame = frame(task.taskId, framePx);
            if (frame != null) frames.put(task.taskId, frame);
        }
        return frames;
    }

    /** framePx: the card's width; the frame keeps the task's own aspect. */
    private static Bitmap frame(int taskId, int framePx) {
        TaskSnapshot snapshot = null;
        try {
            snapshot = TaskSnapshotManager.getInstance().takeTaskSnapshot(taskId, false /* updateCache */,
                    true /* lowResolution */);
            if (snapshot == null || !snapshot.isBufferValid()) return null;
            Bitmap wrapped = snapshot.wrapToBitmap(snapshot.getColorSpace());
            if (wrapped == null || wrapped.getWidth() <= 0) return null;
            Bitmap copy = wrapped.copy(Bitmap.Config.ARGB_8888, false);
            wrapped.recycle();
            if (copy == null) return null;
            // #410: the whole width of the task's real screen at the card's width; the card shows its top at 1:1.
            int height = Math.max(1, Math.round(copy.getHeight() * (framePx / (float) copy.getWidth())));
            Bitmap scaled = Bitmap.createScaledBitmap(copy, framePx, height, true);
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
        shownKeys = new ArrayList<>();
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
            // Tasks attach a member's live view and selection only; membership is the platform's apps snapshot.
            List<ActivityManager.RunningTaskInfo> running = ActivityTaskManager.getInstance().getTasks(TASK_LIMIT);
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
            // A snapshot applied during these reads means they are not one state; that snapshot refreshes the row.
            if (platform.generation() != generation) return RoseAgentWork.unconfirmed();
            return RoseAgentWork.of(controllerUid, user, owners, sessionDevices, tasks).labelled(task ->
                    pm.getApplicationLabel(pm.getApplicationInfoAsUser(task.packageName, 0, task.userId)).toString());
        } catch (RuntimeException e) {
            Log.w(TAG, "Agent work unavailable at " + step + " (" + e.getClass().getSimpleName() + ")");
            return RoseAgentWork.failed(step);
        }
    }

    /** A card's identity across reads: the same display, user and package is the same card (RoseAgentWork.decide). */
    private static String key(RoseAgentWork.Task task) {
        return task.displayId + "/" + task.userId + "/" + task.packageName;
    }

    /**
     * Cards only from this read, each with the frame captured for it in this read or the reason it has none. When the
     * read has the same cards as the screen (same identities, same order), each card is rebound in place: a re-read
     * every second while a live view shows never removes the card under a finger, so a tap or accessibility focus that
     * straddles it is kept. Otherwise the cards are rebuilt and the carousel keeps the card it centred (RoseFan.keep).
     */
    private void show(RoseAgentWork next, Map<Integer, Bitmap> frames, boolean locked) {
        List<String> keys = new ArrayList<>();
        for (RoseAgentWork.Task task : next.items) keys.add(key(task));
        List<String> before = shownKeys;
        int centred = scroller.centred();
        boolean same = keys.equals(before) && cards.getChildCount() == keys.size();
        Map<Integer, Bitmap> previous = shownFrames;
        if (!same) clearCards();
        shownFrames = frames;
        status.setText(next.reason == null ? "" : next.reason);
        link.setText(next.linkLabel());
        setWorking(next.working() > 0 && getWindowVisibility() == VISIBLE);
        int index = 0;
        for (RoseAgentWork.Task task : next.items) {
            LinearLayout card = same ? (LinearLayout) cards.getChildAt(index) : card();
            bind(card, task, frames, locked);
            if (!same) cards.addView(card, new LayoutParams(dp(RoseFan.CARD_WIDTH_DP), dp(RoseFan.CARD_HEIGHT_DP)));
            index++;
        }
        // Rebound in place: the previous frames are no longer on any card, so they are released now.
        if (same) release(previous);
        shownKeys = keys;
        // Placed afresh once the first cards of this entry arrive (an empty or unconfirmed read places nothing).
        if (!scroller.placed()) { if (!keys.isEmpty()) scroller.place(RoseFan.initial(keys.size())); }
        else if (!same) scroller.place(RoseFan.keep(before, centred, keys));
        // Capture again only while a live view is on screen; the row keeps re-reading membership at the same time.
        if (getWindowVisibility() == VISIBLE && next.hasLiveView(locked)) postDelayed(previewTick, PREVIEW_INTERVAL_MS);
    }

    /** An empty Rose card: Oxblood plate, clipped to its rounded outline, centred by the fan on accessibility focus. */
    private LinearLayout card() {
        LinearLayout card = new LinearLayout(getContext());
        card.setOrientation(VERTICAL);
        card.setGravity(Gravity.CENTER_HORIZONTAL);
        GradientDrawable plate = new GradientDrawable();
        plate.setCornerRadius(dp(RoseFan.CARD_RADIUS_DP));
        plate.setColor(RoseTheme.map(theme, CARD_FILL));
        card.setBackground(plate);
        card.setClipToOutline(true);
        card.setElevation(dp(6));
        card.setAccessibilityDelegate(scroller.centreOnFocus());
        card.setOnFocusChangeListener(scroller.centreOnKeyFocus());
        return card;
    }

    /** One card's content from this read: its live view or the reason it has none, its name, and its marking. */
    private void bind(LinearLayout card, RoseAgentWork.Task task, Map<Integer, Bitmap> frames, boolean locked) {
        card.removeAllViews();
        Bitmap frame = frames.get(task.taskId);
        String why = RoseAgentWork.previewText(RoseAgentWork.preview(task, locked), frame != null);
        View body;
        if (why == null) {
            ImageView live = new ImageView(getContext());
            live.setImageBitmap(frame);
            // The frame is the card's width (frame()): its top at 1:1, never stretched; the rest of the screen is
            // below the card's live area and is not drawn.
            live.setScaleType(ImageView.ScaleType.MATRIX);
            body = live;
        } else {
            TextView none = text(why, 12);
            none.setGravity(Gravity.CENTER);
            none.setTextColor(RoseTheme.map(theme, TEXT_MUTED));
            none.setPadding(dp(8), 0, dp(8), 0);
            body = none;
        }
        // The live view takes what the name leaves, so a large font never pushes the name out of the card.
        card.addView(body, new LayoutParams(LayoutParams.MATCH_PARENT, 0, 1f));
        TextView name = text(task.label, 13);
        name.setGravity(Gravity.CENTER);
        name.setSingleLine(true);
        name.setEllipsize(TextUtils.TruncateAt.END);
        name.setPadding(dp(8), dp(6), dp(8), dp(6));
        card.addView(name, new LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT));
        // The current screen of Rose's display has the brighter ink ring; background apps keep the fine neutral ring.
        // Never the ruby running edge: Launcher3 cannot see whether the work is authoritatively running.
        GradientDrawable edge = new GradientDrawable();
        edge.setCornerRadius(dp(RoseFan.CARD_RADIUS_DP));
        edge.setColor(0);
        if (task.topOfDisplay) edge.setStroke(Math.max(1, dp(1)), RoseTheme.map(theme, CURRENT_RING));
        else edge.setStroke(Math.max(1, dp(1)), RoseTheme.map(theme, CARD_RING));
        rings.put(card, task.topOfDisplay ? CURRENT_RING : CARD_RING);
        card.setForeground(edge);
        card.setContentDescription(task.label + (task.topOfDisplay ? ", Rose is working in it now" : ", in Rose's background")
                + (why == null ? ", live view" : ", " + why));
        card.setOnClickListener(v -> select(task));
    }

    /**
     * The tap re-reads the platform off the UI thread; only the newest tap acts. A valid selection sends the
     * controller's viewer a selection request (display and package), which it binds to its own live session or
     * refuses; it is never an unbound viewer, and never carries an Android task id as a Rose id.
     */
    private void select(RoseAgentWork.Task card) {
        int token = ++selection;
        Context context = getContext();
        int framePx = dp(RoseFan.CARD_WIDTH_DP);
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
        view.setTextColor(RoseTheme.map(theme, TEXT));
        return view;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
