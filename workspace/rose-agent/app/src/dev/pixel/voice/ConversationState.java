package dev.pixel.voice;

/** Voice-session state for the conversation surface, derived only from VoiceService's own flags.
 * Pure logic (no Android types) so it is testable. Connected is never claimed from a command or a
 * timer: active needs the session's live && connected; connecting is an open attempt that is not
 * yet active. Session, microphone and Rose output stay separate; nothing here touches tasks. */
final class ConversationState {
 enum Phase { INACTIVE, CONNECTING, ACTIVE, ERROR }
 static final String CONNECTED="Connected", MUTED="Muted", UNMUTED="Unmuted", CALL_ENDED="Call ended";
 final Phase phase; final boolean muted;
 private ConversationState(Phase phase,boolean muted){this.phase=phase;this.muted=muted;}
 /** service=false when VoiceService is not running. Mirrors MainActivity's active/starting/error mapping. */
 static ConversationState of(boolean service,boolean live,boolean connected,boolean muted,boolean communicating,String status){
  if(!service)return new ConversationState(Phase.INACTIVE,false);
  if(live&&connected)return new ConversationState(Phase.ACTIVE,muted);
  if(communicating)return new ConversationState(Phase.CONNECTING,false);
  boolean failed=status!=null&&status.toLowerCase(java.util.Locale.ROOT).matches(".*(failed|unavailable|not ready|denied|error|timed out).*");
  return new ConversationState(failed?Phase.ERROR:Phase.INACTIVE,false);
 }
 boolean active(){return phase==Phase.ACTIVE;}
 boolean ended(){return phase==Phase.INACTIVE||phase==Phase.ERROR;}
 /** The one microphone control: start when idle or failed, set mute/unmute when active, nothing while connecting. */
 String primaryCommand(){return phase==Phase.ACTIVE?(muted?"tray_unmute":"tray_mute"):phase==Phase.CONNECTING?null:"talk";}
 /** End applies to voice only and is offered while a session is active or being opened. */
 boolean canEnd(){return phase==Phase.ACTIVE||phase==Phase.CONNECTING;}

 /** Emits only the four conversation notices, on observed transitions. The first observation after
  * (re)opening the surface only seeds state, so returning never replays a stale notice. A drop into
  * reconnecting is not a call end unless it then ends; a session that never became active emits nothing. */
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
   return null;
  }
 }
}
