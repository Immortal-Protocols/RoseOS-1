package dev.pixel.voice;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Calendar;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.TimeZone;
import rose.models.composer.Catalog;
import rose.models.composer.LocalInference;
import rose.models.composer.Refused;

/**
 * Project 2: the page's path from a request to a rendered layout, with no model and no device:
 * <ul>
 *   <li>the phone's snapshot and request;</li>
 *   <li>Models' real composer (workspace/models/local-composer/java), with the labelled starter in place of a model;</li>
 *   <li>the phone's own check and saved state;</li>
 *   <li>request-to-render timing (WorkspaceTiming), the renderer half Models' contract leaves to OS Product.</li>
 * </ul>
 */
public final class WorkspaceEngineTest {
 static int checks;
 static void expect(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}

 static final WorkspaceSnapshot.App CLOCK=new WorkspaceSnapshot.App("com.google.android.deskclock","com.android.deskclock.DeskClock","Clock","tools",0);
 static final WorkspaceSnapshot.App NOTES=new WorkspaceSnapshot.App("com.example.notes","com.example.notes.Main","Notes",null,0);
 static final WorkspaceSnapshot.App MUSIC=new WorkspaceSnapshot.App("com.example.music","com.example.music.Player","Music",null,0);
 static final WorkspaceSnapshot.App ROSE=new WorkspaceSnapshot.App("dev.pixel.voice","dev.pixel.voice.MainActivity","Rose Agent",null,0);

 static Calendar evening(){Calendar c=Calendar.getInstance(TimeZone.getTimeZone("UTC"));c.setTimeInMillis(0);c.set(Calendar.HOUR_OF_DAY,19);return c;}
 static WorkspaceSnapshot snapshot(List<WorkspaceSnapshot.App> apps,boolean withTask){
  return WorkspaceSnapshot.build(apps,
   Arrays.asList(new WorkspaceSnapshot.Shortcut(MUSIC,"play_liked","Play liked songs")),
   Arrays.asList(new WorkspaceSnapshot.Template("timer","Start a timer",CLOCK)),
   withTask?Arrays.asList(new WorkspaceSnapshot.Task("grocery","Make a grocery list","Make a list?","Draft a list",ROSE)):Collections.<WorkspaceSnapshot.Task>emptyList(),
   new HashSet<String>(),1794600000000L,evening());
 }
 static final List<WorkspaceSnapshot.App> APPS=Arrays.asList(CLOCK,NOTES,MUSIC,ROSE);

 public static void main(String[] args)throws Exception{
  catalogParity();starter();engine();timing();endToEnd();
  System.out.println("WorkspaceEngineTest: "+checks+" checks passed");
 }

 /** Models' cat-1 is OS Product's cat-1: the same bounds the phone's own check enforces. */
 static void catalogParity(){
  Catalog c=Catalog.cat1();
  expect(WorkspaceLayout.CATALOG.equals(c.version),"catalog version");
  expect(c.limits.maxSections==WorkspaceLayout.MAX_SECTIONS&&c.limits.maxItemsPerSection==WorkspaceLayout.MAX_ITEMS&&c.limits.maxTotalItems==WorkspaceLayout.MAX_TOTAL_ITEMS,"limits");
  expect(c.limits.deadlineMs==WorkspaceEngine.DEADLINE_MS,"deadline");
  expect(c.components.keySet().equals(WorkspaceLayout.CATALOG_RULES.keySet()),"the same five components");
  for(Map.Entry<String,Catalog.Component> e:c.components.entrySet()){
   WorkspaceLayout.Rule r=WorkspaceLayout.CATALOG_RULES.get(e.getKey());Catalog.Component m=e.getValue();
   expect(r.min==m.min&&r.max==m.max&&r.actions==m.actions,"bounds of "+e.getKey());
   expect(new HashSet<>(r.items).equals(new HashSet<>(m.items)),"item kinds of "+e.getKey());
   if(r.items.contains(WorkspaceLayout.ACTIONS))expect(r.tasksAsItems==!m.excludeActionKinds.contains(WorkspaceLayout.AGENT_TASK),"agent_task rule of "+e.getKey());
  }
 }

