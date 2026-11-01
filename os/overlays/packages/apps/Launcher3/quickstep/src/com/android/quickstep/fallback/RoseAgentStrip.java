/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import static com.android.launcher3.util.Executors.UI_HELPER_EXECUTOR;

import android.animation.ObjectAnimator;
import android.animation.ValueAnimator;
import android.app.ActivityManager;
import android.app.ActivityTaskManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.ContextWrapper;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.drawable.GradientDrawable;
import android.hardware.display.DisplayManager;
import android.os.UserHandle;
import android.util.AttributeSet;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.TextView;
import com.android.launcher3.R;
import com.android.quickstep.RecentsActivity;

import java.util.ArrayList;
import java.util.List;

/**
 * RoseOS (#156): the agent row of the fallback overview, above Launcher3's own human-task carousel and scrolling
 * independently of it. Cards are Rose Agent's real running work (RoseAgentWork). Selecting one opens Rose's viewer of
 * that work only while it is the agent screen's current task. Uses only what this recents component already holds:
 * the platform lets the configured recents component read all tasks (ActivityTaskManagerService.isGetTasksAllowed)
 * and display owners are system-recorded. No permission, no provider read, no invented card.
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
    }

    private static final float IDLE = 0.35f;

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
        Context context = getContext();
        UI_HELPER_EXECUTOR.execute(() -> {
            RoseAgentWork next = read(context);
            post(() -> { if (token == generation) show(next); });
        });
    }

    /**
     * The platform's running work, filtered to the controller's own displays and the current user before any label is
     * looked up (RoseAgentWork.of, then labelled). A label that cannot be read shows the package name; only a failed
     * controller, display or task read makes the row unavailable, named by its step (no exception text shown or kept).
     */
    private static RoseAgentWork read(Context context) {
        RoseAgentWork.Step step = RoseAgentWork.Step.CONTROLLER;
        try {
            int user = ActivityManager.getCurrentUser();
            PackageManager pm = context.getPackageManager();
            int controllerUid;
            try {
                controllerUid = pm.getPackageUidAsUser(RoseAgentWork.CONTROLLER, user);
            } catch (PackageManager.NameNotFoundException e) {
                controllerUid = -1;
            }
            step = RoseAgentWork.Step.DISPLAYS;
            List<RoseAgentWork.Display> displays = new ArrayList<>();
            for (android.view.Display d : context.getSystemService(DisplayManager.class).getDisplays()) {
                displays.add(new RoseAgentWork.Display(d.getDisplayId(), d.getOwnerUid()));
            }
            step = RoseAgentWork.Step.TASKS;
            List<RoseAgentWork.Task> tasks = new ArrayList<>();
            List<Integer> seenDisplays = new ArrayList<>();
            // getTasks lists tasks front to back, so the first task met on a display is its current screen.
            for (ActivityManager.RunningTaskInfo t : ActivityTaskManager.getInstance().getTasks(64)) {
                if (t.topActivity == null) continue;
                String pkg = t.topActivity.getPackageName();
                boolean top = !seenDisplays.contains(t.displayId);
                seenDisplays.add(t.displayId);
                tasks.add(new RoseAgentWork.Task(t.taskId, t.displayId, t.userId, pkg, pkg, top));
            }
            return RoseAgentWork.of(controllerUid, user, displays, tasks).labelled(task ->
                    pm.getApplicationLabel(pm.getApplicationInfoAsUser(task.packageName, 0, task.userId)).toString());
        } catch (RuntimeException e) {
            Log.w(TAG, "Agent work unavailable at " + step + " (" + e.getClass().getSimpleName() + ")");
            return RoseAgentWork.failed(step);
        }
    }

    private void show(RoseAgentWork next) {
        cards.removeAllViews();
        status.setText(next.reason == null ? "" : next.reason);
        link.setText(next.linkLabel());
        setWorking(next.working() > 0 && getWindowVisibility() == VISIBLE);
        for (RoseAgentWork.Task task : next.items) {
            TextView card = text(task.label, 15);
            card.setGravity(Gravity.CENTER);
            card.setMinWidth(dp(120));
            card.setMinHeight(dp(72));
            card.setPadding(dp(16), dp(12), dp(16), dp(12));
            GradientDrawable shape = new GradientDrawable();
            shape.setCornerRadius(dp(18));
            // The card Rose is working in now is marked; background apps stay plain.
            shape.setColor(task.topOfDisplay ? 0x55FFFFFF : 0x33FFFFFF);
            if (task.topOfDisplay) shape.setStroke(dp(2), 0xFFFFFFFF);
            card.setBackground(shape);
            LayoutParams lp = new LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT);
            lp.setMarginEnd(dp(10));
            card.setContentDescription(task.label + (task.topOfDisplay ? ", Rose is working in it now" : ", in Rose's background"));
            card.setOnClickListener(v -> select(task));
            cards.addView(card, lp);
        }
    }

    /**
     * The tap re-reads the platform off the UI thread; only the newest tap acts. A valid selection sends the
     * controller's viewer a selection request (display and package), which it binds to its own live session or
     * refuses; it is never an unbound viewer, and never carries an Android task id as a Rose id.
     */
    private void select(RoseAgentWork.Task card) {
        int token = ++selection;
        Context context = getContext();
        UI_HELPER_EXECUTOR.execute(() -> {
            RoseAgentWork fresh = read(context);
            post(() -> {
                if (token != selection) return;
                show(fresh);
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
