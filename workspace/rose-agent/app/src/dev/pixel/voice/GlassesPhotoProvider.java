package dev.pixel.voice;

import android.content.*;
import android.database.Cursor;
import android.net.Uri;
import android.os.*;
import android.content.pm.PackageManager;
import android.util.Base64;
import java.io.*;
import java.net.URL;
import java.security.MessageDigest;
import javax.net.ssl.HttpsURLConnection;
import org.json.JSONObject;

/** Narrow, caller-verified image bridge; never exposes Rose Agent credentials. */
public final class GlassesPhotoProvider extends ContentProvider {
 private static final String CAMERA="dev.pixel.glassescamera";
 private static final String CERT="2a9f8b9ab942113cea8462b0ca41f6614c5649e016e5903309ce3ecabc3f1fca";
 public boolean onCreate(){return true;}
 private void verifyCaller(){
  int uid=Binder.getCallingUid();String[] packages=getContext().getPackageManager().getPackagesForUid(uid);
  if(packages==null||!java.util.Arrays.asList(packages).contains(CAMERA))throw new SecurityException("Only Glasses Camera may share images");
  try{
   android.content.pm.Signature[] signatures=getContext().getPackageManager().getPackageInfo(CAMERA,PackageManager.GET_SIGNING_CERTIFICATES).signingInfo.getApkContentsSigners();
   if(signatures.length!=1||!CERT.equals(VoiceService.hex(MessageDigest.getInstance("SHA-256").digest(signatures[0].toByteArray()))))throw new SecurityException("Camera signature mismatch");
  }catch(SecurityException e){throw e;}catch(Exception e){throw new SecurityException("Camera identity unavailable");}
 }
 private JSONObject request(VoiceService service,String path,JSONObject payload)throws Exception{
  HttpsURLConnection c=(HttpsURLConnection)new URL(service.endpoint+path).openConnection();
  try{
   c.setSSLSocketFactory(service.ssl);c.setHostnameVerifier((h,s)->true);c.setConnectTimeout(6000);c.setReadTimeout(12000);
   
   if(payload!=null){c.setRequestMethod("POST");c.setRequestProperty("Content-Type","application/json");c.setDoOutput(true);byte[] bytes=payload.toString().getBytes("UTF-8");c.setFixedLengthStreamingMode(bytes.length);try(OutputStream o=c.getOutputStream()){o.write(bytes);}}
   int code=c.getResponseCode();JSONObject result=new JSONObject(VoiceService.read(code<400?c.getInputStream():c.getErrorStream()));
   if(code>=400)throw new IOException(result.optString("error","Photo could not be shared"));return result;
  }finally{c.disconnect();}
 }
 @Override public Bundle call(String method,String id,Bundle extras){
  verifyCaller();Bundle result=new Bundle();
  try{
   if(!"share".equals(method)||id==null||!id.matches("[a-f0-9-]{36}")||extras==null)throw new IllegalArgumentException("Invalid capture");
   byte[] jpeg=extras.getByteArray("jpeg");long at=extras.getLong("capturedAt");
   if(jpeg==null||jpeg.length<100||jpeg.length>512000||Math.abs(System.currentTimeMillis()-at)>120000)throw new IllegalArgumentException("Invalid or expired photo");
   VoiceService service=VoiceService.instance;
   if(service==null||service.ssl==null||!service.live||!service.connected)throw new IOException("Connect Rose Agent before sharing a photo");
   JSONObject state=request(service,"/glasses/status",null);
   if(!state.optBoolean("ready"))throw new IOException("Connect Rose Agent before sharing a photo");
   JSONObject response=request(service,"/glasses/photo",new JSONObject().put("id",id).put("capturedAt",at).put("sessionId",state.getString("sessionId")).put("jpeg",Base64.encodeToString(jpeg,Base64.NO_WRAP)));
   result.putBoolean("ok",response.optBoolean("ok"));
  }catch(Exception e){result.putBoolean("ok",false);result.putString("error",e.getMessage());}
  return result;
 }
 public Cursor query(Uri u,String[] p,String s,String[] a,String o){throw new UnsupportedOperationException();}
 public String getType(Uri u){return null;}
 public Uri insert(Uri u,ContentValues v){throw new UnsupportedOperationException();}
 public int delete(Uri u,String s,String[] a){throw new UnsupportedOperationException();}
 public int update(Uri u,ContentValues v,String s,String[] a){throw new UnsupportedOperationException();}
}
