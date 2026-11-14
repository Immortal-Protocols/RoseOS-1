package dev.pixel.voice;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Project 2: the hub's error paths (WorkspaceRecovery), each driven on the JVM with the real composer and
 * dispatcher.
 * <ul>
 *   <li>The worker throws anything, an Error included.</li>
 *   <li>Applying a result throws, before or after the composer has finished it.</li>
 *   <li>Handling a conversational request throws on the main thread.</li>
 * </ul>
 * In every case the composer is left free, each caller is answered once, and nothing is thrown on.
 */
public final class WorkspaceRecoveryTest {
 static int checks;
 static void expect(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}

 @SuppressWarnings("unchecked")
 static Map<String,Object> json(String s){return (Map<String,Object>)MiniJson.parse(s);}
 static WorkspaceAgent.Request arrange(String id)throws Exception{return WorkspaceAgent.parse(json("{\"schemaVersion\":1,\"requestId\":\""+id+"\",\"kind\":\"arrange\"}"));}

 public static void main(String[] args)throws Exception{
  worker();applyBeforeFinish();applyAfterFinish();handling();once();
  System.out.println("WorkspaceRecoveryTest: "+checks+" checks passed");
 }

 /** Worker path: any Throwable becomes model_unavailable, and the composer is then freed by the normal finish. */
 static void worker()throws Exception{
  Throwable[] failures={new OutOfMemoryError("model"),new StackOverflowError(),new IllegalStateException("sources"),new Exception("checked")};
  for(Throwable f:failures){
   String out=WorkspaceRecovery.compose("w1",()->{throw f;});
   Map<String,Object> m=json(out);
   expect("failed".equals(m.get("status"))&&"model_unavailable".equals(m.get("failure"))&&"w1".equals(m.get("requestId")),f.getClass().getSimpleName()+" becomes model_unavailable");
  }
  expect("model_unavailable".equals(json(WorkspaceRecovery.compose("w2",()->null)).get("failure")),"no result at all is model_unavailable");
  expect("x".equals(WorkspaceRecovery.compose("w3",()->"x")),"a result passes through untouched");

  // End to end: the failed result frees the composer, the caller hears unavailable, and the next request runs.
  WorkspaceState st=new WorkspaceState();WorkspaceComposer c=new WorkspaceComposer(st);WorkspaceRequests d=new WorkspaceRequests(st,c);
  List<Map<String,Object>> got=new ArrayList<>();
  WorkspaceRequests.Step s=d.conversation(arrange("oom"),false,false,got::add);
  String result=WorkspaceRecovery.compose(c.requestId(),()->{throw new OutOfMemoryError();});
  d.finished(s.gen,c.finish(s.gen,result,WorkspaceTest.REG),false);
  expect(!c.busy()&&"unavailable".equals(got.get(0).get("status")),"after an Error the composer is free and the caller hears unavailable");
  expect(c.begin("next",false)>0&&c.busy(),"the next request runs");
 }

 /** Applying throws before the composer finished: the composer is freed, the caller hears uncertain/internal once. */
 static void applyBeforeFinish()throws Exception{
  WorkspaceState st=new WorkspaceState();WorkspaceComposer c=new WorkspaceComposer(st);WorkspaceRequests d=new WorkspaceRequests(st,c);
  List<Map<String,Object>> got=new ArrayList<>();final int[] redraws={0};
  WorkspaceRequests.Step s=d.conversation(arrange("a1"),false,true,got::add);
  WorkspaceRecovery.apply(()->{throw new IllegalStateException("bug");},c,d,s.gen,()->redraws[0]++);
  expect(!c.busy()&&c.phase()==WorkspaceComposer.Phase.FAILED,"the composer is not left busy");
  expect(got.size()==1&&"uncertain".equals(got.get(0).get("status"))&&"internal".equals(got.get(0).get("reason"))&&!got.get(0).containsKey("revision"),"caller hears uncertain/internal: "+got);
  expect(d.waitingCount()==0&&redraws[0]==1,"nobody left waiting; the page redraws");
  expect(st.layout()==null&&st.revision()==0,"nothing changed");
  // A late real result for the same generation is ignored and answers nobody again.
  d.finished(s.gen,c.finish(s.gen,WorkspaceTest.result("a1","s-1","Late",WorkspaceTest.GRID),WorkspaceTest.REG),true);
  expect(got.size()==1&&st.layout()==null,"a late result after recovery changes nothing");
  // A redraw that itself throws is contained too.
  WorkspaceRequests.Step s2=d.conversation(arrange("a2"),false,true,got::add);
  WorkspaceRecovery.apply(()->{throw new IllegalStateException();},c,d,s2.gen,()->{throw new IllegalStateException("draw");});
  expect(!c.busy()&&got.size()==2,"a failing redraw is contained");
 }

 /** Applying throws after the composer committed (say, while drawing): the commit stands, the caller is answered once. */
 static void applyAfterFinish()throws Exception{
  WorkspaceState st=new WorkspaceState();WorkspaceComposer c=new WorkspaceComposer(st);WorkspaceRequests d=new WorkspaceRequests(st,c);
  List<Map<String,Object>> got=new ArrayList<>();
  WorkspaceRequests.Step s=d.conversation(arrange("b1"),false,false,got::add);
  WorkspaceRecovery.apply(()->{
   WorkspaceComposer.Outcome o=c.finish(s.gen,WorkspaceTest.result("b1","s-1","Tonight",WorkspaceTest.GRID),WorkspaceTest.REG);
   d.finished(s.gen,o,false);
   throw new IllegalStateException("drawing failed after the commit");
  },c,d,s.gen,null);
  expect(st.layout()!=null&&"Tonight".equals(st.layout().title)&&st.revision()==1,"the commit stands");
  expect(got.size()==1&&"prepared".equals(got.get(0).get("status")),"the caller was answered once, with the real outcome: "+got);
  expect(!c.busy()&&c.phase()==WorkspaceComposer.Phase.IDLE,"the composer stays idle (fail after a finish is a no-op)");
 }

 /** Handling a conversational request throws on the main thread: the caller hears uncertain/internal, nothing is thrown. */
 static void handling(){
  List<Map<String,Object>> got=new ArrayList<>();
  WorkspaceRecovery.handle(()->{throw new NullPointerException();},got::add,"h1");
  expect(got.size()==1&&"uncertain".equals(got.get(0).get("status"))&&"internal".equals(got.get(0).get("reason"))&&"h1".equals(got.get(0).get("requestId")),"uncertain/internal: "+got);
  WorkspaceRecovery.handle(()->{},got::add,"h2");
  expect(got.size()==1,"a request handled normally is not answered by recovery");
  WorkspaceRecovery.handle(()->{throw new IllegalStateException();},r->{throw new IllegalStateException("reply");},"h3");
  checks++; // reaching here means a failing reply is contained too
 }

 /** A caller is answered once, even if a failure path answers again after the real answer. */
 static void once(){
  List<Map<String,Object>> got=new ArrayList<>();
  WorkspaceRequests.Reply r=WorkspaceRecovery.once(got::add);
  r.send(WorkspaceAgent.result("o1","shown",3L,null));
  WorkspaceRecovery.handle(()->{throw new IllegalStateException();},r,"o1");
  expect(got.size()==1&&"shown".equals(got.get(0).get("status")),"only the first answer reaches the caller");
 }
}
