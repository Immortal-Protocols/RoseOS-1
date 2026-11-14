package dev.pixel.voice;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Project 2: the phone side of App Core's workspace task handoff v1 (docs/APP-BUILDER.md section 4.3), against a
 * fake transport and signer. Nothing is sent anywhere, and no task runs.
 */
public final class WorkspaceTaskClientTest {
 static int checks;
 static void expect(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}

 static final WorkspaceSnapshot.App ROSE=new WorkspaceSnapshot.App("dev.pixel.voice","dev.pixel.voice.MainActivity","Rose Agent",null,0);
 static final WorkspaceSnapshot.Task GROCERY=new WorkspaceSnapshot.Task("grocery","Add missing items to grocery list","Add what tonight's recipe needs to your list?","Compare tonight's recipe with the grocery list and add what is missing",ROSE);

 /** Records every exchange and answers from a script of responses (null: no response, the connection failed). */
 static final class Fake implements WorkspaceTaskClient.Transport {
  final List<WorkspaceTaskClient.Response> script=new ArrayList<>();
  final List<Map<String,String>> headers=new ArrayList<>();final List<String> bodies=new ArrayList<>();
  Fake then(int status,String body){script.add(new WorkspaceTaskClient.Response(status,body));return this;}
  Fake drop(){script.add(null);return this;}
  public WorkspaceTaskClient.Response post(String path,Map<String,String> h,byte[] body)throws Exception{
   expect(WorkspaceTaskClient.PATH.equals(path),"only the task route");
   headers.add(h);bodies.add(new String(body,StandardCharsets.UTF_8));
   WorkspaceTaskClient.Response r=script.isEmpty()?null:script.remove(0);
   if(r==null)throw new java.io.IOException("connection lost");
   return r;
  }
 }
 static int signed;
 static final WorkspaceTaskClient.Signer SIGNER=(method,path)->{
  signed++;
  // The real signer is LocalRoseAuth.headers with this phone's key; the fake returns the same four headers.
  Map<String,String> h=new LinkedHashMap<>();
  h.put(LocalRoseAuth.H_DEVICE,"d");h.put(LocalRoseAuth.H_TIME,"1");h.put(LocalRoseAuth.H_NONCE,"n"+signed);h.put(LocalRoseAuth.H_SIGNATURE,method+" "+path);
  return h;
 };
 static WorkspaceTaskClient client(boolean enabled,Fake f){return new WorkspaceTaskClient(new WorkspaceTaskClient.Gate(enabled),f,SIGNER);}
 static final String ACCEPTED="{\"schemaVersion\":1,\"status\":\"accepted\",\"taskId\":null}";

 public static void main(String[] args)throws Exception{
  body();gate();replies();retries();
  System.out.println("WorkspaceTaskClientTest: "+checks+" checks passed (fake transport; nothing sent)");
 }

 /** First test: only a confirmed task, with exactly App Core's six fields and limits. */
 @SuppressWarnings("unchecked")
 static void body(){
  expect(WorkspaceTaskClient.body("tap-1",GROCERY,false)==null,"an unconfirmed task is never built");
  String b=WorkspaceTaskClient.body("tap-1",GROCERY,true);
  Map<String,Object> m=(Map<String,Object>)MiniJson.parse(b);
  expect(m.keySet().equals(new java.util.LinkedHashSet<>(java.util.Arrays.asList("schemaVersion","requestId","actionId","label","goal","confirmed"))),"exactly the six fields: "+m.keySet());
  expect(Long.valueOf(1).equals(m.get("schemaVersion"))&&"tap-1".equals(m.get("requestId"))&&"task:grocery".equals(m.get("actionId"))&&Boolean.TRUE.equals(m.get("confirmed")),"values");
  expect(GROCERY.label.equals(m.get("label"))&&GROCERY.goal.equals(m.get("goal")),"the template's own label and goal");
  expect(!b.contains(GROCERY.ask),"the ask is display text for the confirm sheet, never sent");
  String sixty1=new String(new char[61]).replace('\0','x'),three01=new String(new char[301]).replace('\0','y'),sixty5=new String(new char[65]).replace('\0','r');
  expect(WorkspaceTaskClient.body("t",new WorkspaceSnapshot.Task("g",sixty1,"a","goal",ROSE),true)==null,"label over 60");
  expect(WorkspaceTaskClient.body("t",new WorkspaceSnapshot.Task("g","l","a",three01,ROSE),true)==null,"goal over 300");
  expect(WorkspaceTaskClient.body(sixty5,GROCERY,true)==null&&WorkspaceTaskClient.body("",GROCERY,true)==null,"requestId 1 to 64");
  expect(WorkspaceTaskClient.body("t",new WorkspaceSnapshot.Task("bad id","l","a","g",ROSE),true)==null,"template id outside task:[A-Za-z0-9._-]{1,48}");
  expect(WorkspaceTaskClient.body("t",new WorkspaceSnapshot.Task(new String(new char[49]).replace('\0','a'),"l","a","g",ROSE),true)==null,"template id over 48");
  expect(WorkspaceTaskClient.body("t",new WorkspaceSnapshot.Task("g","  ","a","g",ROSE),true)==null,"blank label");
  expect(WorkspaceTaskClient.body("t",new WorkspaceSnapshot.Task("g","l\0x","a","g",ROSE),true)==null,"NUL in label");
  expect(WorkspaceTaskClient.body("t",new WorkspaceSnapshot.Task("g","l\uD800","a","g",ROSE),true)==null,"unpaired surrogate (not UTF-8)");
  String emoji=new String(new char[60]).replace("\0","🍝");
  expect(WorkspaceTaskClient.body("t",new WorkspaceSnapshot.Task("g",emoji,"a","g",ROSE),true)!=null,"60 code points is within the limit, as Python counts it");
  expect(WorkspaceTaskClient.body("t",null,true)==null,"no task, nothing to send");
 }

