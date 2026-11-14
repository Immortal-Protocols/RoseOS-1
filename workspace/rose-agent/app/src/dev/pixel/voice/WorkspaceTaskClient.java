package dev.pixel.voice;

import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * Project 2: the phone side of App Core's workspace task handoff v1 (docs/APP-BUILDER.md section 4.3).
 * After the person confirms one Rose task on the workspace page, this sends one signed
 * {@code POST /workspace/v1/tasks} for it.
 *
 * <ul>
 *   <li><b>Off by default.</b> Nothing is sent unless the route is explicitly enabled (Gate). Until then the
 *   snapshot offers no Rose task at all, so none can be chosen.</li>
 *   <li><b>Only what the person confirmed.</b> The body is built from one of the phone's own task templates
 *   (label, goal), checked exactly as App Core's server checks it, with confirmed: true. Nothing else can be
 *   sent, and the composer never sees or writes the goal.</li>
 *   <li><b>Signed.</b> Each request is signed rose-auth-v1, the way the phone signs /hello
 *   (LocalRoseAuth.headers).</li>
 *   <li><b>Idempotent.</b> One requestId per tap. Every retry reuses it, and the server returns the first reply
 *   for a repeat, so a retry never starts a second task.</li>
 *   <li><b>Honest.</b> accepted and duplicate mean started, never done. Anything the phone cannot read
 *   definitely is uncertain.</li>
 * </ul>
 *
 * Pure (no Android types): the transport and the signer are injected, and WorkspaceTaskClientTest drives it
 * with fakes. Nothing here runs a task or opens a connection by itself.
 */
final class WorkspaceTaskClient {
 static final String PATH="/workspace/v1/tasks";
 /** App Core's own limits (rose/workspace_tasks.py LIMITS and ACTION_ID). */
 static final int REQUEST_ID=64,LABEL=60,GOAL=300,BODY_MAX=4*1024;
 static final Pattern ACTION_ID=Pattern.compile("task:[A-Za-z0-9._-]{1,48}");
 /**
  * Attempts in all. A retry follows only a 503 unavailable (the server says nothing started) or no response
  * at all. An unexpected reply (a 500, say) is reported as uncertain and not retried, because the server might
  * have started the task before failing to record it.
  */
 static final int ATTEMPTS=3;

 /** Off unless explicitly enabled; nothing in this source turns it on. */
 static final class Gate {
  private final boolean enabled;
  Gate(boolean enabled){this.enabled=enabled;}
  static Gate disabled(){return new Gate(false);}
  boolean enabled(){return enabled;}
 }

 /** One HTTP exchange; throws when no definite response arrived (connection lost, timeout). */
 interface Transport {
  Response post(String path,Map<String,String> headers,byte[] body)throws Exception;
  /** One GET (App Core's page-task read). Fakes that never read need not implement it. */
  default Response get(String path,Map<String,String> headers)throws Exception{throw new UnsupportedOperationException("get");}
 }
 static final class Response {
  final int status;final String body;
  Response(int status,String body){this.status=status;this.body=body;}
 }
 /** The four rose-auth-v1 headers for one POST, from LocalRoseAuth.headers with this phone's key. */
 interface Signer { Map<String,String> headers(String method,String path)throws Exception; }

 enum Kind { STARTED, ALREADY_STARTED, REFUSED, UNAVAILABLE, NOT_AUTHORIZED, UNCERTAIN, NOT_SENT, SENDING }

 /** What the page shows for the tap. started is never shown as done; the task's own events say that. */
 static final class Outcome {
  final Kind kind;final String reason,taskId;final int attempts;
  Outcome(Kind kind,String reason,String taskId,int attempts){this.kind=kind;this.reason=reason;this.taskId=taskId;this.attempts=attempts;}
  boolean started(){return kind==Kind.STARTED||kind==Kind.ALREADY_STARTED;}
  /** The card's line: never "done". */
  String status(){
   switch(kind){
    case STARTED:case ALREADY_STARTED:return "Started · in Rose's work";
    case UNAVAILABLE:return "Couldn't start · try again";
    case UNCERTAIN:return "Not sure it started · check Rose's work";
    case NOT_AUTHORIZED:return "Couldn't start · this phone isn't paired";
    case NOT_SENT:return "Rose tasks aren't available on this phone yet";
    case SENDING:return "Asking Rose…";
    default:return "Couldn't start";
   }
  }
 }

 /**
  * The request body for one confirmed task, or null when it must not be sent: not confirmed, or a template
  * App Core's server would refuse. Its fields are exactly schemaVersion, requestId, actionId, label, goal
  * and confirmed.
  */
 static String body(String requestId,WorkspaceSnapshot.Task task,boolean confirmed){
  if(!confirmed||task==null)return null;
  String actionId="task:"+task.templateId;
  if(!text(requestId,REQUEST_ID)||!ACTION_ID.matcher(actionId).matches()||!text(task.label,LABEL)||!text(task.goal,GOAL))return null;
  Map<String,Object> m=new LinkedHashMap<>();
  m.put("schemaVersion",1L);m.put("requestId",requestId);m.put("actionId",actionId);
  m.put("label",task.label.trim());m.put("goal",task.goal.trim());m.put("confirmed",Boolean.TRUE);
  String json=WorkspaceLayout.json(m);
  return json.getBytes(StandardCharsets.UTF_8).length<=BODY_MAX?json:null;
 }

