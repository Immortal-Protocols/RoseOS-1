package dev.pixel.voice;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Calendar;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.TimeZone;

/**
 * Project 2: the snapshot and registry the phone builds for a composer request (workspace-contract.md,
 * "Registry and ids"). Every id the snapshot offers resolves in the registry and nothing else does, labels
 * are bounded display text, the version follows the entries, and a layout over those ids passes the phone's
 * own check. Pure JVM; no Android, no model.
 */
public final class WorkspaceSnapshotTest {
 static int checks;
 static void expect(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}

 static final WorkspaceSnapshot.App CLOCK=new WorkspaceSnapshot.App("com.google.android.deskclock","com.android.deskclock.DeskClock","Clock","tools",2);
 static final WorkspaceSnapshot.App NOTES=new WorkspaceSnapshot.App("com.example.notes","com.example.notes.Main","Notes",null,1);
 static final WorkspaceSnapshot.App MUSIC=new WorkspaceSnapshot.App("com.example.music","com.example.music.Player","Music","media",0);
 static final WorkspaceSnapshot.App ROSE=new WorkspaceSnapshot.App("dev.pixel.voice","dev.pixel.voice.MainActivity","Rose Agent",null,0);

 static Calendar at(int hour){Calendar c=Calendar.getInstance(TimeZone.getTimeZone("UTC"));c.setTimeInMillis(0);c.set(Calendar.HOUR_OF_DAY,hour);return c;}
 static final long NOW=1794600000000L;

 static WorkspaceSnapshot sample(java.util.Collection<String> pinned){
  return WorkspaceSnapshot.build(Arrays.asList(CLOCK,NOTES,MUSIC,ROSE),
   Arrays.asList(new WorkspaceSnapshot.Shortcut(MUSIC,"play_liked","Play liked songs")),
   Arrays.asList(new WorkspaceSnapshot.Template("timer","Start a timer",CLOCK)),
   Arrays.asList(new WorkspaceSnapshot.Task("grocery","Make a grocery list","Make a grocery list from tonight's recipe?","Draft a grocery list for the recipe on screen",ROSE)),
   new LinkedHashSet<>(pinned),NOW,at(18));
 }

 @SuppressWarnings("unchecked")
 static List<Map<String,Object>> list(WorkspaceSnapshot s,String kind){return (List<Map<String,Object>>)s.snapshot.get(kind);}

 public static void main(String[] args)throws Exception{
  ids();labels();bounds();version();request();roundTrip();
  System.out.println("WorkspaceSnapshotTest: "+checks+" checks passed");
 }

