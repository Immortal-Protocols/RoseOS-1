package dev.pixel.voice;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Rose's managed background workspace overview (MANAGED-WORKSPACE-OVERVIEW-001), as pure logic so it is testable on
 * the host. Layout and wording follow the selected HiFi's Rose's apps view (prototypes/rose-hifi at ce126f56:
 * views.js LABEL/OBSERVE, renderAgents, task strip and Stop sheet), bound to the real contract instead of its
 * simulated windows:
 * <ul>
 * <li>The controller has ONE actual workspace (one session, one target app). It is shown as one card, never as
 * several simultaneous windows; several logical tasks may use it one after another (PR318: one workspace generation
 * per process, reused by follow-up tasks).</li>
 * <li>Logical tasks are listed as rows with their real goal, activity, result and status; finished tasks keep
 * their result and need no live session.</li>
 * <li>Watch is offered for a task only with an explicit association {controllerTaskId, sessionId, targetPackage}
 * that the task itself carries, made in the same server lifetime and pairing as the current link, and equal to
 * the live controller read. Whichever workspace happens to be open is never taken as a task's binding.</li>
 * <li>Stop captures the selected task's identity (id plus scope). It shows Stopping until an authoritative answer
 * for that same identity; a timeout is shown as unknown, never as stopped; an answer or a task with the same id
 * from another server lifetime or pairing is a different task and is never stopped or settled by it.</li>
 * </ul>
 * Opening, leaving (Back/Home) and re-entering the overview change nothing: no task or workspace is started,
 * stopped or brought to the foreground by this class. Reason texts are fixed and never carry an identifier. */
final class WorkspaceOverview {
 private WorkspaceOverview(){}

 // ---------- Fixed texts (HiFi views.js LABEL/OBSERVE wording, real-contract reasons) ----------
 static final String TITLE="Rose’s apps";
 static final String EMPTY="Rose isn’t using any apps right now.";
 static final String WATCH_OK="Watch Rose’s workspace for this task";
 static final String WATCH_DISCONNECTED="Rose isn’t connected, so its workspace can’t be checked.";
 static final String WATCH_CHECKING="Rose is checking its background workspace.";
 static final String WATCH_UNREADABLE="Rose couldn’t check its background workspace.";
 static final String WATCH_FINISHED="This task is finished. Its result is shown here.";
 static final String WATCH_UNLINKED="Rose hasn’t linked this task to its workspace, so Watch isn’t offered.";
 static final String WATCH_OLD_LINK="This task was linked before Rose reconnected, so its workspace can’t be checked.";
 static final String WATCH_NO_WORKSPACE="Rose’s workspace isn’t open right now.";
 static final String WATCH_NOT_READY="Rose’s workspace isn’t ready to watch yet.";
 static final String WATCH_MOVED_ON="Rose’s workspace has moved on from this task.";
 static final String STOP_SENDING="Sending Stop…";
 static final String STOP_STOPPING="Stopping…";
 static final String STOP_UNKNOWN="Rose hasn’t confirmed the stop. This task may still be running.";
 static final String STOP_DONE="Stopped.";
 static final String STOP_NOT_RUNNING="That task wasn’t running any more, so nothing was stopped.";
 static final String STOP_REFUSED="Rose couldn’t stop this task. It may still be running.";
 static final String STOP_REPLACED="That task was replaced, so nothing else was stopped.";

