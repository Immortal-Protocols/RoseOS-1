package dev.pixel.voice;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * Project 2: what became of each Rose task the person confirmed on the workspace page. App Core owns the
 * page-task identity and its authoritative state, agreed with App Core on 2026-11-14:
 * <ul>
 *   <li>POST /workspace/v1/tasks returns taskId "wt-&lt;16 hex&gt;" with accepted and duplicate;</li>
 *   <li>state v1 is {schemaVersion 1, taskId, requestId, actionId, state, detail, phoneTasks, createdAt, updatedAt};</li>
 *   <li>it arrives live as {kind: "workspace_task"} on /ws/events, is replayed for the 20 most recent on
 *   reconnect, and can be read with GET /workspace/v1/tasks/&lt;taskId&gt;.</li>
 * </ul>
 * This side keeps the phone's record of each tap and decides what its card shows.
 *
 * <ul>
 *   <li><b>Never inferred.</b> Acceptance is "Started", never done. Only App Core's own state for this taskId
 *   moves the card.</li>
 *   <li><b>Strict.</b> A state that is malformed, out of bounds, or for an unknown or other task is ignored.</li>
 *   <li><b>Ordered and final.</b> An update older than the one held is dropped. done, failed, cancelled and
 *   uncertain are final and never change. A final with an equal or later updatedAt wins over running.</li>
 *   <li><b>Recoverable.</b> Records are saved, so a restart keeps them. When a reply was lost there is a
 *   requestId but no taskId; replayed states, which carry requestId, re-associate that record.</li>
 * </ul>
 * Pure (no Android types).
 */
final class WorkspaceTaskTracker {
 static final Pattern TASK_ID=Pattern.compile("wt-[0-9a-f]{16}");
 static final int KEEP=20,PHONE_TASKS=8,PHONE_TASK_ID=128,DETAIL=200;
 static final String RUNNING="running",DONE="done",FAILED="failed",CANCELLED="cancelled",UNCERTAIN="uncertain";
 private static final List<String> STATES=java.util.Arrays.asList(RUNNING,DONE,FAILED,CANCELLED,UNCERTAIN);
 private static final List<String> PHONE_STATUSES=java.util.Arrays.asList(RUNNING,DONE,FAILED,CANCELLED);

 /** App Core's page-task state v1, read strictly. */
 static final class State {
  final String taskId,requestId,actionId,state,detail;final long updatedAt;
  State(String taskId,String requestId,String actionId,String state,String detail,long updatedAt){this.taskId=taskId;this.requestId=requestId;this.actionId=actionId;this.state=state;this.detail=detail;this.updatedAt=updatedAt;}
  boolean finalState(){return !RUNNING.equals(state);}
 }

 /** One tap: what the phone sent, what it was told, and the last state it has seen. */
 static final class Record {
  final String actionId,requestId;String taskId;String state;String detail="";long updatedAt=-1;
  /** The POST's own outcome kind (WorkspaceTaskClient.Kind name), until a state arrives. */
  String sent;
  Record(String actionId,String requestId){this.actionId=actionId;this.requestId=requestId;}
  boolean finalState(){return state!=null&&!RUNNING.equals(state);}
 }

 private final List<Record> records=new ArrayList<>();

 /** A tap was confirmed: its record, before anything is sent. A newer tap of the same action replaces it. */
 Record tapped(String actionId,String requestId){
  for(int i=records.size()-1;i>=0;i--)if(records.get(i).actionId.equals(actionId))records.remove(i);
  Record r=new Record(actionId,requestId);records.add(r);
  while(records.size()>KEEP)records.remove(0);
  return r;
 }

 /** The POST's outcome. Only a well-formed taskId from accepted or duplicate is kept. */
 void replied(String requestId,WorkspaceTaskClient.Outcome o){
  Record r=byRequest(requestId);if(r==null||o==null)return;
  r.sent=o.kind.name();
  if(o.started()&&o.taskId!=null&&TASK_ID.matcher(o.taskId).matches()&&r.taskId==null)r.taskId=o.taskId;
 }

