package dev.pixel.voice;

import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Project 2: what the workspace does when something goes wrong in the hub's own machinery, as opposed to a typed
 * composer failure. It never leaves the composer busy, never answers a caller twice, and never lets the failure
 * reach Rose Agent's main thread, which is Home's.
 *
 * Pure (no Android types): WorkspaceHub calls these on its worker thread and its main thread. WorkspaceRecoveryTest
 * drives each path on the JVM.
 */
final class WorkspaceRecovery {
 private WorkspaceRecovery() {}

 /** The worker's job: read the phone and compose. It may throw anything. */
 interface Work { String run() throws Throwable; }

 /**
  * Worker path. The composer result for a request, whatever happens: any Throwable, an Error such as running
  * out of memory included, becomes a model_unavailable failure for that request, so its result is always
  * posted back and the composer is freed.
  */
 static String compose(String requestId,Work work){
  try{String r=work.run();return r!=null?r:WorkspaceEngine.failed(requestId,"model_unavailable");}
  catch(Throwable t){return WorkspaceEngine.failed(requestId,"model_unavailable");}
 }

 /**
  * Main-thread path, applying a result. If apply throws, the request is failed: the composer becomes idle (a
  * no-op if apply had already finished it), and a caller still waiting hears uncertain/internal. Nothing is
  * thrown on.
  */
 static void apply(Runnable apply,WorkspaceComposer composer,WorkspaceRequests requests,int gen,Runnable redraw){
  try{apply.run();}
  catch(RuntimeException e){
   try{composer.fail(gen,"model_unavailable");}catch(RuntimeException ignored){}
   try{requests.abandon(gen,"internal");}catch(RuntimeException ignored){}
   try{if(redraw!=null)redraw.run();}catch(RuntimeException ignored){}
  }
 }

 /**
  * Main-thread path, a conversational request. If handling it throws, the caller hears uncertain/internal (the
  * phone cannot tell whether anything changed). Nothing is thrown on.
  */
 static void handle(Runnable handle,WorkspaceRequests.Reply reply,String requestId){
  try{handle.run();}
  catch(RuntimeException e){try{reply.send(WorkspaceAgent.result(requestId,"uncertain",null,"internal"));}catch(RuntimeException ignored){}}
 }

 /** A reply that passes on only its first answer: a caller is answered once, even if a failure path answers again. */
 static WorkspaceRequests.Reply once(WorkspaceRequests.Reply reply){
  final AtomicBoolean sent=new AtomicBoolean();
  return result->{if(sent.compareAndSet(false,true))reply.send(result);};
 }
}
