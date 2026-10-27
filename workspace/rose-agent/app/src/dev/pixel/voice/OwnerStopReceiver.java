package dev.pixel.voice;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import java.io.OutputStream;
import java.net.URL;
import javax.net.ssl.HttpsURLConnection;
import org.json.JSONObject;

/** Forwards the background controller's owner Stop to the backend at once. Best effort: the
 * backend also learns of the Stop from the controller state on the agent's next phone action. */
public final class OwnerStopReceiver extends BroadcastReceiver {
 static final String ACTION="dev.pixel.backgroundcontrol.action.OWNER_STOP", CONTROLLER="dev.pixel.backgroundcontrol";
 @Override public void onReceive(Context context,Intent intent){
  if(!ACTION.equals(intent.getAction())||!trusted(context))return;
  VoiceService service=VoiceService.instance;
  if(service==null||service.ssl==null)return;
  String taskId=intent.getStringExtra("taskId");
  PendingResult pending=goAsync();
  new Thread(()->{
   try{
    JSONObject body=new JSONObject().put("source","workspace_notification");
    if(taskId!=null&&taskId.matches("[A-Za-z0-9._:-]{1,128}"))body.put("taskId",taskId);
    HttpsURLConnection c=(HttpsURLConnection)new URL(service.endpoint+"/task/stop").openConnection();
    try{
     c.setSSLSocketFactory(service.ssl);c.setHostnameVerifier((h,s)->true);c.setConnectTimeout(6000);c.setReadTimeout(12000);
     c.setRequestMethod("POST");c.setRequestProperty("Content-Type","application/json");c.setDoOutput(true);
     byte[] bytes=body.toString().getBytes("UTF-8");c.setFixedLengthStreamingMode(bytes.length);
     try(OutputStream o=c.getOutputStream()){o.write(bytes);}
     android.util.Log.i("PixelOwnerStop","stop forwarded status="+c.getResponseCode());
    }finally{c.disconnect();}
   }catch(Exception e){android.util.Log.w("PixelOwnerStop","Stop forward failed: "+e.getClass().getSimpleName());}
   finally{pending.finish();}
  },"owner-stop").start();
 }
 // Android 14 shares the sender's identity; accept only the same-signer background controller.
 private boolean trusted(Context context){
  if(Build.VERSION.SDK_INT<34||!CONTROLLER.equals(getSentFromPackage()))return false;
  return context.getPackageManager().checkSignatures(context.getPackageName(),CONTROLLER)==PackageManager.SIGNATURE_MATCH;
 }
}
