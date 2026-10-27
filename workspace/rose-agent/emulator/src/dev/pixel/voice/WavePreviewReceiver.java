package dev.pixel.voice;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.os.SystemClock;

/** Deterministic visual fixture, compiled into the isolated emulator APK only. */
public final class WavePreviewReceiver extends BroadcastReceiver {
    private static long until;
    private static String mode = "off";
    @Override public void onReceive(Context context, Intent intent) {
        mode = intent.getStringExtra("mode");
        if (mode == null) mode = "off";
        until = SystemClock.elapsedRealtime() + Math.min(600000, Math.max(0,
                intent.getIntExtra("durationMs", 60000)));
        android.util.Log.i("WavePreview", "Emulator-only simulated signal: " + mode);
    }
    static Bundle state() {
        long now = SystemClock.elapsedRealtime();
        if (now > until) return null;
        double t = now / 1000.0;
        float level = (float) (.22 + .13 * Math.sin(t * 11) + .075 * Math.sin(t * 27));
        boolean on = !"off".equals(mode), muted = "mute".equals(mode);
        Bundle b = new Bundle();
        b.putInt("protocolVersion", 1);
        b.putBoolean("available", true);
        b.putBoolean("connected", on);
        b.putBoolean("listening", on && !muted);
        b.putBoolean("muted", muted);
        b.putString("status", muted ? "Muted" : on ? "Listening" : "Disconnected");
        b.putFloat("inputLevel", "right".equals(mode) || "both".equals(mode) ? level : 0);
        b.putFloat("outputLevel", "left".equals(mode) || "both".equals(mode) ? level : 0);
        b.putLong("audioObservedAt", now);
        return b;
    }
}