 /** Off by default: nothing is signed or sent until the route is explicitly enabled. */
 static void gate(){
  Fake f=new Fake().then(200,ACCEPTED);int before=signed;
  WorkspaceTaskClient.Outcome o=new WorkspaceTaskClient(WorkspaceTaskClient.Gate.disabled(),f,SIGNER).start("tap-1",GROCERY,true);
  expect(o.kind==WorkspaceTaskClient.Kind.NOT_SENT&&"disabled".equals(o.reason),"disabled by default");
  expect(f.bodies.isEmpty()&&signed==before,"nothing signed or sent");
  expect("Rose tasks aren't available on this phone yet".equals(o.status()),"the page says so");
  WorkspaceTaskClient.Outcome n=client(true,f).start("tap-1",GROCERY,false);
  expect(n.kind==WorkspaceTaskClient.Kind.NOT_SENT&&"not_confirmed".equals(n.reason)&&f.bodies.isEmpty(),"enabled but not confirmed: nothing sent");
 }

 /** Each reply, read as App Core's route answers it. */
 static void replies(){
  Fake f=new Fake().then(200,"{\"schemaVersion\":1,\"status\":\"accepted\",\"taskId\":\"t-9\"}");
  WorkspaceTaskClient.Outcome a=client(true,f).start("tap-2",GROCERY,true);
  expect(a.kind==WorkspaceTaskClient.Kind.STARTED&&"t-9".equals(a.taskId)&&a.started()&&a.attempts==1,"accepted is started");
  expect("Started · in Rose's work".equals(a.status())&&!a.status().toLowerCase().contains("done"),"started, never done");
  Map<String,String> h=f.headers.get(0);
  expect(h.containsKey(LocalRoseAuth.H_DEVICE)&&h.containsKey(LocalRoseAuth.H_TIME)&&h.containsKey(LocalRoseAuth.H_NONCE)&&"POST /workspace/v1/tasks".equals(h.get(LocalRoseAuth.H_SIGNATURE)),"signed rose-auth-v1 for POST on the task path");
  expect("application/json".equals(h.get("Content-Type")),"JSON body");

  WorkspaceTaskClient.Outcome nul=WorkspaceTaskClient.read(new WorkspaceTaskClient.Response(200,ACCEPTED),1);
  expect(nul.kind==WorkspaceTaskClient.Kind.STARTED&&nul.taskId==null,"taskId may be null; the page follows the task events");
  WorkspaceTaskClient.Outcome d=WorkspaceTaskClient.read(new WorkspaceTaskClient.Response(200,"{\"schemaVersion\":1,\"status\":\"duplicate\",\"taskId\":\"t-9\"}"),1);
  expect(d.kind==WorkspaceTaskClient.Kind.ALREADY_STARTED&&d.started(),"duplicate: the first reply, nothing new started");
  WorkspaceTaskClient.Outcome r=WorkspaceTaskClient.read(new WorkspaceTaskClient.Response(400,"{\"schemaVersion\":1,\"status\":\"refused\",\"reason\":\"the owner has not confirmed this task\"}"),1);
  expect(r.kind==WorkspaceTaskClient.Kind.REFUSED&&"the owner has not confirmed this task".equals(r.reason)&&!r.started(),"refused");
  WorkspaceTaskClient.Outcome u=WorkspaceTaskClient.read(new WorkspaceTaskClient.Response(503,"{\"schemaVersion\":1,\"status\":\"unavailable\"}"),1);
  expect(u.kind==WorkspaceTaskClient.Kind.UNAVAILABLE&&!u.started(),"unavailable: nothing started");
  WorkspaceTaskClient.Outcome auth=WorkspaceTaskClient.read(new WorkspaceTaskClient.Response(401,"{\"schemaVersion\":1,\"error\":\"unknown device\",\"final\":true}"),1);
  expect(auth.kind==WorkspaceTaskClient.Kind.NOT_AUTHORIZED&&"unknown device".equals(auth.reason),"signature refused");
  for(String[] bad:new String[][]{{"500","oops"},{"200","{\"status\":\"accepted\"}"},{"200","{\"schemaVersion\":1,\"status\":\"done\"}"},{"400","{\"schemaVersion\":1,\"status\":\"accepted\"}"},{"200","[]"}}){
   WorkspaceTaskClient.Outcome x=WorkspaceTaskClient.read(new WorkspaceTaskClient.Response(Integer.parseInt(bad[0]),bad[1]),1);
   expect(x.kind==WorkspaceTaskClient.Kind.UNCERTAIN&&!x.started(),"anything else is uncertain, never started: "+bad[0]+" "+bad[1]);
  }
 }