 /**
  * One state from App Core: from a live or replayed workspace_task event, or the GET. It applies to the
  * record with its taskId, or to the record with its requestId when that record has no taskId yet (a lost
  * reply). Returns the record it changed, or null when it was ignored.
  */
 Record apply(State s){
  if(s==null)return null;
  Record r=byTask(s.taskId);
  if(r==null){Record q=byRequest(s.requestId);if(q!=null&&q.taskId==null&&q.actionId.equals(s.actionId)){q.taskId=s.taskId;r=q;}}
  if(r==null)return null;
  if(r.finalState())return null;                 // final never changes
  if(s.updatedAt<r.updatedAt)return null;        // older than what is held
  if(s.updatedAt==r.updatedAt&&!s.finalState())return null; // a final at the same time wins over running
  r.state=s.state;r.detail=s.detail;r.updatedAt=s.updatedAt;
  return r;
 }

 /** The line for an action's card. Acceptance alone is never done. */
 String line(String actionId){
  Record r=byAction(actionId);
  if(r==null)return "Rose task · asks first";
  if(r.state!=null){
   switch(r.state){
    case RUNNING:return "Rose is working";
    case DONE:return "Done · in Rose's work";
    case FAILED:return "Failed · in Rose's work";
    case CANCELLED:return "Stopped · in Rose's work";
    default:return "Not sure how it ended · check Rose's work";
   }
  }
  if(r.sent==null)return "Asking Rose…";
  switch(r.sent){
   case "STARTED":case "ALREADY_STARTED":return "Started · in Rose's work";
   case "REFUSED":return "Couldn't start";
   case "UNAVAILABLE":return "Couldn't start · try again";
   case "NOT_AUTHORIZED":return "Couldn't start · this phone isn't paired";
   case "NOT_SENT":return "Rose tasks aren't available on this phone yet";
   default:return "Not sure it started · check Rose's work";
  }
 }

 /**
  * Shown with Ask again. The first request may have reached Rose even though no reply came, so asking again can
  * start the same task twice: the new request has a new requestId, and App Core cannot tell the two apart.
  */
 static final String ASK_AGAIN_WARNING="Rose may already have this task from your first request, and it may still be running. "
  +"Asking again sends a new request, which could start it a second time. Check Rose's work first.";

 /**
  * Whether the sheet may offer Ask again: only when the send's outcome is unknown (UNCERTAIN), no taskId ever
  * came, and no state for it has arrived. Nothing asks again on its own; it is the person's new confirmation.
  */
 boolean mayAskAgain(String actionId){
  Record r=byAction(actionId);
  return r!=null&&r.taskId==null&&r.state==null&&"UNCERTAIN".equals(r.sent);
 }

 /** Records whose taskId is known but whose end is not: the GET answers these after a restart or a gap. */
 List<String> openTaskIds(){List<String> ids=new ArrayList<>();for(Record r:records)if(r.taskId!=null&&!r.finalState())ids.add(r.taskId);return ids;}
 Record byAction(String actionId){for(int i=records.size()-1;i>=0;i--)if(records.get(i).actionId.equals(actionId))return records.get(i);return null;}
 Record byTask(String taskId){if(taskId==null)return null;for(Record r:records)if(taskId.equals(r.taskId))return r;return null;}
 Record byRequest(String requestId){if(requestId==null)return null;for(Record r:records)if(requestId.equals(r.requestId))return r;return null;}

 // ---------- App Core's wire ----------

