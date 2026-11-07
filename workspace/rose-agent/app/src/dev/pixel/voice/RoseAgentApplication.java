package dev.pixel.voice;

import android.app.Application;

/** Rose Agent's process: starts tracking its own activities' displays before any activity exists (RoseActivities),
 * and the glasses-on hook's Bluetooth audio signal (BluetoothAudioWear, Nov 7 plan 1O). */
public final class RoseAgentApplication extends Application {
 @Override public void onCreate() {
  super.onCreate();
  RoseActivities.install(this);
  BluetoothAudioWear.start(this);
 }
}
