package dev.pixel.backgroundcontrol;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/** Two-app routing (OS Core MULTI-SESSION-DESIGN, agreed): which of the controller's two stable slots a request names,
 * and every refusal, as pure decisions. No Android types; the controller supplies each slot's facts under its lock.
 *
 * - Slots: slot 0 is the legacy session (name pixel-voice-background, notification 102, byte-identical to before);
 *   slot 1 exists only for an explicit start mode "alongside" (name pixel-voice-background-1, notification 103). Each
 *   slot's PR381/PR383 admission is per name, so one slot's admission never clears the other slot's handles.
 * - Capability: sessionLimit from getOwnedAutomatedDevices is a capability, not free slots, admission or membership.
 *   Only an Integer 2 enables alongside; absent, any other value or a read failure means 1.
 * - Targeting: a request that names a session goes to that slot. A legacy request (no sessionId) goes to slot 0, except
 *   where that would guess between two live sessions: a stop with both live is refused (session_required). Commands
 *   and previews already require a sessionId; one naming no slot keeps its legacy refusal in slot 0.
 * - One app per session: Rose's prototype sessions may launch any app (patched launchApplication), so the platform does
 *   not keep an app in one session. The controller refuses a start or open_app for an app that is the other live slot's
 *   target or an attested member of it, and reports a package attested in both sessions as incomplete in both
 *   (app_in_two_sessions), never guessing an owner. */
final class MultiSession {
 static final int SLOTS=2;
 static final String ALONGSIDE="alongside";
 static final String MULTI_SESSION_UNAVAILABLE="multi_session_unavailable", SESSION_LIMIT="session_limit",
  APP_IN_OTHER_SESSION="app_in_other_session", SESSION_REQUIRED="session_required", STALE_SESSION="stale_session",
  INVALID_MODE="invalid_mode", APP_IN_TWO_SESSIONS="app_in_two_sessions", SESSION_CLOSED="session_closed",
  MEMBERSHIP_UNAVAILABLE="membership_unavailable", DEVICE_UNATTESTED="device_unattested";

 /** The stable per-slot session name; slot 0 is exactly the single-session name. */
 static String name(int slot){return slot==0?"pixel-voice-background":"pixel-voice-background-"+slot;}
 /** The owner-supplied notification id of the slot's session; the platform posts each session's notification with it. */
 static int notificationId(int slot){return 102+slot;}
 /** sessionLimit as read from the owned-device Bundle: 2 only for an Integer 2; null (absent or unreadable) or anything
  * else is 1. A capability only: it never says a slot is free or that a request will be admitted. */
 static int sessionLimit(Object value){return value instanceof Integer&&((Integer)value).intValue()==2?2:1;}

 /** What the controller tells this class about one slot, read under its lock. */
 static final class Slot {
  final String sessionId,target,status;final boolean occupied;final List<String> attested;
  /** occupied: the slot holds or is creating a session (a session object, cleanup_failed, or starting through ready).
   *  attested: the packages Android attests for the slot's open session (getAutomatedApps), or empty if unknown. */
  Slot(String sessionId,String target,String status,boolean occupied,List<String> attested){
   this.sessionId=sessionId;this.target=target;this.status=status;this.occupied=occupied;
   this.attested=attested==null?Collections.<String>emptyList():attested;
  }
  boolean holds(String pkg){return occupied&&(pkg.equals(target)||attested.contains(pkg));}
 }
 /** Whether a slot's status is a session that holds or is creating platform state (anything but a finished one). */
 static boolean occupied(boolean hasSession,String status){
  if(hasSession)return true;
  switch(status==null?"":status){case "starting":case "consent_required":case "launching":case "ready":case "blocked":case "cleanup_failed":return true;default:return false;}
 }

 /** A routing decision: a slot index, or a refusal word (with the number of this controller's occupied slots). */
 static final class Route {
  final int slot;final String refusal;final int ownLive;
  private Route(int slot,String refusal,int ownLive){this.slot=slot;this.refusal=refusal;this.ownLive=ownLive;}
  static Route to(int slot){return new Route(slot,null,0);}
  static Route refuse(String why,int ownLive){return new Route(-1,why,ownLive);}
  boolean refused(){return refusal!=null;}
 }
 static int live(Slot[] slots){int n=0;for(Slot s:slots)if(s.occupied)n++;return n;}
 static int bySession(Slot[] slots,String sessionId){
  if(sessionId==null||sessionId.isEmpty())return -1;
  for(int i=0;i<slots.length;i++)if(sessionId.equals(slots[i].sessionId))return i;
  return -1;
 }

