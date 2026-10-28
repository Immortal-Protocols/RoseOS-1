package dev.pixel.voice;

/** Voice-session state for the conversation surface, derived only from VoiceService's own flags and the
 * web client's typed lifecycle reasons (never from display status text). Pure logic (no Android types)
 * so it is testable. Connected is never claimed from a command or a timer: active needs the session's
 * live && connected. Session, microphone and Rose output stay separate; nothing here touches tasks. */
final class ConversationState {
 enum Phase { INACTIVE, CONNECTING, ACTIVE, ERROR }
 enum Failure { NONE, NOT_CONNECTED, DISCONNECTED, BUSY, UNREACHABLE }
 static final String CONNECTED="Connected", MUTED="Muted", UNMUTED="Unmuted", CALL_ENDED="Call ended";
 /** Returned by Notices when an earlier notice must be withdrawn (a new attempt has begun). */
 static final String CLEAR="";
 final Phase phase; final boolean muted; final Failure failure;
 private ConversationState(Phase phase,boolean muted,Failure failure){this.phase=phase;this.muted=muted;this.failure=failure;}

 /** service=false when VoiceService is not running. endReason is the latest cleanup reason the voice
  * client reported (start_timeout, provider_disconnect, start_error, other; or cleanup, handoff,
  * routing_change for deliberate ends). startError is the latest start_error detail (an HTTP status or
  * error name). reachable is the client's own /health result, null until checked. */
 static ConversationState of(boolean service,boolean live,boolean connected,boolean muted,boolean communicating,String endReason,String startError,Boolean reachable){
  if(!service)return new ConversationState(Phase.INACTIVE,false,Failure.NONE);
  if(live&&connected)return new ConversationState(Phase.ACTIVE,muted,Failure.NONE);
  if(communicating)return new ConversationState(Phase.CONNECTING,false,Failure.NONE);
  Failure f=failure(endReason,startError);
  if(f==Failure.NONE&&Boolean.FALSE.equals(reachable))f=Failure.UNREACHABLE;
  return new ConversationState(f==Failure.NONE?Phase.INACTIVE:Phase.ERROR,false,f);
 }
 static Failure failure(String endReason,String startError){
  if(endReason==null)return Failure.NONE;
  switch(endReason){
   case "start_timeout":return Failure.NOT_CONNECTED;
   case "start_error":return "409".equals(startError)?Failure.BUSY:Failure.NOT_CONNECTED;
   case "provider_disconnect":case "other":case "renderer_gone":return Failure.DISCONNECTED; // renderer_gone: VoiceService.voiceRendererGone
   default:return Failure.NONE; // cleanup (user end), handoff, routing_change, or nothing yet
  }
 }
 /** Short persistent explanation shown under the controls; empty when nothing went wrong. */
 String failureText(){
  switch(failure){
   case NOT_CONNECTED:return "Couldn’t connect";
   case DISCONNECTED:return "Voice disconnected";
   case BUSY:return "Voice is in use elsewhere";
   case UNREACHABLE:return "Can’t reach the server";
   default:return "";
  }
 }
 boolean active(){return phase==Phase.ACTIVE;}
 boolean ended(){return phase==Phase.INACTIVE||phase==Phase.ERROR;}
 /** Speak when idle or failed, Cancel (end) while connecting, set mute/unmute when active. */
 String primaryCommand(){return phase==Phase.ACTIVE?(muted?"tray_unmute":"tray_mute"):phase==Phase.CONNECTING?"end":"talk";}
 /** End call applies to voice only and is offered for an active session. */
 boolean canEnd(){return phase==Phase.ACTIVE;}

 /** SystemUI tray protocol v1 fields, from this same state so the tray and conversation agree.
  * connected/listening/muted are true only for an active session; status keeps its existing words. */
 boolean trayConnected(){return active();}
 boolean trayListening(){return active()&&!muted;}
 boolean trayMuted(){return active()&&muted;}
 String trayStatus(boolean service){return phase==Phase.ACTIVE?(muted?"Muted":"Listening"):phase==Phase.CONNECTING?"Connecting":service?"Disconnected":"Ready";}

 /** Emits only the four conversation notices, on observed transitions, plus CLEAR when a new attempt
  * starts so a previous session's notice never lingers. The first observation after (re)opening the
  * surface only seeds state, so returning never replays a stale notice. A drop into reconnecting is not
  * a call end unless it then ends; a session that never became active emits nothing. */
 static final class Notices {
  private ConversationState last;
  private boolean wasActive;
  void reset(){last=null;wasActive=false;}
  String observe(ConversationState next){
   ConversationState prev=last;last=next;
   if(prev==null){wasActive=next.active();return null;}
   if(next.active()){
    boolean connected=!prev.active();wasActive=true;
    if(connected)return CONNECTED;
    return prev.muted!=next.muted?(next.muted?MUTED:UNMUTED):null;
   }
   if(next.ended()&&wasActive){wasActive=false;return CALL_ENDED;}
   if(next.phase==Phase.CONNECTING&&prev.ended())return CLEAR;
   return null;
  }
 }
}
