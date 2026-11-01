package dev.pixel.voice;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/** The local Rose task list for the workspace overview, exactly as App Core's PR321 VoiceProviders local() hands it to
 * onLocalTasks: the full current collection while live, [] once on close, replacement or unpairing; each item
 * {key, id, server, contract, connection, kind, goal, status, detail, result, background, onScreen, awaitingSend,
 * started, finished, binding}. key (server#id, or @started#id without a server) is the identity; binding is the
 * producer's explicit {controllerTaskId, sessionId, targetPackage}, present only under contract 1 with a server.
 * Nothing here infers a binding, adds fields, or sends anything: it is read-only.
 *
 * accept() is the one entry point for the page-to-native handoff (VoiceService's bridge, Core-owned); until that is
 * wired nothing arrives and the overview says no tasks have reached this screen. Pure Java (MiniJson), testable on
 * the host. */
final class WorkspaceOverviewFeed {
 private WorkspaceOverviewFeed(){}
 static final int MAX_CHARS=200000,MAX_TASKS=40;
 private static volatile List<WorkspaceOverview.Task> latest=Collections.emptyList();
 private static volatile String connection="";

 /** The handoff entry point (contract for App Core's VoiceService bridge):
  * <ul>
  * <li>raw: exactly JSON.stringify(list) of one onLocalTasks delivery, unmodified (including "[]").</li>
  * <li>Thread: any (the WebView JavaBridge thread is fine). Non-blocking, no Android calls; the parsed list is
  * published as one immutable volatile snapshot that the overview reads on the UI thread.</li>
  * <li>Validation: at most MAX_CHARS characters and MAX_TASKS items; strict JSON (MiniJson: no duplicate keys or
  * trailing data, depth 32); a top-level array of objects, each with a non-empty key and status, all from one
  * connection. Anything else is dropped whole and the previous list stays; nothing throws.</li>
  * <li>Lifecycle: process-wide, replaced by each delivery. "[]" (close, replacement, unpairing) clears it. The bridge
  * should also pass "[]" when its page or local connection is torn down without a delivery, so no earlier list
  * outlives its connection.</li>
  * </ul> */
 static void accept(String raw){
  Parsed p=parse(raw);if(p==null)return;
  latest=p.tasks;connection=p.connection;
 }
 static List<WorkspaceOverview.Task> tasks(){return latest;}
 /** The scope of the current delivery: the newest task's server within this connection; unknown when empty or when
  * that task has no server (then nothing verifies, so Watch/Stop stay unavailable). */
 static WorkspaceOverview.Scope scope(){return scopeOf(latest);}
 static void clearForTest(){latest=Collections.emptyList();connection="";}

 static final class Parsed { final List<WorkspaceOverview.Task> tasks;final String connection;
  Parsed(List<WorkspaceOverview.Task> tasks,String connection){this.tasks=tasks;this.connection=connection;} }

 /** One onLocalTasks delivery, or null when it is not a list of valid PR321 tasks from one connection. */
 static Parsed parse(String raw){
  if(raw==null||raw.length()>MAX_CHARS)return null;
  Object v;try{v=MiniJson.parse(raw);}catch(RuntimeException e){return null;}
  if(!(v instanceof List))return null;
  List<?> items=(List<?>)v;if(items.size()>MAX_TASKS)return null;
  List<WorkspaceOverview.Task> out=new ArrayList<>();String conn=null;
  for(Object o:items){
   if(!(o instanceof Map))return null;Map<?,?> m=(Map<?,?>)o;
   String key=str(m,"key"),c=str(m,"connection"),status=str(m,"status");
   if(key.isEmpty()||status.isEmpty())return null;
   if(conn==null)conn=c;else if(!conn.equals(c))return null; // one delivery is one connection
   String server=str(m,"server");
   WorkspaceOverview.Scope scope=new WorkspaceOverview.Scope(server,c);
   boolean awaiting=Boolean.TRUE.equals(m.get("awaitingSend"));
   WorkspaceOverview.Binding binding=null;Object b=m.get("binding");
   if(b instanceof Map&&num(m,"contract")==1&&!server.isEmpty()){
    Map<?,?> bm=(Map<?,?>)b;binding=new WorkspaceOverview.Binding(str(bm,"controllerTaskId"),str(bm,"sessionId"),str(bm,"targetPackage"),scope);
    if(!binding.complete())binding=null;
   }
   out.add(new WorkspaceOverview.Task(key,scope,WorkspaceOverview.status(status,awaiting),str(m,"goal"),str(m,"detail"),str(m,"result"),
    millis(m,"started"),millis(m,"finished"),binding));
  }
  return new Parsed(Collections.unmodifiableList(out),conn==null?"":conn);
 }
 static WorkspaceOverview.Scope scopeOf(List<WorkspaceOverview.Task> tasks){
  WorkspaceOverview.Task newest=null;
  for(WorkspaceOverview.Task t:tasks)if(newest==null||t.started>newest.started)newest=t;
  return newest==null?WorkspaceOverview.Scope.UNKNOWN:newest.scope;
 }
 private static String str(Map<?,?> m,String k){Object v=m.get(k);return v instanceof String?(String)v:"";}
 private static double num(Map<?,?> m,String k){Object v=m.get(k);return v instanceof Number?((Number)v).doubleValue():Double.NaN;}
 /** The producer's epoch seconds (time.time()) as milliseconds; 0 when absent or null. */
 private static long millis(Map<?,?> m,String k){double d=num(m,k);return Double.isNaN(d)||Double.isInfinite(d)?0L:Math.round(d*1000);}
}
