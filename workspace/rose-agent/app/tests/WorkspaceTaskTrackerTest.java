package dev.pixel.voice;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Project 2: the phone's record of confirmed Rose tasks, against App Core's page-task state v1 (agreed
 * 2026-11-14): POST taskId "wt-&lt;16 hex&gt;", state {schemaVersion 1, taskId, requestId, actionId, state, detail,
 * phoneTasks, createdAt, updatedAt}, live and replayed workspace_task events, and GET /workspace/v1/tasks/&lt;taskId&gt;.
 * The tests are T1 to T8 as proposed to App Core, using fakes only. Acceptance is never taken for done.
 */
public final class WorkspaceTaskTrackerTest {
 static int checks;
 static void expect(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}

 static final String A="task:grocery",ID="wt-0123456789abcdef",OTHER_ID="wt-fedcba9876543210";
 static String state(String taskId,String requestId,String actionId,String state,double updatedAt){
  return "{\"schemaVersion\":1,\"taskId\":\""+taskId+"\",\"requestId\":\""+requestId+"\",\"actionId\":\""+actionId+"\",\"state\":\""+state+"\",\"detail\":\"\",\"phoneTasks\":[{\"id\":\"local:1\",\"status\":\"running\"}],\"createdAt\":100,\"updatedAt\":"+updatedAt+"}";
 }
 static String event(String s){return "{\"kind\":\"workspace_task\",\"t\":1,\"task\":"+s+"}";}
 static WorkspaceTaskTracker.State parse(String s){return WorkspaceTaskTracker.state(MiniJson.parse(s));}
 static WorkspaceTaskClient.Outcome accepted(String taskId){return new WorkspaceTaskClient.Outcome(WorkspaceTaskClient.Kind.STARTED,null,taskId,1);}

 public static void main(String[] args)throws Exception{
  t1();t2();t3();t4();t5();t6();t7();t8();strict();
  System.out.println("WorkspaceTaskTrackerTest: "+checks+" checks passed (fakes; nothing sent)");
 }

 /** T1: accepted is "Started", never done, whatever else is going on; no taskId means nothing can move it. */
 static void t1(){
  WorkspaceTaskTracker t=new WorkspaceTaskTracker();
  expect("Rose task · asks first".equals(t.line(A)),"before a tap");
  t.tapped(A,"tap-1");
  expect("Asking Rose…".equals(t.line(A)),"while sending");
  t.replied("tap-1",accepted(null));
  expect("Started · in Rose's work".equals(t.line(A)),"accepted, no taskId: started");
  expect(t.apply(parse(state(OTHER_ID,"tap-9",A,"done",200)))==null&&"Started · in Rose's work".equals(t.line(A)),"another page task finishing says nothing about this one");
  WorkspaceTaskTracker u=new WorkspaceTaskTracker();u.tapped(A,"tap-1");u.replied("tap-1",accepted(ID));
  expect("Started · in Rose's work".equals(u.line(A)),"accepted with taskId is still only started, never done");
 }

 /** T2: its own taskId's states move the card, through to each end. */
 static void t2(){
  String[][] ends={{"done","Done · in Rose's work"},{"failed","Failed · in Rose's work"},{"cancelled","Stopped · in Rose's work"},{"uncertain","Not sure how it ended · check Rose's work"}};
  for(String[] e:ends){
   WorkspaceTaskTracker t=new WorkspaceTaskTracker();t.tapped(A,"tap-2");t.replied("tap-2",accepted(ID));
   expect(t.apply(parse(state(ID,"tap-2",A,"running",101)))!=null&&"Rose is working".equals(t.line(A)),"running");
   expect(t.apply(WorkspaceTaskTracker.event(event(state(ID,"tap-2",A,e[0],102.5))))!=null&&e[1].equals(t.line(A)),e[0]);
  }
 }

 /** T3: a state for another task, request, action or device-scoped id is ignored. */
 static void t3(){
  WorkspaceTaskTracker t=new WorkspaceTaskTracker();t.tapped(A,"tap-3");t.replied("tap-3",accepted(ID));
  expect(t.apply(parse(state(OTHER_ID,"tap-3x",A,"done",200)))==null,"another taskId");
  expect(t.apply(parse(state(OTHER_ID,"tap-3","task:other",ID.equals(OTHER_ID)?"done":"done",200)))==null,"another action, even with this requestId");
  expect("Started · in Rose's work".equals(t.line(A)),"unchanged");
  expect(WorkspaceTaskTracker.event("{\"kind\":\"task\",\"task\":"+state(ID,"tap-3",A,"done",200)+"}")==null,"only workspace_task events");
 }

