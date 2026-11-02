package dev.pixel.voice;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** The apps CURRENTLY running in the background under Rose's agent control, for the Rose's apps overview (#225/#156,
 * Nathan's phone acceptance: "precisely and exactly the apps currently running in the background under the agent's
 * control", the same set as the switcher's agent row). Membership comes only from authoritative ownership/lifecycle
 * snapshots, never from task history: a finished task does not mean its app stopped, and an app that stopped or was
 * released is gone at once. Pure logic, no Android types.
 *
 * The snapshot shape is Home's proposal for App Core's shared membership/preview contract (relayed by Chief); until it
 * is agreed, RoseConversationActivity feeds this from the controller's own get_state session (interim, one app at
 * most). A snapshot is always COMPLETE: it replaces what was shown, it is never merged with earlier ones.
 * <ul>
 * <li>registration: one source connection (a provider/listener registration, a reconnect, a re-pair, a process
 * restart is a new one). A new registration drops everything first, so nothing from before it is ever resurrected;
 * its first valid snapshot is applied whatever its generation.</li>
 * <li>generation: rises with every content change within a registration; an equal or lower one is ignored.</li>
 * <li>available=false (with a fixed reason word) clears the apps: unknown is never shown as running or as none.</li>
 * <li>A malformed snapshot (missing fields, duplicate entry ids, an unknown state, an incomplete preview handle) is
 * never partly applied: nothing is known until a valid one arrives.</li>
 * </ul> */
final class CurrentApps {
 private CurrentApps(){}

 /** Fixed reasons (never exception text or identifiers). */
 static final String NOT_REPORTED="not_reported",UNREADABLE="unreadable",LOCKED="locked",NOT_INSTALLED="not_installed",MALFORMED="malformed";
 /** The source could not attest that its list is every app under control: nothing is shown as if it were. */
 static final String INCOMPLETE="incomplete";
 static final String STATE_RUNNING="running",STATE_STARTING="starting",STATE_WAITING="waiting";

 /** How the overview may ask for this app's live content (PR340 preview params): the app's OWN controller session and
  * its controller task tag, from the same authoritative report as the app. Null when no safe preview route exists. */
 static final class Preview {
  final String sessionId,controllerTaskId;
  Preview(String sessionId,String controllerTaskId){this.sessionId=nz(sessionId);this.controllerTaskId=nz(controllerTaskId);}
  boolean complete(){return !sessionId.isEmpty()&&!controllerTaskId.isEmpty();}
 }
 /** One app running under agent control. entryId: stable while that app instance runs (never reused for another). */
 static final class App {
  final String entryId,packageName,state;final Preview preview;
  App(String entryId,String packageName,String state,Preview preview){this.entryId=nz(entryId);this.packageName=nz(packageName);this.state=nz(state);this.preview=preview;}
 }
 /** One complete report. */
 static final class Snapshot {
  final String registration;final long generation;final boolean available;final String reason;final List<App> apps;
  Snapshot(String registration,long generation,boolean available,String reason,List<App> apps){
   this.registration=nz(registration);this.generation=generation;this.available=available;this.reason=nz(reason);this.apps=apps;}
  static Snapshot unavailable(String registration,long generation,String reason){return new Snapshot(registration,generation,false,reason,Collections.<App>emptyList());}
 }
 enum State { AVAILABLE, EMPTY, UNAVAILABLE }

