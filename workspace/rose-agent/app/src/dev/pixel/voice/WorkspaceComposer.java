package dev.pixel.voice;

/**
 * Project 2: one workspace request at a time, from asking to a committed layout, as the approved HiFi (r2)
 * behaves. A newer request supersedes an older one, and a late reply to a superseded request is ignored.
 * A malformed reply, a model failure or a timeout leaves the last layout and its pins untouched. A request
 * made from inside an app never leaves the app: it only raises the offer ("Workspace ready · Show
 * workspace"), which disappears once the workspace is reached. Pure (no Android types); the page calls it
 * on the main thread and runs inference elsewhere.
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
 private String pending;private boolean fromApp;
 private Offer offer=Offer.NONE;private String offerRequest;
 private String lastError;

 WorkspaceComposer(WorkspaceState state){this.state=state;}

 Phase phase(){return phase;}
 Offer offer(){return offer;}
 String lastError(){return lastError;}
 /** While composing, the page keeps the old cards dimmed under a skeleton and disables its buttons in place. */
 boolean busy(){return phase==Phase.COMPOSING;}

 /**
  * Starts a request and returns its generation, which the reply must carry back. text is the typed or
  * spoken request, or null for Arrange for me. Returns -1 for an empty or oversized request.
  */
 int begin(String text,boolean fromApp){
  String request;
  if(text==null)request=ARRANGE;
  else{request=text.trim();if(request.isEmpty()||request.codePointCount(0,request.length())>WorkspaceLayout.MAX_REQUEST)return -1;}
  generation++;phase=Phase.COMPOSING;pending=request;this.fromApp=fromApp;lastError=null;
  if(fromApp){offer=Offer.PREPARING;offerRequest=request;}
  else if(offer==Offer.PREPARING){offer=Offer.NONE;offerRequest=null;}  // superseded from the page itself
  return generation;
 }

 /** Regenerate: the same request again (around the current pins). -1 if there is nothing to regenerate. */
 int regenerate(){
  String r=state.request();if(r==null)return -1;
  return begin(ARRANGE.equals(r)?null:r,false);
 }

 /** The composer's reply for generation gen, checked against the ids resolved for that request. */
 Outcome finish(int gen,String reply,WorkspaceLayout.Ids ids){
  if(gen!=generation||phase!=Phase.COMPOSING)return Outcome.IGNORED;
  WorkspaceLayout layout;
  try{layout=WorkspaceLayout.parse(reply,ids,state.keys());}
  catch(WorkspaceLayout.Invalid e){failNow(e.reason);return Outcome.REJECTED;}
  state.commit(layout,pending);
  phase=Phase.IDLE;
  if(fromApp&&offer==Offer.PREPARING)offer=Offer.READY;
  return Outcome.COMMITTED;
 }

 /** No reply: the model failed, timed out or ran out of memory. Same as a rejected reply. */
 Outcome fail(int gen,String kind){
  if(gen!=generation||phase!=Phase.COMPOSING)return Outcome.IGNORED;
  failNow(kind==null?"failed":kind);return Outcome.REJECTED;
 }

 private void failNow(String reason){
  phase=Phase.FAILED;lastError=reason;
  if(fromApp&&offer==Offer.PREPARING)offer=Offer.FAILED;
 }

 /** The person reached the workspace page: the offer has done its job. */
 void workspaceShown(){offer=Offer.NONE;offerRequest=null;}
 void dismissOffer(){offer=Offer.NONE;offerRequest=null;}

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