 /** T4: a restart keeps the record; a lost reply re-associates by requestId from the reconnect replay. */
 static void t4(){
  WorkspaceTaskTracker t=new WorkspaceTaskTracker();t.tapped(A,"tap-4");
  t.replied("tap-4",new WorkspaceTaskClient.Outcome(WorkspaceTaskClient.Kind.UNCERTAIN,"no_reply",null,3));
  expect("Not sure it started · check Rose's work".equals(t.line(A)),"lost reply: unsure");
  WorkspaceTaskTracker back=WorkspaceTaskTracker.fromJson(t.toJson());
  expect("Not sure it started · check Rose's work".equals(back.line(A)),"survives a restart");
  expect(back.apply(WorkspaceTaskTracker.event(event(state(ID,"tap-4",A,"running",110))))!=null,"the replay carries requestId: re-associated");
  expect(ID.equals(back.byAction(A).taskId)&&"Rose is working".equals(back.line(A)),"now follows its taskId");
  WorkspaceTaskTracker again=WorkspaceTaskTracker.fromJson(back.toJson());
  expect(ID.equals(again.byAction(A).taskId)&&"Rose is working".equals(again.line(A))&&again.openTaskIds().contains(ID),"taskId and state survive, and it is still open for the GET");
  // A duplicate reply also gives the taskId.
  WorkspaceTaskTracker d=new WorkspaceTaskTracker();d.tapped(A,"tap-5");d.replied("tap-5",new WorkspaceTaskClient.Outcome(WorkspaceTaskClient.Kind.ALREADY_STARTED,null,ID,1));
  expect(ID.equals(d.byAction(A).taskId)&&"Started · in Rose's work".equals(d.line(A)),"duplicate re-associates");
 }

 /** T5: older updates are dropped; nothing is upgraded without App Core saying so. */
 static void t5(){
  WorkspaceTaskTracker t=new WorkspaceTaskTracker();t.tapped(A,"tap-6");t.replied("tap-6",accepted(ID));
  t.apply(parse(state(ID,"tap-6",A,"running",120)));
  expect(t.apply(parse(state(ID,"tap-6",A,"running",119)))==null,"older running dropped");
  expect(t.apply(parse(state(ID,"tap-6",A,"done",119)))==null&&"Rose is working".equals(t.line(A)),"an older end is dropped too");
  expect(t.apply(parse(state(ID,"tap-6",A,"running",120)))==null,"the same running again changes nothing");
  expect(t.apply(parse(state(ID,"tap-6",A,"done",120)))!=null&&"Done · in Rose's work".equals(t.line(A)),"a final at the same time wins over running");
 }

 /** T6: with no state at all after acceptance, the card stays started; the GET is how it learns more. */
 static void t6(){
  WorkspaceTaskTracker t=new WorkspaceTaskTracker();t.tapped(A,"tap-7");t.replied("tap-7",accepted(ID));
  expect(t.openTaskIds().size()==1&&ID.equals(t.openTaskIds().get(0)),"open for the GET");
  expect("Started · in Rose's work".equals(t.line(A)),"not done, not failed, just started");
  t.apply(parse(state(ID,"tap-7",A,"failed",130)));
  expect(t.openTaskIds().isEmpty(),"a final leaves nothing to read");
 }

 /** T7: the GET's answers map strictly; 404 unknown, garbage or any other status change nothing. */
 static void t7(){
  expect(WorkspaceTaskTracker.read(new WorkspaceTaskClient.Response(200,state(ID,"tap-8",A,"done",140)),ID).state.equals("done"),"200 state");
  expect(WorkspaceTaskTracker.read(new WorkspaceTaskClient.Response(404,"{\"schemaVersion\":1,\"status\":\"unknown\"}"),ID)==null,"404 unknown");
  expect(WorkspaceTaskTracker.read(new WorkspaceTaskClient.Response(401,"{}"),ID)==null,"401");
  expect(WorkspaceTaskTracker.read(new WorkspaceTaskClient.Response(200,"garbage"),ID)==null,"garbage");
  expect(WorkspaceTaskTracker.read(new WorkspaceTaskClient.Response(200,state(OTHER_ID,"tap-8",A,"done",140)),ID)==null,"a state for another taskId");
  expect(WorkspaceTaskTracker.read(null,ID)==null,"no reply");
  // The client's lookup: signed GET on the task path, gate respected, one attempt.
  final String[] seen={null};
  WorkspaceTaskClient.Transport fake=new WorkspaceTaskClient.Transport(){
   public WorkspaceTaskClient.Response post(String p,Map<String,String> h,byte[] b){throw new AssertionError("no POST");}
   public WorkspaceTaskClient.Response get(String p,Map<String,String> h){seen[0]=p+" "+h.get(LocalRoseAuth.H_SIGNATURE);return new WorkspaceTaskClient.Response(200,state(ID,"tap-8",A,"cancelled",150));}
  };
  WorkspaceTaskClient.Signer signer=(m,p)->{Map<String,String> h=new LinkedHashMap<>();h.put(LocalRoseAuth.H_SIGNATURE,m+" "+p);return h;};
  WorkspaceTaskTracker.State s=new WorkspaceTaskClient(new WorkspaceTaskClient.Gate(true),fake,signer).lookup(ID);
  expect(s!=null&&"cancelled".equals(s.state)&&("/workspace/v1/tasks/"+ID+" GET /workspace/v1/tasks/"+ID).equals(seen[0]),"signed GET for its own taskId: "+seen[0]);
  seen[0]=null;
  expect(new WorkspaceTaskClient(WorkspaceTaskClient.Gate.disabled(),fake,signer).lookup(ID)==null&&seen[0]==null,"gate off: no GET");
  expect(new WorkspaceTaskClient(new WorkspaceTaskClient.Gate(true),fake,signer).lookup("wt-NOTHEX")==null&&seen[0]==null,"a malformed taskId is never requested");
 }