 /** App Core's _text: a non-blank string within the limit (code points), without NUL, encodable as UTF-8. */
 static boolean text(String v,int limit){
  if(v==null||v.trim().isEmpty()||v.codePointCount(0,v.length())>limit||v.indexOf('\0')>=0)return false;
  for(int i=0;i<v.length();i++){char c=v.charAt(i);
   if(Character.isHighSurrogate(c)){if(i+1>=v.length()||!Character.isLowSurrogate(v.charAt(i+1)))return false;i++;}
   else if(Character.isLowSurrogate(c))return false;}
  return true;
 }

 /** One response, read as App Core's route answers (section 4.3 and rose/workspace_routes.py). */
 static Outcome read(Response r,int attempts){
  Map<String,Object> m=null;
  try{Object o=MiniJson.parse(r.body==null?"":r.body);if(o instanceof Map){@SuppressWarnings("unchecked") Map<String,Object> x=(Map<String,Object>)o;m=x;}}catch(RuntimeException ignored){}
  if(r.status==401)return new Outcome(Kind.NOT_AUTHORIZED,m!=null&&m.get("error") instanceof String?(String)m.get("error"):null,null,attempts);
  if(m==null||!Long.valueOf(1).equals(m.get("schemaVersion")))return new Outcome(Kind.UNCERTAIN,"unreadable_reply",null,attempts);
  Object status=m.get("status");Object taskId=m.get("taskId");String id=taskId instanceof String?(String)taskId:null;
  Object reason=m.get("reason");String why=reason instanceof String?(String)reason:null;
  if(r.status==200&&"accepted".equals(status))return new Outcome(Kind.STARTED,null,id,attempts);
  if(r.status==200&&"duplicate".equals(status))return new Outcome(Kind.ALREADY_STARTED,null,id,attempts);
  if(r.status==400&&"refused".equals(status))return new Outcome(Kind.REFUSED,why,null,attempts);
  if(r.status==503&&"unavailable".equals(status))return new Outcome(Kind.UNAVAILABLE,null,null,attempts);
  return new Outcome(Kind.UNCERTAIN,"unexpected_reply",null,attempts);
 }

 /**
  * App Core's authoritative state for one page task: a signed GET /workspace/v1/tasks/&lt;taskId&gt;, one
  * attempt, behind the same gate. Returns null on 404 unknown, any failure or anything malformed; that is no
  * change, and never a state. Called off the main thread, only for a taskId the phone was given.
  */
 WorkspaceTaskTracker.State lookup(String taskId){
  if(!gate.enabled()||taskId==null||!WorkspaceTaskTracker.TASK_ID.matcher(taskId).matches())return null;
  String path=PATH+"/"+taskId;
  try{return WorkspaceTaskTracker.read(transport.get(path,new LinkedHashMap<>(signer.headers("GET",path))),taskId);}
  catch(Exception e){return null;}
 }

 /**
  * App Core's read by requestId, for a tap whose reply was lost and which the reconnect snapshot no longer
  * holds: a signed GET /workspace/v1/tasks?requestId=&lt;id&gt; (the signature covers the query), one attempt,
  * behind the same gate. Read only: it never re-sends the POST, so a request that never arrived is never
  * started. Returns null on 404 unknown, any failure or anything malformed; that is no change.
  */
 WorkspaceTaskTracker.State lookupRequest(String requestId){
  if(!gate.enabled()||requestId==null||!WorkspaceTaskTracker.REQUEST_LOOKUP.matcher(requestId).matches())return null;
  String path=PATH+"?requestId="+requestId;
  try{return WorkspaceTaskTracker.readByRequest(transport.get(path,new LinkedHashMap<>(signer.headers("GET",path))),requestId);}
  catch(Exception e){return null;}
 }

 private final Gate gate;private final Transport transport;private final Signer signer;
 WorkspaceTaskClient(Gate gate,Transport transport,Signer signer){this.gate=gate;this.transport=transport;this.signer=signer;}

 /**
  * Starts one confirmed task: requestId is the tap's own id, fixed for every attempt. It retries only after a
  * 503 unavailable or no response at all, at most ATTEMPTS in all, always with the same requestId, so the
  * server answers a repeat with the first reply. Called off the main thread.
  */
 Outcome start(String requestId,WorkspaceSnapshot.Task task,boolean confirmed){
  if(!gate.enabled())return new Outcome(Kind.NOT_SENT,"disabled",null,0);
  String body=body(requestId,task,confirmed);
  if(body==null)return new Outcome(Kind.NOT_SENT,confirmed?"invalid_task":"not_confirmed",null,0);
  byte[] bytes=body.getBytes(StandardCharsets.UTF_8);
  Outcome last=null;
  for(int attempt=1;attempt<=ATTEMPTS;attempt++){
   Response r;
   try{
    Map<String,String> headers=new LinkedHashMap<>(signer.headers("POST",PATH));
    headers.put("Content-Type","application/json");
    r=transport.post(PATH,headers,bytes);
   }catch(Exception e){last=new Outcome(Kind.UNCERTAIN,"no_reply",null,attempt);continue;}
   if(r==null){last=new Outcome(Kind.UNCERTAIN,"no_reply",null,attempt);continue;}
   last=read(r,attempt);
   if(last.kind!=Kind.UNAVAILABLE)return last;
  }
  return last;
 }
}
