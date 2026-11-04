package dev.pixel.voice;

import android.content.Context;
import android.content.SharedPreferences;

/** RoseMicInput's one stored state, in Rose's private preferences, and the requests that change it. */
final class RoseMicState {
 private RoseMicState() {}

 static final String PREFS = "rose-mic-input", KEY = "muted";

 static boolean muted(Context c) { return prefs(c).getBoolean(KEY, false); }

 /** Records the state; every listener (Home, the tray service) hears the change. Never touches capture by itself. */
 static void set(Context c, boolean muted) {
  SharedPreferences p = prefs(c);
  if (p.getBoolean(KEY, false) != muted) p.edit().putBoolean(KEY, muted).apply();
 }

 /**
  * A surface asked for a state: it is recorded, and a live call is told through the existing voice command. With no
  * voice service nothing is started; the next call starts from the recorded state.
  */
 static void request(Context c, boolean muted) {
  set(c, muted);
  VoiceService voice = VoiceService.instance;
  if (voice != null && voice.live) voice.command(RoseMicInput.command(muted), "mic_input");
 }

 static SharedPreferences prefs(Context c) { return c.getApplicationContext().getSharedPreferences(PREFS, 0); }
}