 static WorkspaceEngine starterEngine(){return new WorkspaceEngine(new WorkspaceStarter());}

 @SuppressWarnings("unchecked")
 static void starter()throws Exception{
  WorkspaceSnapshot s=snapshot(APPS,true);
  WorkspaceEngine e=starterEngine();
  expect(WorkspaceStarter.ID.equals(e.modelId()),"the starter says what it is");
  String result=e.compose(WorkspaceSnapshot.request("page-1","button",null,s,WorkspaceEngine.DEADLINE_MS,null),null);
  Map<String,Object> m=(Map<String,Object>)MiniJson.parse(result);
  expect("ok".equals(m.get("status")),"starter result is ok: "+result);
  expect("starter".equals(((Map<String,Object>)m.get("model")).get("id")),"the result names the starter, not a model");
  WorkspaceLayout l=WorkspaceLayout.check(result,"page-1",s.registry,new WorkspaceState().keys());
  expect(l.sections.size()==2,"a grid and a button row");
  expect("grid".equals(l.sections.get(0).component)&&l.sections.get(0).items.size()==4,"every app in the grid");
  expect("button".equals(l.sections.get(1).component),"then the direct actions");
  expect(l.sections.get(1).items.containsAll(Arrays.asList("sc:com.example.music/play_liked","intent:timer")),"shortcut and template offered");
  for(WorkspaceLayout.Section c:l.sections)expect(!c.items.contains("task:grocery"),"the starter never offers a Rose task");
  expect(WorkspaceStarter.TITLE.equals(l.title),"title");
  // The request's words never change the starter's answer.
  String other=e.compose(WorkspaceSnapshot.request("page-2","direct_text","something for dinner",s,WorkspaceEngine.DEADLINE_MS,null),null);
  WorkspaceLayout l2=WorkspaceLayout.check(other,"page-2",s.registry,new WorkspaceState().keys());
  expect(l2.sections.get(0).sameContent(l.sections.get(0))&&l2.sections.get(1).sameContent(l.sections.get(1)),"the starter ignores the words");

  // Too little to arrange.
  WorkspaceSnapshot one=WorkspaceSnapshot.build(Arrays.asList(NOTES),Collections.<WorkspaceSnapshot.Shortcut>emptyList(),Collections.<WorkspaceSnapshot.Template>emptyList(),Collections.<WorkspaceSnapshot.Task>emptyList(),new HashSet<String>(),0,evening());
  String r1=e.compose(WorkspaceSnapshot.request("page-3","button",null,one,WorkspaceEngine.DEADLINE_MS,null),null);
  expect("empty_context".equals(((Map<String,Object>)MiniJson.parse(r1)).get("failure")),"one app alone is nothing to arrange: "+r1);
  WorkspaceSnapshot none=WorkspaceSnapshot.build(Collections.<WorkspaceSnapshot.App>emptyList(),Collections.<WorkspaceSnapshot.Shortcut>emptyList(),Collections.<WorkspaceSnapshot.Template>emptyList(),Collections.<WorkspaceSnapshot.Task>emptyList(),new HashSet<String>(),0,evening());
  String r0=e.compose(WorkspaceSnapshot.request("page-4","button",null,none,WorkspaceEngine.DEADLINE_MS,null),null);
  expect("empty_context".equals(((Map<String,Object>)MiniJson.parse(r0)).get("failure")),"an empty snapshot is empty_context");
  String rc=e.compose(WorkspaceSnapshot.request("page-5","button",null,s,WorkspaceEngine.DEADLINE_MS,null),()->true);
  expect("cancelled".equals(((Map<String,Object>)MiniJson.parse(rc)).get("failure")),"cancelled");

  // The kind is read from the line's end: a label cannot pose as a direct action.
  expect(WorkspaceStarter.direct("x1 Play liked songs [shortcut] -> a2"),"shortcut");
  expect(WorkspaceStarter.direct("x2 Start a timer [deeplink]"),"deeplink with its target dropped");
  expect(!WorkspaceStarter.direct("x3 Grab [shortcut] now [agent_task] -> a3"),"a task whose label says [shortcut]");
  expect(!WorkspaceStarter.direct("x4 Reply [notification_action] -> n1"),"notification actions are left to a model");
 }

