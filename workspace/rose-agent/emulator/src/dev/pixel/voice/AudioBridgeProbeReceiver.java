package dev.pixel.voice;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;

/** Synthetic SDK-volume injection for emulator bridge verification only. */
public final class AudioBridgeProbeReceiver extends BroadcastReceiver {
 @Override public void onReceive(Context context,Intent intent){
  if(!android.os.Build.FINGERPRINT.startsWith("Android/sdk_"))return;
  VoiceService voice=VoiceService.instance;
  if(voice==null){android.util.Log.e("AudioBridgeProbe","service_missing");return;}
  float input=intent.getFloatExtra("input",0),output=intent.getFloatExtra("output",0);
  if(!Float.isFinite(input)||!Float.isFinite(output))return;
  boolean muted=intent.getBooleanExtra("muted",false),connected=intent.getBooleanExtra("connected",true);
  String script="conversation={getInputVolume:()=>"+input+",getOutputVolume:()=>"+output+"};ready="+connected+";muted="+muted+";publishAudioLevels();Native.state(JSON.stringify({status:'Audio bridge probe',task:'',ready,muted,connected:ready,level:micLevel}));";
  voice.runOnUiThread(()->voice.web.evaluateJavascript(script,result->new Handler(Looper.getMainLooper()).postDelayed(()->android.util.Log.i("AudioBridgeProbe","input="+voice.trayInputLevel+" output="+voice.trayOutputLevel+" muted="+voice.muted+" live="+voice.live+" observed="+(voice.trayAudioObservedAt>0)),150)));
 }
}
