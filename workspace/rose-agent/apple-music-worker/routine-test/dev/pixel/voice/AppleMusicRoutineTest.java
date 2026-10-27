package dev.pixel.voice;
import android.os.*;
import org.json.*;
public class AppleMusicRoutineTest {
 static void require(boolean b,String why){if(!b)throw new AssertionError(why);}
 static JSONObject run(int stale,String status,boolean loseFocus){
  SystemClock.time=0;PhoneControl.reset(stale,status,loseFocus);
  final JSONObject[] result={null};
  new AppleMusicRoutine(new VoiceService(),new JSONObject().put("title","Diamonds").put("artist","Rihanna"),r->result[0]=r).start();
  Handler.drain();require(result[0]!=null,"No completion");return result[0];
 }
 public static void main(String[] args){
  JSONObject r=run(1,"action_accepted",false);
  require(r.getString("status").equals("verified_playing"),"Rejected submit did not recover");
  require(PhoneControl.imeCalls==2&&PhoneControl.playCalls==1,"Retry replayed wrong action");
  r=run(0,"action_accepted",true);
  require(r.getString("status").equals("verified_playing"),"Lost focus did not recover");
  require(PhoneControl.focusCalls==1&&PhoneControl.imeCalls==1&&PhoneControl.playCalls==1,"Focus recovery duplicated work");
  r=run(20,"action_accepted",false);
  require(r.getString("status").equals("needs_assistance")&&PhoneControl.imeCalls==4&&PhoneControl.playCalls==0,"Retry budget not enforced");
  r=run(0,"action_failed",false);
  require(r.getString("status").equals("needs_assistance")&&PhoneControl.imeCalls==1&&PhoneControl.playCalls==0,"Uncertain submit repeated");
  r=run(0,"action_accepted",false);
  require(PhoneControl.imeCalls==1&&PhoneControl.playCalls==1,"Successful action repeated");
  PhoneControl.selectionRejections=1;
  r=run(0,"action_accepted",false);
  require(r.getString("status").equals("verified_playing")&&PhoneControl.playCalls==1,"Rejected moved row did not recover once");
  PhoneControl.selectionRejections=20;
  r=run(0,"action_accepted",false);
  require(r.getString("status").equals("needs_assistance")&&PhoneControl.playCalls==0,"Moved row retried without bound");
  PhoneControl.selectionRejections=0;PhoneControl.selectionStatus="action_failed";
  r=run(0,"action_accepted",false);
  require(r.getString("status").equals("needs_assistance")&&PhoneControl.playCalls==1,"Uncertain play tap repeated");
  PhoneControl.selectionStatus="action_accepted";SystemClock.time=0;PhoneControl.reset(0,"action_accepted",false);
  final JSONObject[] onlyTitle={null};new AppleMusicRoutine(new VoiceService(),new JSONObject().put("title","Diamonds"),result->onlyTitle[0]=result).start();Handler.drain();
  require(onlyTitle[0]!=null&&onlyTitle[0].getString("status").equals("verified_playing"),"Title-only playback failed");
  require(PhoneControl.query.equals("Diamonds"),"Title-only query gained a trailing space or guessed artist");
  require(onlyTitle[0].getString("artist").equals("Rihanna")&&onlyTitle[0].getString("requestedArtist").isEmpty(),"Selected artist must come from the result row");
  require(PhoneControl.imeCalls==1&&PhoneControl.playCalls==1,"Title-only search replayed");
  PhoneControl.initiallyUnfocused=true;PhoneControl.focusRejections=1;
  r=run(0,"action_accepted",false);
  require(r.getString("status").equals("verified_playing")&&PhoneControl.focusCalls==2&&PhoneControl.touchCalls==0,"Rejected focus must reacquire a fresh target");
  PhoneControl.stuckFocus=true;
  r=run(0,"action_accepted",false);
  require(r.getString("status").equals("verified_playing")&&PhoneControl.touchCalls==1&&PhoneControl.imeCalls==1,"Ineffective semantic focus must use one touch and verify");
  PhoneControl.focusRejections=20;
  r=run(0,"action_accepted",false);
  require(r.getString("status").equals("needs_assistance")&&PhoneControl.focusCalls==4&&PhoneControl.touchCalls==0&&PhoneControl.imeCalls==0,"Focus rejection retry must remain bounded");
  PhoneControl.focusRejections=0;PhoneControl.changedFocusField=true;
  r=run(0,"action_accepted",false);
  require(r.getString("status").equals("needs_assistance")&&PhoneControl.touchCalls==0&&PhoneControl.imeCalls==0,"Changed query must not be touched or submitted");
  PhoneControl.initiallyUnfocused=false;PhoneControl.stuckFocus=false;PhoneControl.changedFocusField=false;
  System.out.println("Real routine state-machine tests passed: stale submit, lost focus, bounded retry, no uncertain replay.");
 }
}
