package dev.pixel.voice;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.ActivityManager;
import android.app.ApplicationExitInfo;
import android.content.ComponentName;
import android.content.Context;
import android.provider.Settings;
import android.view.accessibility.AccessibilityManager;
import java.util.List;
import java.util.concurrent.*;
import java.io.*;
import java.util.regex.*;
import org.json.*;

/** Read-only service health. Never modifies accessibility settings or captures screen content. */
final class AccessibilityHealth {
 static long lastReconnectAt=-30000;
 static JSONObject reconnect(Context c) throws Exception {
  if(PhoneAccessibility.active!=null)return new JSONObject().put("status","connected");
  if(!read(c,false).optBoolean("configuredEnabled"))return new JSONObject().put("status","accessibility_disabled").put("message","Phone controls are disabled in Android Accessibility settings.");
  long now=android.os.SystemClock.elapsedRealtime();
  if(now-lastReconnectAt<30000)return new JSONObject().put("status","accessibility_disconnected").put("message","Permission is enabled; Android has not reconnected the service after the restart request. No action was performed.");
  lastReconnectAt=now;
  try{c.startService(new android.content.Intent(c,PhoneAccessibility.class));return new JSONObject().put("status","reconnect_requested").put("retryAfterMs",2500).put("message","Permission remains enabled. Requested one service restart; verify control_status before retrying the action.");}
  catch(RuntimeException denied){return new JSONObject().put("status","accessibility_disconnected").put("message","Permission is enabled, but Android declined the service restart. No action was performed.");}
 }
 static JSONObject read(Context c, boolean details) throws Exception {
  ComponentName target=new ComponentName(c,PhoneAccessibility.class);
  String enabled=Settings.Secure.getString(c.getContentResolver(),Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
  boolean configured=false;
  if(enabled!=null)for(String value:enabled.split(":"))if(target.equals(ComponentName.unflattenFromString(value)))configured=true;
  AccessibilityManager manager=(AccessibilityManager)c.getSystemService(Context.ACCESSIBILITY_SERVICE);
  boolean reported=false;
  for(AccessibilityServiceInfo info:manager.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK))if(target.equals(ComponentName.unflattenFromString(info.getId())))reported=true;
  boolean bound=PhoneAccessibility.active!=null;
  JSONObject out=new JSONObject().put("configuredEnabled",configured).put("managerReportsEnabled",reported).put("serviceConnected",bound).put("state",bound?"connected":configured?"enabled_but_disconnected":"disabled_in_settings");
  if(details&&android.os.Build.VERSION.SDK_INT>=30){
   JSONArray exits=new JSONArray();
   for(ApplicationExitInfo e:((ActivityManager)c.getSystemService(Context.ACTIVITY_SERVICE)).getHistoricalProcessExitReasons(c.getPackageName(),0,32))exits.put(new JSONObject().put("timestamp",e.getTimestamp()).put("process",e.getProcessName()).put("reason",e.getReason()).put("status",e.getStatus()).put("importance",e.getImportance()));
   out.put("recentProcessExits",exits);
  }
  if(details&&c.checkSelfPermission("android.permission.DUMP")==android.content.pm.PackageManager.PERMISSION_GRANTED){
   java.lang.Process p=new ProcessBuilder("/system/bin/dumpsys","-t","2","accessibility").redirectErrorStream(true).start();
   ExecutorService io=Executors.newSingleThreadExecutor();
   Future<String> result=io.submit(()->{try(InputStream in=p.getInputStream();ByteArrayOutputStream b=new ByteArrayOutputStream()){byte[] buffer=new byte[4096];int n;while(b.size()<262144&&(n=in.read(buffer,0,Math.min(buffer.length,262144-b.size())))!=-1)b.write(buffer,0,n);return b.toString("UTF-8");}});
   try{
    String dump=result.get(2500,TimeUnit.MILLISECONDS);
    for(String field:new String[]{"bound","binding","crashed","enabled"}){
     Matcher m=Pattern.compile("(?im)^.*?(?:m)?"+field+"\\s*services\\s*[:=]([^\\n]*)").matcher(dump);
     boolean found=false,present=false;
     while(m.find()){found=true;String value=m.group(1);present|=value.contains("dev.pixel.voice")&&value.contains("PhoneAccessibility");}
     if(found)out.put(field+"ContainsOurs",present);
    }
   }catch(Exception e){out.put("systemServiceSummary","unavailable");}finally{p.destroyForcibly();result.cancel(true);io.shutdownNow();}
  }
  if(details)out.put("crashFrames",crashFrames());
  return out;
 }
 static JSONArray crashFrames(){
  JSONArray lines=new JSONArray();java.lang.Process p=null;ExecutorService io=Executors.newSingleThreadExecutor();
  try{
   p=new ProcessBuilder("/system/bin/logcat","-d","-b","crash","--uid="+android.os.Process.myUid(),"-v","brief","-t","300").redirectErrorStream(true).start();
   final java.lang.Process proc=p;
   Future<String> read=io.submit(()->{try(InputStream in=proc.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){byte[] buf=new byte[4096];int n;while(out.size()<131072&&(n=in.read(buf,0,Math.min(buf.length,131072-out.size())))!=-1)out.write(buf,0,n);return out.toString("UTF-8");}});
   String logs=read.get(2000,TimeUnit.MILLISECONDS);
   for(String line:logs.split("\n")){
    Matcher exception=Pattern.compile("((?:[a-zA-Z_$][\\w$]*\\.)+[\\w$]*(?:Exception|Error))").matcher(line);
    Matcher frame=Pattern.compile("at ((?:dev\\.pixel\\.voice|android\\.|java\\.|org\\.chromium)[^\\s]+\\([^)]*\\))").matcher(line);
    if(exception.find())lines.put(exception.group(1));else if(frame.find())lines.put(frame.group(1));
    if(lines.length()>=100)break;
   }
  }catch(Exception ignored){}finally{if(p!=null)p.destroyForcibly();io.shutdownNow();}
  return lines;
 }

}