 static void ids(){
  WorkspaceSnapshot s=sample(Collections.<String>emptyList());
  String clock="app:com.google.android.deskclock/com.android.deskclock.DeskClock";
  expect(list(s,"apps").size()==4,"four apps");
  expect(clock.equals(list(s,"apps").get(0).get("id")),"app id is app:<package>/<activity>");
  expect(WorkspaceLayout.APPS.equals(s.registry.kind(clock)),"apps resolve as apps");
  expect(s.app(clock)==CLOCK,"an app id opens its own activity");
  expect("tools".equals(list(s,"apps").get(0).get("category")),"category carried");
  expect(Long.valueOf(2).equals(list(s,"apps").get(0).get("recentRank")),"recent rank carried");
  expect(!list(s,"apps").get(2).containsKey("recentRank"),"unknown recent rank is left out");
  expect(!list(s,"apps").get(1).containsKey("category"),"no category is left out");

  expect(!s.registry.has("launch:com.google.android.deskclock/com.android.deskclock.DeskClock"),"no launch copy of each app");
  expect(list(s,"actions").size()==3,"shortcut, template and task: "+list(s,"actions").size());
  String sc="sc:com.example.music/play_liked";
  expect("shortcut".equals(s.registry.actionKind(sc))&&s.shortcut(sc)!=null,"shortcut id is sc:<package>/<id>");
  expect("app:com.example.music/com.example.music.Player".equals(s.registry.target(sc)),"a shortcut targets its app");
  expect("deeplink".equals(s.registry.actionKind("intent:timer"))&&clock.equals(s.registry.target("intent:timer")),"intent template targets the app that handles it");
  expect(WorkspaceLayout.AGENT_TASK.equals(s.registry.actionKind("task:grocery")),"Rose task is agent_task");
  expect("app:dev.pixel.voice/dev.pixel.voice.MainActivity".equals(s.registry.target("task:grocery")),"a Rose task targets Rose Agent");

  Map<String,Object> task=null,timer=null;
  for(Map<String,Object> a:list(s,"actions")){if("task:grocery".equals(a.get("id")))task=a;if("intent:timer".equals(a.get("id")))timer=a;}
  expect(task!=null&&Boolean.TRUE.equals(task.get("requiresConfirmation")),"a Rose task always asks first");
  expect(Boolean.FALSE.equals(timer.get("requiresConfirmation")),"a direct action does not");
  String json=WorkspaceLayout.json(s.snapshot);
  expect(!json.contains("tonight's recipe")&&!json.contains("Draft a grocery"),"the task's ask and goal never enter the snapshot");
  for(Map<String,Object> a:list(s,"actions"))expect(s.registry.has((String)a.get("targetId")),"every action target is in the snapshot: "+a.get("id"));
  expect(list(s,"notifications").isEmpty()&&list(s,"content").isEmpty(),"no notifications or content before D1 and a content source");
  expect("evening".equals(s.snapshot.get("timeOfDay")),"time of day");
  expect(Long.valueOf(NOW).equals(s.snapshot.get("createdAtMs")),"created time");
  expect(s.present().contains(sc)&&s.present().contains("task:grocery")&&s.present().contains(clock),"present covers apps and actions");

  // Things that cannot be opened are never offered.
  WorkspaceSnapshot.App gone=new WorkspaceSnapshot.App("com.gone","com.gone.Main","Gone",null,0);
  WorkspaceSnapshot t=WorkspaceSnapshot.build(Arrays.asList(CLOCK),
   Arrays.asList(new WorkspaceSnapshot.Shortcut(gone,"x","Orphan shortcut"),new WorkspaceSnapshot.Shortcut(CLOCK,"a/b","Bad id")),
   Arrays.asList(new WorkspaceSnapshot.Template("map","Directions",null),new WorkspaceSnapshot.Template("music","Music",gone)),
   Arrays.asList(new WorkspaceSnapshot.Task("grocery","Grocery","Ask?","Goal",ROSE)),
   new HashSet<String>(),NOW,at(9));
  expect(list(t,"actions").isEmpty(),"orphan shortcuts, unresolved templates and ambiguous ids are left out: "+list(t,"actions").size());
  expect(!t.registry.has("task:grocery"),"no Rose task without Rose Agent in the snapshot");
  expect("morning".equals(t.snapshot.get("timeOfDay")),"morning");
 }

 static void labels(){
  expect("Clock".equals(WorkspaceSnapshot.label("  Clock\n",60)),"trimmed");
  expect("Two words".equals(WorkspaceSnapshot.label("Two\u0000\u0007 \t words",60)),"control characters become one space");
  expect("ab".equals(WorkspaceSnapshot.label("a‎b",60)),"invisible format characters dropped");
  expect(WorkspaceSnapshot.label(" \u0001 ",60)==null,"blank is no label");
  String sixtyOne=new String(new char[61]).replace('\0','x');
  expect(WorkspaceSnapshot.label(sixtyOne,60).length()==60,"cut to 60");
  String emoji=new String(new char[61]).replace("\0","🍝");
  String cut=WorkspaceSnapshot.label(emoji,60);
  expect(cut.codePointCount(0,cut.length())==60&&!Character.isHighSurrogate(cut.charAt(cut.length()-1)),"cut by code points, never inside a pair");
  WorkspaceSnapshot s=WorkspaceSnapshot.build(Arrays.asList(new WorkspaceSnapshot.App("p.q","p.q.A","\u0000",null,0),CLOCK),
   Collections.<WorkspaceSnapshot.Shortcut>emptyList(),Collections.<WorkspaceSnapshot.Template>emptyList(),Collections.<WorkspaceSnapshot.Task>emptyList(),new HashSet<String>(),NOW,at(1));
  expect(list(s,"apps").size()==1,"an app with no visible label is left out");
  expect("night".equals(s.snapshot.get("timeOfDay")),"night");
  expect(!WorkspaceSnapshot.component("a b")&&!WorkspaceSnapshot.component("a/b")&&!WorkspaceSnapshot.component("")&&WorkspaceSnapshot.component("com.a.B$Inner"),"component names");
 }

