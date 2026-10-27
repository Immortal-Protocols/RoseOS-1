package dev.pixel.voice;

import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.ProviderInfo;
import android.net.Uri;
import android.os.Bundle;
import org.json.JSONObject;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.UUID;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;

/** Keeps the install capability on-device; models receive state, never the token. */
final class PlayInstallBridge {
 private final Context context;
 private final SharedPreferences prefs;
 PlayInstallBridge(Context context){this.context=context;this.prefs=context.getSharedPreferences("play-install-bridge",0);}
 private JSONObject call(String method,JSONObject p)throws Exception{
  PackageManager pm=context.getPackageManager();
  ProviderInfo provider=pm.resolveContentProvider("com.android.packageinstaller.agent",0);
  if(provider==null||!"com.android.packageinstaller".equals(provider.packageName))throw new IllegalStateException("Install approval update is not installed");
  PackageInfo info=pm.getPackageInfo(provider.packageName,PackageManager.GET_SIGNING_CERTIFICATES);
  android.content.pm.Signature[] signatures=info.signingInfo.getApkContentsSigners();
  if(signatures.length!=1)throw new SecurityException("Installer identity mismatch");
  StringBuilder hash=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(signatures[0].toByteArray()))hash.append(String.format(Locale.ROOT,"%02x",b&255));
  String expected;
  try(InputStream input=context.getAssets().open("installer-identity.json");ByteArrayOutputStream data=new ByteArrayOutputStream()){
   byte[] buffer=new byte[512];int count;while((count=input.read(buffer))!=-1)data.write(buffer,0,count);
   expected=new JSONObject(data.toString("UTF-8")).getString("sha256").toLowerCase(Locale.ROOT);
  }catch(java.io.FileNotFoundException missing){throw new IllegalStateException("Optional fast-install helper identity is not configured; use the normal installer screen");}
  if(!expected.matches("[0-9a-f]{64}")||!expected.equals(hash.toString()))throw new SecurityException("Installer identity mismatch");
  Bundle args=new Bundle();args.putString("params",p.toString());
  Bundle reply=context.getContentResolver().call(Uri.parse("content://com.android.packageinstaller.agent"),method,null,args);
  if(reply==null||reply.getString("result")==null)throw new IllegalStateException("Installer did not return a result");
  return new JSONObject(reply.getString("result"));
 }
 JSONObject execute(String method,JSONObject p)throws Exception{
  if(method.equals("install_approval_configure"))return call("configure",new JSONObject().put("enabled",p.getBoolean("enabled")));
  if(method.equals("install_approval_prepare")){
   String label=p.getString("label").trim();
   String requestId=UUID.randomUUID().toString();
   JSONObject result=call("arm",new JSONObject().put("label",label).put("packageName",p.optString("packageName")).put("requestId",requestId));
   if(result.has("token")){
    if(!prefs.edit().putString("request",new JSONObject().put("label",label).put("requestId",requestId).put("token",result.getString("token")).toString()).commit())throw new IllegalStateException("Could not save install request");
    result.remove("token");
   }
   return result;
  }
  if(!method.equals("install_approval_finish")&&!method.equals("install_approval_cancel")&&!method.equals("install_approval_status"))throw new IllegalArgumentException("Unknown install operation");
  JSONObject saved=new JSONObject(prefs.getString("request","{}"));
  if(!saved.optString("label").equals(p.getString("label")))throw new IllegalStateException("No matching prepared install request");
  String operation=method.equals("install_approval_finish")?"approve":method.equals("install_approval_cancel")?"revoke":"status";
  return call(operation,saved);
 }
}