 /** Retries: only after 503 or no response, with the same requestId, at most 3 attempts; never after a definite answer. */
 @SuppressWarnings("unchecked")
 static void retries(){
  Fake f=new Fake().then(503,"{\"schemaVersion\":1,\"status\":\"unavailable\"}").drop().then(200,ACCEPTED);
  WorkspaceTaskClient.Outcome o=client(true,f).start("tap-3",GROCERY,true);
  expect(o.kind==WorkspaceTaskClient.Kind.STARTED&&o.attempts==3&&f.bodies.size()==3,"503, then no response, then accepted");
  for(String b:f.bodies)expect("tap-3".equals(((Map<String,Object>)MiniJson.parse(b)).get("requestId")),"every attempt carries the same requestId");
  expect(!f.headers.get(0).get(LocalRoseAuth.H_NONCE).equals(f.headers.get(1).get(LocalRoseAuth.H_NONCE)),"each attempt is signed afresh (new nonce)");

  Fake busy=new Fake().then(503,"{\"schemaVersion\":1,\"status\":\"unavailable\"}").then(503,"{\"schemaVersion\":1,\"status\":\"unavailable\"}").then(503,"{\"schemaVersion\":1,\"status\":\"unavailable\"}").then(200,ACCEPTED);
  WorkspaceTaskClient.Outcome b=client(true,busy).start("tap-4",GROCERY,true);
  expect(b.kind==WorkspaceTaskClient.Kind.UNAVAILABLE&&b.attempts==3&&busy.bodies.size()==3,"at most 3 attempts");
  expect("Couldn't start · try again".equals(b.status()),"the person can try again");

  Fake gone=new Fake().drop().drop().drop();
  WorkspaceTaskClient.Outcome g=client(true,gone).start("tap-5",GROCERY,true);
  expect(g.kind==WorkspaceTaskClient.Kind.UNCERTAIN&&"no_reply".equals(g.reason)&&!g.started(),"no answer at all is uncertain, not started");
  expect("Not sure it started · check Rose's work".equals(g.status()),"the page says it is unsure");

  Fake odd=new Fake().then(500,"oops").then(200,ACCEPTED);
  WorkspaceTaskClient.Outcome x=client(true,odd).start("tap-6",GROCERY,true);
  expect(x.kind==WorkspaceTaskClient.Kind.UNCERTAIN&&odd.bodies.size()==1,"an unexpected reply is not retried (the server might have started it)");
  Fake refused=new Fake().then(400,"{\"schemaVersion\":1,\"status\":\"refused\",\"reason\":\"label must be 1 to 60 characters\"}").then(200,ACCEPTED);
  expect(client(true,refused).start("tap-7",GROCERY,true).kind==WorkspaceTaskClient.Kind.REFUSED&&refused.bodies.size()==1,"a refusal is not retried");
  Fake denied=new Fake().then(401,"{\"schemaVersion\":1,\"error\":\"bad signature\",\"final\":true}").then(200,ACCEPTED);
  expect(client(true,denied).start("tap-8",GROCERY,true).kind==WorkspaceTaskClient.Kind.NOT_AUTHORIZED&&denied.bodies.size()==1,"a 401 is not retried");
  WorkspaceTaskClient.Signer broken=(m,p)->{throw new java.security.GeneralSecurityException("no key");};
  Fake unsigned=new Fake().then(200,ACCEPTED);
  WorkspaceTaskClient.Outcome s=new WorkspaceTaskClient(new WorkspaceTaskClient.Gate(true),unsigned,broken).start("tap-9",GROCERY,true);
  expect(s.kind==WorkspaceTaskClient.Kind.UNCERTAIN&&unsigned.bodies.isEmpty(),"a request that cannot be signed is never sent unsigned");
 }
}