 /** T8: a final state is final: nothing after it, from any source, changes it. */
 static void t8(){
  WorkspaceTaskTracker t=new WorkspaceTaskTracker();t.tapped(A,"tap-9");t.replied("tap-9",accepted(ID));
  t.apply(parse(state(ID,"tap-9",A,"done",160)));
  expect(t.apply(parse(state(ID,"tap-9",A,"running",170)))==null&&"Done · in Rose's work".equals(t.line(A)),"done never goes back to running");
  expect(t.apply(parse(state(ID,"tap-9",A,"failed",180)))==null&&"Done · in Rose's work".equals(t.line(A)),"or becomes another end");
  WorkspaceTaskTracker u=new WorkspaceTaskTracker();u.tapped(A,"tap-10");u.replied("tap-10",accepted(ID));
  u.apply(parse(state(ID,"tap-10",A,"uncertain",160)));
  expect(u.apply(parse(state(ID,"tap-10",A,"done",200)))==null&&u.line(A).startsWith("Not sure how it ended"),"uncertain never becomes done later");
 }

 /** App Core's bounds, enforced on the phone: a bad state is ignored, never shown. */
 static void strict(){
  expect(parse(state(ID,"r",A,"running",1))!=null,"well formed");
  String good=state(ID,"r",A,"running",1);
  String[] bad={
   good.replace("\"schemaVersion\":1","\"schemaVersion\":2"),
   good.replace(ID,"wt-0123"),good.replace(ID,"t-0123456789abcdef"),
   good.replace("\"state\":\"running\"","\"state\":\"finished\""),
   good.replace("\"status\":\"running\"","\"status\":\"maybe\""),
   good.replace("\"detail\":\"\"","\"detail\":\""+new String(new char[201]).replace('\0','d')+"\""),
   good.replace("\"actionId\":\"task:grocery\"","\"actionId\":\"launch:x\""),
   good.replace(",\"updatedAt\":1.0",""),good.replace("\"updatedAt\":1.0","\"updatedAt\":\"soon\""),
   good.replace("[{\"id\":\"local:1\",\"status\":\"running\"}]","[{\"id\":\"\",\"status\":\"running\"}]"),
   good.replace("\"phoneTasks\":[{\"id\":\"local:1\",\"status\":\"running\"}]","\"phoneTasks\":{}")
  };
  for(String b:bad)expect(parse(b)==null,"refused: "+b);
  // App Core sends an all-digit phone task id as a JSON integer (workspace_tasks.py _get).
  String pt="[{\"id\":\"local:1\",\"status\":\"running\"}]";
  expect(parse(good.replace(pt,"[{\"id\":42,\"status\":\"running\"},{\"id\":\"local:1\",\"status\":\"done\"}]"))!=null,"an integer phone task id");
  for(String b:new String[]{"-1","1.5","true","null"})expect(parse(good.replace(pt,"[{\"id\":"+b+",\"status\":\"running\"}]"))==null,"refused phone task id "+b);
  expect(parse(good.replace(pt,"[{\"id\":\""+new String(new char[129]).replace('\0','p')+"\",\"status\":\"running\"}]"))==null,"a phone task id over 128");
  StringBuilder many=new StringBuilder("[");for(int i=0;i<9;i++){if(i>0)many.append(',');many.append("{\"id\":\"p").append(i).append("\",\"status\":\"done\"}");}many.append(']');
  expect(parse(good.replace("[{\"id\":\"local:1\",\"status\":\"running\"}]",many.toString()))==null,"more than 8 phone tasks");
  expect(WorkspaceTaskTracker.event("not json")==null&&WorkspaceTaskTracker.event("{\"kind\":\"workspace_task\",\"task\":{}}")==null,"unreadable events");
  WorkspaceTaskTracker t=new WorkspaceTaskTracker();
  for(int i=0;i<WorkspaceTaskTracker.KEEP+5;i++)t.tapped("task:t"+i,"tap-"+i);
  expect(t.byAction("task:t0")==null&&t.byAction("task:t24")!=null,"the last 20 taps are kept");
  expect(WorkspaceTaskTracker.fromJson("{\"v\":1,\"records\":[{\"actionId\":\"bad id\",\"requestId\":\"r\"},{\"actionId\":\"task:ok\",\"requestId\":\"r\",\"taskId\":\"wt-zz\"}]}").byAction("task:ok").taskId==null,"stored values are checked too");
  expect(WorkspaceTaskTracker.fromJson("garbage").openTaskIds().isEmpty(),"a corrupt store gives none");
 }
}