 /** A start. Legacy (no mode) is today's behaviour in slot 0 (it replaces slot 0's app), refused only when the app is
  * held by the other slot. "alongside" adds the app in a free slot, never stopping another; an app already the target
  * of a slot goes back to that slot (today's same-app behaviour), an app attested in another slot is refused. */
 static Route start(Slot[] slots,String pkg,String mode,int sessionLimit){
  int own=live(slots);
  if(mode==null||mode.isEmpty()){
   for(int i=1;i<slots.length;i++)if(slots[i].holds(pkg))return Route.refuse(APP_IN_OTHER_SESSION,own);
   return Route.to(0);
  }
  if(!ALONGSIDE.equals(mode))return Route.refuse(INVALID_MODE,own);
  if(sessionLimit<2)return Route.refuse(MULTI_SESSION_UNAVAILABLE,own);
  for(int i=0;i<slots.length;i++)if(slots[i].occupied&&pkg.equals(slots[i].target)){
   for(int j=0;j<slots.length;j++)if(j!=i&&slots[j].holds(pkg))return Route.refuse(APP_IN_OTHER_SESSION,own);
   return Route.to(i);
  }
  for(Slot s:slots)if(s.holds(pkg))return Route.refuse(APP_IN_OTHER_SESSION,own);
  for(int i=0;i<slots.length;i++)if(!slots[i].occupied)return Route.to(i);
  return Route.refuse(SESSION_LIMIT,own);
 }
 /** command and preview: the named slot; a name no slot holds stays in slot 0, whose own check refuses it as before. */
 static Route target(Slot[] slots,String sessionId){int i=bySession(slots,sessionId);return Route.to(i<0?0:i);}
 /** get_state: the named slot; a name no slot holds is stale; no name is slot 0 (today's reply). */
 static Route state(Slot[] slots,String sessionId){
  if(sessionId==null||sessionId.isEmpty())return Route.to(0);
  int i=bySession(slots,sessionId);return i<0?Route.refuse(STALE_SESSION,live(slots)):Route.to(i);
 }
 /** The agent's stop: the named slot only; no name with two live sessions is refused rather than guessed; no name
  * otherwise is today's stop of the one live slot (slot 0 when none is live). A name no slot holds stops nothing. */
 static Route stop(Slot[] slots,String sessionId){
  int own=live(slots);
  if(sessionId!=null&&!sessionId.isEmpty()){int i=bySession(slots,sessionId);return i<0?Route.refuse(STALE_SESSION,own):Route.to(i);}
  if(own>1)return Route.refuse(SESSION_REQUIRED,own);
  for(int i=0;i<slots.length;i++)if(slots[i].occupied)return Route.to(i);
  return Route.to(0);
 }
 /** The owner's Stop from a session's notification: that session only. Without a session id (an older notification)
  * it is slot 0, as before; a session id no slot holds (an ended session's notification) stops nothing. */
 static int ownerStop(Slot[] slots,String sessionId){
  if(sessionId==null)return 0;
  return bySession(slots,sessionId);
 }
 /** open_app: confined to the slot's own target, and refused while that app is held by another slot. */
 static String openApp(Slot[] slots,int slot,String requested){
  String own=slots[slot].target;
  if(own==null||(requested!=null&&!requested.equals(own)))return "own_target_only";
  for(int i=0;i<slots.length;i++)if(i!=slot&&slots[i].holds(own))return APP_IN_OTHER_SESSION;
  return null;
 }

 /** One slot's attested membership for current_apps: Android's per-session answer, joined to the owned-device snapshot
  * by the attested deviceId. Closed ids are historical correlation only: a closed session lists nothing, and is
  * lingering (unconfirmed) while the snapshot still lists its device. */
 static final class Membership {
  final boolean open,closed,complete;final Integer deviceId;final List<String> packages;
  /** open=false: no readable answer (no session, refused, malformed, or keys missing). */
  Membership(boolean open,boolean closed,boolean complete,Integer deviceId,List<String> packages){
   this.open=open;this.closed=closed;this.complete=complete;this.deviceId=deviceId;
   this.packages=packages==null?Collections.<String>emptyList():packages;
  }
  static final Membership UNKNOWN=new Membership(false,false,false,null,null);
 }
 /** The deviceId of a per-session Bundle: an Integer, else null (missing keys mean no join). */
 static Integer deviceId(Object value){return value instanceof Integer?(Integer)value:null;}

 /** The reply for each slot: its packages (minus the controller and any package attested in another session), whether
  * that list is complete, and why not. ownedDevices: the device ids of the owned-device snapshot, or null when there is
  * no snapshot (older build or unreadable), in which case nothing is complete. */
 static final class Entry {
  final int slot;final List<String> apps;final boolean complete;final String incompleteReason;
  Entry(int slot,List<String> apps,boolean complete,String incompleteReason){this.slot=slot;this.apps=apps;this.complete=complete;this.incompleteReason=incompleteReason;}
 }
 static List<Entry> sessions(Slot[] slots,Membership[] members,Set<Integer> ownedDevices,String controller){
  Set<String> seen=new HashSet<>(),duplicate=new HashSet<>();
  for(int i=0;i<slots.length;i++)if(slots[i].occupied&&members[i].open&&!members[i].closed)
   for(String p:new LinkedHashSet<>(members[i].packages))if(!seen.add(p))duplicate.add(p);
  List<Entry> out=new ArrayList<>();
  for(int i=0;i<slots.length;i++){
   if(!slots[i].occupied)continue;
   Membership m=members[i];
   if(!m.open){out.add(new Entry(i,Collections.<String>emptyList(),false,MEMBERSHIP_UNAVAILABLE));continue;}
   if(m.deviceId==null||ownedDevices==null){out.add(new Entry(i,Collections.<String>emptyList(),false,DEVICE_UNATTESTED));continue;}
   if(m.closed){
    // Historical id: never membership. Still listed by the snapshot means the device lingers.
    out.add(new Entry(i,Collections.<String>emptyList(),!ownedDevices.contains(m.deviceId),ownedDevices.contains(m.deviceId)?SESSION_CLOSED:""));continue;
   }
   if(!ownedDevices.contains(m.deviceId)){out.add(new Entry(i,Collections.<String>emptyList(),false,DEVICE_UNATTESTED));continue;}
   List<String> apps=new ArrayList<>();boolean dup=false;
   for(String p:new LinkedHashSet<>(m.packages)){if(p.equals(controller))continue;if(duplicate.contains(p)){dup=true;continue;}apps.add(p);}
   Collections.sort(apps);
   out.add(new Entry(i,apps,m.complete&&!dup,dup?APP_IN_TWO_SESSIONS:m.complete?"":CurrentApps.OTHER_USER_OMITTED));
  }
  return out;
 }
}