 /** A page-task state v1, or null if anything in it is malformed or out of bounds. */
 static State state(Object o){
  if(!(o instanceof Map))return null;Map<?,?> m=(Map<?,?>)o;
  if(!Long.valueOf(1).equals(m.get("schemaVersion")))return null;
  String taskId=str(m.get("taskId")),requestId=str(m.get("requestId")),actionId=str(m.get("actionId")),state=str(m.get("state"));
  Object detailRaw=m.get("detail");String detail=detailRaw==null||detailRaw==MiniJson.NULL?"":str(detailRaw);
  if(taskId==null||!TASK_ID.matcher(taskId).matches())return null;
  if(requestId==null||requestId.isEmpty()||requestId.length()>WorkspaceTaskClient.REQUEST_ID)return null;
  if(actionId==null||!WorkspaceTaskClient.ACTION_ID.matcher(actionId).matches())return null;
  if(state==null||!STATES.contains(state))return null;
  if(detail==null||detail.codePointCount(0,detail.length())>DETAIL)return null;
  if(!(m.get("updatedAt") instanceof Number)||!(m.get("createdAt") instanceof Number))return null;
  double updated=((Number)m.get("updatedAt")).doubleValue();
  if(Double.isNaN(updated)||updated<0)return null;
  Object pt=m.get("phoneTasks");
  if(!(pt instanceof List)||((List<?>)pt).size()>PHONE_TASKS)return null;
  for(Object e:(List<?>)pt){
   if(!(e instanceof Map))return null;Map<?,?> t=(Map<?,?>)e;
   // App Core writes an all-digit phone task id as a JSON integer and any other id as a string.
   Object rawId=t.get("id");String st=str(t.get("status"));
   boolean idOk=rawId instanceof Long?(Long)rawId>=0:rawId instanceof String&&!((String)rawId).isEmpty()&&((String)rawId).length()<=PHONE_TASK_ID;
   if(!idOk||st==null||!PHONE_STATUSES.contains(st))return null;
  }
  // Epoch seconds, compared in milliseconds so fractions keep their order.
  return new State(taskId,requestId,actionId,state,detail.trim(),Math.round(updated*1000));
 }

 /** A /ws/events message: the state it carries when it is a workspace_task event, else null. */
 static State event(String raw){
  try{Object o=MiniJson.parse(raw);if(!(o instanceof Map))return null;Map<?,?> m=(Map<?,?>)o;
   if(!"workspace_task".equals(m.get("kind")))return null;return state(m.get("task"));}
  catch(RuntimeException e){return null;}
 }

 /** The GET's reply: the state on 200, nothing on 404 unknown or anything else (no change). */
 static State read(WorkspaceTaskClient.Response r,String taskId){
  if(r==null||r.status!=200)return null;
  try{State s=state(MiniJson.parse(r.body));return s!=null&&s.taskId.equals(taskId)?s:null;}catch(RuntimeException e){return null;}
 }

 private static String str(Object v){return v instanceof String?(String)v:null;}

 // ---------- storage ----------

 String toJson(){
  List<Object> out=new ArrayList<>();
  for(Record r:records){Map<String,Object> m=new LinkedHashMap<>();m.put("actionId",r.actionId);m.put("requestId",r.requestId);m.put("taskId",r.taskId);m.put("state",r.state);m.put("detail",r.detail);m.put("updatedAt",r.updatedAt);m.put("sent",r.sent);out.add(m);}
  Map<String,Object> m=new LinkedHashMap<>();m.put("v",1L);m.put("records",out);return WorkspaceLayout.json(m);
 }

 /** The saved records, read strictly; a corrupt store gives none, and a damaged record is skipped alone. */
 static WorkspaceTaskTracker fromJson(String saved){
  WorkspaceTaskTracker t=new WorkspaceTaskTracker();
  try{
   Object o=MiniJson.parse(saved==null||saved.isEmpty()?"{}":saved);if(!(o instanceof Map))return t;Map<?,?> m=(Map<?,?>)o;
   if(!Long.valueOf(1).equals(m.get("v"))||!(m.get("records") instanceof List))return t;
   for(Object e:(List<?>)m.get("records")){
    if(!(e instanceof Map))continue;Map<?,?> r=(Map<?,?>)e;
    String actionId=str(r.get("actionId")),requestId=str(r.get("requestId"));
    if(actionId==null||requestId==null||!WorkspaceTaskClient.ACTION_ID.matcher(actionId).matches())continue;
    Record rec=new Record(actionId,requestId);
    String taskId=str(r.get("taskId"));if(taskId!=null&&TASK_ID.matcher(taskId).matches())rec.taskId=taskId;
    String state=str(r.get("state"));if(state!=null&&STATES.contains(state))rec.state=state;
    String detail=str(r.get("detail"));if(detail!=null)rec.detail=detail;
    if(r.get("updatedAt") instanceof Long)rec.updatedAt=(Long)r.get("updatedAt");
    rec.sent=str(r.get("sent"));
    t.records.add(rec);
   }
   while(t.records.size()>KEEP)t.records.remove(0);
  }catch(RuntimeException e){return new WorkspaceTaskTracker();}
  return t;
 }
}
