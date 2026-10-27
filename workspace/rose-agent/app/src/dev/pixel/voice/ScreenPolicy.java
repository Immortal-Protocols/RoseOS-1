package dev.pixel.voice;

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.view.WindowManager;

/** The owner's choice between keeping Rose Agent's screen awake and Android's normal timeout. */
final class ScreenPolicy {
 static final String PREFS="screen-policy",KEEP_ON="keep-screen-on";
 static SharedPreferences prefs(Context c){
  SharedPreferences prefs=c.getSharedPreferences(PREFS,0);
  // Version 288 kept this choice in MainActivity's own preferences.
  if(!prefs.contains(KEEP_ON)){SharedPreferences legacy=c.getSharedPreferences("MainActivity",0);if(legacy.contains(KEEP_ON))prefs.edit().putBoolean(KEEP_ON,legacy.getBoolean(KEEP_ON,false)).apply();}
  return prefs;
 }
 static boolean keepScreenOn(Context c){return prefs(c).getBoolean(KEEP_ON,false);}
 static void setKeepScreenOn(Context c,boolean enabled){prefs(c).edit().putBoolean(KEEP_ON,enabled).apply();ScreenTile.refresh(c);}
 // Window scoped: the flag only holds while Rose Agent is resumed and never dismisses the keyguard.
 static void apply(Activity activity,boolean resumed){
  if(resumed&&keepScreenOn(activity))activity.getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
  else activity.getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
 }
}
