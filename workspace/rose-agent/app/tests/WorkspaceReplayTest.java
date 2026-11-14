package dev.pixel.voice;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Calendar;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.TimeZone;
import rose.models.composer.ScriptedInference;

/**
 * Project 2: a replay of the approved HiFi r2 behaviour contracts (prototypes/rose-hifi/HANDOFF-WORKSPACE.md,
 * "Behaviour contracts" and "How to try it"). Each step goes through the real phone path:
 * <ol>
 *   <li>the phone's snapshot and request (WorkspaceSnapshot);</li>
 *   <li>Models' real composer core, with its ScriptedInference in place of the HiFi's simulated generation;</li>
 *   <li>the phone's own check (WorkspaceLayout.check);</li>
 *   <li>the saved state, the request fence and the conversational dispatcher.</li>
 * </ol>
 * ScriptedInference is NOT A MODEL, and nothing here measures one. The replay checks only the contracts that
 * hold without a screen. Geometry, motion, gestures and accessibility need Android, and are not claimed.
 */
public final class WorkspaceReplayTest {
 static int checks;
 static void expect(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}

 static final WorkspaceSnapshot.App CLOCK=new WorkspaceSnapshot.App("com.google.android.deskclock","com.android.deskclock.DeskClock","Clock","tools",0);
 static final WorkspaceSnapshot.App NOTES=new WorkspaceSnapshot.App("com.example.notes","com.example.notes.Main","Notes",null,0);
 static final WorkspaceSnapshot.App MUSIC=new WorkspaceSnapshot.App("com.example.music","com.example.music.Player","Music","audio",0);
 static final WorkspaceSnapshot.App FIREFOX=new WorkspaceSnapshot.App("org.mozilla.firefox","org.mozilla.firefox.App","Firefox",null,0);
 static final WorkspaceSnapshot.App CALENDAR=new WorkspaceSnapshot.App("com.example.calendar","com.example.calendar.Main","Calendar","productivity",0);

 static Calendar at(int hour){Calendar c=Calendar.getInstance(TimeZone.getTimeZone("UTC"));c.setTimeInMillis(0);c.set(Calendar.HOUR_OF_DAY,hour);return c;}

 /** The phone as the first build sees it: apps, a music shortcut and the timer template. */
 static WorkspaceSnapshot phone(List<WorkspaceSnapshot.App> apps,java.util.Set<String> pinned){
  List<WorkspaceSnapshot.Shortcut> sc=new ArrayList<>();if(apps.contains(MUSIC))sc.add(new WorkspaceSnapshot.Shortcut(MUSIC,"play_liked","Play liked songs"));
  return WorkspaceSnapshot.build(apps,sc,Arrays.asList(new WorkspaceSnapshot.Template("timer","Start a timer",CLOCK)),
   Collections.<WorkspaceSnapshot.Task>emptyList(),pinned,1794600000000L,at(18));
 }
 static final List<WorkspaceSnapshot.App> ALL=Arrays.asList(CLOCK,NOTES,MUSIC,FIREFOX,CALENDAR);

 /** The aliases the composer shows the model for a request, by label (so a script can name what it means). */
 static Map<String,String> aliases(String request){
  ScriptedInference probe=new ScriptedInference("{}");
  new WorkspaceEngine(probe).compose(request,null);
  Map<String,String> byLabel=new java.util.LinkedHashMap<>();
  for(String line:probe.calls.get(0).user.split("\n")){
   String t=line.trim();if(!t.matches("[ax]\\d+ .*"))continue;
   String alias=t.substring(0,t.indexOf(' ')),rest=t.substring(t.indexOf(' ')+1);
   int cut=rest.indexOf(" [");String label=cut>=0?rest.substring(0,cut):rest;
   byLabel.put(label,alias);
  }
  return byLabel;
 }
 static String grid(String title,Map<String,String> a,String... labels){return section("grid",title,a,true,labels);}
 static String row(String title,Map<String,String> a,String... labels){return section("button",title,a,false,labels);}
 static String section(String component,String title,Map<String,String> a,boolean actions,String... labels){
  StringBuilder b=new StringBuilder("{\"component\":\""+component+"\",\"title\":\""+title+"\",\"items\":[");
  for(int i=0;i<labels.length;i++){String alias=a.get(labels[i]);if(alias==null)throw new IllegalStateException("no alias for "+labels[i]);if(i>0)b.append(',');b.append('"').append(alias).append('"');}
  return b.append(']').append(actions?",\"actions\":[]":"").append('}').toString();
 }
 static String layout(String title,String... sections){return "{\"title\":\""+title+"\",\"sections\":["+String.join(",",sections)+"]}";}

