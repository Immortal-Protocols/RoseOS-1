package dev.pixel.voice;

import android.content.Context;
import android.content.pm.PackageManager;
import java.io.*;
import java.util.concurrent.*;
import java.util.regex.*;
import org.json.JSONObject;

/** Read only explicit boolean call-mode flags; never return the raw Bluetooth dump. */
final class BluetoothCallState {
 static JSONObject read(Context context)throws Exception {
  JSONObject result=new JSONObject();
  if(context.checkSelfPermission("android.permission.DUMP")!=PackageManager.PERMISSION_GRANTED)return result.put("status","permission_missing");
  Process process=new ProcessBuilder("/system/bin/dumpsys","-t","1","bluetooth_manager").redirectErrorStream(true).start();
  ExecutorService reader=Executors.newSingleThreadExecutor();
  Future<String> output=reader.submit(()->{try(InputStream in=process.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){
   byte[] buf=new byte[8192];int n;while(out.size()<1048576&&(n=in.read(buf,0,Math.min(buf.length,1048576-out.size())))!=-1)out.write(buf,0,n);return out.toString("UTF-8");
  }});
  try {
   String raw=output.get(1800,TimeUnit.MILLISECONDS);
   Matcher match=Pattern.compile("(?m)^\\s*(mVirtualCallStarted|mVoiceRecognitionStarted|mForceScoAudio|mAudioRouteAllowed|Telecom\\.isInCall\\(\\)|Telecom\\.isRinging\\(\\)|AudioManager\\.isBluetoothScoOn\\(\\))\\s*[:=]\\s*(true|false)\\s*$").matcher(raw);
   while(match.find())result.put(match.group(1),Boolean.parseBoolean(match.group(2)));
   return result.put("status",result.length()==0?"flags_not_exposed":"ok");
  }catch(Exception e){return result.put("status","unavailable");}
  finally{process.destroyForcibly();output.cancel(true);reader.shutdownNow();}
 }
}