 @SuppressWarnings("unchecked")
 static void engine(){
  WorkspaceEngine e=starterEngine();
  String bad=e.compose("{not json",null);
  expect(bad.contains("\"failure\":\"bad_request\""),"unreadable request is bad_request: "+bad);
  String wrong=e.compose("{\"contract\":\"rose-layout-v1\",\"requestId\":\"r9\",\"trigger\":\"shout\"}",null);
  Map<String,Object> w=(Map<String,Object>)MiniJson.parse(wrong);
  expect("bad_request".equals(w.get("failure"))&&"r9".equals(w.get("requestId")),"an unknown trigger is refused and keeps its id");
  WorkspaceEngine broken=new WorkspaceEngine(new LocalInference(){
   public Map<String,String> info(){throw new IllegalStateException("no runtime");}
   public Generation generate(Request r){throw new IllegalStateException("no runtime");}
  });
  expect(broken.modelId()==null,"an inference with no identity");
  WorkspaceSnapshot s=snapshot(APPS,false);
  String r=broken.compose(WorkspaceSnapshot.request("page-6","button",null,s,WorkspaceEngine.DEADLINE_MS,null),null);
  expect(r.contains("model_unavailable"),"a runtime that fails is model_unavailable: "+r);
 }

 static void timing(){
  WorkspaceTiming t=new WorkspaceTiming();
  t.requested("r1","button",1_000_000_000L);
  t.rendered("r1",1_100_000_000L);
  expect(t.record("r1").renderedNanos<0,"no render before a result");
  java.util.Map<String,Object> composer=new java.util.LinkedHashMap<>();composer.put("composeMs",12.5);composer.put("note","x");
  t.resulted("r1",WorkspaceComposer.Outcome.COMMITTED,null,"starter",composer,1_200_000_000L);
  t.resulted("r1",WorkspaceComposer.Outcome.REJECTED,"timeout","m",null,1_300_000_000L);
  expect("committed".equals(t.record("r1").outcome),"a second result is ignored");
  t.rendered("r1",1_150_000_000L);
  expect(t.record("r1").renderedNanos<0,"a frame older than the result is not its render");
  t.rendered("r1",1_216_666_666L);t.rendered("r1",1_300_000_000L);
  WorkspaceTiming.Record r=t.record("r1");
  expect(r.requestToResultMs()==200.0&&r.resultToRenderMs()==16.67&&r.requestToRenderMs()==216.67,"times: "+r.requestToRenderMs());
  expect("request-to-render".equals(r.basis()),"rendered basis");
  expect(!r.model,"the starter is not a model");
  expect(Double.valueOf(12.5).equals(r.composer.get("composeMs"))&&!r.composer.containsKey("note"),"composer timing kept, numbers only");

  t.requested("r2","direct_text",2_000_000_000L);
  t.resulted("r2",WorkspaceComposer.Outcome.REJECTED,"model_unavailable","m-1",null,2_050_000_000L);
  t.rendered("r2",2_060_000_000L);
  expect("result-only".equals(t.record("r2").basis())&&t.record("r2").requestToRenderMs()==null,"a failure never renders");
  expect(t.record("r2").model,"a real model id is a model");
  t.rendered("nobody",1);t.resulted("nobody",WorkspaceComposer.Outcome.COMMITTED,null,null,null,1);
  expect(t.record("nobody")==null,"an unknown request is ignored");
  t.requested("r3","button",3_000_000_000L);t.resulted("r3",WorkspaceComposer.Outcome.COMMITTED,null,"scripted",null,2_000_000_000L);
  expect(t.record("r3").resultNanos<0,"a result before its request is ignored");

  String json=t.json();
  expect(json.contains("\"requestToRenderMs\":216.67")&&json.contains("\"basis\":\"result-only\"")&&json.contains("\"model\":false"),"json: "+json);
  expect(!WorkspaceTiming.isModel("scripted")&&!WorkspaceTiming.isModel(null)&&WorkspaceTiming.isModel("qwen-small"),"model labels");
  for(int i=0;i<WorkspaceTiming.KEEP+5;i++)t.requested("k"+i,"button",i);
  expect(t.records().size()==WorkspaceTiming.KEEP&&t.record("r1")==null,"bounded");

  String result="{\"contract\":\"rose-layout-v1\",\"requestId\":\"a\",\"status\":\"ok\",\"model\":{\"id\":\"starter\"},\"timing\":{\"composeMs\":1.5}}";
  expect("starter".equals(WorkspaceTiming.modelId(result))&&Double.valueOf(1.5).equals(WorkspaceTiming.composerTiming(result).get("composeMs")),"read from a result");
  expect(WorkspaceTiming.modelId("garbage")==null&&WorkspaceTiming.composerTiming("[]")==null,"unreadable results give nothing");
 }