 /** One request through the whole path. script builds the scripted output from the aliases the model would see. */
 interface Script { String output(Map<String,String> aliases); }
 static WorkspaceComposer.Outcome run(WorkspaceComposer composer,int gen,WorkspaceSnapshot s,Script script){
  String request=WorkspaceSnapshot.request(composer.requestId(),composer.trigger(),composer.prompt(),s,WorkspaceEngine.DEADLINE_MS,composer.avoid());
  String out=script.output(aliases(request));
  String result=new WorkspaceEngine(new ScriptedInference(out)).compose(request,null);
  return composer.finish(gen,result,s.registry);
 }
 static String titles(WorkspaceState st){StringBuilder b=new StringBuilder();for(WorkspaceLayout.Section c:st.layout().sections)b.append(c.title).append('|');return b.toString();}

 public static void main(String[] args)throws Exception{
  cooking();failureKeepsLayout();supersede();retention();removedApp();conversation();
  System.out.println("WorkspaceReplayTest: "+checks+" checks passed (scripted composer, NOT A MODEL)");
 }

 /** "dinner" → cooking; pin the quick-actions row; Regenerate keeps it at its index; Undo steps back through layouts and pins. */
 static void cooking(){
  WorkspaceState st=new WorkspaceState();WorkspaceComposer c=new WorkspaceComposer(st);
  WorkspaceSnapshot s=phone(ALL,new HashSet<String>());
  int g=c.begin("dinner",false);
  expect("Composing on this phone…".equals(c.status())&&c.busy(),"loading status, controls disabled in place");
  WorkspaceComposer.Outcome o=run(c,g,s,a->layout("Cooking tonight",grid("Kitchen apps",a,"Clock","Notes","Music"),row("Quick actions",a,"Start a timer","Play liked songs"),grid("Browse",a,"Firefox","Calendar")));
  expect(o==WorkspaceComposer.Outcome.COMMITTED&&titles(st).equals("Kitchen apps|Quick actions|Browse|"),"cooking composed: "+titles(st));
  expect("“dinner”".equals(c.status()),"the status line echoes the request in quotes");
  String row=st.layout().sections.get(1).key;
  st.togglePin(row);
  // Regenerate: a different arrangement of the same request. The pinned row keeps index 1, and its ids stay out
  // of the composer's context, so the composer cannot repeat them.
  WorkspaceSnapshot pinned=phone(ALL,new HashSet<>(Arrays.asList("intent:timer","sc:com.example.music/play_liked")));
  int g2=c.regenerate();
  expect(c.avoid().contains("app:com.google.android.deskclock/com.android.deskclock.DeskClock"),"regenerate avoids the previous ids");
  WorkspaceComposer.Outcome o2=run(c,g2,pinned,a->{
   expect(!a.containsKey("Start a timer")&&!a.containsKey("Play liked songs"),"pinned items are not offered again");
   return layout("Cooking, again",grid("Browse",a,"Firefox","Calendar"),grid("Kitchen",a,"Clock","Notes","Music"));
  });
  expect(o2==WorkspaceComposer.Outcome.COMMITTED,"regenerated");
  expect(st.layout().sections.get(1).key.equals(row)&&st.pinned(row),"pins keep their position: index 1 after regenerate ("+titles(st)+")");
  expect(titles(st).equals("Browse|Quick actions|Kitchen|"),"only the unpinned cards around it moved: "+titles(st));
  // Undo steps back through layouts and pins: first the regenerate, then the pin.
  expect(st.undo()&&titles(st).equals("Kitchen apps|Quick actions|Browse|")&&st.pinned(row),"undo the regenerate: the pinned layout");
  expect(st.undo()&&!st.pinned(row),"undo the pin");
 }

