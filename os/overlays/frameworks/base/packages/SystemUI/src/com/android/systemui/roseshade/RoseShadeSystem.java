/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.systemui.roseshade;

import android.app.NotificationManager;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.hardware.display.DisplayManager;
import android.net.wifi.WifiManager;
import android.os.Handler;
import android.provider.Settings;
import android.util.Log;
import android.view.Display;

/**
 * The Rose shade's quick settings, wired to Android (1G). Each tile reads the system's own state and a tap only
 * requests a change; {@link RoseQuickSettings} shows a state only once Android reports it, so a refused change
 * never looks done. SystemUI holds the permissions these calls need (the stock tiles make the same calls).
 */
final class RoseShadeSystem {
    private static final String TAG = "RoseShade";

    interface Listener {
        void onQuickSettingsChanged();
    }

    private final Context mContext;
    private final Handler mHandler;
    private final RoseQuickSettings mState;
    private final Listener mListener;
    private String mTorchCamera;
    private boolean mRegistered;

    private final BroadcastReceiver mReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            refresh();
        }
    };

    private final CameraManager.TorchCallback mTorch = new CameraManager.TorchCallback() {
        @Override
        public void onTorchModeChanged(String cameraId, boolean enabled) {
            if (cameraId.equals(mTorchCamera)) {
                mState.report(RoseQuickSettings.Tile.FLASHLIGHT, enabled);
                mListener.onQuickSettingsChanged();
            }
        }

        @Override
        public void onTorchModeUnavailable(String cameraId) {
            if (cameraId.equals(mTorchCamera)) {
                mState.report(RoseQuickSettings.Tile.FLASHLIGHT, false);
                mListener.onQuickSettingsChanged();
            }
        }
    };

    RoseShadeSystem(Context context, Handler handler, RoseQuickSettings state, Listener listener) {
        mContext = context;
        mHandler = handler;
        mState = state;
        mListener = listener;
    }

    /** Starts following the system while the shade is open. */
    void start() {
        if (mRegistered) return;
        mRegistered = true;
        final IntentFilter filter = new IntentFilter();
        filter.addAction(WifiManager.WIFI_STATE_CHANGED_ACTION);
        filter.addAction(BluetoothAdapter.ACTION_STATE_CHANGED);
        filter.addAction(NotificationManager.ACTION_INTERRUPTION_FILTER_CHANGED);
        mContext.registerReceiver(mReceiver, filter, null, mHandler, Context.RECEIVER_NOT_EXPORTED);
        final CameraManager cameras = mContext.getSystemService(CameraManager.class);
        mTorchCamera = torchCamera(cameras);
        if (cameras != null && mTorchCamera != null) cameras.registerTorchCallback(mTorch, mHandler);
        refresh();
    }

    void stop() {
        if (!mRegistered) return;
        mRegistered = false;
        mContext.unregisterReceiver(mReceiver);
        final CameraManager cameras = mContext.getSystemService(CameraManager.class);
        if (cameras != null) cameras.unregisterTorchCallback(mTorch);
    }

    /** Reads every tile and the brightness again. */
    void refresh() {
        final WifiManager wifi = mContext.getSystemService(WifiManager.class);
        if (wifi != null) mState.report(RoseQuickSettings.Tile.WIFI, wifi.isWifiEnabled());
        final BluetoothAdapter bluetooth = bluetooth();
        if (bluetooth != null) {
            mState.report(RoseQuickSettings.Tile.BLUETOOTH, bluetooth.getState() == BluetoothAdapter.STATE_ON);
        }
        final NotificationManager notifications = mContext.getSystemService(NotificationManager.class);
        if (notifications != null) {
            mState.report(RoseQuickSettings.Tile.DO_NOT_DISTURB,
                    notifications.getZenMode() != Settings.Global.ZEN_MODE_OFF);
        }
        final DisplayManager displays = mContext.getSystemService(DisplayManager.class);
        if (displays != null) mState.reportBrightness(displays.getBrightness(Display.DEFAULT_DISPLAY));
        mListener.onQuickSettingsChanged();
    }

    /** A tile tap: asks Android for the opposite state. The tile follows Android's report, not the tap. */
    void toggle(RoseQuickSettings.Tile tile) {
        final Boolean on = mState.requestToggle(tile);
        if (on == null) return;
        try {
            switch (tile) {
                case WIFI: {
                    final WifiManager wifi = mContext.getSystemService(WifiManager.class);
                    if (wifi != null) wifi.setWifiEnabled(on);
                    break;
                }
                case BLUETOOTH: {
                    final BluetoothAdapter bluetooth = bluetooth();
                    if (bluetooth != null) {
                        if (on) bluetooth.enable();
                        else bluetooth.disable();
                    }
                    break;
                }
                case FLASHLIGHT: {
                    final CameraManager cameras = mContext.getSystemService(CameraManager.class);
                    if (cameras != null && mTorchCamera != null) cameras.setTorchMode(mTorchCamera, on);
                    break;
                }
                case DO_NOT_DISTURB: {
                    final NotificationManager notifications =
                            mContext.getSystemService(NotificationManager.class);
                    if (notifications != null) {
                        notifications.setZenMode(on ? Settings.Global.ZEN_MODE_IMPORTANT_INTERRUPTIONS
                                : Settings.Global.ZEN_MODE_OFF, null, TAG);
                    }
                    break;
                }
            }
        } catch (RuntimeException | android.hardware.camera2.CameraAccessException e) {
            // Refused: the tile keeps showing Android's actual state.
            Log.w(TAG, "Quick setting " + tile + " refused", e);
        }
        mHandler.postDelayed(this::refresh, 300);
    }

    /** The slider moved: 0..1, the display's own brightness scale. */
    void setBrightness(float value) {
        final DisplayManager displays = mContext.getSystemService(DisplayManager.class);
        if (displays == null) return;
        try {
            displays.setBrightness(Display.DEFAULT_DISPLAY, Math.max(0f, Math.min(1f, value)));
            mState.reportBrightness(displays.getBrightness(Display.DEFAULT_DISPLAY));
        } catch (RuntimeException e) {
            Log.w(TAG, "Brightness refused", e);
        }
    }

    private BluetoothAdapter bluetooth() {
        final BluetoothManager manager = mContext.getSystemService(BluetoothManager.class);
        return manager == null ? null : manager.getAdapter();
    }

    private static String torchCamera(CameraManager cameras) {
        if (cameras == null) return null;
        try {
            for (String id : cameras.getCameraIdList()) {
                final CameraCharacteristics c = cameras.getCameraCharacteristics(id);
                final Boolean flash = c.get(CameraCharacteristics.FLASH_INFO_AVAILABLE);
                final Integer facing = c.get(CameraCharacteristics.LENS_FACING);
                if (Boolean.TRUE.equals(flash) && facing != null
                        && facing == CameraCharacteristics.LENS_FACING_BACK) {
                    return id;
                }
            }
        } catch (android.hardware.camera2.CameraAccessException | RuntimeException e) {
            Log.w(TAG, "No torch camera", e);
        }
        return null;
    }
}
