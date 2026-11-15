package dev.pixel.voice;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Calendar;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Project 2: the server-decision client (WorkspaceDecisionClient) against App Core's agreed POST
 * /workspace/v1/compose, with a fake transport only. Nothing is sent and no model runs.
 */
public final class WorkspaceDecisionClientTest {
 static int checks;
 static void expect(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}

 static final class Fake implements WorkspaceTaskClient.Transport {
  final List<WorkspaceTaskClient.Response> script=new ArrayList<>();final List<String> bodies=new ArrayList<>();final List<Map<String,String>> headers=new ArrayList<>();
  Fake then(int status,String body){script.add(new WorkspaceTaskClient.Response(status,body));return this;}
  Fake drop(){script.add(null);return this;}
  public WorkspaceTaskClient.Response post(String path,Map<String,String> h,byte[] body)throws Exception{
   expect(WorkspaceDecisionClient.PATH.equals(path),"only the compose route");
   headers.add(h);bodies.add(new String(body,StandardCharsets.UTF_8));
   WorkspaceTaskClient.Response r=script.isEmpty()?null:script.remove(0);
   if(r==null)throw new java.io.IOException("connection lost");
   return r;
  }
 }
 static final WorkspaceTaskClient.Signer SIGNER=(m,p)->{Map<String,String> h=new LinkedHashMap<>();h.put(LocalRoseAuth.H_SIGNATURE,m+" "+p);return h;};
 static WorkspaceDecisionClient client(boolean on,Fake f){return new WorkspaceDecisionClient(new WorkspaceTaskClient.Gate(on),f,SIGNER);}

 static final WorkspaceSnapshot.App CLOCK=new WorkspaceSnapshot.App("com.google.android.deskclock","com.android.deskclock.DeskClock","Clock","tools",0);
 static final WorkspaceSnapshot.App NOTES=new WorkspaceSnapshot.App("com.example.notes","com.example.notes.Main","Notes",null,0);
 static WorkspaceSnapshot phone(){
  return WorkspaceSnapshot.build(Arrays.asList(CLOCK,NOTES),Collections.<WorkspaceSnapshot.Shortcut>emptyList(),Collections.<WorkspaceSnapshot.Template>emptyList(),
   Collections.<WorkspaceSnapshot.Task>emptyList(),new HashSet<String>(),1794600000000L,Calendar.getInstance());
 }
 static String serverResult(String requestId,String version,String... ids){
  StringBuilder items=new StringBuilder();for(int i=0;i<ids.length;i++){if(i>0)items.append(',');items.append('"').append(ids[i]).append('"');}
  return "{\"contract\":\"rose-layout-v1\",\"requestId\":\""+requestId+"\",\"status\":\"ok\",\"layout\":{\"snapshotVersion\":\""+version
   +"\",\"catalogVersion\":\"cat-1\",\"title\":\"Evening\",\"sections\":[{\"component\":\"grid\",\"title\":\"Apps\",\"items\":["+items+"],\"actions\":[]}]},\"model\":{\"id\":\"rose-server-m\"},\"timing\":{\"composeMs\":900}}";
 }

 public static void main(String[] args){
  gate();body();replies();phoneStaysTheGate();route();cancellation();
  System.out.println("WorkspaceDecisionClientTest: "+checks+" checks passed (fake transport; nothing sent, no model)");
 }

 static void gate(){
  Fake f=new Fake().then(200,"{}");
  WorkspaceSnapshot s=phone();String req=WorkspaceSnapshot.request("r1","button",null,s,WorkspaceEngine.DEADLINE_MS,Collections.<String>emptyList());
  WorkspaceDecisionClient.Outcome o=client(false,f).decide("r1",req);
  expect(o.kind==WorkspaceDecisionClient.Kind.NOT_SENT&&f.bodies.isEmpty(),"gate off: nothing is sent");
  expect(WorkspaceDecisionClient.result("r1",o).contains("\"failure\":\"model_unavailable\""),"and the page keeps its layout");
 }