 /** "Next generation fails": the status says so; the layout and pins are untouched. */
 static void failureKeepsLayout(){
  WorkspaceState st=new WorkspaceState();WorkspaceComposer c=new WorkspaceComposer(st);
  WorkspaceSnapshot s=phone(ALL,new HashSet<String>());
  int g=c.begin(null,false);
  run(c,g,s,a->layout("Best guess",grid("Apps",a,"Clock","Notes","Music")));
  String key=st.layout().sections.get(0).key;st.togglePin(key);
  WorkspaceLayout before=st.layout();long revision=st.revision();
  int g2=c.begin("work",false);
  WorkspaceComposer.Outcome o=run(c,g2,s,a->"not a layout");
  expect(o==WorkspaceComposer.Outcome.REJECTED&&"invalid_output".equals(c.lastFailure()),"malformed output is invalid_output");
  expect(st.layout()==before&&st.pinned(key)&&st.revision()==revision,"layout, pins and revision untouched");
  expect("Couldn't arrange · kept your last layout".equals(c.status()),"failure status");
  // A layout naming an id the phone never offered is refused too (the model cannot create ids).
  int g3=c.begin("work",false);
  WorkspaceComposer.Outcome o3=run(c,g3,s,a->"{\"title\":\"x\",\"sections\":[{\"component\":\"grid\",\"title\":\"x\",\"items\":[\"a99\",\"a98\"],\"actions\":[]}]}");
  expect(o3==WorkspaceComposer.Outcome.REJECTED&&"unknown_ref".equals(c.lastFailure())&&st.layout()==before,"an invented id is unknown_ref; layout kept");
 }

 /** A newer request supersedes an older one; a late result is ignored. */
 static void supersede(){
  WorkspaceState st=new WorkspaceState();WorkspaceComposer c=new WorkspaceComposer(st);
  WorkspaceSnapshot s=phone(ALL,new HashSet<String>());
  int older=c.begin("dinner",false);
  String olderRequest=WorkspaceSnapshot.request(c.requestId(),c.trigger(),c.prompt(),s,WorkspaceEngine.DEADLINE_MS,null);
  int newer=c.begin("work",false);
  WorkspaceComposer.Outcome n=run(c,newer,s,a->layout("Work",grid("Work apps",a,"Calendar","Notes")));
  String late=new WorkspaceEngine(new ScriptedInference(layout("Dinner",grid("Kitchen",a(olderRequest),"Clock","Music")))).compose(olderRequest,null);
  expect(n==WorkspaceComposer.Outcome.COMMITTED&&c.finish(older,late,s.registry)==WorkspaceComposer.Outcome.IGNORED,"the late result is ignored");
  expect("Work".equals(st.layout().title),"the newer request's layout stays");
 }
 static Map<String,String> a(String request){return aliases(request);}

 /** Retention: undo keeps the last 20 steps, recents the last 3, and all of it survives a restart. */
 static void retention(){
  WorkspaceState st=new WorkspaceState();WorkspaceComposer c=new WorkspaceComposer(st);
  WorkspaceSnapshot s=phone(ALL,new HashSet<String>());
  String[] asks={"dinner","work","unwind","reading","music"};
  for(int i=0;i<25;i++){
   int g=c.begin(asks[i%asks.length]+" "+i,false);final int n=i;
   run(c,g,s,a->layout("Layout "+n,grid("Apps "+n,a,"Clock","Notes")));
  }
  expect(st.undoDepth()==WorkspaceState.UNDO&&WorkspaceState.UNDO==20,"undo keeps the last 20");
  expect(st.recent().size()==WorkspaceState.RECENT&&WorkspaceState.RECENT==3&&"music 24".equals(st.recent().get(0).request),"3 recents, newest first");
  WorkspaceState back=WorkspaceState.fromJson(st.toJson());
  expect(back.layout().title.equals("Layout 24")&&back.undoDepth()==20&&back.recent().size()==3&&back.revision()==st.revision(),"survives a restart");
  expect(back.reopen(2)&&back.layout().title.equals("Layout 22"),"a recent reopens");
 }

