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
  parse();kinds();results();revision();
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

 static Map<String,Object> map(Object... kv){Map<String,Object> m=new LinkedHashMap<>();for(int i=0;i<kv.length;i+=2)m.put((String)kv[i],kv[i+1]);return m;}
}
