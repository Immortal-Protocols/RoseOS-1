package dev.pixel.voice;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.KeyguardManager;
import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import java.io.*;
import java.net.URL;
import java.util.*;
import java.util.concurrent.*;
import javax.net.ssl.HttpsURLConnection;
import org.json.*;

/** Event-only X capture. Research and all message delivery happen on the Mac.
 * A filled icon alone is NOT a save: recycled timeline cells also expose it.
 * Require the observed added-bookmark notification and changed control together.
 */
final class BookmarkResearch {
 private static final Handler main=new Handler(Looper.getMainLooper());
 private static final ScheduledExecutorService io=Executors.newSingleThreadScheduledExecutor();
 private static final ExecutorService frames=Executors.newSingleThreadExecutor();
 private static Context context;
 private static boolean started;
 private static volatile boolean enabled;
 private static long candidateAt,noticeAt,handledNotice;
 private static JSONObject candidate;
 private static final Set<String> recent=new HashSet<>();
 static void start(PhoneAccessibility service){
  context=service.getApplicationContext();
  enabled=context.getSharedPreferences("bookmark-research",0).getBoolean("enabled",false);
  AccessibilityServiceInfo info=service.getServiceInfo();
  info.eventTypes|=AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED|AccessibilityEvent.TYPE_ANNOUNCEMENT;
  info.notificationTimeout=0;service.setServiceInfo(info);
  if(!started){started=true;io.scheduleWithFixedDelay(()->{refresh();flush();},0,30,TimeUnit.SECONDS);}
 }
 private static File dir(){File d=new File(context.getFilesDir(),"bookmark-outbox");d.mkdirs();return d;}
 private static JSONObject request(String route,JSONObject body)throws Exception{
  JSONObject config=new JSONObject(VoiceService.read(context.getAssets().open("connection.json")));
  String endpoint=context.getSharedPreferences("MainActivity",0).getString("endpoint",config.getString("endpoint"));
  HttpsURLConnection c=(HttpsURLConnection)new URL(endpoint+route).openConnection();
  c.setSSLSocketFactory(new DeviceIdentity(context).ssl(config.getString("pin")));c.setHostnameVerifier((h,s)->true);
  c.setConnectTimeout(6000);c.setReadTimeout(10000);
  try{if(body!=null){c.setRequestMethod("POST");c.setDoOutput(true);c.setRequestProperty("Content-Type","application/json");try(OutputStream out=c.getOutputStream()){out.write(body.toString().getBytes("UTF-8"));}}
   int status=c.getResponseCode();if(status<200||status>=300)throw new IOException("Bookmark service HTTP "+status);
   return new JSONObject(VoiceService.read(c.getInputStream()));
  }finally{c.disconnect();}
 }
 private static void refresh(){try{boolean next=request("/bookmarks/config",null).optBoolean("enabled");enabled=next;context.getSharedPreferences("bookmark-research",0).edit().putBoolean("enabled",next).apply();}catch(Exception ignored){}}
 private static void flush(){if(context==null)return;File[] files=dir().listFiles((d,n)->n.endsWith(".json"));if(files==null)return;
  Arrays.sort(files,Comparator.comparing(File::getName));for(File f:files){try{JSONObject payload=new JSONObject(VoiceService.read(new FileInputStream(f)));request("/bookmarks/capture",payload);f.delete();context.getSharedPreferences("bookmark-research",0).edit().putString("status","Bookmark queued for research on Mac").apply();}catch(Exception e){context.getSharedPreferences("bookmark-research",0).edit().putString("status","Bookmark saved; waiting for Mac connection").apply();break;}}
 }
 private static String text(CharSequence s){return s==null?"":s.toString().substring(0,Math.min(s.length(),4000));}
 private static void collect(AccessibilityNodeInfo n,JSONArray out,int depth,int[] budget)throws Exception{
  if(n==null)return;try{if(depth>30||budget[0]--<=0||n.isPassword())return;
   String t=text(n.getText()),d=text(n.getContentDescription());if(!t.isEmpty()||!d.isEmpty())out.put(new JSONObject().put("text",t).put("description",d));
   for(int i=0;i<n.getChildCount()&&budget[0]>0;i++)collect(n.getChild(i),out,depth+1,budget);
  }finally{n.recycle();}
 }
 static void observe(PhoneAccessibility service,AccessibilityEvent event){
  if(!enabled||!"com.twitter.android".contentEquals(event.getPackageName()==null?"":event.getPackageName()))return;
  if(((KeyguardManager)service.getSystemService(Context.KEYGUARD_SERVICE)).isKeyguardLocked())return;
  try{
   long now=SystemClock.elapsedRealtime();String description=text(event.getContentDescription());
   if("Delete Bookmark".equals(description)){
    AccessibilityNodeInfo n=event.getSource();if(n!=null)try{
     android.graphics.Rect bounds=new android.graphics.Rect();n.getBoundsInScreen(bounds);
     JSONArray best=new JSONArray();AccessibilityNodeInfo p=n.getParent();
     for(int i=0;i<5&&p!=null;i++){AccessibilityNodeInfo next=p.getParent();JSONArray rows=new JSONArray();collect(p,rows,0,new int[]{100});best=rows;
      // A timeline card normally contains a handle. Stop before traversing the
      // whole feed: neighboring posts must not become the research subject.
      if(rows.toString().matches("(?s).*@[A-Za-z0-9_]{1,15}.*")){if(next!=null)next.recycle();break;}p=next;if(i==4&&p!=null)p.recycle();}
     candidate=new JSONObject().put("postContext",best).put("triggerBounds",new JSONArray(new int[]{bounds.left,bounds.top,bounds.right,bounds.bottom}));candidateAt=now;
    }finally{n.recycle();}
   }
   JSONArray labels=new JSONArray();collect(event.getSource(),labels,0,new int[]{8});
   boolean added=(description+text(event.getText().toString())+labels).contains("Post added to your Bookmarks");
   if(added&&now-noticeAt>700){noticeAt=now;final long notice=now;
    main.postDelayed(()->capture(service,notice),120);
   }
  }catch(Exception ignored){}
 }
 private static void capture(PhoneAccessibility service,long notice){
  if(!enabled||handledNotice==notice||candidate==null||Math.abs(candidateAt-notice)>700)return;
  handledNotice=notice;
  try{
   final JSONObject payload=new JSONObject(candidate.toString());
   String signature=payload.getJSONArray("postContext").toString();if(recent.contains(signature))return;recent.add(signature);if(recent.size()>100)recent.clear();
   payload.put("eventId",UUID.randomUUID().toString()).put("capturedAt",System.currentTimeMillis()).put("packageName","com.twitter.android");
   frames.execute(()->{try{
    JSONObject shot=ScreenCapture.captureForBookmark(service);
    if("ok".equals(shot.optString("status"))&&"com.twitter.android".equals(shot.optString("packageName")))payload.put("imageBase64",shot.getString("imageBase64"));
    File f=new File(dir(),payload.getString("eventId")+".json");try(FileOutputStream out=new FileOutputStream(f)){out.write(payload.toString().getBytes("UTF-8"));out.getFD().sync();}
    io.execute(BookmarkResearch::flush);
   }catch(Exception e){context.getSharedPreferences("bookmark-research",0).edit().putString("status","Bookmark capture failed: "+e.getClass().getSimpleName()).apply();}});
  }catch(Exception ignored){}
 }
}