 /** Removed content: an app uninstalled after composing is left out at render; a result for the old snapshot is stale. */
 static void removedApp(){
  WorkspaceState st=new WorkspaceState();WorkspaceComposer c=new WorkspaceComposer(st);
  WorkspaceSnapshot s=phone(ALL,new HashSet<String>());
  int g=c.begin(null,false);
  run(c,g,s,a->layout("Best guess",grid("Apps",a,"Clock","Notes","Music"),row("Quick actions",a,"Play liked songs")));
  WorkspaceSnapshot without=phone(Arrays.asList(CLOCK,NOTES,FIREFOX,CALENDAR),new HashSet<String>());
  WorkspaceLayout shown=st.layout().available(without.present());
  expect(shown.sections.size()==1&&shown.sections.get(0).items.size()==2,"Music and its shortcut leave the screen; the emptied row is not drawn");
  expect(st.layout().sections.size()==2,"the saved layout itself is not reshuffled");
  int g2=c.begin("dinner",false);
  String request=WorkspaceSnapshot.request(c.requestId(),c.trigger(),c.prompt(),s,WorkspaceEngine.DEADLINE_MS,null);
  String result=new WorkspaceEngine(new ScriptedInference(layout("Dinner",grid("Kitchen",a(request),"Clock","Music")))).compose(request,null);
  expect(c.finish(g2,result,without.registry)==WorkspaceComposer.Outcome.REJECTED&&"stale_snapshot".equals(c.lastFailure()),"composed for the old phone: stale_snapshot");
 }

 /** From an app: ask Rose for cooking. It prepares in the background, never leaves the app, and the offer goes once the workspace is reached. */
 static void conversation()throws Exception{
  WorkspaceState st=new WorkspaceState();WorkspaceComposer c=new WorkspaceComposer(st);WorkspaceRequests d=new WorkspaceRequests(st,c);
  WorkspaceSnapshot s=phone(ALL,new HashSet<String>());
  final List<Map<String,Object>> got=new ArrayList<>();
  @SuppressWarnings("unchecked") Map<String,Object> params=(Map<String,Object>)MiniJson.parse("{\"schemaVersion\":1,\"requestId\":\"conv-1\",\"kind\":\"arrange\"}");
  WorkspaceRequests.Step step=d.conversation(WorkspaceAgent.parse(params),false,false,got::add);
  expect(c.offer()==WorkspaceComposer.Offer.PREPARING&&c.offerText().startsWith("Preparing workspace"),"Preparing workspace…");
  WorkspaceComposer.Outcome o=run(c,step.gen,s,a->layout("Cooking",grid("Kitchen apps",a,"Clock","Notes","Music")));
  d.finished(step.gen,o,false);
  expect(c.offer()==WorkspaceComposer.Offer.READY&&c.offerText().startsWith("Workspace ready"),"Workspace ready · Show workspace");
  expect("prepared".equals(got.get(0).get("status"))&&Long.valueOf(st.revision()).equals(got.get(0).get("revision")),"App Core hears prepared, with the revision");
  c.workspaceShown();
  expect(c.offer()==WorkspaceComposer.Offer.NONE&&c.offerText()==null,"the offer goes once the workspace is reached");
  // A failing one from an app: "Couldn't prepare workspace · kept your last layout".
  WorkspaceLayout before=st.layout();
  @SuppressWarnings("unchecked") Map<String,Object> p2=(Map<String,Object>)MiniJson.parse("{\"schemaVersion\":1,\"requestId\":\"conv-2\",\"kind\":\"regenerate\"}");
  WorkspaceRequests.Step s2=d.conversation(WorkspaceAgent.parse(p2),false,false,got::add);
  WorkspaceComposer.Outcome o2=run(c,s2.gen,s,a->"{}");
  d.finished(s2.gen,o2,false);
  expect("Couldn't prepare workspace · kept your last layout".equals(c.offerText())&&st.layout()==before,"failed offer, layout kept");
  expect("rejected".equals(got.get(1).get("status"))&&"invalid_output".equals(got.get(1).get("reason")),"App Core hears rejected with the token");
 }
}
