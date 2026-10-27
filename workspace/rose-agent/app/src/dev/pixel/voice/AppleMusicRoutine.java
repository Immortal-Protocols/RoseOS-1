package dev.pixel.voice;
import android.os.*;
import org.json.*;
import java.util.*;
import java.util.function.Consumer;

/** Narrow local procedure. Runs short UI steps on main; never blocks for a wait. */
final class AppleMusicRoutine {
 final VoiceService service;final Handler handler=new Handler(Looper.getMainLooper());final Consumer<JSONObject> complete;
 final JSONObject command;final JSONArray steps=new JSONArray();final long started=SystemClock.elapsedRealtime();
 final String requestedTitle,artist,query;String title,selectedArtist,priorResults="",phase="open";boolean requireResultChange;long phaseStarted=started;volatile boolean done;boolean seenApp;int navigation,submitRejections,submitRefocuses,resultRejections,focusRetries;boolean focusTouched;String focusStatus="",focusView="",focusText="";long selectionStarted=-1,verifiedMs=-1;
 static final String PKG="com.apple.android.music";
 AppleMusicRoutine(VoiceService service,JSONObject command,Consumer<JSONObject> complete){this.service=service;this.command=command;this.complete=complete;requestedTitle=command.optString("title").trim();title=requestedTitle;artist=command.optString("artist").trim();query=(title+" "+artist).trim();selectedArtist=artist;}
 boolean running(){return !done;}
 void start(){handler.post(this::tick);}
 void cancel(){if(!done)finish("needs_assistance","Routine interrupted");}
 long now(){return SystemClock.elapsedRealtime();}
 void move(String value){phase=value;phaseStarted=now();}
 JSONObject call(JSONObject request)throws Exception{long at=now();JSONObject r=PhoneControl.execute(service,request);steps.put(new JSONObject().put("phase",phase).put("action",request.getString("action")).put("elapsedMs",now()-at).put("status",r.optString("status")));return r;}
 JSONObject action(String name)throws Exception{return new JSONObject().put("action",name);}
 JSONObject screen()throws Exception{
  JSONObject s=call(action("inspect_ui"));
  if(!"ok".equals(s.optString("status")))throw new IllegalStateException("Screen unavailable: "+s.optString("status"));
  if(!PKG.equals(s.optString("packageName"))){if(seenApp)throw new IllegalStateException("User left Apple Music");return null;}
  seenApp=true;return s;
 }
 List<JSONObject> nodes(JSONObject s)throws Exception{List<JSONObject> out=new ArrayList<>();JSONArray a=s.optJSONArray("nodes");if(a!=null)for(int i=0;i<a.length();i++){JSONObject n=a.getJSONObject(i);if(n.optBoolean("visible",true)&&n.optBoolean("enabled",true)&&!n.optBoolean("password"))out.add(n);}return out;}
 List<AppleMusicMatch.Row> rows(JSONObject s)throws Exception{List<AppleMusicMatch.Row> result=new ArrayList<>();for(JSONObject n:nodes(s))if(n.has("clickTarget"))result.add(new AppleMusicMatch.Row(n.getInt("clickTarget"),n.optString("text"),null,n.optString("viewId")));return result;}
 void safe(JSONObject s)throws Exception{if(s.optBoolean("truncated"))throw new IllegalStateException("Incomplete screen");for(JSONObject n:nodes(s))if(n.optString("class").endsWith("Dialog"))throw new IllegalStateException("Unexpected dialog");JSONArray all=s.optJSONArray("nodes");if(all!=null)for(int i=0;i<all.length();i++)if(all.getJSONObject(i).optBoolean("password"))throw new IllegalStateException("Protected field");}
 boolean player(JSONObject s)throws Exception{for(JSONObject n:nodes(s))if("Player Queue".equals(n.optString("description"))||"com.apple.android.music:id/song_lyrics_word".equals(n.optString("viewId")))return true;return false;}
 JSONObject field(JSONObject s)throws Exception{JSONObject found=null;for(JSONObject n:nodes(s))if(n.optBoolean("editable")&&"android.widget.AutoCompleteTextView".equals(n.optString("class"))){if(found!=null)throw new IllegalStateException("Multiple search fields");found=n;}return found;}
 List<JSONObject> labels(JSONObject s,String label)throws Exception{List<JSONObject> out=new ArrayList<>();for(JSONObject n:nodes(s))if(label.equals(n.optString("text"))||label.equals(n.optString("description")))out.add(n);return out;}
 boolean mini(JSONObject s)throws Exception{for(JSONObject n:nodes(s))if(AppleMusicMatch.norm(n.optString("description")).equals(AppleMusicMatch.norm("playing "+title)))return true;return false;}
 boolean text(JSONObject s,String text)throws Exception{for(JSONObject n:nodes(s))if(AppleMusicMatch.norm(n.optString("text")).equals(AppleMusicMatch.norm(text)))return true;return false;}
 JSONObject tap(JSONObject s,String label,Integer target)throws Exception{JSONObject c=action("tap_label").put("snapshotId",s.getString("snapshotId")).put("label",label);if(target!=null)c.put("nodeId",target);JSONObject r=call(c);if(!"action_accepted".equals(r.optString("status")))throw new IllegalStateException("Tap stopped: "+r.optString("status"));return r;}
 void focus(JSONObject s,JSONObject f)throws Exception{focusRetries=0;focusTouched=false;focusView=f.optString("viewId");focusText=f.optString("text");focusStatus=focusAction(s,f,false);}
 String focusAction(JSONObject s,JSONObject f,boolean touch)throws Exception{String label=f.optString("text",f.optString("description"));JSONObject c=action(touch?"touch_label":label.isEmpty()?"tap":"tap_label").put("snapshotId",s.getString("snapshotId")).put("nodeId",f.getInt("id"));if(!label.isEmpty())c.put("label",label);else if(touch)throw new IllegalStateException("Search focus target has no label");return call(c).optString("status");}
 void awaitFocus(JSONObject s,boolean submitting)throws Exception{
  safe(s);JSONObject f=field(s);
  if(f!=null&&f.optBoolean("focused")&&f.optBoolean("imeEnter")){if(submitting&&!query.equals(f.optString("text")))throw new IllegalStateException("Search text changed while focusing");move(submitting?"submit":"write");return;}
  long elapsed=now()-phaseStarted;
  if(f!=null&&focusView.equals(f.optString("viewId"))&&focusText.equals(f.optString("text"))){
   if(AppleMusicRetry.allowed("tap_label",focusStatus,focusRetries,elapsed)){focusRetries++;focusStatus=focusAction(s,f,focusTouched);return;}
   if(!focusTouched&&elapsed>=300&&(focusStatus.equals("action_failed")||focusStatus.equals("action_accepted"))){focusTouched=true;focusStatus=focusAction(s,f,true);return;}
  }
  if(elapsed>1500)throw new IllegalStateException(submitting?"Search submit focus unavailable":"Search did not receive focus");
 }
 void finish(String status,String reason){if(done)return;done=true;handler.removeCallbacksAndMessages(null);try{JSONObject r=new JSONObject().put("status",status).put("phase",phase).put("title",title).put("artist",selectedArtist).put("requestedTitle",requestedTitle).put("requestedArtist",artist).put("localElapsedMs",now()-started).put("modelCalls",0).put("steps",steps).put("verification","First ranked song from the submitted search, followed by matching selected player title and Pause state; not acoustic verification.");if(verifiedMs>=0)r.put("elapsedToVerifiedPlayingMs",verifiedMs);if(reason!=null)r.put("reason",reason);complete.accept(r);}catch(Exception ignored){}}
 void tick(){
  if(done)return;
  try{
   if(now()-started>20000||System.currentTimeMillis()>command.optLong("expires",Long.MAX_VALUE))throw new IllegalStateException("Routine timed out");
   if(phase.equals("open")){
    if(requestedTitle.isEmpty()||requestedTitle.length()+artist.length()>300)throw new IllegalArgumentException("A song title is required");
    JSONObject r=call(action("open_app").put("packageName",PKG));if(!"launch_requested".equals(r.optString("status")))throw new IllegalStateException("Apple Music did not open: "+r.optString("status"));move("navigate");
   }else{
    JSONObject s=screen();if(s==null){if(now()-phaseStarted>3000)throw new IllegalStateException("Apple Music did not become visible");handler.postDelayed(this::tick,100);return;}
    if(phase.equals("navigate")){
     if(player(s)){if(++navigation>3)throw new IllegalStateException("Could not dismiss player");call(action("back"));}
     else{safe(s);JSONObject f=field(s);if(f==null){if(++navigation>3)throw new IllegalStateException("Search unavailable");List<JSONObject> tabs=labels(s,"Search");Set<Integer> targets=new HashSet<>();for(JSONObject n:tabs)if(n.has("clickTarget"))targets.add(n.getInt("clickTarget"));if(targets.isEmpty()&&(!labels(s,"Navigate up").isEmpty()||!labels(s,"Navigate up.").isEmpty()))call(action("back"));else{if(targets.size()!=1)throw new IllegalStateException("Search tab ambiguous");tap(s,"Search",targets.iterator().next());}}
      else if(!f.optBoolean("focused")||!f.optBoolean("imeEnter")){focus(s,f);move("focus");}
      else move("write");}
    }else if(phase.equals("focus")){awaitFocus(s,false);}
    else if(phase.equals("write")){safe(s);JSONObject f=field(s);if(f==null)throw new IllegalStateException("Search disappeared");priorResults=AppleMusicMatch.signature(rows(s));requireResultChange=!(query.equals(f.optString("text"))||(query+" in Apple Music").equals(f.optString("text")));JSONObject r=call(action("set_text").put("snapshotId",s.getString("snapshotId")).put("nodeId",f.getInt("id")).put("text",query));if(!"action_accepted".equals(r.optString("status")))throw new IllegalStateException("Text entry stopped: "+r.optString("status"));move("submit");}
    else if(phase.equals("submit")){safe(s);JSONObject f=field(s);if(f==null||!query.equals(f.optString("text")))throw new IllegalStateException("Search text mismatch");if(f.optBoolean("imeEnter")&&f.optBoolean("focused")){JSONObject r=call(action("ime_enter").put("snapshotId",s.getString("snapshotId")).put("nodeId",f.getInt("id")));if("action_accepted".equals(r.optString("status")))move("results");else if(AppleMusicRetry.allowed("ime_enter",r.optString("status"),submitRejections,now()-phaseStarted)){submitRejections++;}else throw new IllegalStateException("Search submit stopped: "+r.optString("status"));}else{if(++submitRefocuses>1)throw new IllegalStateException("Search lost submit focus");focus(s,f);move("focus_submit");}}
    else if(phase.equals("focus_submit")){awaitFocus(s,true);}
    else if(phase.equals("results")){safe(s);JSONObject f=field(s);if(f==null||!(query.equals(f.optString("text"))||(query+" in Apple Music").equals(f.optString("text")))){if(now()-phaseStarted>1500)throw new IllegalStateException("Search changed");handler.postDelayed(this::tick,100);return;}List<AppleMusicMatch.Row> resultRows=rows(s);boolean resultsReady=!labels(s,"TOP RESULTS").isEmpty()&&(!requireResultChange||!priorResults.equals(AppleMusicMatch.signature(resultRows)));AppleMusicMatch.Row match=resultsReady?AppleMusicMatch.choose(resultRows,requestedTitle,artist):null;if(match==null){if(now()-phaseStarted>6000)throw new IllegalStateException("Search results did not become ready");}else{title=match.text;selectedArtist=match.artist;if(selectionStarted<0)selectionStarted=now();JSONObject r=call(action("tap_label").put("snapshotId",s.getString("snapshotId")).put("label",title).put("nodeId",match.target));if("action_accepted".equals(r.optString("status")))move("verify");else if(AppleMusicRetry.allowed("tap_label",r.optString("status"),resultRejections,now()-selectionStarted)){resultRejections++;}else throw new IllegalStateException("Song selection stopped: "+r.optString("status"));}}
    else if(phase.equals("verify")){
     if(player(s)&&s.optBoolean("truncated")){call(action("back"));}
     else{safe(s);boolean playing=labels(s,"Pause").size()==1&&((!player(s)&&mini(s))||(player(s)&&text(s,title)&&text(s,selectedArtist)));if(playing){verifiedMs=now()-started;if(command.optBoolean("pauseAfter"))move("pause");else{finish("verified_playing",null);return;}}else if(now()-phaseStarted>6000)throw new IllegalStateException("Playback not verified; song tap will not be repeated");}
    }else if(phase.equals("pause")){
     if(player(s)){call(action("back"));}
     else{safe(s);if(!mini(s))throw new IllegalStateException("Track changed before pause");if(labels(s,"Play").size()==1){finish("verified_playing_then_paused",null);return;}if(labels(s,"Pause").size()!=1)throw new IllegalStateException("Pause unavailable");tap(s,"Pause",null);move("verify_pause");}
    }else if(phase.equals("verify_pause")){safe(s);if(!mini(s))throw new IllegalStateException("Track changed during pause");if(labels(s,"Play").size()==1&&labels(s,"Pause").isEmpty()){finish("verified_playing_then_paused",null);return;}if(now()-phaseStarted>2000)throw new IllegalStateException("Pause not verified; will not repeat it");}
   }
   handler.postDelayed(this::tick,100);
  }catch(Exception error){finish(verifiedMs>=0?"verified_playing_cleanup_needs_assistance":"needs_assistance",error.getMessage());}
 }
}
