package dev.pixel.voice;

import android.content.Context;
import android.content.SharedPreferences;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.media.AudioDeviceInfo;
import android.os.Handler;
import android.os.SystemClock;
import android.util.Log;
import org.json.JSONObject;

/** Optional speech ducking. System focus mode or a reversible, bounded media-only reduction. */
final class VoiceDucking {
 private final AudioManager audio;
 private final Handler main;
 private final SharedPreferences settings;
 private AudioFocusRequest request;
 private boolean enabled, attempted, held, releasing;
 private String state="idle";
 private long probeUntil;
 private boolean lightMode, manual;
 private float reductionDb;
 private int baseline=-1,applied=-1,routeType;
 private final Runnable release=()->release();
 private final Runnable watchdog=()->release();

 VoiceDucking(Context context,Handler handler){
  audio=(AudioManager)context.getSystemService(Context.AUDIO_SERVICE);
  main=handler;settings=context.getSharedPreferences("voice-ducking-trial",0);
  enabled=settings.getBoolean("enabled",false);
  lightMode=settings.getBoolean("lightMode",false);reductionDb=settings.getFloat("reductionDb",4f);
  recoverInterruptedReduction();
 }
 void setEnabled(boolean value){enabled=value;settings.edit().putBoolean("enabled",value).apply();if(!value)release();}
 void setMode(String mode){release();lightMode="light".equals(mode);settings.edit().putBoolean("lightMode",lightMode).apply();}
 void setReduction(float db){if(!Float.isFinite(db)||db<1||db>12)throw new IllegalArgumentException("Reduction must be 1–12 dB");release();reductionDb=db;settings.edit().putFloat("reductionDb",db).apply();}
 private int currentRoute(){AudioDeviceInfo d=audio.getCommunicationDevice();return d==null?AudioDeviceInfo.TYPE_BUILTIN_SPEAKER:d.getType();}
 private void recoverInterruptedReduction(){
  if(manual)return;
  int route=currentRoute(),base=settings.getInt("restoreBase"+route,-1),last=settings.getInt("restoreApplied"+route,-1);
  if(base<0)return;
  if(audio.getStreamVolume(AudioManager.STREAM_MUSIC)==last)audio.setStreamVolume(AudioManager.STREAM_MUSIC,base,0);
  settings.edit().remove("restoreBase"+route).remove("restoreApplied"+route).apply();
 }
 private int reducedIndex(int base){
  try{
   float target=audio.getStreamVolumeDb(AudioManager.STREAM_MUSIC,base,routeType)-reductionDb;
   int best=base;float error=Float.MAX_VALUE;
   for(int i=audio.getStreamMinVolume(AudioManager.STREAM_MUSIC);i<=base;i++){
    float db=audio.getStreamVolumeDb(AudioManager.STREAM_MUSIC,i,routeType),distance=Math.abs(db-target);
    if(Float.isFinite(db)&&distance<error){error=distance;best=i;}
   }
   return best;
  }catch(RuntimeException e){return base;}
 }
 private void applyLight(int base){
  baseline=base;int next=reducedIndex(base),voice=audio.getStreamVolume(AudioManager.STREAM_VOICE_CALL);
  settings.edit().putInt("restoreBase"+routeType,base).putInt("restoreApplied"+routeType,next).apply();
  applied=next;audio.setStreamVolume(AudioManager.STREAM_MUSIC,next,0);
  if(audio.getStreamVolume(AudioManager.STREAM_VOICE_CALL)!=voice){
   audio.setStreamVolume(AudioManager.STREAM_MUSIC,base,0);audio.setStreamVolume(AudioManager.STREAM_VOICE_CALL,voice,0);
   settings.edit().remove("restoreBase"+routeType).remove("restoreApplied"+routeType).apply();
   manual=false;enabled=false;settings.edit().putBoolean("enabled",false).apply();state="shared_volume_disabled";
  }else state="light_active";
 }
 // Preserve the existing call-volume behavior while applying the music offset separately.
 boolean updateMediaBaseline(int value){if(!manual)return false;if(currentRoute()!=routeType){release();return false;}applyLight(value);return true;}
 void probe(int seconds){if(!enabled||seconds<1||seconds>10)return;probeUntil=SystemClock.elapsedRealtime()+seconds*1000L;sample(true);}
 void sample(boolean speaking){
  if(manual&&currentRoute()!=routeType){release();return;}
  if(!manual)recoverInterruptedReduction();
  if(!enabled){release();return;}
  speaking=speaking||SystemClock.elapsedRealtime()<probeUntil;
  if(speaking){
   main.removeCallbacks(release);releasing=false;
   main.removeCallbacks(watchdog);main.postDelayed(watchdog,Math.max(2000,probeUntil-SystemClock.elapsedRealtime()+450));
   if(attempted)return;
   attempted=true;
   if(lightMode){
    try{routeType=currentRoute();manual=true;applyLight(audio.getStreamVolume(AudioManager.STREAM_MUSIC));}
    catch(RuntimeException e){release();state="unavailable";}
    return;
   }
   request=new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
    .setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANCE_NAVIGATION_GUIDANCE)
     .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
    .setAcceptsDelayedFocusGain(false)
    .setOnAudioFocusChangeListener(change->{
     if(change<0){held=false;state="focus_lost";}
    },main).build();
   try{held=audio.requestAudioFocus(request)==AudioManager.AUDIOFOCUS_REQUEST_GRANTED;state=held?"granted":"denied";}
   catch(RuntimeException e){state="unavailable";held=false;}
   Log.i("PixelDucking","speech focus "+state);
  }else if(attempted&&!releasing){releasing=true;main.postDelayed(release,450);}
 }
 void release(){
  probeUntil=0;
  main.removeCallbacks(release);main.removeCallbacks(watchdog);releasing=false;
  if(request!=null){try{audio.abandonAudioFocusRequest(request);}catch(RuntimeException ignored){}}
  if(manual){
   // A user volume change wins; never restore over it or write an old level to a new route.
   if(currentRoute()==routeType){
    if(audio.getStreamVolume(AudioManager.STREAM_MUSIC)==applied)audio.setStreamVolume(AudioManager.STREAM_MUSIC,baseline,0);
    settings.edit().remove("restoreBase"+routeType).remove("restoreApplied"+routeType).apply();
   }
   manual=false;baseline=-1;applied=-1;
  }
  if(attempted)Log.i("PixelDucking","speech focus released");
  request=null;attempted=false;held=false;state="idle";
 }
 JSONObject status()throws Exception{return new JSONObject().put("status","ok").put("enabled",enabled).put("mode",lightMode?"light":"system").put("reductionDb",reductionDb).put("mediaBaseline",baseline).put("mediaApplied",applied).put("focusHeld",held).put("focusState",state);}
}