 static void cancellation(){
  WorkspaceSnapshot s=phone();String req=WorkspaceSnapshot.request("cancel","button",null,s,4000,null);
  Fake before=new Fake();
  expect(client(true,before).decide("cancel",req,()->true).kind==WorkspaceDecisionClient.Kind.NOT_SENT&&before.bodies.isEmpty(),"superseded queued work is not sent");
  java.util.concurrent.atomic.AtomicBoolean stop=new java.util.concurrent.atomic.AtomicBoolean();
  Fake duringSign=new Fake();
  WorkspaceDecisionClient signing=new WorkspaceDecisionClient(new WorkspaceTaskClient.Gate(true),duringSign,(m,p)->{stop.set(true);return SIGNER.headers(m,p);});
  expect(signing.decide("cancel",req,stop::get).kind==WorkspaceDecisionClient.Kind.NOT_SENT&&duringSign.bodies.isEmpty(),"cancellation immediately before sending is honored");
  stop.set(false);
  WorkspaceTaskClient.Transport wire=(p,h,b)->{stop.set(true);return new WorkspaceTaskClient.Response(200,serverResult("cancel",s.snapshotVersion,WorkspaceSnapshot.appId(CLOCK),WorkspaceSnapshot.appId(NOTES)));};
  WorkspaceDecisionClient flying=new WorkspaceDecisionClient(new WorkspaceTaskClient.Gate(true),wire,SIGNER);
  WorkspaceDecisionClient.Outcome result=flying.decide("cancel",req,stop::get);
  expect(result.kind==WorkspaceDecisionClient.Kind.UNCERTAIN&&result.result==null,"a reply after supersession is not applied");
 }

 @SuppressWarnings("unchecked")
 static void body(){
  WorkspaceSnapshot s=phone();String req=WorkspaceSnapshot.request("r2","direct_text","cooking",s,WorkspaceEngine.DEADLINE_MS,Collections.<String>emptyList());
  Fake f=new Fake().then(200,serverResult("r2",s.snapshotVersion,WorkspaceSnapshot.appId(CLOCK)));
  WorkspaceDecisionClient.Outcome o=client(true,f).decide("r2",req);
  expect(o.kind==WorkspaceDecisionClient.Kind.DECIDED&&f.bodies.size()==1,"one attempt, decided");
  Map<String,Object> m=(Map<String,Object>)MiniJson.parse(f.bodies.get(0));
  expect(m.keySet().equals(new java.util.LinkedHashSet<>(Arrays.asList("schemaVersion","requestId","request"))),"exactly the agreed body: "+m.keySet());
  expect(Long.valueOf(1).equals(m.get("schemaVersion"))&&"r2".equals(m.get("requestId")),"schemaVersion 1 and the requestId");
  Map<String,Object> inner=(Map<String,Object>)m.get("request");
  expect("rose-layout-v1".equals(inner.get("contract"))&&"r2".equals(inner.get("requestId"))&&"direct_text".equals(inner.get("trigger"))&&"cooking".equals(inner.get("prompt"))&&inner.get("snapshot") instanceof Map,"the phone's own rose-layout-v1 request, unchanged");
  expect("POST /workspace/v1/compose".equals(f.headers.get(0).get(LocalRoseAuth.H_SIGNATURE))&&"application/json".equals(f.headers.get(0).get("Content-Type")),"signed POST on the compose route");
  expect(WorkspaceDecisionClient.body("other",req)==null,"a request for another requestId is never sent");
  expect(client(true,new Fake()).decide("r2","not json").kind==WorkspaceDecisionClient.Kind.NOT_SENT,"an unreadable request is never sent");
  StringBuilder big=new StringBuilder("{\"contract\":\"rose-layout-v1\",\"requestId\":\"r3\",\"pad\":\"");for(int i=0;i<WorkspaceDecisionClient.BODY_MAX;i++)big.append('x');big.append("\"}");
  Fake g=new Fake();
  expect(client(true,g).decide("r3",big.toString()).kind==WorkspaceDecisionClient.Kind.NOT_SENT&&g.bodies.isEmpty(),"a request over the limit is not sent");
 }

