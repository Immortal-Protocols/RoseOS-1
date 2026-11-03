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
  /** The controller session this app is bound to ("" when unbound or lingering), and the Stop handle for that session
   * (both ids, from its binding; null when Home may not stop it). Neither ever decides membership. */
  final String sessionId;final Preview stop;
  /** The controller session's own status as App Core reported it for this app ("" when unbound, lingering or not
   * reported). It decides only whether the app is authoritatively running and its state word, never membership. */
  final String sessionStatus;
  App(String entryId,String packageName,String state,Preview preview){this(entryId,packageName,state,preview,"",null,"");}
  App(String entryId,String packageName,String state,Preview preview,String sessionId,Preview stop){this(entryId,packageName,state,preview,sessionId,stop,"");}
  App(String entryId,String packageName,String state,Preview preview,String sessionId,Preview stop,String sessionStatus){
   this.entryId=nz(entryId);this.packageName=nz(packageName);this.state=nz(state);this.preview=preview;this.sessionId=nz(sessionId);this.stop=stop;this.sessionStatus=nz(sessionStatus);}
  /** Authoritatively running (#410 runningEdge "authoritatively running only"; rose.workButton.working): the app's
   * controller session is reported exactly "ready". Every other status (launching, starting, scheduled, blocked,
   * consent_required, cleanup_failed, anything unknown) and every unbound or lingering app is not, even though an
   * owned_devices report still lists it as under control (STATE_RUNNING membership). */
  boolean authoritativelyRunning(){return STATE_RUNNING.equals(state)&&SESSION_READY.equals(sessionStatus);}
 }
 /** The one controller session status that means running (App Core's ready). */
 static final String SESSION_READY="ready";
 static boolean sessionRunning(String sessionStatus){return SESSION_READY.equals(sessionStatus);}
 /** A card's state word (#410 manager.stateChip, hidden only while authoritatively running): the truthful transition
  * or hold state for everything else, never "Running" for an app whose session isn't ready. Fixed words only. */
 static String cardState(App a){
  if(a.authoritativelyRunning())return "Running";
  if(STATE_WAITING.equals(a.state))return "Waiting";
  if(STATE_STARTING.equals(a.state))return "Starting";
  switch(a.sessionStatus){
   case "launching":case "starting":case "scheduled":return "Starting";
   case "blocked":case "consent_required":return "Waiting";
   // A failed close: the session is still held and its apps may still be open (App Core: session_unreleased).
   case "cleanup_failed":return "Still open";
   // Unbound, lingering or an unknown status: attested under Rose's control, but no running session to show.
   default:return "Open";
  }
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
   for(App a:apps)b.append('|').append(a.entryId).append(':').append(a.packageName).append(':').append(a.state).append(':').append(a.preview==null?"-":a.preview.sessionId+"/"+a.preview.controllerTaskId)
    .append(':').append(a.sessionId).append(':').append(a.stop==null?"-":a.stop.sessionId+"/"+a.stop.controllerTaskId).append(':').append(a.sessionStatus);
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
   case "idle":case "stopped":case "closed":case "user_stopped":case "failed":case "rejected":case "target_not_allowed":case "consent_required":
    return new Snapshot(registration,generation,true,"",Collections.<App>emptyList());
   // A session whose close failed is still held and its device may still run apps (App Core: session_unreleased).
   case "cleanup_failed":
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
  /** The controller session the snapshot joins this app to (string or null when unbound/lingering). */
  final String sessionId;
  ReplyApp(String packageName,boolean front,boolean target){this(packageName,front,target,null);}
  ReplyApp(String packageName,boolean front,boolean target,String sessionId){this.packageName=packageName;this.front=front;this.target=target;this.sessionId=sessionId;} }
 /** One controller slot's binding (agreed two-app contract, sessions[]): controls and live view only, never membership. */
 static final class Binding {
  final int slot;final String sessionId,taskId,targetPackage,sessionStatus;final boolean bound,frontKnown;
  Binding(int slot,String sessionId,String taskId,String targetPackage,String sessionStatus,boolean bound,boolean frontKnown){
   this.slot=slot;this.sessionId=nz(sessionId);this.taskId=nz(taskId);this.targetPackage=nz(targetPackage);this.sessionStatus=nz(sessionStatus);this.bound=bound;this.frontKnown=frontKnown;}
 }
 static Snapshot fromReply(String registrationFallback,String status,String sessionId,String taskId,Long generation,List<ReplyApp> apps,
   String instance,String sessionStatus,Boolean complete){
  return fromReply(registrationFallback,status,sessionId,taskId,generation,apps,instance,sessionStatus,complete,null);
 }
 /** The source App Core reports the members from (PR379): the platform's owned-device snapshot, independent of any
  * session. Only for it is an attested complete live list shown whatever the informational sessionStatus (a failed
  * close or a missing session must not hide apps still under control). */
 static final String SOURCE_OWNED_DEVICES="owned_devices";
 /** source: the reply's member source (owned_devices, platform or session_windows; null when absent). */
 static Snapshot fromReply(String registrationFallback,String status,String sessionId,String taskId,Long generation,List<ReplyApp> apps,
   String instance,String sessionStatus,Boolean complete,String source){
  String reg=nz(instance).isEmpty()?registrationFallback:"controller:"+instance;long gen=generation==null?Long.MIN_VALUE:generation;
  boolean owned=SOURCE_OWNED_DEVICES.equals(source);
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
  // The owner snapshot is session-independent: its live members need no session id; every other source does.
  if(apps==null||!owned&&nz(sessionId).isEmpty())return Snapshot.unavailable(reg,gen,MALFORMED);
  // PR355 (41140ec): a live session is never attested complete (behind_front_unlisted, windows_unreadable or
  // blocked), so it is the explicit unavailable state before anything else is read from it. complete:false always
  // means cannot confirm, whatever the source.
  if(!Boolean.TRUE.equals(complete))return Snapshot.unavailable(reg,gen,INCOMPLETE);
  // An open session whose apps all left (PR361: handover is not close) is attested empty: no state to show.
  if(apps.isEmpty())return new Snapshot(reg,gen,true,"",Collections.<App>emptyList());
  String state;
  switch(nz(sessionStatus)){
   case "ready":state=STATE_RUNNING;break;case "blocked":state=STATE_WAITING;break;case "launching":state=STATE_STARTING;break;
   // owned_devices: the apps are attested running under control; the session's own state is only informational.
   default:if(!owned)return Snapshot.unavailable(reg,gen,MALFORMED);state=STATE_RUNNING;
  }
  // Live view only for the topmost front app of an explicitly associated, live and ready session (PR340 frames that
  // session's display); every other member, and every member otherwise, says live view isn't available.
  boolean previewable="ready".equals(sessionStatus)&&!nz(sessionId).isEmpty()&&!nz(taskId).isEmpty();
  String owner=nz(sessionId).isEmpty()?"owned":sessionId;
  List<App> out=new ArrayList<>();boolean previewed=false;
  for(ReplyApp a:apps){
   if(a==null)return Snapshot.unavailable(reg,gen,MALFORMED);
   Preview p=null;
   if(a.front&&!previewed&&previewable){p=new Preview(sessionId,taskId);previewed=true;}
   out.add(new App(owner+"/"+nz(a.packageName),a.packageName,state,p,"",null,owner.equals("owned")?"":nz(sessionStatus)));
  }
  for(App a:out)if(a.packageName.isEmpty())return Snapshot.unavailable(reg,gen,MALFORMED);
  return new Snapshot(reg,gen,true,"",out);
 }
 /** The agreed two-app owned_devices reply (contract A): membership is the owner-attested snapshot only (apps[] with
  * complete), bindings (sessions[]) give each app's controls and live view and never decide membership.
  * <ul>
  * <li>registration: "controller:"+controllerEpoch (a controller restart is a new registration); without it the reader's
  * own. generation is the whole-response generation, ordered within an epoch.</li>
  * <li>complete:false (locked, unreadable, incomplete device, duplicate_package...) is cannot-confirm; none+complete is
  * genuinely empty; a complete live list is shown in full, lingering and unbound apps included.</li>
  * <li>An app is bound when its sessionId names a bound binding. Its state is that binding's sessionStatus (ready
  * running, blocked waiting, launching starting; anything else running); unbound or lingering apps are running with no
  * live view, Stop or Watch.</li>
  * <li>Live view: front:true as reported, on a bound session that is ready with frontKnown and both ids. Exactly one
  * front per session is promised; if a session reports more than one, none of them gets live view (Home never picks).</li>
  * <li>Stop: a bound session with both ids (an untagged slot is left to its own notification).</li>
  * </ul> */
 static Snapshot fromBindings(String registrationFallback,String status,Long generation,List<ReplyApp> apps,List<Binding> bindings,
   String controllerEpoch,Boolean complete){
  String reg=nz(controllerEpoch).isEmpty()?registrationFallback:"controller:"+controllerEpoch;long gen=generation==null?Long.MIN_VALUE:generation;
  if(status==null||generation==null)return Snapshot.unavailable(reg,gen,UNREADABLE);
  switch(status){
   case "locked":return Snapshot.unavailable(reg,gen,LOCKED);
   case "none":if(apps!=null&&!apps.isEmpty())return Snapshot.unavailable(reg,gen,MALFORMED);
    return Boolean.TRUE.equals(complete)?new Snapshot(reg,gen,true,"",Collections.<App>emptyList()):Snapshot.unavailable(reg,gen,INCOMPLETE);
   case "live":break;
   default:return Snapshot.unavailable(reg,gen,UNREADABLE);
  }
  if(apps==null)return Snapshot.unavailable(reg,gen,MALFORMED);
  if(!Boolean.TRUE.equals(complete))return Snapshot.unavailable(reg,gen,INCOMPLETE);
  java.util.Map<String,Binding> bySession=new java.util.HashMap<>();
  if(bindings!=null)for(Binding b:bindings)if(b!=null&&b.bound&&!b.sessionId.isEmpty())bySession.put(b.sessionId,b);
  java.util.Map<String,Integer> fronts=new java.util.HashMap<>();Set<String> seen=new HashSet<>();
  for(ReplyApp a:apps){
   if(a==null||nz(a.packageName).isEmpty()||!seen.add(a.packageName))return Snapshot.unavailable(reg,gen,MALFORMED);
   if(a.front&&!nz(a.sessionId).isEmpty())fronts.put(a.sessionId,fronts.containsKey(a.sessionId)?fronts.get(a.sessionId)+1:1);
  }
  List<App> out=new ArrayList<>();
  for(ReplyApp a:apps){
   Binding b=nz(a.sessionId).isEmpty()?null:bySession.get(a.sessionId);
   String state=STATE_RUNNING;Preview preview=null,stop=null;
   if(b!=null){
    switch(b.sessionStatus){case "blocked":state=STATE_WAITING;break;case "launching":state=STATE_STARTING;break;default:state=STATE_RUNNING;}
    boolean ids=!b.sessionId.isEmpty()&&!b.taskId.isEmpty();
    if(ids)stop=new Preview(b.sessionId,b.taskId);
    if(ids&&a.front&&b.frontKnown&&"ready".equals(b.sessionStatus)&&fronts.get(b.sessionId)==1)preview=new Preview(b.sessionId,b.taskId);
   }
   out.add(new App((b==null?"owned":b.sessionId)+"/"+a.packageName,a.packageName,state,preview,b==null?"":b.sessionId,stop,b==null?"":b.sessionStatus));
  }
  return new Snapshot(reg,gen,true,"",out);
 }

 /** One owner Stop of one controller session, captured at the tap (both ids). It is STOPPED only on an authoritative
  * confirmation for exactly that pair: owner_stop's confirmed:true, or a routed summary entry whose slot status is
  * stopped with stopReason user and stoppedSessionId/stoppedTaskId equal to the captured ids. An accepted request,
  * cleanup_failed, stale_session or a reused slot never confirms; no confirmation by TIMEOUT_MS is UNKNOWN (it may still
  * be running), and a later confirmation still settles it. A confirmed Stop does not remove the apps: they stay listed
  * until the owner snapshot drops their device. */
 static final class SessionStop {
  enum Phase { SENDING, STOPPING, STOPPED, UNKNOWN }
  static final long TIMEOUT_MS=15000;
  final String sessionId,taskId;final long startedAt;Phase phase=Phase.SENDING;
  SessionStop(String sessionId,String taskId,long now){this.sessionId=nz(sessionId);this.taskId=nz(taskId);this.startedAt=now;}
  /** Waiting for its confirmation (an unknown Stop may be tried again). */
  boolean pending(){return phase==Phase.SENDING||phase==Phase.STOPPING;}
  /** owner_stop's own reply: confirmed (null when absent or the call failed). Anything but true waits for a summary. */
  void answered(Boolean confirmed){if(phase==Phase.STOPPED)return;if(Boolean.TRUE.equals(confirmed))phase=Phase.STOPPED;else if(phase==Phase.SENDING)phase=Phase.STOPPING;}
  /** One routed sessions-summary entry (slot status, stopReason, stoppedSessionId, stoppedTaskId) as read. */
  void summary(String slotStatus,String stopReason,String stoppedSessionId,String stoppedTaskId){
   if(phase==Phase.STOPPED)return;
   if("stopped".equals(slotStatus)&&"user".equals(stopReason)&&sessionId.equals(nz(stoppedSessionId))&&taskId.equals(nz(stoppedTaskId))&&!sessionId.isEmpty())phase=Phase.STOPPED;
  }
  void tick(long now){if((phase==Phase.SENDING||phase==Phase.STOPPING)&&now-startedAt>=TIMEOUT_MS)phase=Phase.UNKNOWN;}
  /** Still worth asking for a confirmation (a late one may still settle an unknown Stop, until twice the timeout). */
  boolean checking(long now){return phase!=Phase.STOPPED&&now-startedAt<2*TIMEOUT_MS;}
 }
 static String nz(String s){return s==null?"":s;}
}