 static void bounds(){
  List<WorkspaceSnapshot.App> many=new ArrayList<>();List<WorkspaceSnapshot.Shortcut> shortcuts=new ArrayList<>();
  for(int i=0;i<WorkspaceSnapshot.MAX_APPS+10;i++)many.add(new WorkspaceSnapshot.App("p"+i,"p"+i+".A","App "+i,null,0));
  many.add(new WorkspaceSnapshot.App("p0","p0.A","Duplicate",null,0));
  for(int i=0;i<WorkspaceSnapshot.MAX_ACTIONS+5;i++)shortcuts.add(new WorkspaceSnapshot.Shortcut(many.get(i),"s"+i,"Shortcut "+i));
  shortcuts.add(new WorkspaceSnapshot.Shortcut(many.get(0),"s0","Duplicate"));
  WorkspaceSnapshot s=WorkspaceSnapshot.build(many,shortcuts,Collections.<WorkspaceSnapshot.Template>emptyList(),Collections.<WorkspaceSnapshot.Task>emptyList(),new HashSet<String>(),NOW,at(12));
  expect(list(s,"apps").size()==WorkspaceSnapshot.MAX_APPS,"apps bounded");
  expect(list(s,"actions").size()==WorkspaceSnapshot.MAX_ACTIONS,"actions bounded");
  expect("afternoon".equals(s.snapshot.get("timeOfDay")),"afternoon");
  Map<String,Object> first=list(s,"apps").get(0);
  expect("App 0".equals(first.get("label")),"the first of a duplicate id wins");
 }

 static void version(){
  WorkspaceSnapshot a=sample(Collections.<String>emptyList()),b=sample(Collections.<String>emptyList());
  expect(a.snapshotVersion.equals(b.snapshotVersion),"same entries, same version");
  expect(a.snapshotVersion.matches("s-[0-9a-f]{12}"),"version shape");
  expect(a.registry.snapshotVersion.equals(a.snapshotVersion)&&a.snapshotVersion.equals(a.snapshot.get("snapshotVersion")),"registry and snapshot agree");
  WorkspaceSnapshot later=WorkspaceSnapshot.build(Arrays.asList(CLOCK,NOTES,MUSIC,ROSE),
   Arrays.asList(new WorkspaceSnapshot.Shortcut(MUSIC,"play_liked","Play liked songs")),
   Arrays.asList(new WorkspaceSnapshot.Template("timer","Start a timer",CLOCK)),
   Arrays.asList(new WorkspaceSnapshot.Task("grocery","Make a grocery list","Make a grocery list from tonight's recipe?","Draft a grocery list for the recipe on screen",ROSE)),
   new HashSet<String>(),NOW+3600000,at(23));
  expect(a.snapshotVersion.equals(later.snapshotVersion),"time alone does not change the version");
  WorkspaceSnapshot fewer=WorkspaceSnapshot.build(Arrays.asList(CLOCK,NOTES,ROSE),Collections.<WorkspaceSnapshot.Shortcut>emptyList(),
   Arrays.asList(new WorkspaceSnapshot.Template("timer","Start a timer",CLOCK)),Collections.<WorkspaceSnapshot.Task>emptyList(),new HashSet<String>(),NOW,at(18));
  expect(!a.snapshotVersion.equals(fewer.snapshotVersion),"a removed app changes the version");
  WorkspaceSnapshot pinned=sample(Arrays.asList("app:com.example.notes/com.example.notes.Main","app:gone/x"));
  expect(!a.snapshotVersion.equals(pinned.snapshotVersion),"pins change the version");
  expect(pinned.snapshot.get("pinned").equals(Arrays.asList("app:com.example.notes/com.example.notes.Main")),"a pin that is not present is left out");
 }