 static void replies(){
  WorkspaceSnapshot s=phone();String req=WorkspaceSnapshot.request("r4","button",null,s,WorkspaceEngine.DEADLINE_MS,Collections.<String>emptyList());
  Object[][] cases={
   {503,"{\"error\":\"unavailable\"}",WorkspaceDecisionClient.Kind.UNAVAILABLE},
   {400,"{\"schemaVersion\":1,\"status\":\"refused\"}",WorkspaceDecisionClient.Kind.REFUSED},
   {401,"{}",WorkspaceDecisionClient.Kind.NOT_AUTHORIZED},
   {500,"oops",WorkspaceDecisionClient.Kind.UNCERTAIN},
   {200,"garbage",WorkspaceDecisionClient.Kind.UNCERTAIN},
   {200,serverResult("someone-else",s.snapshotVersion,"app:x"),WorkspaceDecisionClient.Kind.UNCERTAIN},
   {200,serverResult("r4",s.snapshotVersion,"app:x").replace("rose-layout-v1","rose-layout-v2"),WorkspaceDecisionClient.Kind.UNCERTAIN},
   {200,serverResult("r4",s.snapshotVersion,"app:x").replace("\"status\":\"ok\"","\"status\":\"maybe\""),WorkspaceDecisionClient.Kind.UNCERTAIN},
   {200,"{\"contract\":\"rose-layout-v1\",\"requestId\":\"r4\",\"status\":\"failed\",\"failure\":\"timeout\"}",WorkspaceDecisionClient.Kind.DECIDED},
  };
  for(Object[] c:cases){
   Fake f=new Fake().then((Integer)c[0],(String)c[1]);
   WorkspaceDecisionClient.Outcome o=client(true,f).decide("r4",req);
   expect(o.kind==c[2]&&f.bodies.size()==1,"HTTP "+c[0]+" "+c[1]+" -> "+o.kind+": one attempt, never retried");
   if(o.kind!=WorkspaceDecisionClient.Kind.DECIDED)expect(WorkspaceDecisionClient.result("r4",o).contains("\"failure\":\"model_unavailable\"")&&WorkspaceDecisionClient.result("r4",o).contains("\"requestId\":\"r4\""),"not decided: a model_unavailable failure for this request");
  }
  Fake lost=new Fake().drop();
  expect(client(true,lost).decide("r4",req).kind==WorkspaceDecisionClient.Kind.UNCERTAIN&&lost.bodies.size()==1,"no reply: uncertain, one attempt");
 }

 /** A server decision is only passed on: the phone's own check decides what is drawn. */
 static void phoneStaysTheGate(){
  WorkspaceSnapshot s=phone();
  WorkspaceState st=new WorkspaceState();WorkspaceComposer c=new WorkspaceComposer(st);
  int gen=c.begin(null,false);String id=c.requestId();
  String req=WorkspaceSnapshot.request(id,c.trigger(),c.prompt(),s,WorkspaceEngine.DEADLINE_MS,c.avoid());
  // The server names an app this phone does not have.
  Fake f=new Fake().then(200,serverResult(id,s.snapshotVersion,WorkspaceSnapshot.appId(CLOCK),"app:com.evil/.Open"));
  String text=WorkspaceDecisionClient.result(id,client(true,f).decide(id,req));
  expect(c.finish(gen,text,s.registry)==WorkspaceComposer.Outcome.REJECTED&&"unknown_ref".equals(c.lastFailure())&&st.layout()==null,"an id the phone does not have is refused; nothing is drawn");
  // A good decision commits, and its model id is the server's.
  int g2=c.begin(null,false);String id2=c.requestId();
  String req2=WorkspaceSnapshot.request(id2,c.trigger(),c.prompt(),s,WorkspaceEngine.DEADLINE_MS,c.avoid());
  WorkspaceDecisionClient.Outcome decided=client(true,new Fake().then(200,serverResult(id2,s.snapshotVersion,WorkspaceSnapshot.appId(CLOCK),WorkspaceSnapshot.appId(NOTES)))).decide(id2,req2);
  String ok=WorkspaceDecisionClient.result(id2,decided);
  expect(c.finish(g2,ok,s.registry)==WorkspaceComposer.Outcome.COMMITTED&&st.layout().sections.size()==1,"a valid server decision commits");
  expect("rose-server-m".equals(WorkspaceTiming.modelId(ok))&&WorkspaceTiming.isModel(WorkspaceTiming.modelId(ok)),"timing records the server's model id");
  // The server unavailable: the layout is kept.
  WorkspaceLayout before=st.layout();
  int g3=c.begin(null,false);String id3=c.requestId();
  String down=WorkspaceDecisionClient.result(id3,client(true,new Fake().then(503,"{\"error\":\"unavailable\"}")).decide(id3,WorkspaceSnapshot.request(id3,c.trigger(),c.prompt(),s,WorkspaceEngine.DEADLINE_MS,c.avoid())));
  expect(c.finish(g3,down,s.registry)==WorkspaceComposer.Outcome.REJECTED&&"model_unavailable".equals(c.lastFailure())&&st.layout()==before,"server unavailable: the last layout is kept");
 }
 /** Timing says where each request was decided, so a server round trip is never read as phone time. */
 static void route(){
  WorkspaceTiming t=new WorkspaceTiming();
  t.requested("a","button",0);t.routed("a",WorkspaceTiming.SERVER);
  t.requested("b","button",0);t.routed("b",WorkspaceTiming.PHONE);
  t.requested("c","button",0);t.routed("c","elsewhere");
  String json=t.json();
  expect(json.contains("\"requestId\":\"a\",\"trigger\":\"button\",\"route\":\"server\"")&&json.contains("\"route\":\"phone\"")&&json.contains("\"requestId\":\"c\",\"trigger\":\"button\",\"route\":null"),"route recorded strictly: "+json);
 }
}
