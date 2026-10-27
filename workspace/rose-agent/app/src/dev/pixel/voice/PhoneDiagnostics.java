package dev.pixel.voice;

import android.app.KeyguardManager;
import android.content.*;
import android.content.pm.PackageManager;
import android.hardware.camera2.*;
import android.media.*;
import android.net.*;
import android.os.*;
import android.view.accessibility.AccessibilityNodeInfo;
import java.io.*;
import java.util.concurrent.*;
import java.util.regex.*;
import org.json.*;

/** On-demand, read-only observations. Never exposes a shell or raw service dumps. */
final class PhoneDiagnostics {
 static JSONObject read(Context c) throws Exception {
  JSONObject out=new JSONObject().put("status","ok").put("observedAt",System.currentTimeMillis());
  out.put("locked",((KeyguardManager)c.getSystemService(Context.KEYGUARD_SERVICE)).isKeyguardLocked());
  PhoneAccessibility accessibility=PhoneAccessibility.active;
  if(accessibility!=null)out.put("editorControl",accessibility.editorDiagnostics());
  if(accessibility!=null)out.put("gestureDiagnostics",accessibility.gestureDiagnostics());
  if(accessibility!=null){AccessibilityNodeInfo root=accessibility.getRootInActiveWindow();if(root!=null){out.put("foregroundPackage",String.valueOf(root.getPackageName()));root.recycle();}}
  AudioManager audio=(AudioManager)c.getSystemService(Context.AUDIO_SERVICE);
  JSONObject sound=new JSONObject().put("mode",audio.getMode()).put("systemMicrophoneMuted",audio.isMicrophoneMute());
  AudioDeviceInfo route=audio.getCommunicationDevice();
  if(route!=null)sound.put("communicationDevice",new JSONObject().put("name",route.getProductName().toString()).put("type",route.getType()));
  VoiceService voice=VoiceService.instance;
  if(voice!=null&&voice.glassesReconnect!=null)out.put("autoStartSuppressedAfterEnd",voice.glassesReconnect.suppressed());
  if(voice!=null&&voice.voiceLifecycle!=null)out.put("voiceLifecycle",voice.voiceLifecycle.snapshot());
  if(voice!=null)sound.put("assistantMicrophoneMuted",voice.muted).put("assistantConnected",voice.connected).put("replySpeaking",voice.replySpeaking).put("assistantStatus",voice.status).put("audioPrepared",voice.communicating&&!voice.recognitionOpening&&voice.requestedRoute>=0);
  if(voice!=null&&voice.phoneCommandStream!=null)out.put("commandDelivery",voice.phoneCommandStream.status());
  if(voice!=null&&voice.clipboardSync!=null)out.put("clipboardTransfer",voice.clipboardSync.state());
  if(voice!=null&&voice.identity!=null)out.put("devicePairing",new JSONObject().put("paired",DeviceIdentity.paired(c)).put("deviceId",voice.identity.id()).put("hardwareBacked",voice.identity.hardwareBacked()));
  if(voice!=null){sound.put("voiceBoostEnabled",voice.voiceBoostEnabled()).put("voiceBoostState",voice.outputBoostState);try{sound.put("bookState",new JSONObject(c.getSharedPreferences("book-state",0).getString("state","{}")));}catch(Exception ignored){}}
  if(voice!=null)try{sound.put("mediaButtons",MediaButtonDiagnostics.read(voice));}catch(Exception ignored){}
  out.put("audio",sound);
  out.put("appContext",PhoneAppContext.read(c));
  out.put("statusBarAudio",AgentTrayService.diagnostics());
  try{out.put("statusBarBinding",trayBinding(c));}catch(Exception e){out.put("statusBarBinding",new JSONObject().put("status","unavailable"));}
  try{out.put("glassesSetup",GlassesSetup.status(c));}catch(Exception e){out.put("glassesSetup",new JSONObject().put("status",e.getClass().getSimpleName()));}
  out.put("installStatus",c.getSharedPreferences("delivery",0).getString("status",""));
  ConnectivityManager network=(ConnectivityManager)c.getSystemService(Context.CONNECTIVITY_SERVICE);
  NetworkCapabilities caps=network.getNetworkCapabilities(network.getActiveNetwork());
  JSONObject connectivity=new JSONObject().put("connected",caps!=null);
  if(caps!=null)connectivity.put("validated",caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)).put("wifi",caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)).put("cellular",caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)).put("vpn",caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN));
  out.put("network",connectivity);
  Intent battery=c.registerReceiver(null,new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
  if(battery!=null){int scale=battery.getIntExtra(BatteryManager.EXTRA_SCALE,0);if(scale>0)out.put("batteryPercent",100*battery.getIntExtra(BatteryManager.EXTRA_LEVEL,0)/scale);}
  out.put("camera",camera(c));out.put("accessibility",AccessibilityHealth.read(c,true));return out;
 }
 static JSONObject trayBinding(Context c)throws Exception{
  if(c.checkSelfPermission("android.permission.DUMP")!=PackageManager.PERMISSION_GRANTED)return new JSONObject().put("status","permission_required");
  java.lang.Process process=new ProcessBuilder("/system/bin/dumpsys","-t","2","activity","services","dev.pixel.voice").redirectErrorStream(true).start();
  ExecutorService reader=Executors.newSingleThreadExecutor();
  Future<String> text=reader.submit(()->{try(InputStream in=process.getInputStream();ByteArrayOutputStream b=new ByteArrayOutputStream()){byte[] buf=new byte[4096];int n;while(b.size()<32768&&(n=in.read(buf,0,Math.min(buf.length,32768-b.size())))!=-1)b.write(buf,0,n);return b.toString("UTF-8");}});
  try{String dump=text.get(2500,TimeUnit.MILLISECONDS);return new JSONObject().put("status","ok").put("serviceListed",dump.contains("AgentTrayService")).put("systemUiBinding",dump.contains("com.android.systemui"));}
  finally{process.destroyForcibly();text.cancel(true);reader.shutdownNow();}
 }
 static JSONObject camera(Context c)throws Exception{
  JSONObject result=new JSONObject();
  if(c.checkSelfPermission("android.permission.DUMP")!=PackageManager.PERMISSION_GRANTED)return result.put("status","permission_required").put("message","One-time USB grant of Android diagnostics permission is required.");
  String dump;
  try{dump=cameraDump();}catch(Exception e){return result.put("status","unavailable").put("message",e.getClass().getSimpleName());}
  return parseCamera(dump,(CameraManager)c.getSystemService(Context.CAMERA_SERVICE));
 }
 static JSONObject parseCamera(String dump,CameraManager cameras)throws Exception{
  JSONObject result=new JSONObject();
  int start=dump.indexOf("Active Camera Clients:");int end=start<0?-1:dump.indexOf("Allowed user IDs:",start);
  if(start<0||end<0)return result.put("status","unavailable").put("message","Camera service did not expose its active-client section.");
  JSONArray active=new JSONArray();
  Matcher m=Pattern.compile("Camera ID: ([^,]+),[^\\n]*?Client Package Name: ([^,\\s)]+)").matcher(dump.substring(start,end));
  while(m.find()){
   String id=m.group(1).trim();JSONObject item=new JSONObject().put("cameraId",id).put("packageName",m.group(2));
   try{Integer facing=cameras.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING);item.put("facing",facing==null?"unknown":facing==CameraCharacteristics.LENS_FACING_FRONT?"front":facing==CameraCharacteristics.LENS_FACING_BACK?"back":"external");}catch(Exception e){item.put("facing","unknown");}
   active.put(item);
  }
  return result.put("status","ok").put("active",active);
 }
 static String cameraDump()throws Exception{
  java.lang.Process process=new ProcessBuilder("/system/bin/dumpsys","-t","2","media.camera").redirectErrorStream(true).start();
  ExecutorService reader=Executors.newSingleThreadExecutor();
  Future<String> text=reader.submit(()->{try(InputStream in=process.getInputStream();ByteArrayOutputStream b=new ByteArrayOutputStream()){byte[] buf=new byte[4096];int n;while(b.size()<65536&&(n=in.read(buf,0,Math.min(buf.length,65536-b.size())))!=-1)b.write(buf,0,n);return b.toString("UTF-8");}});
  try{return text.get(2500,TimeUnit.MILLISECONDS);}finally{process.destroyForcibly();text.cancel(true);reader.shutdownNow();}
 }
}
