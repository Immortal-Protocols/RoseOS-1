package dev.pixel.voice;

import android.content.Context;
import android.media.AudioDeviceCallback;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;
import android.os.Handler;
import android.os.Looper;

/** The glasses-on hook's signal (Nov 7 plan 1O): the owner's chosen proxy (Nathan, 2026-11-07). Any Bluetooth audio
 * output device connecting counts as glasses on, and the last one disconnecting counts as removal. It is not a worn
 * sensor.
 *
 * <p>It watches AudioManager's device list, which needs no permission. Android calls the callback once with the current
 * devices on registration, which gives the hook its baseline, then on every change. GlassesWearHook turns those reports
 * into once-per-transition decisions and ignores blips shorter than its minimum removal. Started once per process from
 * RoseAgentApplication. */
final class BluetoothAudioWear {
 private BluetoothAudioWear() {}

 static final GlassesWearHook.Source SOURCE = new GlassesWearHook.Source(GlassesWearHook.Source.BLUETOOTH_AUDIO, "bluetooth_audio_connection");
 private static boolean started;

 static synchronized void start(Context context) {
  if (started) return;
  final Context app = context.getApplicationContext();
  final AudioManager audio = app.getSystemService(AudioManager.class);
  if (audio == null) return;
  started = true;
  audio.registerAudioDeviceCallback(new AudioDeviceCallback() {
   @Override public void onAudioDevicesAdded(AudioDeviceInfo[] added) { report(app, audio); }
   @Override public void onAudioDevicesRemoved(AudioDeviceInfo[] removed) { report(app, audio); }
  }, new Handler(Looper.getMainLooper()));
 }

 private static void report(Context app, AudioManager audio) {
  GlassesWearHookRunner.onWearSignal(app, SOURCE, connected(audio) ? GlassesWearHook.WORN : GlassesWearHook.NOT_WORN);
 }

 /** Whether any Bluetooth audio output device (classic A2DP or headset profile, LE Audio, hearing aid) is connected. */
 static boolean connected(AudioManager audio) {
  for (AudioDeviceInfo d : audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS)) if (bluetooth(d.getType())) return true;
  return false;
 }

 static boolean bluetooth(int type) {
  switch (type) {
   case AudioDeviceInfo.TYPE_BLUETOOTH_A2DP:
   case AudioDeviceInfo.TYPE_BLUETOOTH_SCO:
   case AudioDeviceInfo.TYPE_HEARING_AID:
   case AudioDeviceInfo.TYPE_BLE_HEADSET:
   case AudioDeviceInfo.TYPE_BLE_SPEAKER:
   case AudioDeviceInfo.TYPE_BLE_BROADCAST:
    return true;
   default:
    return false;
  }
 }
}