 @SuppressWarnings("unchecked")
 static void request()throws Exception{
  WorkspaceSnapshot s=sample(Collections.<String>emptyList());
  Map<String,Object> r=(Map<String,Object>)MiniJson.parse(WorkspaceSnapshot.request("page-1","button",null,s,4000,null));
  expect("rose-layout-v1".equals(r.get("contract"))&&"page-1".equals(r.get("requestId"))&&"button".equals(r.get("trigger")),"request header");
  expect(r.get("prompt")==MiniJson.NULL,"Arrange for me has no prompt");
  expect("cat-1".equals(r.get("catalogVersion")),"catalog");
  Map<String,Object> limits=(Map<String,Object>)r.get("limits");
  expect(Long.valueOf(6).equals(limits.get("maxSections"))&&Long.valueOf(8).equals(limits.get("maxItemsPerSection"))&&Long.valueOf(20).equals(limits.get("maxTotalItems"))&&Long.valueOf(4000).equals(limits.get("deadlineMs")),"limits restate cat-1");
  expect(!r.containsKey("avoid"),"no avoid outside regenerate");
  expect(s.snapshotVersion.equals(((Map<String,Object>)r.get("snapshot")).get("snapshotVersion")),"snapshot embedded");

  List<String> prev=new ArrayList<>();for(int i=0;i<70;i++)prev.add("app:p"+i+"/A");prev.add("app:p0/A");prev.add("");
  Map<String,Object> g=(Map<String,Object>)MiniJson.parse(WorkspaceSnapshot.request("page-2","direct_text","dinner",s,4000,prev));
  List<Object> avoid=(List<Object>)g.get("avoid");
  expect(avoid.size()==WorkspaceSnapshot.MAX_AVOID&&"app:p0/A".equals(avoid.get(0))&&new HashSet<>(avoid).size()==avoid.size(),"avoid: unique, at most 64");
  expect("dinner".equals(g.get("prompt")),"prompt carried");

  // The composer's regenerate supplies the previous layout's ids as avoid.
  WorkspaceState state=new WorkspaceState();WorkspaceComposer c=new WorkspaceComposer(state);
  int gen=c.begin("dinner",false);
  expect(c.avoid().isEmpty(),"a fresh request avoids nothing");
  String grid="{\"component\":\"grid\",\"title\":\"Apps\",\"items\":[\"app:com.google.android.deskclock/com.android.deskclock.DeskClock\",\"app:com.example.notes/com.example.notes.Main\"],\"actions\":[]}";
  c.finish(gen,result(c.requestId(),s.snapshotVersion,grid),s.registry);
  c.regenerate();
  expect(c.avoid().equals(Arrays.asList("app:com.google.android.deskclock/com.android.deskclock.DeskClock","app:com.example.notes/com.example.notes.Main")),"regenerate avoids the previous layout's ids");
  c.begin("something else",false);
  expect(c.avoid().isEmpty(),"a new request clears avoid");
 }

 static String result(String requestId,String version,String sections){
  return "{\"contract\":\"rose-layout-v1\",\"requestId\":\""+requestId+"\",\"status\":\"ok\",\"layout\":{\"snapshotVersion\":\""+version
   +"\",\"catalogVersion\":\"cat-1\",\"title\":\"Tonight\",\"sections\":["+sections+"]}}";
 }

 /** A layout over the snapshot's own ids passes the phone's check; the same layout on a newer snapshot is stale. */
 static void roundTrip()throws Exception{
  WorkspaceSnapshot s=sample(Collections.<String>emptyList());
  WorkspaceState state=new WorkspaceState();
  String grid="{\"component\":\"grid\",\"title\":\"Kitchen\",\"items\":[\"app:com.google.android.deskclock/com.android.deskclock.DeskClock\",\"intent:timer\",\"app:com.example.music/com.example.music.Player\"],\"actions\":[]}";
  String row="{\"component\":\"button\",\"title\":\"Quick actions\",\"items\":[\"sc:com.example.music/play_liked\",\"task:grocery\"]}";
  WorkspaceLayout l=WorkspaceLayout.check(result("r1",s.snapshotVersion,grid+","+row),"r1",s.registry,state.keys());
  expect(l.sections.size()==2,"a layout over real ids passes");
  expect(l.available(s.present()).sections.size()==2,"everything is present to render");
  try{WorkspaceLayout.check(result("r1",s.snapshotVersion,"{\"component\":\"grid\",\"title\":\"T\",\"items\":[\"task:grocery\",\"intent:timer\"],\"actions\":[]}"),"r1",s.registry,state.keys());expect(false,"task in grid");}
  catch(WorkspaceLayout.Invalid e){expect("disallowed_ref".equals(e.failure),"a Rose task never sits in a grid");}
  WorkspaceSnapshot fewer=WorkspaceSnapshot.build(Arrays.asList(CLOCK,NOTES,ROSE),Collections.<WorkspaceSnapshot.Shortcut>emptyList(),
   Arrays.asList(new WorkspaceSnapshot.Template("timer","Start a timer",CLOCK)),Collections.<WorkspaceSnapshot.Task>emptyList(),new HashSet<String>(),NOW,at(18));
  try{WorkspaceLayout.check(result("r1",s.snapshotVersion,grid),"r1",fewer.registry,state.keys());expect(false,"stale");}
  catch(WorkspaceLayout.Invalid e){expect("stale_snapshot".equals(e.failure),"a layout for an older snapshot is stale");}
  // Rendering a saved layout after Music (and the task) went away leaves them out: the grid keeps two, and
  // the button row, with nothing left, is not drawn.
  WorkspaceLayout shown=l.available(fewer.present());
  expect(shown.sections.size()==1,"an emptied row is not drawn");
  expect(shown.sections.get(0).items.size()==2&&!shown.sections.get(0).items.contains("app:com.example.music/com.example.music.Player"),"a removed app is left out at render");
 }
}