 /** The page's whole flow, as WorkspacePage runs it, minus the views and the thread. */
 static void endToEnd()throws Exception{
  WorkspaceSnapshot s=snapshot(APPS,true);
  WorkspaceState state=new WorkspaceState();WorkspaceComposer c=new WorkspaceComposer(state);
  WorkspaceEngine e=starterEngine();WorkspaceTiming t=new WorkspaceTiming();
  int gen=c.begin(null,false);
  t.requested(c.requestId(),c.trigger(),10);
  String result=e.compose(WorkspaceSnapshot.request(c.requestId(),c.trigger(),c.prompt(),s,WorkspaceEngine.DEADLINE_MS,c.avoid()),null);
  WorkspaceComposer.Outcome o=c.finish(gen,result,s.registry);
  t.resulted(c.requestId(),o,c.lastFailure(),WorkspaceTiming.modelId(result),WorkspaceTiming.composerTiming(result),20);
  t.rendered(c.requestId(),30);
  expect(o==WorkspaceComposer.Outcome.COMMITTED&&state.layout().sections.size()==2,"Arrange commits the starter layout");
  expect("request-to-render".equals(t.record("page-1").basis()),"and its first frame is timed");

  // Pin the grid, regenerate: the grid keeps its place, and the request carries avoid.
  String grid=state.layout().sections.get(0).key;state.togglePin(grid);
  int g2=c.regenerate();
  expect(!c.avoid().isEmpty(),"regenerate avoids the previous ids");
  WorkspaceSnapshot pinned=WorkspaceSnapshot.build(APPS,Arrays.asList(new WorkspaceSnapshot.Shortcut(MUSIC,"play_liked","Play liked songs")),
   Arrays.asList(new WorkspaceSnapshot.Template("timer","Start a timer",CLOCK)),Collections.<WorkspaceSnapshot.Task>emptyList(),
   new HashSet<>(state.layout().sections.get(0).items),0,evening());
  String r2=e.compose(WorkspaceSnapshot.request(c.requestId(),c.trigger(),c.prompt(),pinned,WorkspaceEngine.DEADLINE_MS,c.avoid()),null);
  WorkspaceComposer.Outcome o2=c.finish(g2,r2,pinned.registry);
  // With the pinned apps out of the composer's context, the starter has only actions left, and the pinned grid stays first.
  expect(o2==WorkspaceComposer.Outcome.COMMITTED,"regenerate commits: "+r2);
  expect(state.layout().sections.get(0).key.equals(grid)&&state.pinned(grid),"the pinned grid keeps index 0");
  expect(state.canUndo(),"undo is there");

  // A stale result (built for an older snapshot) keeps the layout.
  int g3=c.begin("dinner",false);
  String stale=e.compose(WorkspaceSnapshot.request(c.requestId(),c.trigger(),c.prompt(),s,WorkspaceEngine.DEADLINE_MS,null),null);
  WorkspaceLayout before=state.layout();
  expect(c.finish(g3,stale,pinned.registry)==WorkspaceComposer.Outcome.REJECTED&&"stale_snapshot".equals(c.lastFailure()),"stale result rejected");
  expect(state.layout()==before,"and the layout kept");
  expect("Couldn't arrange · kept your last layout".equals(c.status()),"failure status");
 }
}
