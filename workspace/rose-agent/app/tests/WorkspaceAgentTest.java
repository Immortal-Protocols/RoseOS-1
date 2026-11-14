package dev.pixel.voice;

import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Project 2: Rose Agent as App Core's composer provider (docs/APP-BUILDER.md section 4 on #782). Strict request
 * parsing, the kinds offered without a model, the section 4.2 results, and the store's revision. Pure JVM.
 */
public final class WorkspaceAgentTest {
 static int checks;
 static void expect(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}

 @SuppressWarnings("unchecked")
 static Map<String,Object> json(String s){return (Map<String,Object>)MiniJson.parse(s);}
 static String refusal(String params){try{WorkspaceAgent.parse(json(params));return null;}catch(WorkspaceAgent.Refused e){return e.reason;}}

 public static void main(String[] args)throws Exception{
  parse();kinds();results();revision();dispatch();
  System.out.println("WorkspaceAgentTest: "+checks+" checks passed");
 }

 static void parse()throws Exception{
  WorkspaceAgent.Request p=WorkspaceAgent.parse(json("{\"schemaVersion\":1,\"requestId\":\"r-1\",\"kind\":\"prompt\",\"text\":\"  dinner for two  \"}"));
  expect("r-1".equals(p.requestId)&&"prompt".equals(p.kind)&&"dinner for two".equals(p.text),"prompt");
  WorkspaceAgent.Request a=WorkspaceAgent.parse(json("{\"schemaVersion\":1,\"requestId\":\"r-2\",\"kind\":\"arrange\"}"));
  expect("arrange".equals(a.kind)&&a.text==null,"arrange has no text");
  expect(WorkspaceAgent.parse(json("{\"schemaVersion\":1,\"requestId\":\"r-3\",\"kind\":\"undo\",\"text\":null}")).text==null,"an explicit null text is no text");
  expect("schema_version".equals(refusal("{\"schemaVersion\":2,\"requestId\":\"r\",\"kind\":\"arrange\"}")),"schema 2");
  expect("schema_version".equals(refusal("{\"requestId\":\"r\",\"kind\":\"arrange\"}")),"no schema");
  expect("unknown_field".equals(refusal("{\"schemaVersion\":1,\"requestId\":\"r\",\"kind\":\"arrange\",\"layout\":{}}")),"no layout from the server, ever");
  expect("request_id".equals(refusal("{\"schemaVersion\":1,\"requestId\":\"\",\"kind\":\"arrange\"}")),"empty id");
  String long65=new String(new char[65]).replace('\0','x');
  expect("request_id".equals(refusal("{\"schemaVersion\":1,\"requestId\":\""+long65+"\",\"kind\":\"arrange\"}")),"id over 64");
  expect("kind".equals(refusal("{\"schemaVersion\":1,\"requestId\":\"r\",\"kind\":\"layout\"}")),"unknown kind");
  expect("text".equals(refusal("{\"schemaVersion\":1,\"requestId\":\"r\",\"kind\":\"prompt\"}")),"prompt needs text");
  expect("text".equals(refusal("{\"schemaVersion\":1,\"requestId\":\"r\",\"kind\":\"prompt\",\"text\":\"   \"}")),"blank text");
  String long501=new String(new char[501]).replace('\0','w');
  expect("text".equals(refusal("{\"schemaVersion\":1,\"requestId\":\"r\",\"kind\":\"prompt\",\"text\":\""+long501+"\"}")),"text over 500");
  expect("text".equals(refusal("{\"schemaVersion\":1,\"requestId\":\"r\",\"kind\":\"arrange\",\"text\":\"x\"}")),"arrange takes no text");
  expect("bad_request".equals(refusalOf(null)),"no params");
  Map<String,Object> m=json("{\"schemaVersion\":9,\"requestId\":\"keep-me\",\"kind\":\"arrange\"}");
  expect("keep-me".equals(WorkspaceAgent.requestId(m)),"a refusal still echoes a usable id");
  expect(WorkspaceAgent.requestId(json("{\"requestId\":7}"))==null,"an unusable id is not echoed");
 }
 static String refusalOf(Map<String,Object> m){try{WorkspaceAgent.parse(m);return null;}catch(WorkspaceAgent.Refused e){return e.reason;}}

 static void kinds(){
  expect(WorkspaceAgent.kinds(false).equals(Arrays.asList("arrange","regenerate","undo")),"no prompt without a model: the starter ignores words");
  expect(WorkspaceAgent.kinds(true).equals(Arrays.asList("prompt","arrange","regenerate","undo")),"all four with a model");
  expect(!WorkspaceAgent.offered("prompt",false)&&WorkspaceAgent.offered("arrange",false),"offered");
 }

