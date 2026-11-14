package dev.pixel.voice;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Project 2: every request to the workspace goes through this one dispatcher, whether it comes from the page
 * (Arrange, typed text, Regenerate, Undo) or from a conversation (App Core's workspace_compose). The page and the
 * provider therefore share one state and one composer and never write separately.
 *
 * It decides whether a request needs the composer or can be answered at once. It answers every waiting
 * conversational caller exactly once, with its section 4.2 result:
 * <ul>
 *   <li>a newer request supersedes an older one, whose caller is told uncertain;</li>
 *   <li>undo needs no composer;</li>
 *   <li>a kind the phone does not offer is refused before anything changes.</li>
 * </ul>
 *
 * Pure, and on one thread: WorkspaceHub calls it on the main thread and runs the composer elsewhere.
 */
final class WorkspaceRequests {
 /** A conversational caller waiting for its section 4.2 result. */
 interface Reply { void send(Map<String,Object> result); }

 /** What the hub does next for a request: answer now (result), or compose generation gen. */
 static final class Step {
  final Map<String,Object> result;final int gen;
  private Step(Map<String,Object> result,int gen){this.result=result;this.gen=gen;}
  boolean composes(){return result==null;}
 }

 final WorkspaceState state;final WorkspaceComposer composer;
 private final Map<Integer,Waiting> waiting=new LinkedHashMap<>();
 /** How many answered conversational requests are remembered, so a repeated requestId gets its first answer. */
 static final int ANSWERED=50;
 private final Map<String,Map<String,Object>> answered=new LinkedHashMap<>();
 /** Requests still composing, by requestId: a repeat while composing waits for the same answer. */
 private final Map<String,List<Reply>> repeats=new LinkedHashMap<>();
 private static final class Waiting {final String requestId;final Reply reply;Waiting(String requestId,Reply reply){this.requestId=requestId;this.reply=reply;}}

 WorkspaceRequests(WorkspaceState state,WorkspaceComposer composer){this.state=state;this.composer=composer;}

 /** A request from the page itself: text, or null for Arrange for me. No caller waits. */
 Step page(String text){
  int gen=composer.begin(text,false);
  if(gen<0)return new Step(WorkspaceAgent.result(null,"rejected",null,"text"),-1);
  supersede(gen);return new Step(null,gen);
 }
 /** Regenerate from the page. */
 Step pageRegenerate(){
  int gen=composer.regenerate();
  if(gen<0)return new Step(WorkspaceAgent.result(null,"rejected",null,"nothing_to_regenerate"),-1);
  supersede(gen);return new Step(null,gen);
 }

 /**
  * A conversational request (section 4.1). model says whether a real model is on the phone; onScreen whether
  * the page is showing now (only for undo, which answers at once). The reply is called exactly once: now for
  * an immediate answer, or from finished() for a composition.
  */
 Step conversation(WorkspaceAgent.Request r,boolean model,boolean onScreen,Reply caller){
  // Section 4.1: a repeated requestId returns the first outcome and composes nothing new.
  Map<String,Object> first=answered.get(r.requestId);
  if(first!=null){caller.send(first);return new Step(first,-1);}
  List<Reply> joined=repeats.get(r.requestId);
  if(joined!=null){joined.add(caller);return new Step(WorkspaceAgent.result(r.requestId,"uncertain",null,"still_composing"),-1);}
  final List<Reply> all=new ArrayList<>();all.add(caller);
  Reply reply=result->{
   answered.put(r.requestId,result);while(answered.size()>ANSWERED)answered.remove(answered.keySet().iterator().next());
   repeats.remove(r.requestId);
   for(Reply x:all)x.send(result);
  };
  if(!WorkspaceAgent.offered(r.kind,model)){
   Map<String,Object> m=WorkspaceAgent.result(r.requestId,"rejected",null,"kind_not_offered");reply.send(m);return new Step(m,-1);
  }
  if(WorkspaceAgent.UNDO.equals(r.kind)){
   if(composer.busy()){Map<String,Object> m=WorkspaceAgent.result(r.requestId,"rejected",null,"busy");reply.send(m);return new Step(m,-1);}
   boolean changed=state.undo();
   Map<String,Object> m=WorkspaceAgent.undone(r.requestId,changed,onScreen,state.revision());reply.send(m);return new Step(m,-1);
  }
  int gen;
  if(WorkspaceAgent.REGENERATE.equals(r.kind))gen=composer.regenerate(true,r.requestId);
  else gen=composer.begin(WorkspaceAgent.PROMPT.equals(r.kind)?r.text:null,true,r.requestId);
  if(gen<0){
   Map<String,Object> m=WorkspaceAgent.result(r.requestId,"rejected",null,WorkspaceAgent.REGENERATE.equals(r.kind)?"nothing_to_regenerate":"text");
   reply.send(m);return new Step(m,-1);
  }
  supersede(gen);
  waiting.put(gen,new Waiting(r.requestId,reply));repeats.put(r.requestId,all);
  return new Step(null,gen);
 }

 /**
  * The composer's result for gen reached the main thread, already applied through WorkspaceComposer (outcome).
  * Its waiting caller, if any, gets shown or prepared with the new revision, or the mapped failure.
  */
 void finished(int gen,WorkspaceComposer.Outcome outcome,boolean onScreen){
  Waiting w=waiting.remove(gen);
  if(w!=null)w.reply.send(WorkspaceAgent.composed(w.requestId,outcome,composer.lastFailure(),onScreen,state.revision()));
 }

 /** Every older waiting caller is answered uncertain: its request was superseded and will never commit. */
 private void supersede(int gen){
  List<Integer> older=new ArrayList<>();for(Integer g:waiting.keySet())if(g<gen)older.add(g);
  for(Integer g:older){Waiting w=waiting.remove(g);w.reply.send(WorkspaceAgent.composed(w.requestId,WorkspaceComposer.Outcome.IGNORED,null,false,state.revision()));}
 }

 /** Callers still waiting (for release: each is answered uncertain). */
 void abandonAll(){
  for(Waiting w:new ArrayList<>(waiting.values()))w.reply.send(WorkspaceAgent.result(w.requestId,"uncertain",null,"stopped"));
  waiting.clear();
 }
 int waitingCount(){return waiting.size();}
}
