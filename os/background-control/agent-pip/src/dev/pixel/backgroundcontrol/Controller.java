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
 static StopLedger stops=new StopLedger(); static int width,height,operations; static boolean blocked; static IntentSender consent;
 // The replacement race (Replacement): sessionToken is the start token of the held session; pending is a start's request
 // waiting for the earliest point Android can admit it. Closed sessions stay unconfirmed until Android admits a later request.
 static Replacement replacement=new Replacement(); static long sessionToken,pendingToken; static Runnable pending;
 static Map<Integer,AccessibilityNodeInfo> nodes=new HashMap<>();
 static Map<Integer,Integer> parents=new HashMap<>(); static Map<Integer,LabelTargets.Node> labels=new LinkedHashMap<>(); static boolean truncated;
 static long snapshotTime; static final long DEADLINE=600000;
 // Whether this OS build lets this controller drive any app (AnyAppControl); read at each start. observed: the app in
 // front of the workspace at the last inspection, which may differ from the target (an installer, a sign-in screen).
 static boolean anyApp; static String observed;
 /** Two stable slots (MultiSession; OS Core MULTI-SESSION-DESIGN). Each Slot keeps one session's complete state: session,
  * start token, ids, status, its own StopLedger record, Replacement (per-name admission), pending request, snapshot,
  * node map, freshness, preview rate and size. The static fields above hold the state of the slot being worked on
  * (`active`): enter() saves the active slot and loads another, leave() restores, always under this class's lock. Between
  * calls slot 0 is loaded, so every single-session path and every reader of these fields (viewer, MainActivity, overview
  * selection) is exactly slot 0 as before. Callbacks, lifecycle events, launch, the deadline and the dispatched request
  * run inside their own slot (in(), executor()), so a late or stale event of one session never touches the other. */
 static final class Slot{
  final int index;Object session;long generation,started,sessionToken,pendingToken,snapshotTime,lastPreview;
  String id,target,taskId,status="idle",error="",snapshotId,observed;StopLedger stops=new StopLedger();int width,height,operations;
  boolean blocked,truncated,anyApp;IntentSender consent;Replacement replacement=new Replacement();Runnable pending;
  Map<Integer,AccessibilityNodeInfo> nodes=new HashMap<>();Map<Integer,Integer> parents=new HashMap<>();Map<Integer,LabelTargets.Node> labels=new LinkedHashMap<>();
  Slot(int index){this.index=index;}
 }
 static final Slot[] slots={new Slot(0),new Slot(1)}; static Slot active=slots[0];
 static void save(Slot s){s.session=session;s.generation=generation;s.started=started;s.sessionToken=sessionToken;s.pendingToken=pendingToken;s.snapshotTime=snapshotTime;s.lastPreview=lastPreview;
  s.id=id;s.target=target;s.taskId=taskId;s.status=status;s.error=error;s.snapshotId=snapshotId;s.observed=observed;s.stops=stops;s.width=width;s.height=height;s.operations=operations;
  s.blocked=blocked;s.truncated=truncated;s.anyApp=anyApp;s.consent=consent;s.replacement=replacement;s.pending=pending;s.nodes=nodes;s.parents=parents;s.labels=labels;}
 static void load(Slot s){session=s.session;generation=s.generation;started=s.started;sessionToken=s.sessionToken;pendingToken=s.pendingToken;snapshotTime=s.snapshotTime;lastPreview=s.lastPreview;
  id=s.id;target=s.target;taskId=s.taskId;status=s.status;error=s.error;snapshotId=s.snapshotId;observed=s.observed;stops=s.stops;width=s.width;height=s.height;operations=s.operations;
  blocked=s.blocked;truncated=s.truncated;anyApp=s.anyApp;consent=s.consent;replacement=s.replacement;pending=s.pending;nodes=s.nodes;parents=s.parents;labels=s.labels;}
 static Slot enter(Slot s){Slot was=active;if(s!=was){save(was);load(s);active=s;}return was;}
 static void leave(Slot was){enter(was);}
 /** r, run later under the lock inside slot s. */
 static Runnable in(Slot s,Runnable r){return ()->{synchronized(Controller.class){Slot was=enter(s);try{r.run();}finally{leave(was);}}};}
 /** The executor for one slot's Android callbacks (session and lifecycle): each runs inside that slot. */
 static Executor executor(Slot s){return r->main.post(in(s,r));}
 static Throwable root(Throwable e){while(e instanceof InvocationTargetException&&e.getCause()!=null)e=e.getCause();return e;}
 static synchronized JSONObject state(Context c)throws Exception{return new JSONObject().put("status",status).put("sessionId",id==null?JSONObject.NULL:id).put("targetPackage",target==null?JSONObject.NULL:target).put("error",error).put("reason",reason(c)).put("deviceLocked",locked(c)).put("blocked",blocked).put("width",width).put("height",height).put("operationCount",operations).put("elapsedMs",started==0?0:SystemClock.elapsedRealtime()-started).put("foregroundFallback",false).put("taskId",nullable(taskId)).put("anyAppControl",anyApp).put("stopReason",stops.reason()).put("stoppedSessionId",nullable(stops.sessionId())).put("stoppedTaskId",nullable(stops.taskId()));}
 static Object nullable(String s){return s==null?JSONObject.NULL:s;}
 /** The platform's own pin list for control of any app (empty unless the OS build opts this controller in),
  * matched against this package's actual signers. Unreadable means no: the controller never claims more than Android allows. */
 static boolean anyAppControl(Context c){
  try{int res=android.content.res.Resources.getSystem().getIdentifier("config_pixelComputerControlPrototypeAgents","array","android");if(res==0)return false;
   String[] pins=android.content.res.Resources.getSystem().getStringArray(res);
   Signature[] signers=c.getPackageManager().getPackageInfo(c.getPackageName(),PackageManager.GET_SIGNING_CERTIFICATES).signingInfo.getApkContentsSigners();
   List<String> digests=new ArrayList<>();for(Signature s:signers){StringBuilder h=new StringBuilder();for(byte b:java.security.MessageDigest.getInstance("SHA-256").digest(s.toByteArray()))h.append(String.format("%02x",b));digests.add(h.toString());}
   return AnyAppControl.pinned(c.getPackageName(),digests,pins);
  }catch(Throwable e){return false;}
 }
 static boolean locked(Context c){KeyguardManager k=c.getSystemService(KeyguardManager.class);return k!=null&&k.isDeviceLocked();}
 // Android refuses locked creation with ERROR_DEVICE_LOCKED (2) unless the OS build and owner
 // allow it; a locked session blocks on installer, permission, settings and secure screens.
 static String reason(Context c){if(error.contains("target_in_foreground"))return "target_in_foreground";if(error.contains("owner_unlock_required")||blocked&&locked(c))return "owner_unlock_required";if("rejected".equals(status)&&error.equals("Android session error 2"))return "device_locked";if("starting".equals(status)){String w=pending!=null&&pendingToken==generation?replacement.blocker():replacement.unanswered(generation)?Replacement.REQUEST_UNANSWERED:"";if(!w.isEmpty())return w;}if("rejected".equals(status))return Replacement.reason(error);return "";}
 static void clearNodes(){for(AccessibilityNodeInfo n:nodes.values())n.recycle();nodes.clear();parents.clear();labels.clear();truncated=false;snapshotId=null;}
 // Only the owner's Stops (notification, Rose's owner_stop, the viewer's confirmed Stop) are "user"; that also refuses
 // later starts of that task.
 static synchronized void stop(Context c,String why){stops.stopped(why,id,taskId);if(StopLedger.USER.equals(why))announceOwnerStop(c,id,taskId);++generation;Object old=session;session=null;consent=null;clearNodes();blocked=false;status="stopped";if(old!=null)try{old.getClass().getMethod("close").invoke(old);replacement.closing(sessionToken,old);}catch(Throwable e){session=old;error=root(e).toString();status="cleanup_failed";} }
 // Tells Rose Agent at once, so it can end the task even if the agent makes no further phone call.
 // Shared sender identity lets the receiver check this package and its signing certificate.
 static void announceOwnerStop(Context c,String session,String task){try{Intent i=new Intent("dev.pixel.backgroundcontrol.action.OWNER_STOP").setPackage("dev.pixel.voice").putExtra("sessionId",session).putExtra("taskId",task).putExtra("stoppedAt",System.currentTimeMillis());BroadcastOptions o=BroadcastOptions.makeBasic();o.setShareIdentityEnabled(true);c.sendBroadcast(i,null,o.toBundle());}catch(RuntimeException e){Log.w("BackgroundControl","Owner stop notice not delivered",e);}}
 /** Launches Android's pending consent; false when none is pending (the owner sees that, #225). */
 static boolean requestConsent(Activity a)throws Exception{IntentSender pending; synchronized(Controller.class){pending=consent;}if(pending==null)return false;a.startIntentSenderForResult(pending,3,null,0,0,0);return true;}
 // The refused task is named on its own; the running workspace, if any, is only under "current".
 static JSONObject refusal(String task,JSONObject current)throws Exception{return refusal(stops.refusal(task),current);}
 static JSONObject refusal(Map<String,Object> fields,JSONObject current)throws Exception{JSONObject r=new JSONObject();for(Map.Entry<String,Object> f:fields.entrySet())r.put(f.getKey(),f.getValue());return r.put("current",current);}
 static String taskParam(JSONObject p)throws Exception{return StopLedger.taskId(p.has("taskId")&&!p.isNull("taskId")?p.getString("taskId"):null);}
 // An agent stop tagged for another task (for example a late turn-end cleanup) leaves this workspace alone.
 /** #156: resolves an overview selection against every slot, each by its own live session only (resolveSelectionOne): a
  * slot binds only while its session is held and ready, its own display (read live from its accessibility windows) is the
  * requested one and its target is the requested package. Never cleanup_failed, a closed session's historical ids, an
  * unbound device or the attested display id. One display per session, so at most one slot matches. */
 static synchronized WatchBinding resolveSelection(int display,String pkg){
  for(Slot s:slots){Slot was=enter(s);try{WatchBinding b=resolveSelectionOne(display,pkg);if(b.mode==WatchBinding.Mode.BOUND)return b;}finally{leave(was);}}
  return WatchBinding.INVALID;
 }
 /** One slot: its own live session only. Its own display comes from its accessibility windows. */
 @SuppressWarnings("unchecked") static synchronized WatchBinding resolveSelectionOne(int display,String pkg){int own=-1;if(session!=null)try{List<AccessibilityWindowInfo> windows=(List<AccessibilityWindowInfo>)session.getClass().getMethod("getAccessibilityWindows").invoke(session);for(AccessibilityWindowInfo w:windows){int d=w.getDisplayId();own=own==-1?d:(own==d?own:-2);w.recycle();}}catch(Throwable e){own=-1;}return WatchBinding.fromSelection(display,pkg,session!=null,status,own<0?-1:own,target,id,taskId);}
 static synchronized JSONObject agentStop(Context c,JSONObject p)throws Exception{String task=taskParam(p);Map<String,Object> stale=stops.taskMismatch(task,taskId);if(stale!=null)return refusal(stale,state(c));stop(c,StopLedger.AGENT);return state(c);}
 static synchronized JSONObject start(Context context,JSONObject p)throws Exception{
  Context c=context.getApplicationContext();String pkg=p.getString("packageName");String task=taskParam(p);anyApp=anyAppControl(c);
  if(stops.refuses(task))return refusal(task,state(c));
  if(pkg.equals(c.getPackageName())||c.getPackageManager().getLaunchIntentForPackage(pkg)==null)throw new IllegalArgumentException("Target must be an installed launchable app");
  if(session!=null&&pkg.equals(target)&&"ready".equals(status)){if(task!=null)taskId=task;return state(c);}
  stop(c,StopLedger.AGENT);if(session!=null)throw new IllegalStateException("Previous session cleanup is incomplete");stops.started();final long token=++generation;target=pkg;taskId=task;id=UUID.randomUUID().toString();started=SystemClock.elapsedRealtime();operations=0;width=height=0;error="";blocked=false;status="starting";
  final Slot slot=active;final String sid=id;
  Runnable request=()->{
   try{
    synchronized(Controller.class){if(token!=generation)return;}
    NotificationManager nm=c.getSystemService(NotificationManager.class);nm.createNotificationChannel(new NotificationChannel("background-live","Background app control",NotificationManager.IMPORTANCE_LOW));
    PendingIntent stop=PendingIntent.getBroadcast(c,slot.index,new Intent(c,StopReceiver.class).putExtra("sessionId",sid),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
    PendingIntent view=PendingIntent.getActivity(c,1+10*slot.index,watch(new Intent(c,AgentPipActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),slot.index,sid,task),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
    Notification.Builder nb=new Notification.Builder(c,"background-live").setSmallIcon(android.R.drawable.ic_menu_view).setContentTitle("Rose Agent is using "+c.getPackageManager().getApplicationLabel(c.getPackageManager().getApplicationInfo(pkg,0))).setContentText("Tap to watch full screen. Rose keeps working in the background.").setContentIntent(view).setOngoing(true).addAction(new Notification.Action.Builder(null,"Stop",stop).build());
    // The lock screen shows that a session is running, not which app it controls.
    nb.setVisibility(Notification.VISIBILITY_PRIVATE).setPublicVersion(new Notification.Builder(c,"background-live").setSmallIcon(android.R.drawable.ic_menu_view).setContentTitle("Rose Agent is using an app").setContentText("Unlock to see or stop it").build());
    Notification.Builder.class.getMethod("setRequestPromotedOngoing",boolean.class).invoke(nb,true);
    Class<?> type=Class.forName(EXT+"ComputerControlExtensions");int version=((Number)type.getMethod("getVersion").invoke(null)).intValue();Object ext=type.getMethod("getInstance",Context.class,int.class).invoke(null,c,version);
    Class<?> b=Class.forName(EXT+"ComputerControlSession$Params$Builder");Object builder=b.getConstructor(Context.class).newInstance(c);b.getMethod("setName",String.class).invoke(builder,MultiSession.name(slot.index));b.getMethod("setTargetPackageNames",List.class).invoke(builder,Arrays.asList(pkg));
    Class<?> n=Class.forName(EXT+"ComputerControlSession$NotificationParams$Builder");Object nBuilder=n.getConstructor(Notification.class,int.class).newInstance(nb.build(),MultiSession.notificationId(slot.index));b.getMethod("setNotificationParams",Class.forName(EXT+"ComputerControlSession$NotificationParams")).invoke(builder,n.getMethod("build").invoke(nBuilder));
    Class<?> cb=Class.forName(EXT+"ComputerControlSession$Callback");Object callback=Proxy.newProxyInstance(cb.getClassLoader(),new Class<?>[]{cb},(proxy,method,args)->{
     if(method.getName().equals("hashCode"))return System.identityHashCode(proxy);if(method.getName().equals("equals"))return proxy==args[0];if(method.getName().equals("toString"))return "PixelVoiceBackgroundCallback";
     synchronized(Controller.class){
      // Every callback follows Android's same-name check, so the first one for this request is its admission.
      switch(method.getName()){case "onSessionCreated":case "onSessionCreationFailed":case "onSessionPending":replacement.admitted(token);replacement.answered(token);break;case "onSessionClosed":replacement.closed(token);break;}
      if(token!=generation){if(method.getName().equals("onSessionCreated")){args[0].getClass().getMethod("close").invoke(args[0]);replacement.closing(token,args[0]);}dispatch();return null;}
      switch(method.getName()){
       case "onSessionPending":consent=(IntentSender)args[0];status="consent_required";Activity a=MainActivity.visible.get();if(a!=null)requestConsent(a);break;
       case "onSessionCreated":session=args[0];sessionToken=token;consent=null;status="launching";attachLifecycle(token);worker.execute(()->launch(c,token,slot));break;
       case "onSessionCreationFailed":status="rejected";error="Android session error "+args[0];break;
       case "onSessionClosed":session=null;clearNodes();status="closed";error="Android close reason "+(args==null||args.length==0?"unspecified":args[0]);break;
      }
     }return null;
    });
    synchronized(Controller.class){if(token!=generation)return;replacement.requested(token);}
    type.getMethod("requestSession",Class.forName(EXT+"ComputerControlSession$Params"),Executor.class,cb).invoke(ext,b.getMethod("build").invoke(builder),executor(slot),callback);
   }catch(Throwable e){synchronized(Controller.class){replacement.answered(token);if(token==generation){error=root(e).toString();status=AnyAppControl.targetRefused(error)?"target_not_allowed":Replacement.listed(error)?"rejected":"failed";}dispatch();}}
  };
  pending=request;pendingToken=token;dispatch();
  main.postDelayed(in(slot,()->{synchronized(Controller.class){if(token==generation)stop(c,StopLedger.DEADLINE);}}),DEADLINE);
  return state(c);
 }
 static void attachLifecycle(long token)throws Exception{
  Class<?> cb=Class.forName(EXT+"ComputerControlSession$LifecycleCallback");
  Object listener=Proxy.newProxyInstance(cb.getClassLoader(),new Class<?>[]{cb},(proxy,m,args)->{
   if(m.getName().equals("hashCode"))return System.identityHashCode(proxy);if(m.getName().equals("equals"))return proxy==args[0];if(m.getName().equals("toString"))return "BackgroundLifecycle";
   synchronized(Controller.class){if(m.getName().equals("onClosed")){replacement.closed(token);dispatch();}if(token!=generation)return null;switch(m.getName()){
    case "onBlocked":blocked=true;clearNodes();status="blocked";error="Android block reason "+args[0];break;
    case "onActive":blocked=false;if(session!=null&&!status.equals("launching"))status="ready";break;
    case "onClosed":session=null;clearNodes();status="closed";error="Android close reason "+(args==null||args.length==0?"unspecified":args[0]);break;
   }}return null;
  });session.getClass().getMethod("setLifecycleCallback",Executor.class,cb).invoke(session,executor(active),listener);
 }
 static void launch(Context c,long token,Slot slot){try{
  synchronized(Controller.class){Slot was=enter(slot);try{if(token!=generation||session==null)return;
   // One app per session: Rose's sessions may launch any app, so never launch one another slot holds (MultiSession).
   if(otherHolds(slot,target))throw new IllegalStateException(MultiSession.APP_IN_OTHER_SESSION);
   session.getClass().getMethod("launchApplication",String.class).invoke(session,target);}finally{leave(was);}}
  long until=SystemClock.elapsedRealtime()+15000;
  while(SystemClock.elapsedRealtime()<until){synchronized(Controller.class){Slot was=enter(slot);try{if(token!=generation||session==null)return;Image frame=(Image)session.getClass().getMethod("getScreenshot").invoke(session);if(frame!=null){width=frame.getWidth();height=frame.getHeight();frame.close();status="ready";return;}}finally{leave(was);}}Thread.sleep(150);}
  throw new IllegalStateException("Target did not produce an allowed background frame");
 }catch(Throwable e){synchronized(Controller.class){Slot was=enter(slot);try{if(token==generation){++generation;error=root(e).toString();Object old=session;session=null;clearNodes();status="failed";if(old!=null)try{old.getClass().getMethod("close").invoke(old);replacement.closing(token,old);}catch(Throwable ignored){}}}finally{leave(was);}}}}
 /** Sends the pending start's one request at the earliest point Android can admit it (Replacement.clear: every session
  * this controller closed has reported onClosed and no earlier request is unanswered); Android decides admission itself.
  * An older start's request is dropped. No timer and no retry: it runs, under the lock, when a start is made, a request is
  * answered or a closed session is reported; the start's own deadline still applies while it waits. */
 static void dispatch(){dispatchOne();for(Slot s:slots)if(s!=active){Slot was=enter(s);try{dispatchOne();}finally{leave(was);}}}
 // One request at a time across both slots (agreed P1): none is sent while another slot's request is posted or unanswered;
 // when a posted request has run (sent, or dropped as stale), every slot is tried again.
 static boolean posting;
 static void dispatchOne(){if(pending==null)return;if(pendingToken!=generation){pending=null;return;}if(!replacement.clear()||posting||othersUnanswered())return;Runnable r=pending;pending=null;posting=true;main.post(in(active,()->{posting=false;r.run();dispatch();}));}
 static boolean othersUnanswered(){for(Slot s:slots)if(s!=active&&Replacement.REQUEST_UNANSWERED.equals(s.replacement.blocker()))return true;return false;}
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
 static AccessibilityNodeInfo node(JSONObject p)throws Exception{AccessibilityNodeInfo n=nodes.get(p.getInt("nodeId"));if(n==null||!AnyAppControl.actionable(n.getPackageName()==null?"":n.getPackageName().toString(),target,anyApp))throw new IllegalArgumentException("Unknown target node; inspect_ui first");validate(n);return n;}
 static void validate(AccessibilityNodeInfo n){Rect old=new Rect(),now=new Rect();n.getBoundsInScreen(old);String text=String.valueOf(n.getText());if(!n.refresh())throw new IllegalStateException("Node is stale");n.getBoundsInScreen(now);if(!old.equals(now)||!text.equals(String.valueOf(n.getText()))||!n.isEnabled()||!n.isVisibleToUser())throw new IllegalStateException("Observed target changed; inspect again");}
 static void checkPoint(int x,int max){if(x<0||x>=max)throw new IllegalArgumentException("Point outside background display");}
 static void swipe(JSONArray p)throws Exception{for(int i=0;i<4;i++)checkPoint(p.getInt(i),i%2==0?width:height);session.getClass().getMethod("swipe",int.class,int.class,int.class,int.class).invoke(session,p.getInt(0),p.getInt(1),p.getInt(2),p.getInt(3));}
 static JSONObject base()throws Exception{return new JSONObject().put("status","ok").put("sessionId",id).put("targetPackage",target).put("snapshotId",snapshotId).put("width",width).put("height",height).put("scope","background_target").put("packageName",target).put("validForMs",30000).put("displayWidth",width).put("displayHeight",height).put("observedPackage",observed==null?target:observed).put("anyAppControl",anyApp);}
 /** Rose Agent's own read-only preview of the live session (#225, PreviewPolicy): one bounded JPEG of the session's
  * own display, refused with one fixed word unless the caller names the live session and its task. Unlike capture()
  * it is never the agent's observation: the node map, snapshot id/time, width/height, operation count and observed app
  * are left exactly as they were, so the agent's next action keeps its freshness, and no snapshot id is returned.
  * Secure windows are already blanked by the platform (the session display is not VIRTUAL_DISPLAY_FLAG_SECURE). Not
  * advertised in capabilities, and refused on Rose Agent's app_command (Mac) path. */
 static long lastPreview;
 static synchronized JSONObject preview(Context c,JSONObject p)throws Exception{
  String requestedSession=p.isNull("sessionId")?"":p.optString("sessionId",""),requestedTask=p.isNull("taskId")?"":p.optString("taskId","");
  long now=SystemClock.elapsedRealtime();
  String refused=PreviewPolicy.refusal(requestedSession,requestedTask,locked(c),session!=null,status,blocked,now-started>DEADLINE,id,taskId,now,lastPreview);
  if(refused!=null)return new JSONObject().put("status",refused);
  lastPreview=now;
  Image image;try{image=(Image)session.getClass().getMethod("getScreenshot").invoke(session);}catch(Throwable e){image=null;}
  if(image==null)return new JSONObject().put("status",PreviewPolicy.NO_FRAME);
  Bitmap all=null,crop=null,small=null;
  try{
   int w=image.getWidth(),h=image.getHeight();int[] size=PreviewPolicy.size(w,h);
   if(size==null)return new JSONObject().put("status",PreviewPolicy.NO_FRAME);
   Image.Plane plane=image.getPlanes()[0];
   all=Bitmap.createBitmap(plane.getRowStride()/plane.getPixelStride(),h,Bitmap.Config.ARGB_8888);all.copyPixelsFromBuffer(plane.getBuffer());
   crop=Bitmap.createBitmap(all,0,0,w,h);small=Bitmap.createScaledBitmap(crop,size[0],size[1],true);
   ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] jpeg=null;
   for(int quality:PreviewPolicy.QUALITIES){out.reset();small.compress(Bitmap.CompressFormat.JPEG,quality,out);if(PreviewPolicy.fits(out.size())){jpeg=out.toByteArray();break;}}
   if(jpeg==null)return new JSONObject().put("status",PreviewPolicy.NO_FRAME);
   return new JSONObject().put("status",PreviewPolicy.OK).put("sessionId",id).put("taskId",taskId).put("targetPackage",target)
    .put("capturedAt",System.currentTimeMillis()).put("width",small.getWidth()).put("height",small.getHeight())
    .put("mimeType","image/jpeg").put("imageBase64",android.util.Base64.encodeToString(jpeg,android.util.Base64.NO_WRAP));
  }catch(Throwable e){return new JSONObject().put("status",PreviewPolicy.NO_FRAME);}
  finally{if(small!=null&&small!=crop)small.recycle();if(crop!=null&&crop!=all)crop.recycle();if(all!=null)all.recycle();image.close();}
 }
 /** The apps currently running under agent control, for Rose Agent's own overview (CurrentApps; #156/#225): the live
  * session's application windows on its display, then its target. Membership is the live session only, never task
  * history: no live session lists nothing, and the generation rises on every change. The windows are read without
  * touching the agent's observation (node map, snapshot id/time, width/height, operation count, observed app); each
  * root is released at once. Blocked or locked sessions are not read. complete is true only for a genuinely empty set
  * (no live session); a live set says why it is incomplete (CurrentApps). Not advertised in capabilities, and refused on
  * Rose Agent's app_command (Mac) path. */
 static long appsGeneration;static String appsSignature="";
 static synchronized JSONObject currentApps(Context c)throws Exception{
  boolean locked=locked(c),live=session!=null&&CurrentApps.live(status);
  List<String> front=null;
  if(live&&!locked&&!blocked)try{
   List<String> read=new ArrayList<>();
   List<AccessibilityWindowInfo> windows=(List<AccessibilityWindowInfo>)session.getClass().getMethod("getAccessibilityWindows").invoke(session);
   for(AccessibilityWindowInfo w:windows){
    if(w.getType()==AccessibilityWindowInfo.TYPE_APPLICATION){AccessibilityNodeInfo n=w.getRoot();if(n!=null){CharSequence pkg=n.getPackageName();if(pkg!=null)read.add(pkg.toString());n.recycle();}}
    w.recycle();
   }
   front=read;
  }catch(Throwable e){front=null;}
  // First the platform's snapshot of every session device this controller owns (PR374), independent of any session, so a
  // failed-close or replacement device is seen exactly as Launcher3 sees it; it is not read while locked.
  CurrentApps.Owned owned=null;
  if(!locked)try{
   Class<?> type=Class.forName(EXT+"ComputerControlExtensions");int version=((Number)type.getMethod("getVersion").invoke(null)).intValue();
   Object ext=type.getMethod("getInstance",Context.class,int.class).invoke(null,c,version);
   Bundle b=ext==null?null:(Bundle)type.getMethod("getOwnedAutomatedDevices").invoke(ext);
   if(b!=null)owned=CurrentApps.owned(b.get("generation"),b.get("deviceIds"),b.get("deviceComplete"),b.get("appDeviceIds"),b.get("appPackageNames"));
  }catch(Throwable e){owned=null;}
  // Otherwise the platform's own answer for this session's device (#156 parity), when this OS build has it; otherwise, or
  // when it fails (SecurityException, IllegalStateException) or is malformed, the session windows above, marked incomplete.
  CurrentApps.Platform platform=null;
  if(owned==null&&live&&!locked)try{
   Bundle b=(Bundle)session.getClass().getMethod("getAutomatedApps").invoke(session);
   if(b!=null)platform=CurrentApps.platform(b.get("generation"),b.get("packageNames"),b.get("userId"),b.get("complete"),b.get("closed"),android.os.Process.myUid()/100000);
  }catch(Throwable e){platform=null;}
  // Bindings only (MultiSession.bind): which occupied slot's session each listed device belongs to, with that session's
  // target and one front app; they never decide membership or completeness (snapshot-only rule).
  MultiSession.Bound bound=bindings(locked,owned,front);
  CurrentApps.Result r=owned!=null?CurrentApps.ofDevices(locked,bound.targets,owned,bound.fronts,c.getPackageName())
   :platform!=null?CurrentApps.of(locked,session!=null,status,target,platform,front,c.getPackageName())
   :CurrentApps.of(locked,session!=null||replacement.releasing(),status,blocked,target,front,c.getPackageName());
  boolean listed=CurrentApps.LIVE.equals(r.status);JSONArray apps=new JSONArray();
  // sessionStatus: the held session's status (with owned_devices only informational: it may be cleanup_failed, or null with
  // no session held); snapshotGeneration: the platform's global generation for an owned-device snapshot, else null.
  boolean held=listed&&session!=null;
  // apps[].sessionId: with the snapshot, the bound slot whose device lists the app (null for an unbound device: shown, but no
  // preview, Stop or Watch); without it, the one held session.
  for(CurrentApps.App a:r.apps)apps.put(new JSONObject().put("packageName",a.packageName).put("front",a.front).put("target",a.target)
   .put("sessionId",owned!=null?nullable(bound.sessionOf(a.packageName)):held?id:JSONObject.NULL));
  JSONArray sessions=new JSONArray();
  for(int i=0;i<bound.slots.size();i++){MultiSession.Bind s=bound.slots.get(i);sessions.put(new JSONObject().put("slot",s.slot).put("sessionId",nullable(s.sessionId))
   .put("taskId",nullable(s.taskId)).put("targetPackage",nullable(s.target)).put("sessionStatus",s.status).put("bound",bound.bound.get(i)).put("frontKnown",s.frontKnown));}
  JSONObject reply=new JSONObject().put("status",r.status).put("sessionId",held?id:JSONObject.NULL).put("taskId",held?nullable(taskId):JSONObject.NULL)
   .put("sessionStatus",held?status:JSONObject.NULL)
   .put("complete",r.complete).put("incompleteReason",r.incompleteReason).put("source",r.source)
   .put("snapshotGeneration",owned!=null?(Object)owned.generation:JSONObject.NULL).put("apps",apps).put("sessions",sessions);
  // generation: the controller's own counter over the whole reply (membership, bindings, front, status, lock), rising on every
  // change and never falling within one controllerEpoch (new for each controller process). The platform's per-device
  // generation restarts with each device and is never forwarded. No timestamps.
  String signature=reply.toString();if(!signature.equals(appsSignature)){appsSignature=signature;appsGeneration++;}
  return reply.put("generation",appsGeneration).put("controllerEpoch",EPOCH);
 }
 static final String EPOCH=UUID.randomUUID().toString();
 /** current_apps bindings (MultiSession.bind): each occupied slot's attested deviceId (a closed answer's ids are historical
  * only), and its one front app, the topmost application window, only when ready, unblocked and unlocked. front0: the
  * active slot's window read above. Nothing is read while locked. */
 static MultiSession.Bound bindings(boolean locked,CurrentApps.Owned owned,List<String> front0){
  save(active);List<MultiSession.Bind> occupied=new ArrayList<>();
  for(Slot s:slots){
   if(!MultiSession.occupied(s.session!=null,s.status))continue;
   Integer device=null;boolean closed=false;
   if(!locked&&s.session!=null)try{
    Bundle b=(Bundle)s.session.getClass().getMethod("getAutomatedApps").invoke(s.session);
    CurrentApps.Platform pl=b==null?null:CurrentApps.platform(b.get("generation"),b.get("packageNames"),b.get("userId"),b.get("complete"),b.get("closed"),android.os.Process.myUid()/100000);
    if(pl!=null){closed=pl.closed;device=MultiSession.deviceId(b.get("deviceId"));}
   }catch(Throwable e){device=null;}
   List<String> w=!locked&&s.session!=null&&"ready".equals(s.status)&&!s.blocked?(s==active?front0:windows(s.session)):null;
   occupied.add(new MultiSession.Bind(s.index,s.id,s.taskId,s.target,s.status,device,closed,w!=null,w==null||w.isEmpty()?null:w.get(0)));
  }
  return MultiSession.bind(occupied,owned);
 }
 /** A session display's application window packages, top first, or null when unreadable; every root is released at once.
  * Read-only: never the agent's observation. */
 @SuppressWarnings("unchecked") static List<String> windows(Object s){
  try{List<String> read=new ArrayList<>();
   for(AccessibilityWindowInfo w:(List<AccessibilityWindowInfo>)s.getClass().getMethod("getAccessibilityWindows").invoke(s)){
    if(w.getType()==AccessibilityWindowInfo.TYPE_APPLICATION){AccessibilityNodeInfo n=w.getRoot();if(n!=null){CharSequence pkg=n.getPackageName();if(pkg!=null)read.add(pkg.toString());n.recycle();}}
    w.recycle();
   }return read;}catch(Throwable e){return null;}
 }
 // ---- Two-app routing (MultiSession). Every provider operation enters the slot its request names, runs the unchanged
 // single-session operation there and leaves; replies add "sessions" (each slot's summary). Legacy requests (no mode,
 // no sessionId) are slot 0 exactly as before; nothing guesses between two live sessions.
 interface Op{JSONObject run()throws Exception;}
 static JSONObject in(int slot,Op op)throws Exception{Slot was=enter(slots[slot]);try{return op.run();}finally{leave(was);}}
 /** A later slot's notification opens the viewer bound to that session (WatchBinding extras); slot 0 keeps its legacy
  * unbound entry. A slot without a valid task id is an invalid binding: the viewer shows unavailable, never slot 0. */
 static Intent watch(Intent i,int slot,String session,String task){if(slot>0){i.putExtra(WatchBinding.EXTRA_SESSION,session);if(task!=null)i.putExtra(WatchBinding.EXTRA_TASK,task);}return i;}
 /** The packages Android attests for an open session of this controller's own user (getAutomatedApps), else empty. */
 static List<String> attested(Object s){
  if(s==null)return Collections.emptyList();
  try{Bundle b=(Bundle)s.getClass().getMethod("getAutomatedApps").invoke(s);CurrentApps.Platform pl=b==null?null:CurrentApps.platform(b.get("generation"),b.get("packageNames"),b.get("userId"),b.get("complete"),b.get("closed"),android.os.Process.myUid()/100000);
   return pl==null||pl.closed?Collections.<String>emptyList():pl.packages;}catch(Throwable e){return Collections.emptyList();}
 }
 /** Whether another slot's session targets or is attested to hold pkg. Called inside slot own (others are saved). */
 static boolean otherHolds(Slot own,String pkg){for(Slot s:slots)if(s!=own&&s.session!=null&&(pkg.equals(s.target)||attested(s.session).contains(pkg)))return true;return false;}
 static MultiSession.Slot[] view(boolean members){
  save(active);MultiSession.Slot[] v=new MultiSession.Slot[slots.length];
  for(int i=0;i<slots.length;i++){Slot s=slots[i];v[i]=new MultiSession.Slot(s.id,s.target,s.status,MultiSession.occupied(s.session!=null,s.status),members?attested(s.session):null);}
  return v;
 }
 /** The owned-device snapshot Bundle (PR374), or null on an older build or any failure. */
 static Bundle owned(Context c){
  try{Class<?> type=Class.forName(EXT+"ComputerControlExtensions");int version=((Number)type.getMethod("getVersion").invoke(null)).intValue();
   Object ext=type.getMethod("getInstance",Context.class,int.class).invoke(null,c,version);return ext==null?null:(Bundle)type.getMethod("getOwnedAutomatedDevices").invoke(ext);}catch(Throwable e){return null;}
 }
 /** The platform's sessionLimit capability: 2 or 1, conservative 1 when absent or unreadable. Never free slots. */
 static int sessionLimit(Context c){Bundle b=owned(c);return MultiSession.sessionLimit(b==null?null:b.get("sessionLimit"));}
 static JSONArray summary()throws Exception{
  save(active);JSONArray a=new JSONArray();
  for(Slot s:slots)a.put(new JSONObject().put("slot",s.index).put("sessionId",nullable(s.id)).put("targetPackage",nullable(s.target)).put("taskId",nullable(s.taskId)).put("status",s.status)
   .put("stopReason",s.stops.reason()).put("stoppedSessionId",nullable(s.stops.sessionId())).put("stoppedTaskId",nullable(s.stops.taskId())));
  return a;
 }
 static JSONObject refused(MultiSession.Route r,Context c)throws Exception{return new JSONObject().put("status",r.refusal).put("actionExecuted",false).put("ownLive",r.ownLive).put("current",state(c)).put("sessions",summary());}
 static synchronized JSONObject route(Context c,String op,JSONObject p)throws Exception{
  String sid=p.isNull("sessionId")?null:p.optString("sessionId",null);
  switch(op){
   case "start":{
    String task=taskParam(p),mode=p.isNull("mode")?"":p.optString("mode","");
    // An owner Stop of a task in any slot refuses later starts of that task in every slot.
    save(active);for(Slot s:slots)if(s.stops.refuses(task))return refusal(s.stops.refusal(task),state(c)).put("sessions",summary());
    MultiSession.Route r=MultiSession.start(view(true),p.getString("packageName"),mode,mode.isEmpty()?1:sessionLimit(c));
    if(r.refused())return refused(r,c);
    return in(r.slot,()->start(c,p)).put("sessions",summary());
   }
   case "get_state":{
    MultiSession.Route r=MultiSession.state(view(false),sid);
    if(r.refused())return new JSONObject().put("status",r.refusal).put("sessionId",sid).put("sessions",summary());
    return in(r.slot,()->state(c)).put("sessions",summary());
   }
   case "command":{
    MultiSession.Route r=MultiSession.target(view(false),sid);
    if("open_app".equals(p.optString("action"))&&MultiSession.APP_IN_OTHER_SESSION.equals(MultiSession.openApp(view(true),r.slot,p.isNull("packageName")?null:p.optString("packageName",null))))
     return new JSONObject().put("status",MultiSession.APP_IN_OTHER_SESSION).put("actionExecuted",false).put("sessionId",sid);
    return in(r.slot,()->command(c,p));
   }
   case "preview":return in(MultiSession.target(view(false),sid).slot,()->preview(c,p));
   case "stop":{
    MultiSession.Route r=MultiSession.stop(view(false),sid);
    if(r.refused())return refused(r,c);
    return in(r.slot,()->agentStop(c,p)).put("sessions",summary());
   }
   case "current_apps":return currentApps(c);
   case "owner_stop":{
    // The owner's Stop of one whole session from Rose Agent's own UI (refused on the Mac path by PhoneControl): it names
    // the session and its exact task; confirmed by exactly the bindingsContract rule, never by acceptance.
    String task=taskParam(p);
    MultiSession.Route r=sid==null||sid.isEmpty()?MultiSession.Route.refuse(MultiSession.STALE_SESSION,MultiSession.live(view(false))):MultiSession.state(view(false),sid);
    if(r.refused())return new JSONObject().put("status",r.refusal).put("actionExecuted",false).put("confirmed",false).put("sessionId",nullable(sid)).put("sessions",summary());
    if(!Objects.equals(task,slots[r.slot].taskId))return new JSONObject().put("status","stale_task").put("actionExecuted",false).put("confirmed",false)
     .put("sessionId",sid).put("rejectedTaskId",nullable(task)).put("sessions",summary());
    return in(r.slot,()->{stop(c,StopLedger.USER);return state(c).put("confirmed","stopped".equals(status)&&StopLedger.USER.equals(stops.reason())
     &&sid.equals(stops.sessionId())&&Objects.equals(task,stops.taskId()));}).put("sessions",summary());
   }
   default:throw new IllegalArgumentException("Unknown background operation");
  }
 }
 /** The viewer's slot (MultiSession.viewer), read under the lock with every slot's fields current; null: unavailable. */
 static Slot viewed(WatchBinding b){int i=MultiSession.viewer(view(false),b.mode==WatchBinding.Mode.BOUND,b.session);return i<0?null:slots[i];}
 /** The owner's Stop on the controller's own screen (MainActivity): every occupied slot, each as the owner's Stop, so
  * nothing keeps running unseen; with none occupied it is slot 0's stop, as before. The outcome is cleanup_failed (with
  * the first error) while any slot still holds a session it could not close. */
 static synchronized JSONObject stopAll(Context c,String why)throws Exception{
  MultiSession.Slot[] v=view(false);String outcome="stopped",firstError="";
  for(int i=0;i<slots.length;i++){
   if(!v[i].occupied&&!(i==0&&MultiSession.live(v)==0))continue;
   Slot was=enter(slots[i]);try{stop(c,why);if("cleanup_failed".equals(status)&&"stopped".equals(outcome)){outcome="cleanup_failed";firstError=error;}}finally{leave(was);}
  }
  return new JSONObject().put("status",outcome).put("error",firstError).put("sessions",summary());
 }
 /** The owner's Stop from one session's notification: that session only (MultiSession.ownerStop). */
 static synchronized void ownerStop(Context c,String sessionId){int i=MultiSession.ownerStop(view(false),sessionId);if(i<0)return;Slot was=enter(slots[i]);try{stop(c,StopLedger.USER);}finally{leave(was);}}
 /** The owner's confirmed Stop from the agent viewer (#410, Nathan-approved): only the viewer's own bound pair, rechecked
  * here under the lock (MultiSession.viewerStop: bound, unlocked, a slot holding that id and still occupied, the same task
  * and the session object the viewer attached). Then the same owner Stop as the notification's and owner_stop, confirmed
  * by owner_stop's rule. A refusal stops nothing. Called only from the viewer's confirmation (AgentPipActivity). */
 static synchronized JSONObject viewerStop(Context c,WatchBinding b,Object attachedSession)throws Exception{
  MultiSession.Slot[] v=view(false);int i=b.mode==WatchBinding.Mode.BOUND?MultiSession.bySession(v,b.session):-1;Slot s=i<0?null:slots[i];
  MultiSession.Route r=MultiSession.viewerStop(v,b.mode==WatchBinding.Mode.BOUND,b.session,b.task,s==null?null:s.taskId,
   s!=null&&attachedSession!=null&&s.session==attachedSession,locked(c));
  if(r.refused())return new JSONObject().put("status",r.refusal).put("actionExecuted",false).put("confirmed",false);
  Slot was=enter(slots[r.slot]);
  try{stop(c,StopLedger.USER);return new JSONObject().put("status",status).put("actionExecuted",true).put("confirmed","stopped".equals(status)
   &&StopLedger.USER.equals(stops.reason())&&b.session.equals(stops.sessionId())&&Objects.equals(b.task,stops.taskId()));}
  finally{leave(was);}
 }
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
  observed=null;
  for(AccessibilityWindowInfo w:windows){AccessibilityNodeInfo n=w.getRoot();if(n!=null&&observed==null&&w.getType()==AccessibilityWindowInfo.TYPE_APPLICATION&&n.getPackageName()!=null)observed=n.getPackageName().toString();if(n!=null)walk(n,out,0,-1);w.recycle();}return base().put("nodes",out).put("truncated",truncated);
 }
 static void walk(AccessibilityNodeInfo n,JSONArray out,int depth,int parent)throws Exception{
  if(depth>35||nodes.size()>=300){truncated=true;n.recycle();return;}if(AnyAppControl.actionable(n.getPackageName()==null?"":n.getPackageName().toString(),target,anyApp)&&n.isVisibleToUser()){
   int key=nodes.size();nodes.put(key,n);int ancestor=parent;parents.put(key,ancestor);labels.put(key,new LabelTargets.Node(key,ancestor,n.isEnabled(),n.isClickable(),n.isPassword(),String.valueOf(n.getText()==null?"":n.getText()),String.valueOf(n.getContentDescription()==null?"":n.getContentDescription())));parent=key;Rect r=new Rect();n.getBoundsInScreen(r);JSONObject row=new JSONObject().put("id",key).put("packageName",String.valueOf(n.getPackageName())).put("parentId",ancestor).put("nodeId",key).put("class",String.valueOf(n.getClassName())).put("viewId",(n.getViewIdResourceName()==null?"":n.getViewIdResourceName())).put("visible",true).put("longClickable",n.isLongClickable()).put("focused",n.isFocused()).put("imeEnter",n.getActionList().contains(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER)).put("checked",n.isChecked()).put("selected",n.isSelected()).put("text",n.isPassword()?"":String.valueOf(n.getText()==null?"":n.getText())).put("description",n.isPassword()?"":String.valueOf(n.getContentDescription()==null?"":n.getContentDescription())).put("className",String.valueOf(n.getClassName())).put("enabled",n.isEnabled()).put("clickable",n.isClickable()).put("editable",n.isEditable()).put("password",n.isPassword()).put("scrollable",n.isScrollable()).put("bounds",new JSONArray(Arrays.asList(r.left,r.top,r.right,r.bottom)));int click=clickParent(key);if(click>=0)row.put("clickTarget",click);out.put(row);
  }else{for(int i=0;i<n.getChildCount();i++){AccessibilityNodeInfo child=n.getChild(i);if(child!=null)walk(child,out,depth+1,parent);}n.recycle();return;}
  for(int i=0;i<n.getChildCount();i++){AccessibilityNodeInfo child=n.getChild(i);if(child!=null)walk(child,out,depth+1,parent);}
 }
 static int clickParent(int key){return LabelTargets.target(labels,key);}
 static JSONObject apps(Context c)throws Exception{JSONArray apps=new JSONArray();Intent i=new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);Set<String> seen=new HashSet<>();for(ResolveInfo r:c.getPackageManager().queryIntentActivities(i,0)){String pkg=r.activityInfo.packageName;if(!pkg.equals(c.getPackageName())&&seen.add(pkg))apps.put(new JSONObject().put("packageName",pkg).put("label",r.loadLabel(c.getPackageManager())));}return new JSONObject().put("status","ok").put("apps",apps);}
}
