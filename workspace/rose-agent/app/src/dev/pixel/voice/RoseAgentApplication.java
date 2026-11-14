package dev.pixel.voice;

import android.app.Application;

/** Rose Agent's process: starts tracking its own activities' displays before any activity exists (RoseActivities),
 * the glasses-on hook's Bluetooth audio signal (BluetoothAudioWear, Nov 7 plan 1O), and the return control's source
 * (TrayReturn, Nov 9 plan 1C). */
public final class RoseAgentApplication extends Application {
 @Override public void onCreate() {
  super.onCreate();
  RoseActivities.install(this);
  BluetoothAudioWear.start(this);
  // 1C: the return control's exact-task source; with no SystemUI target it offers nothing, so the control stays hidden.
  RoseConversationActivity.returnSource = TrayReturn.INSTANCE;
 }
}
