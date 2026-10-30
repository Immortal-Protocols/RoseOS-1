/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import static com.android.launcher3.util.Executors.UI_HELPER_EXECUTOR;

import android.app.ActivityManager;
import android.app.ActivityTaskManager;
import android.content.ComponentName;
import android.content.Context;
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

    private final TextView title, status;
    private final LinearLayout cards;
    private RoseAgentWork work = RoseAgentWork.unavailable("Loading");
    private int generation;

    public RoseAgentStrip(Context context, AttributeSet attrs) {
        super(context, attrs);
        setOrientation(VERTICAL);
        int pad = dp(12);
        setPadding(pad, pad, pad, 0);
        title = text("Rose is working in", 13);
        addView(title);
        HorizontalScrollView scroller = new HorizontalScrollView(context);
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

    @Override
    protected void onWindowVisibilityChanged(int visibility) {
        super.onWindowVisibilityChanged(visibility);
        if (visibility == VISIBLE) refresh();
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

    private static RoseAgentWork read(Context context) {
        try {
            int user = ActivityManager.getCurrentUser();
            int controllerUid;
            try {
                controllerUid = context.getPackageManager().getPackageUidAsUser(RoseAgentWork.CONTROLLER, user);
            } catch (PackageManager.NameNotFoundException e) {
                controllerUid = -1;
            }
            List<RoseAgentWork.Display> displays = new ArrayList<>();
            for (android.view.Display d : context.getSystemService(DisplayManager.class).getDisplays()) {
                displays.add(new RoseAgentWork.Display(d.getDisplayId(), d.getOwnerUid()));
            }
            List<RoseAgentWork.Task> tasks = new ArrayList<>();
            List<Integer> seenDisplays = new ArrayList<>();
            PackageManager pm = context.getPackageManager();
            // getTasks lists tasks front to back, so the first task met on a display is its current screen.
            for (ActivityManager.RunningTaskInfo t : ActivityTaskManager.getInstance().getTasks(64)) {
                if (t.topActivity == null) continue;
                String pkg = t.topActivity.getPackageName();
                CharSequence label;
                try {
                    label = pm.getApplicationLabel(pm.getApplicationInfoAsUser(pkg, 0, t.userId));
                } catch (PackageManager.NameNotFoundException e) {
                    label = pkg;
                }
                boolean top = !seenDisplays.contains(t.displayId);
                seenDisplays.add(t.displayId);
                tasks.add(new RoseAgentWork.Task(t.taskId, t.displayId, t.userId, pkg, label.toString(), top));
            }
            return RoseAgentWork.of(controllerUid, user, displays, tasks);
        } catch (RuntimeException e) {
            Log.w(TAG, "Agent work unavailable", e);
            return RoseAgentWork.unavailable("Rose's work could not be read");
        }
    }

    private void show(RoseAgentWork next) {
        work = next;
        cards.removeAllViews();
        status.setText(next.reason == null ? "" : next.reason);
        for (RoseAgentWork.Task task : next.items) {
            TextView card = text(task.label, 15);
            card.setGravity(Gravity.CENTER);
            card.setMinWidth(dp(120));
            card.setMinHeight(dp(72));
            card.setPadding(dp(16), dp(12), dp(16), dp(12));
            GradientDrawable shape = new GradientDrawable();
            shape.setCornerRadius(dp(18));
            shape.setColor(0x33FFFFFF);
            card.setBackground(shape);
            LayoutParams lp = new LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT);
            lp.setMarginEnd(dp(10));
            card.setContentDescription("Rose is working in " + task.label);
            card.setOnClickListener(v -> select(task));
            cards.addView(card, lp);
        }
    }

    private void select(RoseAgentWork.Task task) {
        RoseAgentWork.Open result = RoseAgentWork.open(task.taskId, work);
        String notice = RoseAgentWork.describe(result, task.label);
        if (notice != null) {
            status.setText(notice);
            refresh();
            return;
        }
        try {
            getContext().startActivityAsUser(new Intent().setComponent(VIEWER)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), UserHandle.of(task.userId));
        } catch (RuntimeException e) {
            Log.w(TAG, "Viewer unavailable", e);
            status.setText("Rose's viewer could not be opened");
        }
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