 static void results(){
  Map<String,Object> shown=WorkspaceAgent.composed("r1",WorkspaceComposer.Outcome.COMMITTED,null,true,7);
  expect(shown.equals(map("schemaVersion",1L,"requestId","r1","status","shown","revision",7L)),"shown: "+shown);
  Map<String,Object> prepared=WorkspaceAgent.composed("r2",WorkspaceComposer.Outcome.COMMITTED,null,false,8);
  expect("prepared".equals(prepared.get("status"))&&Long.valueOf(8).equals(prepared.get("revision")),"prepared off screen");
  Map<String,Object> unavailable=WorkspaceAgent.composed("r3",WorkspaceComposer.Outcome.REJECTED,"model_unavailable",true,8);
  expect("unavailable".equals(unavailable.get("status"))&&!unavailable.containsKey("revision"),"model_unavailable reads as unavailable");
  Map<String,Object> rejected=WorkspaceAgent.composed("r4",WorkspaceComposer.Outcome.REJECTED,"stale_snapshot",true,8);
  expect("rejected".equals(rejected.get("status"))&&"stale_snapshot".equals(rejected.get("reason"))&&!rejected.containsKey("revision"),"other failures are rejected with the token");
  Map<String,Object> superseded=WorkspaceAgent.composed("r5",WorkspaceComposer.Outcome.IGNORED,null,true,8);
  expect("uncertain".equals(superseded.get("status"))&&"superseded".equals(superseded.get("reason")),"a superseded request is uncertain");
  expect("uncertain".equals(WorkspaceAgent.result("r6","shown",null,null).get("status")),"shown without a revision is uncertain");
  expect("uncertain".equals(WorkspaceAgent.result("r6","prepared",-1L,null).get("status")),"prepared with a bad revision is uncertain");
  expect(WorkspaceAgent.undone("r7",true,false,9).equals(map("schemaVersion",1L,"requestId","r7","status","prepared","revision",9L)),"undo off screen");
  Map<String,Object> nothing=WorkspaceAgent.undone("r8",false,true,9);
  expect("rejected".equals(nothing.get("status"))&&"nothing_to_undo".equals(nothing.get("reason")),"nothing to undo");
  expect(WorkspaceLayout.json(shown).equals("{\"schemaVersion\":1,\"requestId\":\"r1\",\"status\":\"shown\",\"revision\":7}"),"wire form");
 }

 /** The store's revision grows with every change and survives storage; undo moves it forward, never back. */
 static void revision()throws Exception{
  WorkspaceState s=new WorkspaceState();
  expect(s.revision()==0,"starts at 0");
  WorkspaceLayout l=WorkspaceLayout.check(WorkspaceTest.ok(WorkspaceTest.GRID),"r1",WorkspaceTest.REG,s.keys());
  s.commit(l,"dinner");expect(s.revision()==1,"commit");
  expect(s.togglePin(s.layout().sections.get(0).key)&&s.revision()==2,"pin");
  expect(s.undo()&&s.revision()==3,"undo moves the revision forward");
  expect(!s.undo()||s.revision()==4,"each change counts");
  long before=s.revision();
  WorkspaceState back=WorkspaceState.fromJson(s.toJson());
  expect(back.revision()==before,"revision survives storage");
  expect(WorkspaceState.fromJson("{\"v\":1,\"rev\":-3}").revision()==0,"a negative stored revision is ignored");
 }