 // ---------- Inputs ----------
 /** One server lifetime and pairing, as the link reports them. A task, binding or answer belongs to the scope it
  * was received in; an empty part is unknown and never matches anything (so it never verifies a binding). */
 static final class Scope {
  static final Scope UNKNOWN=new Scope("","");
  final String server,pairing;
  Scope(String server,String pairing){this.server=server==null?"":server;this.pairing=pairing==null?"":pairing;}
  boolean known(){return !server.isEmpty()&&!pairing.isEmpty();}
  boolean same(Scope o){return o!=null&&known()&&o.known()&&server.equals(o.server)&&pairing.equals(o.pairing);}
 }
 /** The explicit association a logical task carries to the controller session it uses. */
 static final class Binding {
  final String controllerTaskId,sessionId,targetPackage;final Scope scope;
  Binding(String controllerTaskId,String sessionId,String targetPackage,Scope scope){
   this.controllerTaskId=nz(controllerTaskId);this.sessionId=nz(sessionId);this.targetPackage=nz(targetPackage);this.scope=scope==null?Scope.UNKNOWN:scope;}
  boolean complete(){return !controllerTaskId.isEmpty()&&!sessionId.isEmpty()&&!targetPackage.isEmpty()&&scope.known();}
 }
 /** Logical task status, normalised from the producer (PR318 Task.status: running, done, failed, cancelled; a done
  * task holding ready_to_send waits for the owner's yes on its draft). */
 enum Status { RUNNING, NEEDS_YOU, DONE, FAILED, CANCELLED, UNKNOWN }
 static Status status(String producerStatus,boolean readyToSend){
  if(producerStatus==null)return Status.UNKNOWN;
  switch(producerStatus){
   case "running":return Status.RUNNING;
   case "done":return readyToSend?Status.NEEDS_YOU:Status.DONE;
   case "failed":return Status.FAILED;
   case "cancelled":return Status.CANCELLED;
   default:return Status.UNKNOWN;
  }
 }
 /** One logical task as received: id and scope are its identity; binding is null when the producer gave none. */
 static final class Task {
  final String id;final Scope scope;final Status status;final String goal,activity,result;final long started,finished;final Binding binding;
  Task(String id,Scope scope,Status status,String goal,String activity,String result,long started,long finished,Binding binding){
   this.id=nz(id);this.scope=scope==null?Scope.UNKNOWN:scope;this.status=status==null?Status.UNKNOWN:status;
   this.goal=nz(goal).trim();this.activity=nz(activity).trim();this.result=nz(result).trim();this.started=started;this.finished=finished;this.binding=binding;}
  Identity identity(){return new Identity(id,scope);}
  boolean active(){return status==Status.RUNNING||status==Status.NEEDS_YOU;}
 }
 /** A task identity: its id within one scope. The same id in another scope is another task. */
 static final class Identity {
  final String id;final Scope scope;
  Identity(String id,Scope scope){this.id=nz(id);this.scope=scope==null?Scope.UNKNOWN:scope;}
  boolean same(Identity o){return o!=null&&!id.isEmpty()&&id.equals(o.id)&&scope.same(o.scope);}
  boolean sameId(Identity o){return o!=null&&!id.isEmpty()&&id.equals(o.id);}
 }
 /** Whether the controller's state could be read, by fixed category; when not OK the fields say nothing. */
 enum Read { OK, CHECKING, UNREADABLE }
 /** The one live controller workspace as read now (get_state: status, sessionId, taskId, targetPackage). */
 static final class Workspace {
  static final Workspace CHECKING=new Workspace(Read.CHECKING,"","","","","",Scope.UNKNOWN);
  static final Workspace UNREADABLE=new Workspace(Read.UNREADABLE,"","","","","",Scope.UNKNOWN);
  final Read read;final String status,sessionId,controllerTaskId,targetPackage,appLabel;final Scope scope;
  Workspace(Read read,String status,String sessionId,String controllerTaskId,String targetPackage,String appLabel,Scope scope){
   this.read=read==null?Read.UNREADABLE:read;this.status=nz(status);this.sessionId=nz(sessionId);this.controllerTaskId=nz(controllerTaskId);
   this.targetPackage=nz(targetPackage);this.appLabel=nz(appLabel).trim();this.scope=scope==null?Scope.UNKNOWN:scope;}
  static Workspace read(String status,String sessionId,String controllerTaskId,String targetPackage,String appLabel,Scope scope){
   return new Workspace(Read.OK,status,sessionId,controllerTaskId,targetPackage,appLabel,scope);}
  boolean live(){return read==Read.OK&&!sessionId.isEmpty()&&("ready".equals(status)||"blocked".equals(status)||"launching".equals(status));}
 }

