package dev.pixel.voice;

/**
 * Project 2: one workspace request at a time, from asking to a committed layout, as the approved HiFi (r2)
 * behaves. A newer request supersedes an older one, and a late result for a superseded request is ignored.
 * A failed result, an invalid one, or no answer leaves the last layout and its pins untouched. A request
 * made from a conversation never leaves the app in front: it raises the offer ("Workspace ready · Show
 * workspace"), which goes once the workspace is reached.
 *
 * Requests map onto Models' rose-layout-v1 triggers (button, direct_text, conversational) and App Core's
 * composer intent/result v1 kinds (arrange, prompt, regenerate, undo; docs/APP-BUILDER.md section 4). Pure
 * (no Android types); the page calls it on the main thread and runs the composer elsewhere.
 */
final class WorkspaceComposer {
 enum Phase { IDLE, COMPOSING, FAILED }
 enum Offer { NONE, PREPARING, READY, FAILED }
 enum Outcome { COMMITTED, REJECTED, IGNORED }

 /** "Arrange for me" asks for a best guess; its request is shown as this label. */
 static final String ARRANGE="Arrange for me";

 private final WorkspaceState state;
 private Phase phase=Phase.IDLE;
 private int generation;
 private String pending,requestId,trigger;private boolean conversational;
 private Offer offer=Offer.NONE;private String offerRequest;
 private String lastFailure;
 private java.util.List<String> avoid=new java.util.ArrayList<>();

 WorkspaceComposer(WorkspaceState state){this.state=state;}

 Phase phase(){return phase;}
 Offer offer(){return offer;}
 /** The last failure's rose-layout-v1 token (never model text), or null. */
 String lastFailure(){return lastFailure;}
 /** While composing, the page keeps the old sections dimmed under a skeleton and disables its buttons in place. */
 boolean busy(){return phase==Phase.COMPOSING;}
 /** The current request's id and rose-layout-v1 trigger, for the composer call. */
 String requestId(){return requestId;}
 String trigger(){return trigger;}
 /** Regenerate's soft hint for the current request: the previous layout's item ids; empty otherwise. */
 java.util.List<String> avoid(){return new java.util.ArrayList<>(avoid);}
 /** The current request's text: null for a best guess. */
 String prompt(){return ARRANGE.equals(pending)?null:pending;}

 /**
  * Starts a request and returns its generation. text is the typed or transcribed request, or null for
  * Arrange for me. conversational marks a request delivered from a conversation (App Core's prompt kind),
  * with that route's requestId; page requests get their own. Returns -1 for an empty or oversized request.
  */
 int begin(String text,boolean conversational,String routeRequestId){
  String request;
  if(text==null)request=ARRANGE;
  else{request=text.trim();if(request.isEmpty()||request.codePointCount(0,request.length())>WorkspaceLayout.MAX_REQUEST)return -1;}
  generation++;phase=Phase.COMPOSING;pending=request;this.conversational=conversational;lastFailure=null;avoid=new java.util.ArrayList<>();
  trigger=text==null?"button":conversational?"conversational":"direct_text";
  requestId=conversational&&routeRequestId!=null?routeRequestId:"page-"+generation;
  if(conversational){offer=Offer.PREPARING;offerRequest=request;}
  else if(offer==Offer.PREPARING){offer=Offer.NONE;offerRequest=null;}  // superseded from the page itself
  return generation;
 }
 int begin(String text,boolean conversational){return begin(text,conversational,null);}

 /** Regenerate: the same request again (around the current pins). -1 if there is nothing to regenerate. */
 int regenerate(){
  String r=state.request();if(r==null)return -1;
  int gen=begin(ARRANGE.equals(r)?null:r,false);
  if(gen>0&&state.layout()!=null){
   java.util.Set<String> ids=new java.util.LinkedHashSet<>();
   for(WorkspaceLayout.Section c:state.layout().sections){ids.addAll(c.items);ids.addAll(c.actions);}
   avoid=new java.util.ArrayList<>(ids);
  }
  return gen;
 }

 /** The composer's result for generation gen, checked against the registry built for that request. */
 Outcome finish(int gen,String result,WorkspaceLayout.Registry registry){
  if(gen!=generation||phase!=Phase.COMPOSING)return Outcome.IGNORED;
  WorkspaceLayout layout;
  try{layout=WorkspaceLayout.check(result,requestId,registry,state.keys());}
  catch(WorkspaceLayout.Invalid e){failNow(e.failure);return Outcome.REJECTED;}
  state.commit(layout,pending);
  phase=Phase.IDLE;
  if(conversational&&offer==Offer.PREPARING)offer=Offer.READY;
  return Outcome.COMMITTED;
 }

 /** No result: the runtime failed, timed out or ran out of memory. Same as a failed result. */
 Outcome fail(int gen,String failure){
  if(gen!=generation||phase!=Phase.COMPOSING)return Outcome.IGNORED;
  failNow(failure);return Outcome.REJECTED;
 }

 private void failNow(String failure){
  phase=Phase.FAILED;lastFailure=WorkspaceLayout.FAILURES.contains(failure)?failure:"invalid_output";
  if(conversational&&offer==Offer.PREPARING)offer=Offer.FAILED;
 }

 /** The person reached the workspace page: the offer has done its job. */
 void workspaceShown(){offer=Offer.NONE;offerRequest=null;}
 void dismissOffer(){offer=Offer.NONE;offerRequest=null;}

 /**
  * App Core's result status for the last request (docs/APP-BUILDER.md 4.2): shown or prepared only for a
  * committed layout (onScreen says which), unavailable when no model is loaded, rejected otherwise. Only
  * the provider decides shown versus prepared; the composer is never told.
  */
 static String routeStatus(Outcome outcome,String failure,boolean onScreen){
  if(outcome==Outcome.COMMITTED)return onScreen?"shown":"prepared";
  if(outcome==Outcome.IGNORED)return "uncertain";
  return "model_unavailable".equals(failure)?"unavailable":"rejected";
 }

 /** The status line (fixed height on the page; only its text changes). */
 String status(){
  if(phase==Phase.COMPOSING)return "Composing on this phone…";
  if(phase==Phase.FAILED)return "Couldn't arrange · kept your last layout";
  String r=state.request();
  return r==null?"":ARRANGE.equals(r)?"Best guess":"“"+r+"”";
 }

 /** The card shown inside an app after a request from a conversation; null when there is none. */
 String offerText(){
  switch(offer){
   case PREPARING:return "Preparing workspace: “"+offerRequest+"”";
   case READY:return "Workspace ready: “"+offerRequest+"”";
   case FAILED:return "Couldn't prepare workspace · kept your last layout";
   default:return null;
  }
 }
}
