package dev.pixel.voice;

import android.content.Context;
import android.os.SystemClock;
import org.json.*;
import java.io.*;
import java.util.*;
import java.util.concurrent.*;

/** Adapter for the unmodified YouTube app. Uses fresh UI state, never saved tap coordinates. */
final class YouTubeAgent {
 static final String PKG="com.google.android.youtube";
 final Context context;
 String query="";long deadline;
 final LinkedHashMap<String,YouTubeVideo> results=new LinkedHashMap<>();
 boolean playbackDispatched;String phase="";
 YouTubeAgent(Context context){this.context=context;}
 void check()throws Exception{if(SystemClock.elapsedRealtime()>deadline)throw new Exception("YouTube operation timed out");}
 JSONObject call(JSONObject command)throws Exception{check();return PhoneControl.execute(context,command);}
 JSONObject action(String name)throws Exception{return new JSONObject().put("action",name);}
 JSONObject screen()throws Exception{
  JSONObject s=call(action("inspect_ui"));
  long until=SystemClock.elapsedRealtime()+700;
  while(("no_window".equals(s.optString("status"))||s.optString("packageName").isEmpty()||"null".equals(s.optString("packageName")))&&SystemClock.elapsedRealtime()<until){Thread.sleep(60);s=call(action("inspect_ui"));}
  if(!"ok".equals(s.optString("status"))||!PKG.equals(s.optString("packageName")))throw new Exception("YouTube is not the foreground app ("+s.optString("status")+", "+s.optString("packageName")+")");
  if(s.optBoolean("truncated"))throw new Exception("YouTube screen is incomplete; use ordinary screen controls");
  JSONArray a=s.getJSONArray("nodes");for(int i=0;i<a.length();i++)if(a.getJSONObject(i).optBoolean("password"))throw new Exception("A protected input is visible");return s;
 }
 List<JSONObject> nodes(JSONObject s)throws Exception{List<JSONObject> a=new ArrayList<>();JSONArray rows=s.getJSONArray("nodes");for(int i=0;i<rows.length();i++)a.add(rows.getJSONObject(i));return a;}
 JSONObject byId(JSONObject s,String id)throws Exception{for(JSONObject n:nodes(s))if(id.equals(n.optString("viewId")))return n;return null;}
 JSONObject label(JSONObject s,String text)throws Exception{JSONObject found=null;for(JSONObject n:nodes(s))if(n.has("clickTarget")&&n.optBoolean("enabled",true)&&(text.equals(n.optString("text"))||text.equals(n.optString("description")))){if(found!=null&&found.optInt("clickTarget")!=n.optInt("clickTarget"))throw new Exception("Ambiguous YouTube control");found=n;}return found;}
 JSONObject tap(JSONObject s,JSONObject n)throws Exception{
  String label=n.optString("description");if(label.isEmpty())label=n.optString("text");
  if(label.isEmpty())throw new Exception("Unlabelled control needs visual inspection");
  return call(action("tap_label").put("snapshotId",s.getString("snapshotId")).put("nodeId",n.getInt("clickTarget")).put("label",label));
 }
 void accepted(JSONObject r)throws Exception{if(!"action_accepted".equals(r.optString("status")))throw new Exception("YouTube action not accepted: "+r.optString("status"));}
 JSONObject waitField()throws Exception{long until=SystemClock.elapsedRealtime()+2000;do{JSONObject s=screen();JSONObject n=byId(s,PKG+":id/search_edit_text");if(n!=null&&n.optBoolean("editable"))return s;Thread.sleep(60);}while(SystemClock.elapsedRealtime()<until);throw new Exception("YouTube search field did not appear");}
 JSONObject navigateSearch()throws Exception{
  for(int i=0;i<5;i++){
   JSONObject s=screen();if(byId(s,PKG+":id/search_edit_text")!=null)return s;
   JSONObject n=byId(s,PKG+":id/search_query");if(n==null)n=label(s,"Search");
   if(n!=null){phase="open_search";JSONObject r=tap(s,n);if(Arrays.asList("stale_screen","label_target_mismatch","label_not_found").contains(r.optString("status")))continue;accepted(r);return waitField();}
   // Back dismisses the current player or panel; stop immediately if the user leaves YouTube.
   accepted(call(action("back")));Thread.sleep(100);
  }
  throw new Exception("Could not find YouTube search");
 }
 JSONArray collect(JSONObject s)throws Exception{
  JSONArray out=new JSONArray();LinkedHashMap<String,YouTubeVideo> next=new LinkedHashMap<>();
  for(JSONObject n:nodes(s)){
   if(!n.has("clickTarget"))continue;YouTubeVideo v=YouTubeVideo.parse(n.optString("description"));if(v==null)continue;
   String id=UUID.nameUUIDFromBytes((query+"\n"+v.label).getBytes("UTF-8")).toString();if(next.containsKey(id))continue;next.put(id,v);
   out.put(new JSONObject().put("id",id).put("position",next.size()).put("title",v.title).put("channel",v.channel).put("description",v.label));
  }
  results.clear();results.putAll(next);return out;
 }
 JSONObject search(String text)throws Exception{
  if(text.trim().isEmpty()||text.length()>200)throw new IllegalArgumentException("Query must contain 1–200 characters");
  phase="navigate_search";JSONObject s=navigateSearch();phase="enter_query";JSONObject f=byId(s,PKG+":id/search_edit_text");
  accepted(call(action("set_text").put("snapshotId",s.getString("snapshotId")).put("nodeId",f.getInt("id")).put("text",text)));
  phase="submit_search";boolean submitted=false;
  for(int attempt=0;attempt<3&&!submitted;attempt++){
   s=screen();f=byId(s,PKG+":id/search_edit_text");if(f==null||!text.equals(f.optString("text"))||!f.optBoolean("imeEnter"))throw new Exception("Search field changed before submission");
   JSONObject r=call(action("ime_enter").put("snapshotId",s.getString("snapshotId")).put("nodeId",f.getInt("id")));
   if("action_accepted".equals(r.optString("status")))submitted=true;
   else if(!Arrays.asList("stale_screen","label_target_mismatch").contains(r.optString("status")))throw new Exception("Search submission was not confirmed");
  }
  if(!submitted)throw new Exception("Search kept changing before submission");
  phase="read_results";query=text;results.clear();long until=SystemClock.elapsedRealtime()+6500;
  do{s=screen();JSONObject q=byId(s,PKG+":id/search_query");if(q!=null&&text.equals(q.optString("text"))){JSONArray videos=collect(s);if(videos.length()>0)return new JSONObject().put("status","verified").put("query",query).put("results",videos).put("scope","Currently visible regular video results; excludes Shorts, ads and channel cards").put("startedPlayback",false);if(label(s,"No results found")!=null)return new JSONObject().put("status","verified").put("query",query).put("results",videos).put("startedPlayback",false);}Thread.sleep(100);}while(SystemClock.elapsedRealtime()<until);
  throw new Exception("Regular video results did not become readable; no video was selected");
 }
 MediaSnapshot media()throws Exception{
  check();Process process=new ProcessBuilder("/system/bin/dumpsys","-t","1","media_session").redirectErrorStream(true).start();ExecutorService reader=Executors.newSingleThreadExecutor();
  Future<String> text=reader.submit(()->{try(InputStream in=process.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){byte[] b=new byte[4096];int n;while(out.size()<262144&&(n=in.read(b,0,Math.min(b.length,262144-out.size())))!=-1)out.write(b,0,n);return out.toString("UTF-8");}});
  try{return MediaSnapshot.parse(text.get(1500,TimeUnit.MILLISECONDS),PKG);}finally{process.destroyForcibly();reader.shutdownNow();}
 }
 JSONObject state()throws Exception{
  MediaSnapshot m=media();JSONObject out=new JSONObject().put("status","ok").put("app","YouTube").put("mediaStatus",m.status).put("playbackState",m.state).put("query",query);
  if("ok".equals(m.status)||"not_ready".equals(m.status))out.put("playing",m.playing).put("description",m.description);
  if(Double.isFinite(m.speed))out.put("reportedPlaybackSpeed",m.speed);
  return out.put("verification","YouTube-specific Android media session; not acoustic verification");
 }
 void showControls(JSONObject s)throws Exception{
  JSONObject n=byId(s,PKG+":id/watch_while_time_bar_view_overlay");
  if(n==null)throw new Exception("Player overlay is not available; use screen controls");
  JSONArray b=n.getJSONArray("bounds");acceptedGesture(call(action("touch_point").put("snapshotId",s.getString("snapshotId")).put("nodeId",n.getInt("id")).put("x",(b.getInt(0)+b.getInt(2))/2).put("y",(b.getInt(1)+b.getInt(3))/2)));
  Thread.sleep(80);
 }
 void acceptedGesture(JSONObject r)throws Exception{if(!Arrays.asList("action_accepted","gesture_dispatched").contains(r.optString("status")))throw new Exception("Player controls could not be revealed: "+r.optString("status"));}
 JSONObject playback(boolean desired)throws Exception{
  MediaSnapshot before=media();if(!"ok".equals(before.status))throw new Exception("No unambiguous YouTube playback session");
  if(before.playing==desired)return state().put("status","verified").put("alreadyInRequestedState",true);
  String name=desired?"Play video":"Pause video";
  JSONObject s=null,n=null;boolean revealed=false;long controlsUntil=SystemClock.elapsedRealtime()+2500;
  do{s=screen();n=label(s,name);if(n!=null)break;
   if(!revealed&&byId(s,PKG+":id/watch_while_time_bar_view_overlay")!=null){showControls(s);revealed=true;}else Thread.sleep(80);
  }while(SystemClock.elapsedRealtime()<controlsUntil);
  if(n==null)throw new Exception("Requested player control is unavailable");
  playbackDispatched=true;JSONObject r=tap(s,n);
  if(!"action_accepted".equals(r.optString("status"))){if(Arrays.asList("stale_screen","label_target_mismatch","label_not_found").contains(r.optString("status")))playbackDispatched=false;throw new Exception("Playback control outcome was not confirmed");}
  long until=SystemClock.elapsedRealtime()+2500;
  do{MediaSnapshot after=media();if("ok".equals(after.status)&&after.playing==desired&&before.description.equals(after.description))return state().put("status","verified");Thread.sleep(100);}while(SystemClock.elapsedRealtime()<until);
  throw new Exception("Player changed or playback state was not confirmed; do not repeat automatically");
 }
 JSONObject play(String id)throws Exception{
  YouTubeVideo video=results.get(id);if(video==null)throw new Exception("Result expired or unknown; search again before selecting a video");
  JSONObject s=screen(),q=byId(s,PKG+":id/search_query");if(q==null||!query.equals(q.optString("text")))throw new Exception("Search results are no longer visible; search again");
  JSONObject n=label(s,video.label);if(n==null)throw new Exception("Selected result changed or moved; search again");
  playbackDispatched=true;JSONObject clicked=tap(s,n);if(!"action_accepted".equals(clicked.optString("status"))){if(Arrays.asList("stale_screen","label_target_mismatch","label_not_found").contains(clicked.optString("status")))playbackDispatched=false;throw new Exception("Video selection was not confirmed");}
  long until=SystemClock.elapsedRealtime()+6500;boolean resumed=false;
  do{MediaSnapshot m=media();if("ok".equals(m.status)&&!m.playing&&YouTubeVideo.matchesMetadata(m.description,video.title)&&!resumed){resumed=true;return playback(true).put("selectedTitle",video.title).put("selectedChannel",video.channel);}if("ok".equals(m.status)&&m.playing&&YouTubeVideo.matchesMetadata(m.description,video.title))return state().put("status","verified").put("selectedTitle",video.title).put("selectedChannel",video.channel);Thread.sleep(120);}while(SystemClock.elapsedRealtime()<until);
  throw new Exception("Selected video playback is not confirmed; it may be loading or showing an ad. Do not select it again automatically");
 }
 JSONObject waitLabel(String text,long ms)throws Exception{
  long until=SystemClock.elapsedRealtime()+ms;do{JSONObject s=screen();if(label(s,text)!=null)return s;Thread.sleep(80);}while(SystemClock.elapsedRealtime()<until);throw new Exception("YouTube control did not appear: "+text);
 }
 JSONObject player()throws Exception{
  phase="reveal_player_controls";long until=SystemClock.elapsedRealtime()+3000;boolean revealed=false,dismissed=false;
  do{JSONObject s=screen();
   if(label(s,"Minimized player")!=null)throw new Exception("Expand the current minimized player before this operation");
   if(byId(s,PKG+":id/player_overflow_button")!=null&&label(s,"More options")!=null)return s;
   if(!dismissed&&byId(s,PKG+":id/design_bottom_sheet")!=null){
    dismissed=true;int old=s.getInt("windowId");accepted(call(action("back")));long wait=SystemClock.elapsedRealtime()+1000;
    do{Thread.sleep(80);s=screen();}while(s.getInt("windowId")==old&&SystemClock.elapsedRealtime()<wait);
    continue;
   }
   if(byId(s,PKG+":id/watch_while_time_bar_view_overlay")!=null&&!revealed){showControls(s);revealed=true;}else Thread.sleep(80);
  }while(SystemClock.elapsedRealtime()<until);throw new Exception("Current YouTube video controls unavailable");
 }
 void press(JSONObject s,String text)throws Exception{JSONObject n=label(s,text);if(n==null)throw new Exception("Missing control: "+text);accepted(tap(s,n));}
 JSONObject menu()throws Exception{JSONObject s=player();press(s,"More options");return waitLabel("More",1800);}
 JSONObject fullScreen(boolean enabled)throws Exception{
  String target=enabled?"Enter fullscreen":"Exit fullscreen",opposite=enabled?"Exit fullscreen":"Enter fullscreen";JSONObject s=player();
  if(label(s,opposite)!=null)return new JSONObject().put("status","verified").put("fullscreen",enabled).put("alreadyInRequestedState",true);
  playbackDispatched=true;press(s,target);waitLabel(opposite,2000);
  return new JSONObject().put("status","verified").put("fullscreen",enabled);
 }
 JSONObject minimize()throws Exception{
  JSONObject s=player();playbackDispatched=true;press(s,"Minimize");long until=SystemClock.elapsedRealtime()+2000;
  do{s=screen();if(byId(s,PKG+":id/mini_player")!=null||byId(s,PKG+":id/miniplayer")!=null||label(s,"Expand")!=null||label(s,"Expand mini player")!=null||label(s,"Minimized player")!=null)return new JSONObject().put("status","verified").put("minimized",true);Thread.sleep(80);}while(SystemClock.elapsedRealtime()<until);
  throw new Exception("Minimize dispatched but mini-player not confirmed");
 }
 JSONObject adjacent(String direction)throws Exception{
  if(!direction.equals("next")&&!direction.equals("previous"))throw new IllegalArgumentException("Use next or previous");MediaSnapshot before=media();
  JSONObject s=player();String target=direction.equals("next")?"Next video":"Previous video";
  if(label(s,target)==null)throw new Exception("No enabled "+target+" control for this video");playbackDispatched=true;press(s,target);
  long until=SystemClock.elapsedRealtime()+6500;do{MediaSnapshot after=media();if("ok".equals(after.status)&&!after.description.equals(before.description))return state().put("status","verified").put("direction",direction);Thread.sleep(120);}while(SystemClock.elapsedRealtime()<until);
  throw new Exception("Navigation dispatched; a different video was not confirmed. Do not repeat automatically");
 }
 JSONObject autoplay(boolean enabled)throws Exception{
  JSONObject s=player();String wanted=enabled?"Autoplay is on":"Autoplay is off",other=enabled?"Autoplay is off":"Autoplay is on";
  if(label(s,wanted)!=null)return new JSONObject().put("status","verified").put("autoplay",enabled).put("alreadyInRequestedState",true);
  playbackDispatched=true;press(s,other);waitLabel(wanted,2000);return new JSONObject().put("status","verified").put("autoplay",enabled);
 }
 Boolean loopState(JSONObject s)throws Exception{
  JSONObject row=label(s,"Loop video");if(row==null)return null;int target=row.getInt("clickTarget");
  for(JSONObject n:nodes(s))if(n.optInt("clickTarget",-1)==target){if("On".equals(n.optString("text")))return true;if("Off".equals(n.optString("text")))return false;}return null;
 }
 JSONObject loopMenu()throws Exception{JSONObject s=menu();press(s,"More");return waitLabel("Loop video",1800);}
 JSONObject loop(boolean enabled)throws Exception{
  JSONObject s=loopMenu();Boolean current=loopState(s);if(current==null)throw new Exception("Loop state unavailable");
  if(current!=enabled){playbackDispatched=true;press(s,"Loop video");Thread.sleep(120);s=screen();if(loopState(s)==null)s=loopMenu();if(!Boolean.valueOf(enabled).equals(loopState(s)))throw new Exception("Loop selection was not confirmed");}
  accepted(call(action("back")));return new JSONObject().put("status","verified").put("loop",enabled).put("alreadyInRequestedState",current==enabled);
 }
 double speedValue(JSONObject s)throws Exception{
  for(JSONObject n:nodes(s)){String text=n.optString("description");if(text.matches("[0-9]+(?:\\.[0-9]+)?x"))return Double.parseDouble(text.substring(0,text.length()-1));}throw new Exception("Readable speed value unavailable");
 }
 JSONObject speed(double rate)throws Exception{
  if(!Double.isFinite(rate)||rate<0.25||rate>3||Math.abs(rate*4-Math.rint(rate*4))>0.001)throw new IllegalArgumentException("Speed must be 0.25 to 3 in increments of 0.25");
  JSONObject s=screen();if(label(s,"Increase playback speed")!=null)return speedApply(rate);
  s=menu();JSONObject n=null;for(JSONObject row:nodes(s))if((row.optString("text")+" "+row.optString("description")).trim().startsWith("Playback speed")&&row.has("clickTarget")){n=row;break;}
  if(n!=null){accepted(tap(s,n));return speedApply(rate);}
  return new JSONObject().put("status","visual_target_required").put("target","Playback speed").put("continuation","youtube_speed_apply").put("windowId",s.getInt("windowId"));
 }
 double stableSpeed()throws Exception{
  double last=Double.NaN;long stable=0,until=SystemClock.elapsedRealtime()+1700;
  do{double current=speedValue(screen());long now=SystemClock.elapsedRealtime();if(current!=last){last=current;stable=now;}else if(now-stable>=350)return current;Thread.sleep(80);}while(SystemClock.elapsedRealtime()<until);
  throw new Exception("Speed value did not settle");
 }
 JSONObject speedApply(double rate)throws Exception{
  if(!Double.isFinite(rate)||rate<0.25||rate>3||Math.abs(rate*4-Math.rint(rate*4))>0.001)throw new IllegalArgumentException("Invalid speed");
  JSONObject s=waitLabel("Increase playback speed",2500);double current=stableSpeed();s=screen();boolean changed=Math.abs(current-rate)>0.001;
  if(changed){
   // Prefer the visible preset closest to the target, then the labeled +/- buttons.
   JSONObject best=null;double distance=Math.abs(current-rate);
   for(JSONObject n:nodes(s)){String d=n.optString("description");if(!n.has("clickTarget")||!d.matches("[0-9]+(?:\\.[0-9]+)?,.*"))continue;double value=Double.parseDouble(d.substring(0,d.indexOf(',')));if(Math.abs(value-rate)<distance){best=n;distance=Math.abs(value-rate);}}
   if(best!=null){playbackDispatched=true;accepted(tap(s,best));Thread.sleep(100);s=screen();current=speedValue(s);}
   for(int i=0;i<60&&Math.abs(current-rate)>0.001;i++){
    String control=current<rate?"Increase playback speed":"Decrease playback speed";double old=current;playbackDispatched=true;press(s,control);
    long until=SystemClock.elapsedRealtime()+600;do{s=screen();current=speedValue(s);if(Math.abs(old-current)>0.001)break;Thread.sleep(40);}while(SystemClock.elapsedRealtime()<until);
    if(Math.abs(old-current)<0.001||Math.abs(current-rate)>Math.abs(old-rate)+0.001)throw new Exception("Speed adjustment did not converge; stop without retrying");
   }
  }
  current=stableSpeed();if(Math.abs(current-rate)>0.001)throw new Exception("Requested speed not confirmed");
  accepted(call(action("back")));return new JSONObject().put("status","verified").put("speed",current).put("alreadyInRequestedState",!changed).put("verification","Read the actual speed control after selection");
 }
 JSONObject execute(String operation,JSONObject p)throws Exception{
  deadline=SystemClock.elapsedRealtime()+16000;playbackDispatched=false;phase=operation;
  try{
   switch(operation){
    case "youtube_inspect":return screen();
    case "youtube_fullscreen":return fullScreen(p.getBoolean("enabled"));
    case "youtube_minimize":return minimize();
    case "youtube_navigate":return adjacent(p.getString("direction"));
    case "youtube_autoplay":return autoplay(p.getBoolean("enabled"));
    case "youtube_loop":return loop(p.getBoolean("enabled"));
    case "youtube_speed":return speed(p.getDouble("rate"));
    case "youtube_speed_apply":return speedApply(p.getDouble("rate"));
    case "youtube_search":return search(p.getString("query"));
    case "youtube_results":{JSONObject s=screen(),q=byId(s,PKG+":id/search_query");if(q==null)throw new Exception("YouTube search results are not visible");query=q.getString("text");return new JSONObject().put("status","verified").put("query",query).put("results",collect(s)).put("scope","Currently visible regular video results");}
    case "youtube_play":return play(p.getString("resultId"));
    case "youtube_state":return state();
    case "youtube_playback":{String action=p.getString("action");if(!action.equals("pause")&&!action.equals("resume"))throw new Exception("Unsupported playback action");return playback(action.equals("resume"));}
    default:throw new Exception("Unknown YouTube operation");
   }
  }catch(Exception e){return new JSONObject().put("status",playbackDispatched?"uncertain":"needs_assistance").put("reason",e.getMessage()).put("phase",phase).put("app","YouTube").put("playbackMayHaveChanged",playbackDispatched);}
 }
}