 /** Applies snapshots by the rules above; what the overview shows. One per overview owner (the panel). */
 static final class Tracker {
  private String registration="";private long generation=Long.MIN_VALUE;private boolean received;
  private List<App> apps=Collections.emptyList();private String reason=NOT_REPORTED;
  /** Registrations replaced by a newer one: their late reports are ignored, so nothing they listed comes back. */
  private final java.util.LinkedHashSet<String> retired=new java.util.LinkedHashSet<>();
  /** Nothing is known: teardown, or the overview's source was replaced. */
  synchronized void reset(){if(!registration.isEmpty())retire(registration);registration="";generation=Long.MIN_VALUE;received=false;apps=Collections.emptyList();reason=NOT_REPORTED;}
  /** true when what is shown changed. */
  synchronized boolean apply(Snapshot s){
   if(s==null)return false;
   String before=key();
   if(retired.contains(s.registration))return false;
   if(!s.registration.equals(registration)){
    // A new source connection: forget everything from the old one before looking at the new report.
    if(!registration.isEmpty())retire(registration);
    registration=s.registration;generation=Long.MIN_VALUE;received=false;apps=Collections.emptyList();reason=NOT_REPORTED;
   }else if(received&&s.generation<=generation)return false;
   generation=s.generation;
   String bad=s.registration.isEmpty()?MALFORMED:validate(s);
   if(bad!=null){received=false;apps=Collections.emptyList();reason=bad;return !key().equals(before);}
   received=true;
   if(!s.available){apps=Collections.emptyList();reason=s.reason.isEmpty()?UNREADABLE:s.reason;}
   else{apps=Collections.unmodifiableList(new ArrayList<>(s.apps));reason="";}
   return !key().equals(before);
  }
  private void retire(String r){retired.add(r);while(retired.size()>64)retired.remove(retired.iterator().next());}
  synchronized String registration(){return registration;}
  synchronized State state(){return !received||!reason.isEmpty()?State.UNAVAILABLE:apps.isEmpty()?State.EMPTY:State.AVAILABLE;}
  synchronized List<App> apps(){return apps;}
  /** Empty unless UNAVAILABLE. */
  synchronized String reason(){return state()==State.UNAVAILABLE?(received?reason:reason.isEmpty()?NOT_REPORTED:reason):"";}
  synchronized String key(){StringBuilder b=new StringBuilder(registration).append('|').append(received).append('|').append(reason);
   for(App a:apps)b.append('|').append(a.entryId).append(':').append(a.packageName).append(':').append(a.state).append(':').append(a.preview==null?"-":a.preview.sessionId+"/"+a.preview.controllerTaskId);
   return b.toString();}
 }

 /** null when valid, otherwise MALFORMED. */
 static String validate(Snapshot s){
  if(s.available&&s.apps==null)return MALFORMED;
  if(!s.available)return s.apps==null||s.apps.isEmpty()?null:MALFORMED;
  Set<String> ids=new HashSet<>();
  for(App a:s.apps){
   if(a==null||a.entryId.isEmpty()||a.packageName.isEmpty()||!ids.add(a.entryId))return MALFORMED;
   if(!STATE_RUNNING.equals(a.state)&&!STATE_STARTING.equals(a.state)&&!STATE_WAITING.equals(a.state))return MALFORMED;
   if(a.preview!=null&&!a.preview.complete())return MALFORMED;
  }
  return null;
 }

