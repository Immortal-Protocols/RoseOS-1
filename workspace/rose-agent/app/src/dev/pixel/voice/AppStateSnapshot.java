package dev.pixel.voice;
import android.content.*;
import android.content.pm.*;
import android.net.Uri;
import android.os.Bundle;
import org.json.JSONObject;
import java.util.concurrent.*;

/** Small, read-only state from an app signed with our own app identity. */
final class AppStateSnapshot {
 private static final ExecutorService pool=Executors.newFixedThreadPool(2);
 static JSONObject read(Context context,String pkg){
  try{
   if(context.getPackageManager().checkSignatures(context.getPackageName(),pkg)!=PackageManager.SIGNATURE_MATCH)return null;
   ProviderInfo p=context.getPackageManager().resolveContentProvider(pkg+".agent",0);
   if(p==null||!pkg.equals(p.packageName))return null;
   Future<JSONObject> job=pool.submit(()->{
    Bundle args=new Bundle();args.putString("params","{}");
    Bundle result=context.getContentResolver().call(Uri.parse("content://"+pkg+".agent"),"get_state","app-state",args);
    String raw=result==null?null:result.getString("result");
    if(raw==null||raw.length()>12000)return null;
    JSONObject state=new JSONObject(raw);String status=state.optString("status");
    if(!status.equals("ok")&&!status.equals("verified"))return null;
    return new JSONObject().put("packageName",pkg).put("observedAt",System.currentTimeMillis()).put("data",state);
   });
   try{return job.get(800,TimeUnit.MILLISECONDS);}catch(Exception e){job.cancel(true);return null;}
  }catch(Exception ignored){return null;}
 }
}