 // ---------- Stop ----------
 enum StopPhase { NONE, SENDING, STOPPING, UNKNOWN, STOPPED, NOT_RUNNING, REFUSED, REPLACED }
 /** The answer to a Stop request, for the identity it was sent for. */
 enum Ack { ACCEPTED, STOPPED, NOT_RUNNING, REFUSED }
 /** One Stop for one captured identity. Held by the screen's owner across Back/Home/re-entry. */
 static final class Stop {
  static final long TIMEOUT_MS=15000;
  Identity target;StopPhase phase=StopPhase.NONE;long sentAt;
  /** Captures the selected identity; refused (false) while a Stop for any task is still unresolved, or when the
   * identity is not verified (unknown scope). */
  boolean request(Identity selected,long now){
   if(selected==null||selected.id.isEmpty()||!selected.scope.known()||pending())return false;
   target=selected;phase=StopPhase.SENDING;sentAt=now;return true;
  }
  boolean pending(){return phase==StopPhase.SENDING||phase==StopPhase.STOPPING||phase==StopPhase.UNKNOWN;}
  /** An answer counts only for the captured identity; a duplicate or late answer never moves a settled Stop. */
  void ack(Identity about,Ack ack){
   if(target==null||!target.same(about)||!pending()||ack==null)return;
   switch(ack){
    case ACCEPTED:if(phase==StopPhase.SENDING)phase=StopPhase.STOPPING;break;
    case STOPPED:phase=StopPhase.STOPPED;break;
    case NOT_RUNNING:phase=StopPhase.NOT_RUNNING;break;
    default:phase=StopPhase.REFUSED;
   }
  }
  /** No authoritative answer within the timeout: unknown, never stopped. A later answer still settles it. */
  void tick(long now){if((phase==StopPhase.SENDING||phase==StopPhase.STOPPING)&&now-sentAt>=TIMEOUT_MS)phase=StopPhase.UNKNOWN;}
  /** Settles from the task list: the same identity reaching an end state is authoritative; the same id in another
   * scope means the selected task was replaced, and the replacement is left alone. */
  void observe(List<Task> tasks){
   if(target==null||!pending())return;
   for(Task t:tasks){
    if(target.same(t.identity())){
     if(t.status==Status.CANCELLED)phase=StopPhase.STOPPED;
     else if(t.status==Status.DONE||t.status==Status.FAILED)phase=StopPhase.NOT_RUNNING;
     return;
    }
   }
   for(Task t:tasks)if(target.sameId(t.identity())){phase=StopPhase.REPLACED;return;}
  }
  String note(){
   switch(phase){
    case SENDING:return STOP_SENDING;case STOPPING:return STOP_STOPPING;case UNKNOWN:return STOP_UNKNOWN;
    case STOPPED:return STOP_DONE;case NOT_RUNNING:return STOP_NOT_RUNNING;case REFUSED:return STOP_REFUSED;
    case REPLACED:return STOP_REPLACED;default:return "";
   }
  }
 }

 // ---------- Output ----------
 /** Row/card state, as the HiFi's status dot (style.css .sdot data-state). */
 enum Dot { RUNNING, ATTENTION, STOPPING, STOPPED, DONE, IDLE }
 enum Place { DISCONNECTED, CHECKING, UNREADABLE, NONE, STARTING, OPEN, WAITING, ENDED, UNKNOWN }
 /** The one real workspace card. */
 static final class Card {
  final Place place;final Dot dot;final String label,detail;final int linkedTasks;
  Card(Place place,Dot dot,String label,String detail,int linkedTasks){this.place=place;this.dot=dot;this.label=label;this.detail=detail;this.linkedTasks=linkedTasks;}
 }
 static final class Row {
  final Identity identity;final Dot dot;final String title,state,detail,result,watchReason,stopNote;final boolean canWatch,canStop,selected,linked;
  Row(Identity identity,Dot dot,String title,String state,String detail,String result,boolean canWatch,String watchReason,boolean canStop,String stopNote,boolean selected,boolean linked){
   this.identity=identity;this.dot=dot;this.title=title;this.state=state;this.detail=detail;this.result=result;this.canWatch=canWatch;
   this.watchReason=watchReason;this.canStop=canStop;this.stopNote=stopNote;this.selected=selected;this.linked=linked;}
  /** The spoken name of the row: title, state and, when Watch can't open, why. */
  String description(){return title+", "+state+(canWatch?"":". "+watchReason);}
 }
 static final class Model {
  final boolean connected;final Card card;final List<Row> rows;final Row selected;final String empty;
  Model(boolean connected,Card card,List<Row> rows,Row selected,String empty){this.connected=connected;this.card=card;this.rows=rows;this.selected=selected;this.empty=empty;}
  /** A stable key: equal keys render the same, so re-entry and repeated reads redraw nothing. */
  String key(){
   StringBuilder b=new StringBuilder().append(connected).append('|').append(card.place).append(card.dot).append(card.label).append('|').append(card.detail);
   for(Row r:rows)b.append('\n').append(r.identity.id).append(r.dot).append(r.state).append(r.detail).append(r.result).append(r.canWatch).append(r.canStop).append(r.stopNote).append(r.selected).append(r.linked);
   return b.toString();
  }
 }
 static final int MAX_ROWS=6;

