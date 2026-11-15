package dev.pixel.voice;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Project 2: request-to-render timing, the renderer half that rose-layout-v1 leaves to OS Product
 * ("Request-to-render latency needs the renderer's completion timestamp"). For one request it keeps three points,
 * each a System.nanoTime() reading passed in by the hub or the page:
 * <ul>
 *   <li><b>requested:</b> in WorkspaceHub.start, on the main thread, right after the composer accepted the
 *   request. For a conversational request this is on the phone, so App Core's link and the provider's transit
 *   are not included.</li>
 *   <li><b>result:</b> on the composer's worker thread, as soon as compose returns and before the result is posted
 *   to the main thread. So main-thread queueing falls after it.</li>
 *   <li><b>first draw:</b> the first draw pass of the page's view tree, while the page is on screen, after a
 *   committed layout (a ViewTreeObserver.OnDrawListener). This is when the view tree is drawn, not when pixels
 *   are presented on the display.</li>
 * </ul>
 *
 * <b>Visibility.</b> A human wait is never reported as latency:
 * <ul>
 *   <li><b>request-to-first-draw:</b> only a result that arrives while the page is on screen, with the page staying
 *   on screen until its first draw, gives requestToRenderMs and resultToRenderMs.</li>
 *   <li><b>prepared-then-shown:</b> a result prepared off screen, or one whose page left the screen before its
 *   first draw, is drawn only when the person comes back. Its resultToShownDrawMs includes that wait
 *   (includesHumanWait), and it never gives requestToRenderMs.</li>
 *   <li>A draw while the page is off screen is not counted.</li>
 * </ul>
 *
 * A record carries the composer's own timing as reported, so composer-side and page-side numbers stay apart. A
 * result that is not from a model (the starter layout, a scripted composer) is labelled so, and is never a model
 * measurement. Nothing here holds the request's text: only its id, trigger and numbers. Pure (no Android types).
 */
final class WorkspaceTiming {
 static final int KEEP=20;
 /** Where a request was decided: the Rose server's model (requestToResultMs then includes the network), or the phone's starter. */
 static final String SERVER="server",PHONE="phone";

 /** One request, from asking to the first draw of its layout on screen (or to its failure). */
 static final class Record {
  final String requestId,trigger;
  final long requestedNanos;
  long resultNanos=-1,renderedNanos=-1;
  String outcome,failure,modelId;boolean model;
  /** Whether the page was on screen when the result arrived, and whether it left the screen before the first draw. */
  boolean visibleAtResult,leftBeforeDraw;
  /** A newer committed layout replaced this one before it was drawn, so no draw ever showed it. */
  boolean replacedBeforeDraw;
  /** SERVER or PHONE (WorkspaceTiming.routed); null if never set. */
  String route;
  Map<String,Object> composer=new LinkedHashMap<>();
  Record(String requestId,String trigger,long requestedNanos){this.requestId=requestId;this.trigger=trigger;this.requestedNanos=requestedNanos;}

  /** Seen on screen from the result to its first draw: the only case that measures the phone, not a person. */
  boolean continuouslyVisible(){return visibleAtResult&&!leftBeforeDraw;}
  /**
   * The record's basis:
   * <ul>
   *   <li>"request-to-first-draw": drawn while seen throughout;</li>
   *   <li>"prepared-then-shown": drawn only after the person came back;</li>
   *   <li>"result-only": failed, superseded, replaced before its first draw, or not drawn yet.</li>
   * </ul>
   */
  String basis(){return renderedNanos<0?"result-only":continuouslyVisible()?"request-to-first-draw":"prepared-then-shown";}
  Double requestToResultMs(){return resultNanos<0?null:ms(requestedNanos,resultNanos);}
  /** Phone-side only: null unless the basis is request-to-first-draw. */
  Double resultToRenderMs(){return resultNanos<0||renderedNanos<0||!continuouslyVisible()?null:ms(resultNanos,renderedNanos);}
  /** Phone-side only: null unless the basis is request-to-first-draw. Never includes a human wait. */
  Double requestToRenderMs(){return renderedNanos<0||!continuouslyVisible()?null:ms(requestedNanos,renderedNanos);}
  /** prepared-then-shown only: result to the first draw after the person came back. Includes their wait. */
  Double resultToShownDrawMs(){return resultNanos<0||renderedNanos<0||continuouslyVisible()?null:ms(resultNanos,renderedNanos);}

  Map<String,Object> toMap(){
   Map<String,Object> m=new LinkedHashMap<>();
   m.put("requestId",requestId);m.put("trigger",trigger);m.put("route",route);m.put("basis",basis());
   m.put("outcome",outcome);if(failure!=null)m.put("failure",failure);
   m.put("modelId",modelId);m.put("model",model);
   m.put("visibleAtResult",resultNanos>=0?visibleAtResult:null);m.put("leftBeforeDraw",leftBeforeDraw);m.put("replacedBeforeDraw",replacedBeforeDraw);
   m.put("requestToResultMs",num(requestToResultMs()));m.put("resultToRenderMs",num(resultToRenderMs()));m.put("requestToRenderMs",num(requestToRenderMs()));
   m.put("resultToShownDrawMs",num(resultToShownDrawMs()));m.put("includesHumanWait",resultToShownDrawMs()!=null);
   m.put("composer",composer);
   return m;
  }
 }

