package dev.pixel.voice;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.app.KeyguardManager;
import android.graphics.Rect;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import org.json.*;
import java.util.*;

/** On-demand UI access plus the user-enabled, event-only X bookmark capture. */
public final class PhoneAccessibility extends AccessibilityService {
 static PhoneAccessibility active;
 String snapshotId,signature;
 TargetSnapshotGuard.Frame snapshotFrame;
 TextInputGuard.Frame snapshotInputFrame;
 long snapshotTime;
 long lastWindowChangeElapsed;
 private GestureTrace gestureTrace;
 private synchronized GestureTrace gestures(){
  if(gestureTrace==null)gestureTrace=new GestureTrace(SystemClock::elapsedRealtime,rows->getSharedPreferences("gesture-diagnostics",0).edit().putString("records",new JSONArray(rows).toString()).apply());
  return gestureTrace;
 }
 JSONArray gestureDiagnostics(){return new JSONArray(gestures().snapshot());}
 public void onServiceConnected(){if(android.os.Build.VERSION.SDK_INT>=33){setCacheEnabled(false);android.accessibilityservice.AccessibilityServiceInfo info=getServiceInfo();info.flags|=android.accessibilityservice.AccessibilityServiceInfo.FLAG_INPUT_METHOD_EDITOR;setServiceInfo(info);}active=this;BookmarkResearch.start(this);}
 public android.accessibilityservice.InputMethod onCreateInputMethod(){return new android.accessibilityservice.InputMethod(this);}
 public int onStartCommand(android.content.Intent intent,int flags,int id){new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(()->stopSelf(id),2500);return START_NOT_STICKY;}
 public void onAccessibilityEvent(AccessibilityEvent event){
  BookmarkResearch.observe(this,event);
  if(event.getEventType()==AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED){
   lastWindowChangeElapsed=SystemClock.elapsedRealtime();
   VoiceService voice=VoiceService.instance;
   if(voice!=null)voice.appWindowChanged();
  }
 }
 public void onInterrupt(){invalidate();}
 public void onDestroy(){if(active==this)active=null;invalidate();super.onDestroy();}
 JSONObject editorDiagnostics() throws Exception {
  JSONObject out=new JSONObject().put("flags",getServiceInfo().flags);
  if(android.os.Build.VERSION.SDK_INT<33)return out;
  android.accessibilityservice.InputMethod editor=getInputMethod();out.put("available",editor!=null);
  if(editor!=null){android.view.inputmethod.EditorInfo info=editor.getCurrentInputEditorInfo();out.put("started",editor.getCurrentInputStarted()).put("connection",editor.getCurrentInputConnection()!=null);if(info!=null)out.put("package",info.packageName).put("imeOptions",info.imeOptions).put("actionId",info.actionId);}
  return out;
 }
 void invalidate(){snapshotId=null;signature=null;snapshotFrame=null;snapshotInputFrame=null;}
 static String text(CharSequence s){return s==null?"":s.toString().substring(0,Math.min(s.length(),200));}
 static final class Screen {
  JSONObject json=new JSONObject();
  Map<Integer,AccessibilityNodeInfo> nodes=new HashMap<>();
  Map<Integer,LabelTargets.Node> labels=new LinkedHashMap<>();
  int visited=0,scanned=0;
  boolean truncated=false;
  void release(){for(AccessibilityNodeInfo n:nodes.values())n.recycle();nodes.clear();}
 }
 void walk(AccessibilityNodeInfo node,JSONArray result,Screen s,int depth,int parent)throws Exception{
  if(node==null)return;
  if(depth>50||s.visited>=300||s.scanned++>=2000){s.truncated=true;node.recycle();return;}
  if(node.isVisibleToUser()){
   Rect bounds=new Rect();node.getBoundsInScreen(bounds);
   if(bounds.isEmpty()){node.recycle();return;}
   s.visited++;
   int id=s.nodes.size();s.nodes.put(id,node);
   boolean secret=node.isPassword();
   s.labels.put(id,new LabelTargets.Node(id,parent,node.isEnabled()&&!bounds.isEmpty(),node.isClickable(),secret,text(node.getText()),text(node.getContentDescription())));
   JSONObject out=new JSONObject().put("id",id).put("parentId",parent).put("class",text(node.getClassName()))
    .put("viewId",text(node.getViewIdResourceName())).put("text",secret?"[password]":text(node.getText()))
    .put("visible",!bounds.isEmpty()).put("description",secret?"":text(node.getContentDescription())).put("password",secret)
    .put("clickable",node.isClickable()).put("longClickable",node.isLongClickable()).put("editable",node.isEditable()).put("scrollable",node.isScrollable())
    .put("focused",node.isFocused()).put("imeEnter",node.getActionList().contains(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER)).put("enabled",node.isEnabled()).put("checked",node.isChecked()).put("selected",node.isSelected())
    .put("bounds",new JSONArray(new int[]{bounds.left,bounds.top,bounds.right,bounds.bottom}));
   result.put(out);
   for(int i=0;i<node.getChildCount();i++)walk(node.getChild(i),result,s,depth+1,id);
  }else node.recycle();
 }
 TargetSnapshotGuard.Frame frame(Screen s)throws Exception{
  Map<Integer,TargetSnapshotGuard.Node> nodes=new LinkedHashMap<>();JSONArray rows=s.json.getJSONArray("nodes");
  for(int i=0;i<rows.length();i++){
   JSONObject row=rows.getJSONObject(i),meaning=new JSONObject(row.toString());JSONArray b=row.getJSONArray("bounds");meaning.remove("bounds");
   int id=row.getInt("id");nodes.put(id,new TargetSnapshotGuard.Node(id,meaning.toString(),b.getInt(0),b.getInt(1),b.getInt(2),b.getInt(3)));
  }
  return new TargetSnapshotGuard.Frame(s.json.getString("packageName")+":"+s.json.getInt("windowId"),s.truncated,nodes);
 }
 AccessibilityNodeInfo phoneRoot(){
  if(android.os.Build.VERSION.SDK_INT>=30){
   List<android.view.accessibility.AccessibilityWindowInfo> windows=getWindowsOnAllDisplays().get(android.view.Display.DEFAULT_DISPLAY);
   android.view.accessibility.AccessibilityWindowInfo selected=null;
   if(windows!=null)for(android.view.accessibility.AccessibilityWindowInfo window:windows){
    if(window.getType()==android.view.accessibility.AccessibilityWindowInfo.TYPE_ACCESSIBILITY_OVERLAY)continue;
    if(!window.isActive()&&!window.isFocused())continue;
    if(selected==null||window.getLayer()>selected.getLayer())selected=window;
   }
   if(selected!=null)return selected.getRoot();
  }
  AccessibilityNodeInfo root=getRootInActiveWindow();
  if(root!=null&&android.os.Build.VERSION.SDK_INT>=30){
   android.view.accessibility.AccessibilityWindowInfo window=root.getWindow();
   if(window==null||window.getDisplayId()!=android.view.Display.DEFAULT_DISPLAY){root.recycle();return null;}
  }
  return root;
 }
 Screen screen()throws Exception{
  Screen s=new Screen();if(android.os.Build.VERSION.SDK_INT>=33)clearCache();AccessibilityNodeInfo root=phoneRoot();
  if(root==null){s.json.put("status","no_window");return s;}
  s.json.put("status","ok").put("packageName",text(root.getPackageName())).put("windowId",root.getWindowId());
  JSONArray nodes=new JSONArray();walk(root,nodes,s,0,-1);
  for(int i=0;i<nodes.length();i++){JSONObject row=nodes.getJSONObject(i);int target=LabelTargets.target(s.labels,row.getInt("id"));if(target>=0)row.put("clickTarget",target);}
  s.json.put("nodes",nodes).put("truncated",s.truncated);return s;
 }
 TextInputGuard.Frame inputFrame(Screen s)throws Exception{
  Map<Integer,TextInputGuard.Node> nodes=new LinkedHashMap<>();JSONArray rows=s.json.getJSONArray("nodes");
  for(int i=0;i<rows.length();i++){
   JSONObject n=rows.getJSONObject(i);JSONArray b=n.getJSONArray("bounds");int id=n.getInt("id");
   nodes.put(id,new TextInputGuard.Node(id,n.getInt("parentId"),n.getString("class"),n.getString("viewId"),n.getString("text"),n.getString("description"),
    n.getBoolean("editable"),n.getBoolean("enabled"),n.getBoolean("password"),n.getBoolean("visible"),n.getBoolean("focused"),
    b.getInt(0),b.getInt(1),b.getInt(2),b.getInt(3)));
  }
  return new TextInputGuard.Frame(s.json.getString("packageName")+":"+s.json.getInt("windowId"),s.truncated,nodes);
 }
 static String hash(String value){try{byte[] bytes=java.security.MessageDigest.getInstance("SHA-256").digest(value.getBytes(java.nio.charset.StandardCharsets.UTF_8));StringBuilder out=new StringBuilder();for(byte b:bytes)out.append(String.format(java.util.Locale.ROOT,"%02x",b&255));return out.toString();}catch(Exception ignored){return "";}}
 Map<String,Object> windowEvidence(){
  Map<String,Object> out=new LinkedHashMap<>();
  if(lastWindowChangeElapsed>0)out.put("lastWindowChangeAgoMs",Math.max(0,SystemClock.elapsedRealtime()-lastWindowChangeElapsed));
  try{AccessibilityNodeInfo root=phoneRoot();
   if(root!=null){try{out.put("packageName",text(root.getPackageName()));out.put("windowId",root.getWindowId());}finally{root.recycle();}}
  }catch(RuntimeException ignored){/* Missing diagnostic evidence must not stop a gesture. */}
  return out;
 }
 Map<String,Object> screenEvidence(Screen s){
  Map<String,Object> out=windowEvidence();out.put("screenHash",hash(s.json.toString()));out.put("nodeCount",s.nodes.size());
  for(AccessibilityNodeInfo n:s.nodes.values())if(n.isFocused()){out.put("focusedViewId",text(n.getViewIdResourceName()));break;}
  return out;
 }
 String beginGesture(JSONObject command,Screen s,AccessibilityNodeInfo target,Rect bounds,float[] points,int duration){
  Map<String,Object> r=new LinkedHashMap<>();r.put("commandId",command.optString("id"));r.put("action",command.optString("action"));r.put("startedAt",System.currentTimeMillis());r.put("durationMs",duration);
  r.put("snapshotAgeMs",snapshotId==null?-1:Math.max(0,SystemClock.elapsedRealtime()-snapshotTime));
  r.put("screenChangeRejectionEnabled",getSharedPreferences("screen-change-guard",0).getBoolean("enabled",true));
  if(s!=null){r.put("screenChangedBeforeDispatch",!s.json.toString().equals(signature));r.put("before",screenEvidence(s));}
  else r.put("before",windowEvidence());
  if(target!=null){Map<String,Object> t=new LinkedHashMap<>();t.put("viewId",text(target.getViewIdResourceName()));t.put("className",text(target.getClassName()));t.put("focused",target.isFocused());t.put("enabled",target.isEnabled());t.put("editable",target.isEditable());
   if(bounds!=null)t.put("bounds",Arrays.asList(bounds.left,bounds.top,bounds.right,bounds.bottom));r.put("target",t);}
  List<Float> p=new ArrayList<>();for(float point:points)p.add(point);r.put("points",p);
  return gestures().begin(r);
 }
 boolean dispatchObservedGesture(GestureDescription description,String traceId){
  // Observe Android's existing callback without waiting, delaying, or retrying.
  boolean queued;
  try{queued=dispatchGesture(description,new GestureResultCallback(){
   public void onCompleted(GestureDescription g){gestures().event(traceId,"completed",Collections.singletonMap("callbackWindow",windowEvidence()));}
   public void onCancelled(GestureDescription g){gestures().event(traceId,"cancelled",Collections.singletonMap("callbackWindow",windowEvidence()));}
  },null);}catch(RuntimeException e){gestures().event(traceId,"dispatch_error",Collections.emptyMap());throw e;}
  gestures().event(traceId,queued?"queued":"rejected",Collections.emptyMap());return queued;
 }
 JSONObject rejected(String message,String reason,Screen current,int target)throws Exception{
  JSONObject result=new JSONObject().put("status","stale_screen").put("message",message);
  try{result.put("screenRejection",ScreenRejectionDiagnostics.compare(reason,snapshotFrame,current==null?null:frame(current),target,snapshotId==null?-1:SystemClock.elapsedRealtime()-snapshotTime));}catch(Exception ignored){}
  invalidate();return result;
 }
 JSONObject execute(JSONObject command)throws Exception{
  if(((KeyguardManager)getSystemService(KEYGUARD_SERVICE)).isKeyguardLocked()){invalidate();return new JSONObject().put("status","locked");}
  String action=command.getString("action");
  if(action.equals("home")||action.equals("back")){
   invalidate();boolean ok=performGlobalAction(action.equals("home")?GLOBAL_ACTION_HOME:GLOBAL_ACTION_BACK);
   return new JSONObject().put("status",ok?"action_accepted":"action_failed");
  }
  Screen s=screen();
  try{
   if(!s.json.getString("status").equals("ok"))return s.json;
   if(action.equals("inspect_ui")){
    gestures().observe(screenEvidence(s));
    snapshotId=UUID.randomUUID().toString();snapshotTime=SystemClock.elapsedRealtime();signature=s.json.toString();snapshotFrame=frame(s);snapshotInputFrame=inputFrame(s);
    return s.json.put("snapshotId",snapshotId).put("validForMs",30000).put("gestureDiagnostics",gestureDiagnostics());
   }
   if(snapshotId==null||!snapshotId.equals(command.optString("snapshotId"))||SystemClock.elapsedRealtime()-snapshotTime>30000){
    return rejected("Inspect the current screen again before acting.",snapshotId==null?"snapshot_missing":!snapshotId.equals(command.optString("snapshotId"))?"snapshot_replaced":"snapshot_expired",s,command.optInt("nodeId",-1));
   }
   boolean screenChanged=!signature.equals(s.json.toString());
   boolean screenChangeRejectionEnabled=getSharedPreferences("screen-change-guard",0).getBoolean("enabled",true);
   boolean changed=screenChangeRejectionEnabled&&screenChanged;
   int stableInput=-1;
   if(action.equals("set_text")||action.equals("ime_enter")){
    stableInput=EditorTargetGuard.resolve(snapshotInputFrame,inputFrame(s),command.getInt("nodeId"));
    if(stableInput<0)return rejected("The observed editor changed; inspect again before typing or submitting.","editor_target_changed",s,command.getInt("nodeId"));
   }
   AccessibilityNodeInfo node=null;
   if(action.equals("tap_label")||action.equals("touch_label")){
    String label=command.getString("label");Set<Integer> targets=LabelTargets.resolve(s.labels,label);
    if(command.has("nodeId")){
     targets=LabelTargets.disambiguate(targets,command.getInt("nodeId"));
     if(targets.isEmpty())return new JSONObject().put("status","label_target_mismatch").put("message","The chosen node does not match that label in the current snapshot.");
    }
    int matches=targets.size();
    if(matches!=1)return new JSONObject().put("status",matches==0?"label_not_found":"ambiguous_label").put("label",label).put("matches",matches);
    node=s.nodes.get(targets.iterator().next());
   }else node=s.nodes.get(stableInput>=0?stableInput:command.getInt("nodeId"));
   if(node==null||!node.isEnabled())return new JSONObject().put("status","node_unavailable");
   if(changed&&stableInput<0){
    boolean targeted=action.equals("tap")||action.equals("long_press")||action.equals("tap_label")||action.equals("touch_label")||action.equals("touch_point");
    int target=-1;for(Map.Entry<Integer,AccessibilityNodeInfo> entry:s.nodes.entrySet())if(entry.getValue()==node){target=entry.getKey();break;}
    Integer x=action.equals("touch_point")?command.getInt("x"):null,y=action.equals("touch_point")?command.getInt("y"):null;
    int margin=Math.max(1,(int)Math.ceil(24*getResources().getDisplayMetrics().density));
    if(!targeted||!TargetSnapshotGuard.allows(snapshotFrame,frame(s),target,x,y,margin)){
     return rejected("The target, surrounding controls, or screen identity changed. Inspect again before acting.",targeted?"target_guard_rejected":"full_snapshot_changed",s,target);
    }
   }
   Rect targetBounds=new Rect();node.getBoundsInScreen(targetBounds);
   if(targetBounds.isEmpty())return new JSONObject().put("status","target_not_visible");
   if(node.isPassword())return new JSONObject().put("status","password_field").put("message","Enter passwords directly on the phone.");
   boolean ok=false;
   if(action.equals("swipe")||action.equals("drag")){
    Rect area=new Rect(targetBounds);
    AccessibilityNodeInfo parent=node.getParent();
    while(parent!=null){
     Rect clip=new Rect();parent.getBoundsInScreen(clip);
     boolean visible=area.intersect(clip);AccessibilityNodeInfo next=parent.getParent();parent.recycle();parent=next;
     if(!visible){if(parent!=null)parent.recycle();return new JSONObject().put("status","target_not_visible");}
    }
    JSONArray b=command.getJSONArray(action.equals("drag")?"points":"bounds");int[] requested=new int[b.length()];
    for(int i=0;i<b.length();i++)requested[i]=b.getInt(i);
    int duration=action.equals("drag")?command.optInt("durationMs",600):350;
    int[] visible={area.left,area.top,area.right,area.bottom};
    float[] points=action.equals("drag")?DragPath.checked(visible,requested,duration):SwipePath.horizontal(visible,requested,command.getString("direction"));
    Path path=new Path();path.moveTo(points[0],points[1]);path.lineTo(points[2],points[3]);
    String traceId=beginGesture(command,s,node,area,points,duration);
    boolean queued=dispatchObservedGesture(new GestureDescription.Builder().addStroke(new GestureDescription.StrokeDescription(path,0,duration)).build(),traceId);
    invalidate();return new JSONObject().put("status",queued?"gesture_dispatched":"action_failed").put("gestureId",traceId).put("message","Gesture queued. Inspect and view a fresh screenshot to verify the intended change.");
   }
   if(action.equals("touch_label")||action.equals("touch_point")){
    // Constrain visual point taps to a fresh observed surface and its ancestors.
    Rect area=new Rect();node.getBoundsInScreen(area);
    AccessibilityNodeInfo parent=node.getParent();
    while(parent!=null){
     Rect clip=new Rect();parent.getBoundsInScreen(clip);
     boolean visible=area.intersect(clip);AccessibilityNodeInfo next=parent.getParent();parent.recycle();parent=next;
     if(!visible){if(parent!=null)parent.recycle();return new JSONObject().put("status","target_not_visible");}
    }
    if(area.isEmpty())return new JSONObject().put("status","target_not_visible");
    float x=area.exactCenterX(),y=area.exactCenterY();
    if(action.equals("touch_point")){
     int[] xy=TapPoint.checked(new int[]{area.left,area.top,area.right,area.bottom},command.getInt("x"),command.getInt("y"));x=xy[0];y=xy[1];
     for(AccessibilityNodeInfo other:s.nodes.values())if(other.isPassword()){Rect secret=new Rect();other.getBoundsInScreen(secret);if(secret.contains(xy[0],xy[1]))return new JSONObject().put("status","password_field");}
    }
    Path path=new Path();path.moveTo(x,y);
    String traceId=beginGesture(command,s,node,area,new float[]{x,y},70);
    boolean queued=dispatchObservedGesture(new GestureDescription.Builder().addStroke(new GestureDescription.StrokeDescription(path,0,70)).build(),traceId);
    invalidate();return new JSONObject().put("status",queued?"gesture_dispatched":"action_failed").put("gestureId",traceId).put("message","Touch was queued, not verified. Inspect the screen to verify the outcome before further action.");
   }
   if((action.equals("tap")||action.equals("tap_label"))&&node.isClickable()){
    // WebView virtual inputs can accept CLICK without acquiring input focus.
    if(node.isEditable()&&!node.isFocused())node.performAction(AccessibilityNodeInfo.ACTION_FOCUS);
    ok=node.performAction(AccessibilityNodeInfo.ACTION_CLICK);
   }
   else if(action.equals("long_press")&&node.isLongClickable()){
    ok=node.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK);
   }
   else if(action.equals("set_text")&&node.isEditable()){
    String value=command.getString("text");if(value.length()>4000)throw new IllegalArgumentException("Text too long");
    Bundle args=new Bundle();args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,value);
    ok=node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT,args);
   }else if(action.equals("ime_enter")&&node.isEditable()){
    if(!node.getActionList().contains(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER))return new JSONObject().put("status","unsupported_action").put("message","This field does not expose a keyboard submit action.");
    if(!node.isFocused()){
     node.performAction(AccessibilityNodeInfo.ACTION_FOCUS);
     node.refresh();
     if(!node.isFocused())return new JSONObject().put("status","input_not_focused").put("message","The input has not acquired keyboard focus. Focus it and inspect again before submitting.");
    }
    android.accessibilityservice.InputMethod editor=android.os.Build.VERSION.SDK_INT>=33?getInputMethod():null;
    android.view.inputmethod.EditorInfo info=editor==null?null:editor.getCurrentInputEditorInfo();
    android.accessibilityservice.InputMethod.AccessibilityInputConnection connection=editor==null?null:editor.getCurrentInputConnection();
    int editorAction=info==null?-1:KeyboardActionPolicy.select(text(node.getPackageName()),info.packageName,editor.getCurrentInputStarted(),info.imeOptions,info.actionId,info.actionLabel==null?"":info.actionLabel.toString());
    if(connection!=null&&editorAction>=0){
     connection.performEditorAction(editorAction);invalidate();
     return new JSONObject().put("status","action_accepted").put("submissionMethod","accessibility_input_connection").put("editorTargetValidated",true).put("originalNodeId",command.getInt("nodeId")).put("resolvedNodeId",stableInput).put("message","Requested the editor's declared action; inspect the screen to verify its result.");
    }
    ok=node.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.getId());
   }else if(action.equals("scroll")&&node.isScrollable()){
    String direction=command.getString("direction");
    if(!direction.equals("forward")&&!direction.equals("backward"))throw new IllegalArgumentException("Invalid scroll direction");
    ok=node.performAction(direction.equals("forward")?AccessibilityNodeInfo.ACTION_SCROLL_FORWARD:AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD);
   }
   invalidate();JSONObject result=new JSONObject().put("screenChangeRejectionEnabled",screenChangeRejectionEnabled).put("screenChanged",screenChanged).put("status",ok?"action_accepted":"action_failed").put("message","Inspect again to verify the result.");
   if(action.equals("ime_enter"))result.put("editorControl",editorDiagnostics().put("targetPackage",text(node.getPackageName())).put("targetFocused",node.isFocused()));
   if(action.equals("set_text"))result.put("typingValidation",!screenChangeRejectionEnabled?"screen_change_check_disabled":stableInput>=0?"stable_field":"unchanged_screen");
   return result;
  }finally{s.release();}
 }
}