 /** The overview from the link's state, the controller read, the task list and the owner's selection/Stop.
  * connected: the link to Rose is up; scope: the current server lifetime and pairing. The only state it touches is
  * the owner's Stop, settled from the task list (Stop.observe); it never starts, stops or opens anything. */
 static Model of(boolean connected,Scope scope,Workspace ws,List<Task> tasks,Identity selected,Stop stop){
  if(ws==null)ws=Workspace.UNREADABLE;if(scope==null)scope=Scope.UNKNOWN;
  List<Task> list=new ArrayList<>();if(tasks!=null)for(Task t:tasks)if(t!=null&&!t.id.isEmpty())list.add(t);
  if(stop!=null)stop.observe(list);
  // Active work first, then the most recent results; the current scope's tasks before older ones.
  final Scope cur=scope;
  Collections.sort(list,(a,b)->{
   if(a.active()!=b.active())return a.active()?-1:1;
   boolean ac=a.scope.same(cur),bc=b.scope.same(cur);if(ac!=bc)return ac?-1:1;
   long ta=Math.max(a.finished,a.started),tb=Math.max(b.finished,b.started);return Long.compare(tb,ta);
  });
  if(list.size()>MAX_ROWS)list=new ArrayList<>(list.subList(0,MAX_ROWS));
  boolean stopPending=stop!=null&&stop.pending();
  List<Row> rows=new ArrayList<>();Row sel=null;int linked=0;
  for(Task t:list){
   String reason=watchReason(connected,scope,ws,t);
   // One workspace, used one task after another: of the active tasks carrying the same binding only the newest is
   // the session's current user, so an older task never watches its successor's work.
   if(reason.isEmpty()&&newerOnSameBinding(t,list))reason=WATCH_MOVED_ON;
   boolean canWatch=reason.isEmpty();
   if(canWatch)linked++;
   boolean mine=stop!=null&&stop.target!=null&&stop.target.same(t.identity());
   String stopNote=mine?stop.note():"";
   boolean stopping=mine&&stop.pending();
   // Only running work is stoppable: a task holding a draft for the owner's yes has already finished (PR318 done).
   boolean canStop=connected&&t.status==Status.RUNNING&&t.scope.same(scope)&&!stopPending;
   Dot dot=stopping?Dot.STOPPING:dot(t.status);
   String state=stopping?STOP_STOPPING:label(t.status);
   Row r=new Row(t.identity(),dot,title(t),state,t.active()?t.activity:"",t.result,canWatch,canWatch?WATCH_OK:reason,canStop,stopNote,
    selected!=null&&selected.same(t.identity()),canWatch);
   rows.add(r);if(r.selected)sel=r;
  }
  return new Model(connected,card(connected,ws,linked),Collections.unmodifiableList(rows),sel,rows.isEmpty()?EMPTY:"");
 }

