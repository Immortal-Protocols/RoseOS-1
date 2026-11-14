/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.systemui.roseshade;

import android.content.Context;
import android.service.notification.NotificationListenerService.RankingMap;
import android.service.notification.StatusBarNotification;

import com.android.systemui.CoreStartable;
import com.android.systemui.dagger.SysUISingleton;
import com.android.systemui.statusbar.NotificationListener;
import com.android.wm.shell.AgentTrayPrototype;

import java.io.PrintWriter;

import javax.inject.Inject;

/**
 * Starts the Rose notification shade (1G) with SystemUI: it receives the owner's notifications from SystemUI's own
 * NotificationListener (no second listener service), cancels the ones the owner dismissed through it, and takes
 * downward status-bar pulls through AgentTrayPrototype's status-bar hook. Bound by {@link RoseShadeModule}; off when
 * persist.wm.debug.rose_shade is false.
 */
@SysUISingleton
public final class RoseShadeStartable implements CoreStartable {
    private final Context mContext;
    private final NotificationListener mListener;

    @Inject
    public RoseShadeStartable(Context context, NotificationListener listener) {
        mContext = context;
        mListener = listener;
    }

    @Override
    public void start() {
        if (!RoseShadeController.enabled()) return;
        final RoseShadeController shade = new RoseShadeController(mContext, mListener::cancelNotification);
        // SystemUI's listener calls its handlers on the main thread.
        mListener.addNotificationHandler(new NotificationListener.NotificationHandler() {
            @Override
            public void onNotificationPosted(StatusBarNotification sbn, RankingMap rankingMap) {
                shade.onPosted(sbn);
            }

            @Override
            public void onNotificationRemoved(StatusBarNotification sbn, RankingMap rankingMap) {
                shade.onRemoved(sbn.getKey());
            }

            @Override
            public void onNotificationRemoved(StatusBarNotification sbn, RankingMap rankingMap, int reason) {
                shade.onRemoved(sbn.getKey());
            }

            @Override
            public void onNotificationRankingUpdate(RankingMap rankingMap) {}

            @Override
            public void onNotificationsInitialized() {}
        });
        // The listener may already be connected: take what is posted now.
        final StatusBarNotification[] active = mListener.getActiveNotifications();
        if (active != null) {
            for (StatusBarNotification sbn : active) shade.onPosted(sbn);
        }
        AgentTrayPrototype.setStatusBarDragHandler(shade::onStatusBarTouch);
    }

    @Override
    public void dump(PrintWriter pw, String[] args) {}

    @Override
    public boolean isDumpCritical() {
        return false;
    }

    @Override
    public void onBootCompleted() {}
}
