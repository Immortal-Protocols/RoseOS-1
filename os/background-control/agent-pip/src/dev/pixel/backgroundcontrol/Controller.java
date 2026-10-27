package dev.pixel.backgroundcontrol;

import android.app.*;
import android.content.*;
import android.content.pm.*;
import android.graphics.*;
import android.media.Image;
import android.os.*;
import android.util.*;
import android.view.accessibility.*;
import java.io.*;
import java.lang.reflect.*;
import java.util.*;
import java.util.concurrent.*;
import org.json.*;

public final class Controller {
 static final String EXT="com.android.extensions.computercontrol.";
 static final Handler main=new Handler(Looper.getMainLooper());
 static final Executor worker=Executors.newSingleThreadExecutor();
 static Object session; static long generation,started; static String id,target,taskId,status="idle",error="",snapshotId;
 static final StopLedger stops=new StopLedger(); static int width,height,operations; static boolean blocked; static IntentSender consent;
 static final Map<Integer,AccessibilityNodeInfo> nodes=new HashMap<>();
 static final Map<Integer,Integer> parents=new HashMap<>(); static final Map<Integer,LabelTargets.Node> labels=new LinkedHashMap<>(); static boolean truncated;
 static long snapshotTime; static final long DEADLINE=600000;
 static Throwable root(Throwable e){while(e instanceof InvocationTargetException&&e.getCause()!=null)e=e.getCause();return e;}
 static synchronized JSONObject state(Context c)throws Exception{return new JSONObject().put("status",status).put("sessionId",id==null?JSONObject.NULL:id).put("targetPackage",target==null?JSONObject.NULL:target).put("error",error).put("reason",reason(c)).put("deviceLocked",locked(c)).put("blocked",blocked).put("width",width).put("height",height).put("operationCount",operations).put("elapsedMs",started==0?0:SystemClock.elapsedRealtime()-started).put("foregroundFallback",false).put("taskId",nullable(taskId)).put("stopReason",stops.reason()).put("stoppedSessionId",nullable(stops.sessionId())).put("stoppedTaskId",nullable(stops.taskId()));}
 static Object nullable(String s){return s==null?JSONObject.NULL:s;}
 static boolean locked(Context c){KeyguardManager k=c.getSystemService(KeyguardManager.class);return k!=null&&k.isDeviceLocked();}
 // Android refuses locked creation with ERROR_DEVICE_LOCKED (2) unless the OS build and owner
 // allow it; a locked session blocks on installer, permission, settings and secure screens.
 static String reason(Context c){if(error.contains("target_in_foreground"))return "target_in_foreground";if(error.contains("owner_unlock_required")||blocked&&locked(c))return "owner_unlock_required";if("rejected".equals(status)&&error.equals("Android session error 2"))return "device_locked";return "";}
 static void clearNodes(){for(AccessibilityNodeInfo n:nodes.values())n.recycle();nodes.clear();parents.clear();labels.clear();truncated=false;snapshotId=null;}
 // Only the owner's notification/viewer Stop is "user"; it also refuses later starts of that task.
 static synchronized void stop(Context c,String why){stops.stopped(why,id,taskId);if(StopLedger.USER.equals(why))announceOwnerStop(c,id,taskId);++generation;Object old=session;session=null;consent=null;clearNodes();blocked=false;status="stopped";if(old!=null)try{old.getClass().getMethod("close").invoke(old);}catch(Throwable e){session=old;error=root(e).toString();status="cleanup_failed";} }
 // Tells Rose Agent at once, so it can end the task even if the agent makes no further phone call.
 // Shared sender identity lets the receiver check this package and its signing certificate.
 static void announceOwnerStop(Context c,String session,String task){try{Intent i=new Intent("dev.pixel.backgroundcontrol.action.OWNER_STOP").setPackage("dev.pixel.voice").putExtra("sessionId",session).putExtra("taskId",task).putExtra("stoppedAt",System.currentTimeMillis());BroadcastOptions o=BroadcastOptions.makeBasic();o.setShareIdentityEnabled(true);c.sendBroadcast(i,null,o.toBundle());}catch(RuntimeException e){Log.w("BackgroundControl","Owner stop notice not delivered",e);}}
 static void requestConsent(Activity a)throws Exception{IntentSender pending; synchronized(Controller.class){pending=consent;}if(pending!=null)a.startIntentSenderForResult(pending,3,null,0,0,0);}
 // The refused task is named on its own; the running workspace, if any, is only under "current".
 static JSONObject refusal(String task,JSONObject current)throws Exception{return refusal(stops.refusal(task),current);}
 static JSONObject refusal(Map<String,Object> fields,JSONObject current)throws Exception{JSONObject r=new JSONObject();for(Map.Entry<String,Object> f:fields.entrySet())r.put(f.getKey(),f.getValue());return r.put("current",current);}
 static String taskParam(JSONObject p)throws Exception{return StopLedger.taskId(p.has("taskId")&&!p.isNull("taskId")?p.getString("taskId"):null);}
 // An agent stop tagged for another task (for example a late turn-end cleanup) leaves this workspace alone.
 static synchronized JSONObject agentStop(Context c,JSONObject p)throws Exception{String task=taskParam(p);Map<String,Object> stale=stops.taskMismatch(task,taskId);if(stale!=null)return refusal(stale,state(c));stop(c,StopLedger.AGENT);return state(c);}
 static synchronized JSONObject start(Context context,JSONObject p)throws Exception{
  Context c=context.getApplicationContext();String pkg=p.getString("packageName");String task=taskParam(p);
  if(stops.refuses(task))return refusal(task,state(c));
  if(pkg.equals(c.getPackageName())||c.getPackageManager().getLaunchIntentForPackage(pkg)==null)throw new IllegalArgumentException("Target must be an installed launchable app");
  if(session!=null&&pkg.equals(target)&&"ready".equals(status)){if(task!=null)taskId=task;return state(c);}
  stop(c,StopLedger.AGENT);if(session!=null)throw new IllegalStateException("Previous session cleanup is incomplete");stops.started();final long token=++generation;target=pkg;taskId=task;id=UUID.randomUUID().toString();started=SystemClock.elapsedRealtime();operations=0;width=height=0;error="";blocked=false;status="starting";
  main.post(()->{
   try{
    synchronized(Controller.class){if(token!=generation)return;}
    NotificationManager nm=c.getSystemService(NotificationManager.class);nm.createNotificationChannel(new NotificationChannel("background-live","Background app control",NotificationManager.IMPORTANCE_LOW));
    PendingIntent stop=PendingIntent.getBroadcast(c,0,new Intent(c,StopReceiver.class),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
    PendingIntent view=PendingIntent.getActivity(c,1,new Intent(c,AgentPipActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
    Notification.Builder nb=new Notification.Builder(c,"background-live").setSmallIcon(android.R.drawable.ic_menu_view).setContentTitle("Rose Agent is using "+c.getPackageManager().getApplicationLabel(c.getPackageManager().getApplicationInfo(pkg,0))).setContentText("Tap to watch in a floating window").setContentIntent(view).setOngoing(true).addAction(new Notification.Action.Builder(null,"Stop",stop).build());
    // The lock screen shows that a session is running, not which app it controls.
    nb.setVisibility(Notification.VISIBILITY_PRIVATE).setPublicVersion(new Notification.Builder(c,"background-live").setSmallIcon(android.R.drawable.ic_menu_view).setContentTitle("Rose Agent is using an app").setContentText("Unlock to see or stop it").build());
    Notification.Builder.class.getMethod("setRequestPromotedOngoing",boolean.class).invoke(nb,true);
    Class<?> type=Class.forName(EXT+"ComputerControlExtensions");int version=((Number)type.getMethod("getVersion").invoke(null)).intValue();Object ext=type.getMethod("getInstance",Context.class,int.class).invoke(null,c,version);
    Class<?> b=Class.forName(EXT+"ComputerControlSession$Params$Builder");Object builder=b.getConstructor(Context.class).newInstance(c);b.getMethod("setName",String.class).invoke(builder,"pixel-voice-background");b.getMethod("setTargetPackageNames",List.class).invoke(builder,Arrays.asList(pkg));
    Class<?> n=Class.forName(EXT+"ComputerControlSession$NotificationParams$Builder");Object nBuilder=n.getConstructor(Notification.class,int.class).newInstance(nb.build(),102);b.getMethod("setNotificationParams",Class.forName(EXT+"ComputerControlSession$NotificationParams")).invoke(builder,n.getMethod("build").invoke(nBuilder));
    Class<?> cb=Class.forName(EXT+"ComputerControlSession$Callback");Object callback=Proxy.newProxyInstance(cb.getClassLoader(),new Class<?>[]{cb},(proxy,method,args)->{
     if(method.getName().equals("hashCode"))return System.identityHashCode(proxy);if(method.getName().equals("equals"))return proxy==args[0];if(method.getName().equals("toString"))return "PixelVoiceBackgroundCallback";
     synchronized(Controller.class){
      if(token!=generation){if(method.getName().equals("onSessionCreated"))args[0].getClass().getMethod("close").invoke(args[0]);return null;}
      switch(method.getName()){
       case "onSessionPending":consent=(IntentSender)args[0];status="consent_required";Activity a=MainActivity.visible.get();if(a!=null)requestConsent(a);break;
       case "onSessionCreated":session=args[0];consent=null;status="launching";attachLifecycle(token);worker.execute(()->launch(c,token));break;
       case "onSessionCreationFailed":status="rejected";error="Android session error "+args[0];break;
       case "onSessionClosed":session=null;clearNodes();status="closed";error="Android close reason "+(args==null||args.length==0?"unspecified":args[0]);break;
      }
     }return null;
    });
    type.getMethod("requestSession",Class.forName(EXT+"ComputerControlSession$Params"),Executor.class,cb).invoke(ext,b.getMethod("build").invoke(builder),(Executor)(r->main.post(r)),callback);
   }catch(Throwable e){synchronized(Controller.class){if(token==generation){status="failed";error=root(e).toString();}}}
  });
  main.postDelayed(()->{synchronized(Controller.class){if(token==generation)stop(c,StopLedger.DEADLINE);}},DEADLINE);
  return state(c);
 }
 static void attachLifecycle(long token)throws Exception{
  Class<?> cb=Class.forName(EXT+"ComputerControlSession$LifecycleCallback");
  Object listener=Proxy.newProxyInstance(cb.getClassLoader(),new Class<?>[]{cb},(proxy,m,args)->{
   if(m.getName().equals("hashCode"))return System.identityHashCode(proxy);if(m.getName().equals("equals"))return proxy==args[0];if(m.getName().equals("toString"))return "BackgroundLifecycle";
   synchronized(Controller.class){if(token!=generation)return null;switch(m.getName()){
    case "onBlocked":blocked=true;clearNodes();status="blocked";error="Android block reason "+args[0];break;
    case "onActive":blocked=false;if(session!=null&&!status.equals("launching"))status="ready";break;
    case "onClosed":session=null;clearNodes();status="closed";error="Android close reason "+(args==null||args.length==0?"unspecified":args[0]);break;
   }}return null;
  });session.getClass().getMethod("setLifecycleCallback",Executor.class,cb).invoke(session,(Executor)(r->main.post(r)),listener);
 }
 static void launch(Context c,long token){try{
  synchronized(Controller.class){if(token!=generation||session==null)return;session.getClass().getMethod("launchApplication",String.class).invoke(session,target);}
  long until=SystemClock.elapsedRealtime()+15000;
  while(SystemClock.elapsedRealtime()<until){synchronized(Controller.class){if(token!=generation||session==null)return;Image frame=(Image)session.getClass().getMethod("getScreenshot").invoke(session);if(frame!=null){width=frame.getWidth();height=frame.getHeight();frame.close();status="ready";return;}}Thread.sleep(150);}
  throw new IllegalStateException("Target did not produce an allowed background frame");
 }catch(Throwable e){synchronized(Controller.class){if(token==generation){++generation;error=root(e).toString();Object old=session;session=null;clearNodes();status="failed";if(old!=null)try{old.getClass().getMethod("close").invoke(old);}catch(Throwable ignored){}}}}}
 static void require(JSONObject p)throws Exception{if(session==null||!"ready".equals(status)||blocked)throw new IllegalStateException("Background session is not ready: "+status);if(!id.equals(p.getString("sessionId")))throw new IllegalArgumentException("Stale session ID");if(SystemClock.elapsedRealtime()-started>DEADLINE)throw new IllegalStateException("Session expired");}
 static void fresh(JSONObject p)throws Exception{if(snapshotId==null||SystemClock.elapsedRealtime()-snapshotTime>30000||!snapshotId.equals(p.optString("snapshotId",p.optString("screenshotId"))))throw new IllegalArgumentException("Fresh background snapshot required");}
 static synchronized JSONObject command(Context c,JSONObject p)throws Exception{
  Map<String,Object> stale=stops.taskMismatch(taskParam(p),taskId);if(stale!=null)return refusal(stale,state(c));
  require(p);String action=p.getString("action");
  if(action.equals("screenshot"))return capture();if(action.equals("inspect_ui"))return inspect();if(action.equals("app_context"))return base().put("packageName",target);if(action.equals("setup_ui_control"))return base().put("uiControlEnabled",true);if(action.equals("home")){stop(c,StopLedger.AGENT);return state(c);}
  fresh(p);if(operations>=200)throw new IllegalStateException("Session operation limit reached");
  if(action.equals("screen_gesture")){JSONArray points=p.getJSONArray("points");if(points.length()!=2&&points.length()!=4)throw new IllegalArgumentException("Expected point or endpoints");for(int i=0;i<points.length();i++)checkPoint(points.getInt(i),i%2==0?width:height);if(points.length()==2)session.getClass().getMethod("tap",int.class,int.class).invoke(session,points.getInt(0),points.getInt(1));else swipe(points);}
  else if(action.equals("tap")||action.equals("long_press")||action.equals("tap_label")||action.equals("touch_label")||action.equals("touch_point")){
   AccessibilityNodeInfo n=null;
   if(action.endsWith("label")){Set<Integer> targets=LabelTargets.resolve(labels,p.getString("label"));if(p.has("nodeId"))targets=LabelTargets.disambiguate(targets,p.getInt("nodeId"));if(targets.size()!=1)throw new IllegalArgumentException(targets.isEmpty()?"Label target not found":"Ambiguous label");n=nodes.get(targets.iterator().next());validate(n);}

   else if(p.has("nodeId"))n=node(p);
   if(n!=null){if(n.isPassword())throw new SecurityException("Password field requires user");if(action.equals("tap")||action.equals("tap_label")){if(!n.performAction(AccessibilityNodeInfo.ACTION_CLICK))throw new IllegalStateException("Background click rejected");}else if(action.equals("long_press")){if(!n.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK))throw new IllegalStateException("Background long click rejected");}else{Rect r=new Rect();n.getBoundsInScreen(r);int x=p.optInt("x",r.centerX()),y=p.optInt("y",r.centerY());if(!r.contains(x,y))throw new IllegalArgumentException("Point outside observed node");session.getClass().getMethod("tap",int.class,int.class).invoke(session,x,y);}}
   else {int x=p.getInt("x"),y=p.getInt("y");checkPoint(x,width);checkPoint(y,height);session.getClass().getMethod(action.equals("long_press")?"longPress":"tap",int.class,int.class).invoke(session,x,y);}
  }
  else if(action.equals("ime_enter")){AccessibilityNodeInfo n=node(p);if(n.isPassword()||!n.isEditable()||!n.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.getId()))throw new IllegalStateException("Background editor submit rejected");}
  else if(action.equals("swipe")||action.equals("drag")){AccessibilityNodeInfo n=node(p);Rect r=new Rect();n.getBoundsInScreen(r);JSONArray points=p.optJSONArray("points");if(points==null){JSONArray bounds=p.getJSONArray("bounds");Rect requested=new Rect(bounds.getInt(0),bounds.getInt(1),bounds.getInt(2),bounds.getInt(3));if(!r.contains(requested))throw new IllegalArgumentException("Swipe outside observed node");int y=requested.centerY();boolean left=p.getString("direction").equals("left");points=new JSONArray(new int[]{left?requested.right-1:requested.left,y,left?requested.left:requested.right-1,y});}for(int i=0;i<4;i+=2)if(!r.contains(points.getInt(i),points.getInt(i+1)))throw new IllegalArgumentException("Gesture outside observed node");swipe(points);}
  else if(action.equals("set_text")){AccessibilityNodeInfo n=node(p);if(!n.isEditable()||!n.isEnabled()||!n.isVisibleToUser())throw new IllegalArgumentException("Node is not an editable visible control");if(n.isPassword())throw new SecurityException("Password fields require user interaction");String text=p.getString("text");if(text.length()>4000)throw new IllegalArgumentException("Text exceeds limit");if(!n.isFocused()){n.performAction(AccessibilityNodeInfo.ACTION_FOCUS);long focusUntil=SystemClock.elapsedRealtime()+500;while(n.refresh()&&!n.isFocused()&&SystemClock.elapsedRealtime()<focusUntil)SystemClock.sleep(25);if(!n.isFocused())throw new IllegalStateException("Could not focus background field");}session.getClass().getMethod("insertText",String.class,boolean.class,boolean.class).invoke(session,text,true,p.optBoolean("commit",false));}
  else if(action.equals("scroll")){AccessibilityNodeInfo n=node(p);String d=p.optString("direction","forward");if(!d.equals("forward")&&!d.equals("backward"))throw new IllegalArgumentException("direction must be forward or backward");if(!n.isScrollable()||!n.performAction(d.equals("forward")?AccessibilityNodeInfo.ACTION_SCROLL_FORWARD:AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD))throw new IllegalStateException("Background scroll rejected");}
  else if(action.equals("back"))session.getClass().getMethod("performAction",int.class).invoke(session,Class.forName(EXT+"ComputerControlSession").getField("ACTION_GO_BACK").getInt(null));
  else if(action.equals("open_app")){if(!target.equals(p.optString("packageName",target)))throw new IllegalArgumentException("Start a new target-scoped session to change app");session.getClass().getMethod("launchApplication",String.class).invoke(session,target);}
  else throw new IllegalArgumentException("Unsupported background action");
  operations++;clearNodes();return state(c).put("status","action_accepted").put("action",action);
 }
 static AccessibilityNodeInfo node(JSONObject p)throws Exception{AccessibilityNodeInfo n=nodes.get(p.getInt("nodeId"));if(n==null||!target.contentEquals(n.getPackageName()==null?"":n.getPackageName()))throw new IllegalArgumentException("Unknown target node; inspect_ui first");validate(n);return n;}
 static void validate(AccessibilityNodeInfo n){Rect old=new Rect(),now=new Rect();n.getBoundsInScreen(old);String text=String.valueOf(n.getText());if(!n.refresh())throw new IllegalStateException("Node is stale");n.getBoundsInScreen(now);if(!old.equals(now)||!text.equals(String.valueOf(n.getText()))||!n.isEnabled()||!n.isVisibleToUser())throw new IllegalStateException("Observed target changed; inspect again");}
 static void checkPoint(int x,int max){if(x<0||x>=max)throw new IllegalArgumentException("Point outside background display");}
 static void swipe(JSONArray p)throws Exception{for(int i=0;i<4;i++)checkPoint(p.getInt(i),i%2==0?width:height);session.getClass().getMethod("swipe",int.class,int.class,int.class,int.class).invoke(session,p.getInt(0),p.getInt(1),p.getInt(2),p.getInt(3));}
 static JSONObject base()throws Exception{return new JSONObject().put("status","ok").put("sessionId",id).put("targetPackage",target).put("snapshotId",snapshotId).put("width",width).put("height",height).put("scope","background_target").put("packageName",target).put("validForMs",30000).put("displayWidth",width).put("displayHeight",height);}
 static JSONObject capture()throws Exception{
  Image image=(Image)session.getClass().getMethod("getScreenshot").invoke(session);if(image==null)throw new IllegalStateException("No allowed target frame available");
  Bitmap all=null,crop=null,encoded=null;
  try{
   Image.Plane p=image.getPlanes()[0];width=image.getWidth();height=image.getHeight();
   all=Bitmap.createBitmap(p.getRowStride()/p.getPixelStride(),height,Bitmap.Config.ARGB_8888);all.copyPixelsFromBuffer(p.getBuffer());crop=Bitmap.createBitmap(all,0,0,width,height);encoded=crop;
   // JSON base64 is sent as a UTF-16 Bundle string. Keep the whole reply well below Binder's shared 1 MiB buffer.
   ByteArrayOutputStream out=new ByteArrayOutputStream();int quality=80;
   for(;;){
    out.reset();encoded.compress(Bitmap.CompressFormat.JPEG,quality,out);
    if(out.size()<=280000)break;
    if(quality>40){quality-=10;continue;}
    int nextWidth=encoded.getWidth()*3/4,nextHeight=encoded.getHeight()*3/4;
    if(nextWidth<64||nextHeight<64)throw new IllegalStateException("Background image exceeds safe Binder transport limit");
    Bitmap smaller=Bitmap.createScaledBitmap(encoded,nextWidth,nextHeight,true);if(encoded!=crop)encoded.recycle();encoded=smaller;quality=80;
   }
   clearNodes();snapshotId=UUID.randomUUID().toString();snapshotTime=SystemClock.elapsedRealtime();
   return base().put("width",encoded.getWidth()).put("height",encoded.getHeight()).put("capturedAt",System.currentTimeMillis()).put("screenshotId",snapshotId).put("mimeType","image/jpeg").put("imageBase64",android.util.Base64.encodeToString(out.toByteArray(),android.util.Base64.NO_WRAP));
  }finally{if(encoded!=null&&encoded!=crop)encoded.recycle();if(crop!=null&&crop!=all)crop.recycle();if(all!=null)all.recycle();image.close();}
 }
 static JSONObject inspect()throws Exception{
  clearNodes();snapshotId=UUID.randomUUID().toString();snapshotTime=SystemClock.elapsedRealtime();JSONArray out=new JSONArray();List<AccessibilityWindowInfo> windows=(List<AccessibilityWindowInfo>)session.getClass().getMethod("getAccessibilityWindows").invoke(session);
  for(AccessibilityWindowInfo w:windows){AccessibilityNodeInfo n=w.getRoot();if(n!=null)walk(n,out,0,-1);w.recycle();}return base().put("nodes",out).put("truncated",truncated);
 }
 static void walk(AccessibilityNodeInfo n,JSONArray out,int depth,int parent)throws Exception{
  if(depth>35||nodes.size()>=300){truncated=true;n.recycle();return;}if(target.contentEquals(n.getPackageName()==null?"":n.getPackageName())&&n.isVisibleToUser()){
   int key=nodes.size();nodes.put(key,n);int ancestor=parent;parents.put(key,ancestor);labels.put(key,new LabelTargets.Node(key,ancestor,n.isEnabled(),n.isClickable(),n.isPassword(),String.valueOf(n.getText()==null?"":n.getText()),String.valueOf(n.getContentDescription()==null?"":n.getContentDescription())));parent=key;Rect r=new Rect();n.getBoundsInScreen(r);JSONObject row=new JSONObject().put("id",key).put("parentId",ancestor).put("nodeId",key).put("class",String.valueOf(n.getClassName())).put("viewId",(n.getViewIdResourceName()==null?"":n.getViewIdResourceName())).put("visible",true).put("longClickable",n.isLongClickable()).put("focused",n.isFocused()).put("imeEnter",n.getActionList().contains(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER)).put("checked",n.isChecked()).put("selected",n.isSelected()).put("text",n.isPassword()?"":String.valueOf(n.getText()==null?"":n.getText())).put("description",n.isPassword()?"":String.valueOf(n.getContentDescription()==null?"":n.getContentDescription())).put("className",String.valueOf(n.getClassName())).put("enabled",n.isEnabled()).put("clickable",n.isClickable()).put("editable",n.isEditable()).put("password",n.isPassword()).put("scrollable",n.isScrollable()).put("bounds",new JSONArray(Arrays.asList(r.left,r.top,r.right,r.bottom)));int click=clickParent(key);if(click>=0)row.put("clickTarget",click);out.put(row);
  }else{for(int i=0;i<n.getChildCount();i++){AccessibilityNodeInfo child=n.getChild(i);if(child!=null)walk(child,out,depth+1,parent);}n.recycle();return;}
  for(int i=0;i<n.getChildCount();i++){AccessibilityNodeInfo child=n.getChild(i);if(child!=null)walk(child,out,depth+1,parent);}
 }
 static int clickParent(int key){return LabelTargets.target(labels,key);}
 static JSONObject apps(Context c)throws Exception{JSONArray apps=new JSONArray();Intent i=new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);Set<String> seen=new HashSet<>();for(ResolveInfo r:c.getPackageManager().queryIntentActivities(i,0)){String pkg=r.activityInfo.packageName;if(!pkg.equals(c.getPackageName())&&seen.add(pkg))apps.put(new JSONObject().put("packageName",pkg).put("label",r.loadLabel(c.getPackageManager())));}return new JSONObject().put("status","ok").put("apps",apps);}
}