 /** One dispatcher for page and conversation: each waiting caller is answered exactly once. */
 static void dispatch()throws Exception{
  WorkspaceState state=new WorkspaceState();WorkspaceComposer composer=new WorkspaceComposer(state);
  WorkspaceRequests d=new WorkspaceRequests(state,composer);
  final java.util.List<Map<String,Object>> got=new java.util.ArrayList<>();
  WorkspaceRequests.Reply reply=got::add;

  // prompt is not offered without a model: refused before anything changes.
  WorkspaceRequests.Step p=d.conversation(WorkspaceAgent.parse(json("{\"schemaVersion\":1,\"requestId\":\"c1\",\"kind\":\"prompt\",\"text\":\"dinner\"}")),false,true,reply);
  expect(!p.composes()&&"kind_not_offered".equals(got.get(0).get("reason"))&&state.layout()==null&&!composer.busy(),"prompt refused without a model");
  // undo with nothing to undo, and regenerate with nothing to regenerate.
  d.conversation(WorkspaceAgent.parse(json("{\"schemaVersion\":1,\"requestId\":\"c2\",\"kind\":\"undo\"}")),false,true,reply);
  expect("nothing_to_undo".equals(got.get(1).get("reason")),"nothing to undo");
  d.conversation(WorkspaceAgent.parse(json("{\"schemaVersion\":1,\"requestId\":\"c3\",\"kind\":\"regenerate\"}")),false,true,reply);
  expect("nothing_to_regenerate".equals(got.get(2).get("reason")),"nothing to regenerate");

  // arrange composes, keeping App Core's requestId; the caller waits.
  WorkspaceRequests.Step a=d.conversation(WorkspaceAgent.parse(json("{\"schemaVersion\":1,\"requestId\":\"c4\",\"kind\":\"arrange\"}")),false,false,reply);
  expect(a.composes()&&"c4".equals(composer.requestId())&&"button".equals(composer.trigger())&&d.waitingCount()==1&&got.size()==3,"arrange waits for the composer (rose-layout-v1: App Core arrange is trigger button)");
  expect(composer.offer()==WorkspaceComposer.Offer.PREPARING,"a conversational request raises the offer");
  // A page request supersedes it: the caller hears uncertain, once.
  WorkspaceRequests.Step pg=d.page(null);
  expect(pg.composes()&&got.size()==4&&"uncertain".equals(got.get(3).get("status"))&&"c4".equals(got.get(3).get("requestId"))&&d.waitingCount()==0,"superseded caller told uncertain");
  composer.finish(a.gen,"{}",WorkspaceTest.REG);d.finished(a.gen,WorkspaceComposer.Outcome.IGNORED,false);
  expect(got.size()==4,"a late result for the superseded request answers nobody again");

  // A conversational arrange that commits: prepared off screen, with the store's revision.
  WorkspaceRequests.Step b=d.conversation(WorkspaceAgent.parse(json("{\"schemaVersion\":1,\"requestId\":\"c5\",\"kind\":\"arrange\"}")),false,false,reply);
  WorkspaceComposer.Outcome o=composer.finish(b.gen,WorkspaceTest.result("c5","s-1","Tonight",WorkspaceTest.GRID),WorkspaceTest.REG);
  d.finished(b.gen,o,false);
  expect(o==WorkspaceComposer.Outcome.COMMITTED&&got.size()==5,"committed");
  expect("prepared".equals(got.get(4).get("status"))&&Long.valueOf(state.revision()).equals(got.get(4).get("revision"))&&state.revision()==1,"prepared with revision 1: "+got.get(4));
  // A second layout from the page (no caller waits), then undo from the conversation, on screen: shown, and the
  // revision moves forward rather than back.
  WorkspaceRequests.Step second=d.page(null);
  d.finished(second.gen,composer.finish(second.gen,WorkspaceTest.result(composer.requestId(),"s-1","Later",WorkspaceTest.CAROUSEL),WorkspaceTest.REG),true);
  expect(state.revision()==2&&got.size()==5,"the page's own request answers nobody");
  d.conversation(WorkspaceAgent.parse(json("{\"schemaVersion\":1,\"requestId\":\"c6\",\"kind\":\"undo\"}")),false,true,reply);
  expect("shown".equals(got.get(5).get("status"))&&Long.valueOf(3).equals(got.get(5).get("revision")),"undo shown at revision 3: "+got.get(5));
  expect("Tonight".equals(state.layout().title),"undo restored the first layout");
  // A failed composition reads as rejected with its token, or unavailable for no model.
  WorkspaceRequests.Step f=d.conversation(WorkspaceAgent.parse(json("{\"schemaVersion\":1,\"requestId\":\"c7\",\"kind\":\"arrange\"}")),false,true,reply);
  d.finished(f.gen,composer.fail(f.gen,"model_unavailable"),true);
  expect("unavailable".equals(got.get(6).get("status")),"no model is unavailable");
  // Undo while composing is refused rather than racing the composer.
  WorkspaceRequests.Step g=d.conversation(WorkspaceAgent.parse(json("{\"schemaVersion\":1,\"requestId\":\"c8\",\"kind\":\"arrange\"}")),false,true,reply);
  d.conversation(WorkspaceAgent.parse(json("{\"schemaVersion\":1,\"requestId\":\"c9\",\"kind\":\"undo\"}")),false,true,reply);
  expect("busy".equals(got.get(7).get("reason")),"undo while composing is refused");
  d.abandonAll();
  expect("uncertain".equals(got.get(8).get("status"))&&"c8".equals(got.get(8).get("requestId"))&&d.waitingCount()==0,"stopping answers the waiting caller");
  expect(g.composes(),"c8 had started");

  // The hub's floor keeps the revision from going back after a lost store.
  WorkspaceState lost=WorkspaceState.fromJson("not json");lost.raiseRevision(7);lost.raiseRevision(3);
  expect(lost.revision()==7,"raised, never lowered");
 }

 static Map<String,Object> map(Object... kv){Map<String,Object> m=new LinkedHashMap<>();for(int i=0;i<kv.length;i+=2)m.put((String)kv[i],kv[i+1]);return m;}
}
