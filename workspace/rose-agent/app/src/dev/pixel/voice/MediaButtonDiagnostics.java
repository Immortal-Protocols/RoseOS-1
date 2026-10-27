package dev.pixel.voice;
import android.content.*;import android.content.pm.PackageManager;import android.bluetooth.*;import android.os.*;import java.io.*;import java.util.concurrent.*;import java.util.regex.*;import org.json.*;
final class MediaButtonDiagnostics {
 static JSONObject cached;static long sampled;
 static synchronized JSONObject read(VoiceService v)throws Exception{
  JSONObject r=new JSONObject().put("sessionActive",v.mediaSession!=null&&v.mediaSession.isActive()).put("lastButton",v.lastButton).put("compatibilityEnabled",v.getSharedPreferences("glasses-button-compatibility",0).getBoolean("enabled",false)).put("headsetSetupAttempted",v.recognitionAttempted).put("headsetSetupAccepted",v.recognitionAccepted).put("doubleTapMutes",v.doubleTapMutes());
  r.put("callMode",BluetoothCallState.read(v));
  if(v.glassesTransportTrace!=null)r.put("transport",v.glassesTransportTrace.read());
  if(v.mediaSession!=null&&v.mediaSession.getController().getPlaybackState()!=null)r.put("playbackState",v.mediaSession.getController().getPlaybackState().getState());
  try{BluetoothAdapter b=v.getSystemService(BluetoothManager.class).getAdapter();r.put("headsetProfile",b.getProfileConnectionState(BluetoothProfile.HEADSET)).put("mediaProfile",b.getProfileConnectionState(BluetoothProfile.A2DP));}catch(Exception ignored){}
  if(cached==null||SystemClock.elapsedRealtime()-sampled>10000){cached=dump(v);sampled=SystemClock.elapsedRealtime();}return r.put("routing",cached);
 }
 static JSONObject dump(VoiceService v)throws Exception{
  JSONObject r=new JSONObject();if(v.checkSelfPermission("android.permission.DUMP")!=PackageManager.PERMISSION_GRANTED)return r.put("status","permission_missing");
  java.lang.Process p=new ProcessBuilder("/system/bin/dumpsys","-t","1","media_session").redirectErrorStream(true).start();ExecutorService e=Executors.newSingleThreadExecutor();
  Future<String> text=e.submit(()->{try(InputStream in=p.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){byte[] buf=new byte[4096];int n;while(out.size()<131072&&(n=in.read(buf,0,Math.min(buf.length,131072-out.size())))!=-1)out.write(buf,0,n);return out.toString("UTF-8");}});
  try{String raw=text.get(1500,TimeUnit.MILLISECONDS);JSONArray packages=new JSONArray();java.util.Set<String> seen=new java.util.LinkedHashSet<>();
   for(String line:raw.split("\n")){
    Matcher m=Pattern.compile("([a-zA-Z][a-zA-Z0-9_]*(?:\\.[a-zA-Z][a-zA-Z0-9_]*)+)").matcher(line);
    if(line.contains("Media button session")||line.contains("MediaButtonSession")){if(m.find())r.put("buttonSessionPackage",m.group(1));else r.put("buttonSessionPackage","none");}
    if(line.trim().startsWith("package=")){if(m.find()&&seen.add(m.group(1)))packages.put(m.group(1));}
   }
   return r.put("status","ok").put("sessionPackages",packages);
  }catch(Exception ex){return r.put("status","unavailable");}finally{p.destroyForcibly();text.cancel(true);e.shutdownNow();}
 }
}