 /** Why Watch can't open for this task (empty when it can): only its own explicit binding, from the current scope,
  * equal to the live read in all three fields. */
 static String watchReason(boolean connected,Scope scope,Workspace ws,Task t){
  if(!connected)return WATCH_DISCONNECTED;
  if(!t.active())return WATCH_FINISHED;
  if(t.binding==null||!t.binding.complete())return WATCH_UNLINKED;
  if(!t.binding.scope.same(scope)||!t.scope.same(scope))return WATCH_OLD_LINK;
  if(ws.read==Read.CHECKING)return WATCH_CHECKING;
  if(ws.read!=Read.OK)return WATCH_UNREADABLE;
  if(!ws.scope.same(scope))return WATCH_OLD_LINK;
  if("idle".equals(ws.status)||ws.sessionId.isEmpty())return WATCH_NO_WORKSPACE;
  if(!ws.live())return WATCH_NOT_READY;
  if(!ws.sessionId.equals(t.binding.sessionId)||!ws.controllerTaskId.equals(t.binding.controllerTaskId)||!ws.targetPackage.equals(t.binding.targetPackage))return WATCH_MOVED_ON;
  return "";
 }
 static boolean newerOnSameBinding(Task t,List<Task> all){
  for(Task o:all)if(o!=t&&o.active()&&o.binding!=null&&o.started>t.started&&o.binding.scope.same(t.binding.scope)
   &&o.binding.sessionId.equals(t.binding.sessionId)&&o.binding.controllerTaskId.equals(t.binding.controllerTaskId))return true;
  return false;
 }
 /** The binding Watch opens with for the selected task, re-checked against a fresh read at the tap; null when the
  * selection is gone, replaced or no longer verified. Never substitutes the open workspace's session. */
 static Binding watchBinding(Identity selected,boolean connected,Scope scope,Workspace fresh,List<Task> tasks){
  if(selected==null||tasks==null)return null;
  List<Task> list=new ArrayList<>();for(Task t:tasks)if(t!=null)list.add(t);
  for(Task t:list)if(selected.same(t.identity()))
   return watchReason(connected,scope,fresh==null?Workspace.UNREADABLE:fresh,t).isEmpty()&&!newerOnSameBinding(t,list)?t.binding:null;
  return null;
 }

 static Card card(boolean connected,Workspace ws,int linked){
  if(!connected)return new Card(Place.DISCONNECTED,Dot.IDLE,"Not connected","Rose isn’t connected, so its workspace can’t be checked.",0);
  if(ws.read==Read.CHECKING)return new Card(Place.CHECKING,Dot.IDLE,"Checking…",WATCH_CHECKING,0);
  if(ws.read!=Read.OK)return new Card(Place.UNREADABLE,Dot.IDLE,"Can’t check",WATCH_UNREADABLE,0);
  String app=ws.appLabel.isEmpty()?"an app":ws.appLabel;
  String used=linked==0?"Not linked to a task shown here.":linked==1?"Used by one task shown here.":"Used by "+linked+" tasks shown here, one after another.";
  switch(ws.status){
   case "idle":return new Card(Place.NONE,Dot.IDLE,"No workspace open",EMPTY,0);
   case "ready":return new Card(Place.OPEN,Dot.RUNNING,"Open in "+app,used,linked);
   case "launching":case "starting":case "scheduled":return new Card(Place.STARTING,Dot.RUNNING,"Starting "+app,"Rose’s workspace is still starting.",linked);
   case "blocked":return new Card(Place.WAITING,Dot.ATTENTION,"Waiting for unlock","Rose’s workspace in "+app+" continues after you unlock.",linked);
   case "consent_required":return new Card(Place.WAITING,Dot.ATTENTION,"Waiting for your consent","Android is waiting for your consent before Rose’s workspace can start.",0);
   case "target_not_allowed":return new Card(Place.ENDED,Dot.STOPPED,"Not allowed","This phone doesn’t let Rose use that app in the background.",0);
   case "stopped":case "closed":case "user_stopped":case "cleanup_failed":return new Card(Place.ENDED,Dot.STOPPED,"Workspace closed","Rose’s workspace has stopped.",0);
   case "failed":case "rejected":return new Card(Place.ENDED,Dot.STOPPED,"Couldn’t start","Rose’s workspace couldn’t start.",0);
   default:return new Card(Place.UNKNOWN,Dot.IDLE,"Unknown state","Rose’s workspace is in a state Rose doesn’t recognise.",0);
  }
 }
 static Dot dot(Status s){
  switch(s){case RUNNING:return Dot.RUNNING;case NEEDS_YOU:return Dot.ATTENTION;case DONE:return Dot.DONE;case FAILED:case CANCELLED:return Dot.STOPPED;default:return Dot.IDLE;}
 }
 static String label(Status s){
  switch(s){case RUNNING:return "Working";case NEEDS_YOU:return "Needs your OK";case DONE:return "Done";case FAILED:return "Couldn’t finish";case CANCELLED:return "Stopped";default:return "Unknown";}
 }
 static String title(Task t){return t.goal.isEmpty()?"Rose’s task":t.goal;}
 private static String nz(String s){return s==null?"":s;}
}
