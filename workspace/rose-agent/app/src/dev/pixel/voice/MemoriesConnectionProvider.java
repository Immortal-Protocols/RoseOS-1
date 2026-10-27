package dev.pixel.voice;
import android.content.*;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.*;
import java.io.*;
import java.net.URL;
import javax.net.ssl.*;
import org.json.*;

/** Only our signed Memories app may use this phone's pairing; no key is shared. */
public final class MemoriesConnectionProvider extends ContentProvider {
 public boolean onCreate(){return true;}
 private void verifyCaller(){
  int uid=Binder.getCallingUid();String[] packages=getContext().getPackageManager().getPackagesForUid(uid);
  if(packages==null||!java.util.Arrays.asList(packages).contains("dev.pixel.memories")||getContext().getPackageManager().checkSignatures(android.os.Process.myUid(),uid)!=PackageManager.SIGNATURE_MATCH)
   throw new SecurityException("Only the signed Pixel Memories app may use this connection");
 }
 static boolean allowed(String route,boolean post){
  if(post)return "/memories/review".equals(route)||"/memories/undo".equals(route);
  return "/memories/review".equals(route);
 }
 @Override public Bundle call(String method,String route,Bundle extras){
  verifyCaller();boolean post=extras!=null&&extras.containsKey("body");
  if(!"request".equals(method)||!allowed(route,post))throw new SecurityException("Unsupported Memories request");
  String body=post?extras.getString("body"):null;
  if(post&&(body==null||body.getBytes(java.nio.charset.StandardCharsets.UTF_8).length>4000))throw new IllegalArgumentException("Memory request too large");
  // Use Rose Agent's identity only after checking the original caller. The
  // caller cannot supply a server address or access non-Memories endpoints.
  long caller=Binder.clearCallingIdentity();Bundle result=new Bundle();HttpsURLConnection connection=null;
  try{
   if(!DeviceIdentity.paired(getContext()))throw new IOException("Pair Rose Agent with your Mac first.");
   JSONObject config=new JSONObject(VoiceService.read(getContext().getAssets().open("connection.json")));
   String endpoint=getContext().getSharedPreferences("MainActivity",0).getString("endpoint",config.getString("endpoint"));
   VoiceService service=VoiceService.instance;
   SSLSocketFactory ssl=service!=null&&service.ssl!=null?service.ssl:new DeviceIdentity(getContext()).ssl(config.getString("pin"));
   connection=(HttpsURLConnection)new URL(endpoint+route).openConnection();connection.setSSLSocketFactory(ssl);connection.setHostnameVerifier((h,s)->true);connection.setConnectTimeout(8000);connection.setReadTimeout(35000);
   if(post){connection.setRequestMethod("POST");connection.setRequestProperty("Content-Type","application/json");connection.setDoOutput(true);try(OutputStream out=connection.getOutputStream()){out.write(body.getBytes("UTF-8"));}}
   int status=connection.getResponseCode();ByteArrayOutputStream bytes=new ByteArrayOutputStream();
   try(InputStream in=status<400?connection.getInputStream():connection.getErrorStream()){byte[] buffer=new byte[8192];for(int n;(n=in.read(buffer))!=-1;){if(bytes.size()+n>500000)throw new IOException("Memories response too large");bytes.write(buffer,0,n);}}
   result.putInt("status",status);result.putString("body",bytes.toString("UTF-8"));
  }catch(Exception e){result.putInt("status",503);result.putString("body","{\"error\":"+JSONObject.quote(e.getMessage()==null?e.getClass().getSimpleName():e.getMessage())+"}");}
  finally{if(connection!=null)connection.disconnect();Binder.restoreCallingIdentity(caller);}
  return result;
 }
 public Cursor query(Uri u,String[] p,String s,String[] a,String o){throw new UnsupportedOperationException();}
 public String getType(Uri u){return null;}
 public Uri insert(Uri u,ContentValues v){throw new UnsupportedOperationException();}
 public int delete(Uri u,String s,String[] a){throw new UnsupportedOperationException();}
 public int update(Uri u,ContentValues v,String s,String[] a){throw new UnsupportedOperationException();}
}
