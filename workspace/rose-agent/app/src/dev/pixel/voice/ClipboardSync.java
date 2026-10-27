package dev.pixel.voice;

import android.content.ClipData;
import android.content.ClipDescription;
import android.content.ClipboardManager;
import android.os.PersistableBundle;
import java.io.*;
import java.net.URL;
import javax.net.ssl.HttpsURLConnection;
import org.json.JSONObject;

/** Separate clipboard transport: never enters voice, task, or memory context. */
final class ClipboardSync {
 private final VoiceService service;
 private String endpoint;
 private javax.net.ssl.SSLSocketFactory ssl;
 private volatile boolean stopped,connected;
 private volatile HttpsURLConnection connection;
 private Thread worker;
 private volatile String status="starting",lastId="";
 private static final String CLIP_ID="dev.pixel.clipboard.transferId";
 ClipboardSync(VoiceService service){this.service=service;}
 void start(){
  try{JSONObject cfg=new JSONObject(VoiceService.read(service.getAssets().open("clipboard-connection.json")));endpoint=cfg.getString("endpoint");ssl=service.identity.systemTrustedSsl();}
  catch(Exception e){status="not_configured";return;}
  // Android owns clipboard lifetime. Remove the obsolete timer metadata without clearing
  // anything the user may have copied since the previous version ran.
  service.getSharedPreferences("clipboard-transfer",0).edit().remove("clearAt").apply();
  worker=new Thread(this::run,"PixelClipboard");worker.setDaemon(true);worker.start();
 }
 void stop(){stopped=true;connected=false;if(connection!=null)connection.disconnect();if(worker!=null)worker.interrupt();}
 JSONObject state(){JSONObject j=new JSONObject();try{j.put("connected",connected).put("status",status).put("lastTransferId",lastId).put("defaultIme",android.provider.Settings.Secure.getString(service.getContentResolver(),android.provider.Settings.Secure.DEFAULT_INPUT_METHOD)).put("clipboardFormat","plain_text").put("clipboardLifetime","android_managed");}catch(Exception ignored){}return j;}
 private HttpsURLConnection open(String path)throws Exception{
  HttpsURLConnection c=(HttpsURLConnection)new URL(endpoint+path).openConnection();
  c.setSSLSocketFactory(ssl);c.setConnectTimeout(6000);c.setReadTimeout(20000);return c;
 }
 private void receipt(String id,String result){service.requests.submit(()->{
  HttpsURLConnection c=null;try{c=open("/receipt");c.setRequestMethod("POST");c.setDoOutput(true);c.setRequestProperty("Content-Type","application/json");
   try(OutputStream out=c.getOutputStream()){out.write(new JSONObject().put("id",id).put("status",result).toString().getBytes("UTF-8"));}c.getResponseCode();
  }catch(Exception ignored){}finally{if(c!=null)c.disconnect();}
 });}
 private void accept(String raw)throws Exception{
  JSONObject event=new JSONObject(raw);if(!"clipboard".equals(event.optString("type")))return;
  final String id=event.getString("id"),text=event.getString("text");final long expires=event.getLong("expires");
  // expires is the delivery deadline, not a lifetime for text already on the clipboard.
  if(!id.matches("[a-f0-9-]{36}")||text.getBytes("UTF-8").length>65536||System.currentTimeMillis()>expires)return;
  service.runOnUiThread(()->{
   if(stopped||System.currentTimeMillis()>expires)return;
   try{
    String previous=service.getSharedPreferences("clipboard-transfer",0).getString("lastId","");
    if(!id.equals(previous)){
     // URI-only clips fail in native fields that read Item.getText() directly (including
     // Compose). Use standard text; retain sensitive preview marking and no relay logs.
     ClipData clip=ClipData.newPlainText("Transferred text",text);PersistableBundle extras=new PersistableBundle();extras.putBoolean(ClipDescription.EXTRA_IS_SENSITIVE,true);extras.putString(CLIP_ID,id);clip.getDescription().setExtras(extras);
     ((ClipboardManager)service.getSystemService(android.content.Context.CLIPBOARD_SERVICE)).setPrimaryClip(clip);
     service.getSharedPreferences("clipboard-transfer",0).edit().putString("lastId",id).apply();
    }
    lastId=id;status="copied";receipt(id,"copied");
   }catch(Exception e){status="copy_failed";receipt(id,"failed");}
  });
 }
 private void run(){int backoff=500;
  while(!stopped){
   try{
    HttpsURLConnection c=open("/receive");connection=c;
    if(c.getResponseCode()!=200)throw new IOException("Unavailable");connected=true;status="connected";backoff=500;
    try(Reader reader=new BufferedReader(new InputStreamReader(c.getInputStream(),"UTF-8"))){
     StringBuilder line=new StringBuilder();int ch;
     while(!stopped&&(ch=reader.read())!=-1){
      if(ch=='\n'){String value=line.toString();line.setLength(0);if(value.startsWith("data: "))accept(value.substring(6));}
      else{line.append((char)ch);if(line.length()>140000)throw new IOException("Oversized event");}
     }
    }
   }catch(Exception ignored){}finally{connected=false;if(connection!=null)connection.disconnect();connection=null;}
   if(!stopped)try{Thread.sleep(backoff);}catch(InterruptedException ignored){}backoff=Math.min(20000,backoff*2);
  }
 }
}
