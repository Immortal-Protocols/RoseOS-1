package dev.pixel.voice;

import java.io.*;
import java.net.URL;
import javax.net.ssl.HttpsURLConnection;
import org.json.JSONObject;

/** Authenticated, certificate-pinned notifications; no separate action executor. */
final class PhoneCommandStream {
 private final VoiceService service;
 private volatile HttpsURLConnection connection;
 private volatile boolean stopped;
 private volatile boolean connected;
 private volatile long lastMessageAt;
 private volatile Thread worker;
 PhoneCommandStream(VoiceService service){this.service=service;}
 synchronized void start(){if(worker!=null)return;worker=new Thread(this::run,"PhoneCommandPush");worker.setDaemon(true);worker.start();}
 void stop(){stopped=true;connected=false;HttpsURLConnection c=connection;if(c!=null)c.disconnect();Thread t=worker;if(t!=null)t.interrupt();}
 JSONObject status(){JSONObject o=new JSONObject();try{o.put("pushConnected",connected).put("lastMessageAt",lastMessageAt).put("pollingFallback",true);}catch(Exception ignored){}return o;}
 private void emit(String event){if(stopped)return;service.runOnUiThread(()->{if(!stopped&&service.web!=null)service.web.evaluateJavascript("window.receivePhonePush&&window.receivePhonePush("+JSONObject.quote(event)+")",null);});}
 private void run(){int backoff=500;
  while(!stopped){
   try{
    HttpsURLConnection c=(HttpsURLConnection)new URL(service.endpoint+"/phone/push").openConnection();connection=c;
    c.setSSLSocketFactory(service.ssl);c.setHostnameVerifier((h,s)->true);c.setConnectTimeout(6000);c.setReadTimeout(30000);
    c.setRequestProperty("Accept","text/event-stream");
    if(c.getResponseCode()!=200)throw new IOException("Push not available");
    connected=true;backoff=500;emit("{\"type\":\"transport\",\"connected\":true}");
    try(BufferedReader reader=new BufferedReader(new InputStreamReader(c.getInputStream(),"UTF-8"))){
     StringBuilder frame=new StringBuilder(),line;
     int ch;StringBuilder current=new StringBuilder();
     // Bound input while reading, before constructing a potentially huge line.
     while(!stopped&&(ch=reader.read())!=-1){
      if(ch=='\n'){
       line=current;String text=line.toString();current.setLength(0);
       if(text.isEmpty()||text.equals("\r")){if(frame.length()>0){String payload=frame.toString();new JSONObject(payload);lastMessageAt=System.currentTimeMillis();emit(payload);frame.setLength(0);}}
       else if(text.startsWith("data: ")){if(frame.length()>0)frame.append('\n');frame.append(text.substring(6).trim());}
       if(frame.length()>262144)throw new IOException("Oversized push frame");
      }else{current.append((char)ch);if(current.length()>262144)throw new IOException("Oversized push line");}
     }
    }
   }catch(Exception ignored){}finally{
    connected=false;emit("{\"type\":\"transport\",\"connected\":false}");HttpsURLConnection c=connection;connection=null;if(c!=null)c.disconnect();
   }
   if(!stopped)try{Thread.sleep(backoff);}catch(InterruptedException ignored){}backoff=Math.min(20000,backoff*2);
  }
 }
}
