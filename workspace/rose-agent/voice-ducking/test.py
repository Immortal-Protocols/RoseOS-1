#!/usr/bin/env python3
"""Exercise the real focus-lease controller with a deterministic clock/audio manager."""
from pathlib import Path
import tempfile, subprocess
root=Path(__file__).resolve().parents[2]
java=root/'tools/jdk/Contents/Home/bin'
files={
'android/content/SharedPreferences.java':'''package android.content; import java.util.*; public class SharedPreferences {Map<String,Object> m=new HashMap<>();public boolean getBoolean(String k,boolean d){return (Boolean)m.getOrDefault(k,d);}public int getInt(String k,int d){return (Integer)m.getOrDefault(k,d);}public float getFloat(String k,float d){return (Float)m.getOrDefault(k,d);}public SharedPreferences edit(){return this;}public SharedPreferences putBoolean(String k,boolean v){m.put(k,v);return this;}public SharedPreferences putInt(String k,int v){m.put(k,v);return this;}public SharedPreferences putFloat(String k,float v){m.put(k,v);return this;}public SharedPreferences remove(String k){m.remove(k);return this;}public void apply(){}}''',
'android/content/Context.java':'''package android.content; public class Context {public static final String AUDIO_SERVICE="audio"; public android.media.AudioManager audio=new android.media.AudioManager(); public SharedPreferences prefs=new SharedPreferences(); public Object getSystemService(String key){return audio;} public SharedPreferences getSharedPreferences(String key,int mode){return prefs;}}''',
'android/media/AudioAttributes.java':'''package android.media; public class AudioAttributes {public static final int USAGE_ASSISTANCE_NAVIGATION_GUIDANCE=12,CONTENT_TYPE_SPEECH=1;public static class Builder {public Builder setUsage(int n){return this;}public Builder setContentType(int n){return this;}public AudioAttributes build(){return new AudioAttributes();}}}''',
'android/media/AudioFocusRequest.java':'''package android.media;public class AudioFocusRequest {public int gain;public AudioManager.OnAudioFocusChangeListener listener;public static class Builder {AudioFocusRequest r=new AudioFocusRequest();public Builder(int gain){r.gain=gain;}public Builder setAudioAttributes(AudioAttributes a){return this;}public Builder setAcceptsDelayedFocusGain(boolean b){if(b)throw new AssertionError("delayed grant");return this;}public Builder setOnAudioFocusChangeListener(AudioManager.OnAudioFocusChangeListener l,android.os.Handler h){r.listener=l;return this;}public AudioFocusRequest build(){return r;}}}''',
'android/media/AudioDeviceInfo.java':'''package android.media;public class AudioDeviceInfo {public static final int TYPE_BUILTIN_SPEAKER=2;public int type=7;public int getType(){return type;}}''',
'android/media/AudioManager.java':'''package android.media;public class AudioManager {public static final int AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK=3,AUDIOFOCUS_REQUEST_GRANTED=1;public interface OnAudioFocusChangeListener {void onAudioFocusChange(int c);}public static final int STREAM_MUSIC=3,STREAM_VOICE_CALL=0;public int media=20,voice=10;public boolean alias=false;public AudioDeviceInfo device=new AudioDeviceInfo();public AudioDeviceInfo getCommunicationDevice(){return device;}public int getStreamVolume(int stream){return stream==3?media:voice;}public int getStreamMinVolume(int stream){return 0;}public float getStreamVolumeDb(int stream,int index,int type){return -50+index*2;}public void setStreamVolume(int stream,int value,int flags){if(stream==3){media=value;if(alias)voice=value;}else voice=value;}public int requests,abandons,result=1;public AudioFocusRequest active; public int requestAudioFocus(AudioFocusRequest r){if(r.gain!=3)throw new AssertionError("wrong focus");requests++;active=r;return result;}public int abandonAudioFocusRequest(AudioFocusRequest r){if(r!=active)throw new AssertionError("wrong release");abandons++;active=null;return 1;}}''',
'android/os/SystemClock.java':'''package android.os;public class SystemClock {public static long now;public static long elapsedRealtime(){return now;}}''',
'android/os/Handler.java':'''package android.os;import java.util.*;public class Handler {Map<Runnable,Long> jobs=new HashMap<>(); public void removeCallbacks(Runnable r){jobs.remove(r);}public void postDelayed(Runnable r,long delay){jobs.put(r,SystemClock.now+delay);}public void advance(long n){SystemClock.now+=n;while(true){Runnable next=null;for(Map.Entry<Runnable,Long> e:jobs.entrySet())if(e.getValue()<=SystemClock.now){next=e.getKey();break;}if(next==null)return;jobs.remove(next);next.run();}}}''',
'android/util/Log.java':'''package android.util;public class Log {public static int i(String t,String m){return 0;}}''',
'org/json/JSONObject.java':'''package org.json;import java.util.*;public class JSONObject {public Map<String,Object> m=new HashMap<>();public JSONObject put(String k,Object v){m.put(k,v);return this;}}''',
'dev/pixel/voice/DuckingTest.java':'''package dev.pixel.voice;import android.content.*;import android.os.*;public class DuckingTest {
 static void check(boolean x,String s){if(!x)throw new AssertionError(s);}
 public static void main(String[] args)throws Exception{
 Context c=new Context();Handler h=new Handler();VoiceDucking d=new VoiceDucking(c,h);
 d.sample(true);check(c.audio.requests==0,"off by default");d.setEnabled(true);d.sample(true);d.sample(true);check(c.audio.requests==1,"one request per burst");
 d.sample(false);h.advance(200);d.sample(true);h.advance(300);check(c.audio.abandons==0,"short gaps do not pump music");
 d.sample(false);h.advance(450);check(c.audio.abandons==1,"release after speech");
 d.sample(true);d.setEnabled(false);check(c.audio.abandons==2,"off immediately releases");
 d.setEnabled(true);c.audio.result=0;d.sample(true);d.sample(true);check(c.audio.requests==3,"denial is not retried each frame");d.sample(false);h.advance(450);
 c.audio.result=1;d.sample(true);h.advance(2000);check(c.audio.active==null,"stalled audio meter releases focus");
 d.probe(1);h.advance(1000);d.sample(false);h.advance(450);check(c.audio.active==null,"probe ends");
 d.sample(true);d.release();check(c.audio.active==null,"disconnect releases");
 d.setMode("light");int previousRequests=c.audio.requests;d.sample(true);
 check(c.audio.media==18&&c.audio.voice==10,"light mode lowers music four dB only");check(c.audio.requests==previousRequests,"light mode does not request system duck");
 d.sample(false);h.advance(450);check(c.audio.media==20,"restores original music");
 d.sample(true);c.audio.media=16;d.release();check(c.audio.media==16,"user media adjustment wins");
 c.audio.media=20;d.sample(true);d.updateMediaBaseline(22);check(c.audio.media==20,"hardware volume updates baseline with offset");d.release();check(c.audio.media==22,"release restores latest baseline");
 c.audio.media=20;d.sample(true);VoiceDucking recovered=new VoiceDucking(c,h);check(c.audio.media==20,"new instance restores interrupted reduction");d.release();
 c.audio.media=20;d.sample(true);c.audio.device.type=2;c.audio.media=12;d.sample(true);check(c.audio.media==12,"route change does not apply old volume to new route");c.audio.device.type=7;c.audio.media=18;d.sample(false);check(c.audio.media==20,"old route recovers when returned");
 d.sample(true);d.setEnabled(false);check(c.audio.media==20,"off restores manual volume immediately");
 c.audio.alias=true;d.setEnabled(true);d.sample(true);check(c.audio.voice==10&&c.audio.media==20,"shared stream refuses and restores");check(d.status().m.get("enabled").equals(false),"unsafe alias disables trial");
 System.out.println("PASS: default off, bounded focus, gap hold, disable, denied request, watchdog, probe expiry, disconnect");
 }}'''
}
with tempfile.TemporaryDirectory() as t:
 dest=Path(t)
 for name,src in files.items():
  p=dest/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(src)
 p=dest/'dev/pixel/voice/VoiceDucking.java';p.write_text((root/'rose-agent/app/src/dev/pixel/voice/VoiceDucking.java').read_text())
 subprocess.run([str(java/'javac'),'-d',t,*map(str,dest.rglob('*.java'))],check=True)
 subprocess.run([str(java/'java'),'-cp',t,'dev.pixel.voice.DuckingTest'],check=True)