 /** The interim source (until App Core's attested contract): the controller's own get_state session, which the
  * controller starts, owns and closes. With no live session there is no session display, so nothing is under
  * control (authoritatively empty). A live session reports only its target, never the apps behind it on its display,
  * so its membership is not attested complete: INCOMPLETE (the explicit unavailable/loading state), never a partial
  * list presented as every app. Read failures keep their fixed word. verified is no longer used for membership: live
  * view comes with the attested contract's own handle. */
 static Snapshot fromController(String registration,long generation,String failure,String status,String sessionId,String controllerTaskId,
   String targetPackage,WorkspaceOverview.Binding verified){
  if(failure!=null&&!failure.isEmpty())return Snapshot.unavailable(registration,generation,failure);
  switch(nz(status)){
   case "idle":case "stopped":case "closed":case "user_stopped":case "cleanup_failed":case "failed":case "rejected":case "target_not_allowed":case "consent_required":
    return new Snapshot(registration,generation,true,"",Collections.<App>emptyList());
   case "ready":case "launching":case "starting":case "scheduled":case "blocked":
    return Snapshot.unavailable(registration,generation,INCOMPLETE);
   default:return Snapshot.unavailable(registration,generation,UNREADABLE);
  }
 }
 /** The overview's words for an unavailable state (fixed; never identifiers). */
 static String text(String reason){
  switch(nz(reason)){
   case LOCKED:return "Unlock your phone to see Rose’s apps.";
   case NOT_INSTALLED:return "Rose’s background control isn’t on this phone.";
   case MALFORMED:return "Rose’s apps couldn’t be read.";
   case NOT_REPORTED:return "Rose is checking which apps it’s using.";
   case INCOMPLETE:return "Rose can’t confirm every app it’s using yet.";
   default:return "Rose couldn’t check which apps it’s using.";
  }
 }
 /** One reply of the controller's phone-local call("current_apps") (App Core PR355): status live|none|locked,
  * sessionId, taskId, generation, apps[{packageName, front, target}], read as fields (null when absent). Proposed
  * additions this also reads, absent in PR355: instance (the controller process's epoch, so a restart is a new
  * registration even though its generation starts again), sessionStatus (ready|blocked|launching, for each card's
  * state) and complete (true only when the list is attested to be every app on the session's device). Without
  * complete:true neither a list nor an empty "none" is shown as authoritative (INCOMPLETE, the loading/unavailable
  * state); App Core's incompleteReason enum is not settled, so its value is not read. Without sessionStatus the read is
  * malformed (no state is
  * guessed). The preview handle goes only to the topmost front app: PR340 frames the session display, so the apps
  * behind it say live view isn't available. registrationFallback names this reader when instance is absent. */
 static final class ReplyApp { final String packageName;final boolean front,target;
  ReplyApp(String packageName,boolean front,boolean target){this.packageName=packageName;this.front=front;this.target=target;} }
 static Snapshot fromReply(String registrationFallback,String status,String sessionId,String taskId,Long generation,List<ReplyApp> apps,
   String instance,String sessionStatus,Boolean complete){
  String reg=nz(instance).isEmpty()?registrationFallback:"controller:"+instance;long gen=generation==null?Long.MIN_VALUE:generation;
  if(status==null||generation==null)return Snapshot.unavailable(reg,gen,UNREADABLE);
  switch(status){
   case "locked":return Snapshot.unavailable(reg,gen,LOCKED);
   // An empty list is authoritative only when attested complete (App Core's correction: the reply could not tell
   // incomplete from genuinely empty); anything else is unavailable, never "none running".
   case "none":if(apps!=null&&!apps.isEmpty())return Snapshot.unavailable(reg,gen,MALFORMED);
    return Boolean.TRUE.equals(complete)?new Snapshot(reg,gen,true,"",Collections.<App>emptyList()):Snapshot.unavailable(reg,gen,INCOMPLETE);
   case "live":break;
   default:return Snapshot.unavailable(reg,gen,UNREADABLE);
  }
  if(apps==null||nz(sessionId).isEmpty())return Snapshot.unavailable(reg,gen,MALFORMED);
  String state;
  switch(nz(sessionStatus)){
   case "ready":state=STATE_RUNNING;break;case "blocked":state=STATE_WAITING;break;case "launching":state=STATE_STARTING;break;
   default:return Snapshot.unavailable(reg,gen,MALFORMED);
  }
  if(!Boolean.TRUE.equals(complete))return Snapshot.unavailable(reg,gen,INCOMPLETE);
  List<App> out=new ArrayList<>();boolean previewed=false;
  for(ReplyApp a:apps){
   if(a==null)return Snapshot.unavailable(reg,gen,MALFORMED);
   Preview p=null;
   if(a.front&&!previewed&&!nz(taskId).isEmpty()){p=new Preview(sessionId,taskId);previewed=true;}
   out.add(new App(sessionId+"/"+nz(a.packageName),a.packageName,state,p));
  }
  for(App a:out)if(a.packageName.isEmpty())return Snapshot.unavailable(reg,gen,MALFORMED);
  return new Snapshot(reg,gen,true,"",out);
 }
 static String nz(String s){return s==null?"":s;}
}