 /** Composer ids that are not models: the page's starter layout and Models' scripted test double. */
 static boolean isModel(String modelId){return modelId!=null&&!modelId.isEmpty()&&!WorkspaceStarter.ID.equals(modelId)&&!"scripted".equals(modelId);}

 private final List<Record> records=new ArrayList<>();

 /** A request started (WorkspaceComposer.begin). A repeat of the same id starts over. */
 void requested(String requestId,String trigger,long nanos){
  Record r=find(requestId);if(r!=null)records.remove(r);
  records.add(new Record(requestId,trigger,nanos));
  while(records.size()>KEEP)records.remove(0);
 }

 /**
  * The composer's result, taken when compose returned on the worker. outcome is WorkspaceComposer's (COMMITTED,
  * REJECTED or IGNORED). composerTiming is the result's own timing (contextMs, generationMs, validationMs,
  * composeMs) or null. visible is whether the page is on screen as the result is applied (WorkspaceHub.onScreen).
  * A second result for the same request is ignored.
  */
 void resulted(String requestId,WorkspaceComposer.Outcome outcome,String failure,String modelId,Map<String,?> composerTiming,long nanos,boolean visible){
  Record r=find(requestId);if(r==null||r.resultNanos>=0||nanos<r.requestedNanos)return;
  r.resultNanos=nanos;r.visibleAtResult=visible;r.outcome=outcome.name().toLowerCase(java.util.Locale.ROOT);r.failure=failure;
  if(outcome==WorkspaceComposer.Outcome.COMMITTED)for(Record o:records)if(o!=r&&"committed".equals(o.outcome)&&o.renderedNanos<0)o.replacedBeforeDraw=true;
  r.modelId=modelId;r.model=isModel(modelId);
  if(composerTiming!=null)for(Map.Entry<String,?> e:composerTiming.entrySet())if(e.getValue() instanceof Number)r.composer.put(e.getKey(),((Number)e.getValue()).doubleValue());
 }

 /** Where the request is decided: SERVER or PHONE. Anything else is ignored. */
 void routed(String requestId,String route){
  Record r=find(requestId);if(r!=null&&(SERVER.equals(route)||PHONE.equals(route)))r.route=route;
 }

 /**
  * The page left the screen or came back (WorkspaceHub.onScreen changed). Leaving marks every committed record
  * not yet drawn, so its eventual draw is reported as prepared-then-shown, with the wait, and never as latency.
  */
 void visibility(boolean onScreen){
  if(onScreen)return;
  for(Record r:records)if("committed".equals(r.outcome)&&r.renderedNanos<0)r.leftBeforeDraw=true;
 }

 /**
  * A draw pass after a committed layout. It counts only while the page is on screen, only for a committed request,
  * only once, and never before the result. Returns whether the page can stop listening for this request: true once
  * it is counted or can never count; false while it waits for a draw on screen.
  */
 boolean rendered(String requestId,long drawNanos,boolean onScreen){
  Record r=find(requestId);
  if(r==null||r.renderedNanos>=0||!"committed".equals(r.outcome)||r.replacedBeforeDraw)return true;
  if(drawNanos<r.resultNanos)return false;
  if(!onScreen){r.leftBeforeDraw=true;return false;}
  r.renderedNanos=drawNanos;return true;
 }

 /** The composer's model id in a result ("starter", a model's id), or null. Never throws. */
 static String modelId(String result){
  try{Object m=((Map<?,?>)MiniJson.parse(result)).get("model");Object id=m instanceof Map?((Map<?,?>)m).get("id"):null;return id instanceof String?(String)id:null;}
  catch(RuntimeException e){return null;}
 }
 /** The composer's own timing in a result, or null. Never throws. */
 @SuppressWarnings("unchecked")
 static Map<String,Object> composerTiming(String result){
  try{Object t=((Map<?,?>)MiniJson.parse(result)).get("timing");return t instanceof Map?(Map<String,Object>)t:null;}
  catch(RuntimeException e){return null;}
 }

 Record record(String requestId){return find(requestId);}
 List<Record> records(){return new ArrayList<>(records);}

 /** The kept records, newest last, for benchmarks and logs: ids, triggers and numbers only. */
 String json(){
  List<Object> out=new ArrayList<>();for(Record r:records)out.add(r.toMap());
  Map<String,Object> m=new LinkedHashMap<>();m.put("contract",WorkspaceLayout.CONTRACT);m.put("records",out);
  return WorkspaceLayout.json(m);
 }

 private Record find(String requestId){for(int i=records.size()-1;i>=0;i--)if(records.get(i).requestId.equals(requestId))return records.get(i);return null;}
 static double ms(long from,long to){return Math.round((to-from)/1e4)/100.0;}
 private static Object num(Double v){return v;}
}
