package dev.pixel.voice;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Project 2: request-to-render timing, the renderer half that rose-layout-v1 leaves to OS Product
 * ("Request-to-render latency needs the renderer's completion timestamp"). For one request it keeps:
 * <ul>
 *   <li>when it was asked;</li>
 *   <li>when the composer's result reached the page;</li>
 *   <li>when the first frame showing the committed layout was drawn.</li>
 * </ul>
 * All times are monotonic nanoseconds. Pure (no Android types): the page passes System.nanoTime() at each point,
 * and Choreographer's frame time for the render.
 *
 * A record carries the composer's own timing as reported, so composer-side and page-side numbers stay apart. A
 * result that is not from a model (the starter layout, a scripted composer) is labelled so, and is never a model
 * measurement. Nothing here holds the request's text: only its id, trigger and numbers.
 */
final class WorkspaceTiming {
 static final int KEEP=20;

 /** One request, from asking to the first frame of its layout (or to its failure). */
 static final class Record {
  final String requestId,trigger;
  final long requestedNanos;
  long resultNanos=-1,renderedNanos=-1;
  String outcome,failure,modelId;boolean model;
  Map<String,Object> composer=new LinkedHashMap<>();
  Record(String requestId,String trigger,long requestedNanos){this.requestId=requestId;this.trigger=trigger;this.requestedNanos=requestedNanos;}

  /** basis is "request-to-render" once a frame showed the layout; otherwise "result-only" (failed, superseded or not drawn yet). */
  String basis(){return renderedNanos>=0?"request-to-render":"result-only";}
  Double requestToResultMs(){return resultNanos<0?null:ms(requestedNanos,resultNanos);}
  Double resultToRenderMs(){return resultNanos<0||renderedNanos<0?null:ms(resultNanos,renderedNanos);}
  Double requestToRenderMs(){return renderedNanos<0?null:ms(requestedNanos,renderedNanos);}

  Map<String,Object> toMap(){
   Map<String,Object> m=new LinkedHashMap<>();
   m.put("requestId",requestId);m.put("trigger",trigger);m.put("basis",basis());
   m.put("outcome",outcome);if(failure!=null)m.put("failure",failure);
   m.put("modelId",modelId);m.put("model",model);
   m.put("requestToResultMs",num(requestToResultMs()));m.put("resultToRenderMs",num(resultToRenderMs()));m.put("requestToRenderMs",num(requestToRenderMs()));
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
  * The composer's result reached the page. outcome is WorkspaceComposer's (COMMITTED, REJECTED or IGNORED).
  * composerTiming is the result's own timing (contextMs, generationMs, validationMs, composeMs) or null. A
  * second result for the same request is ignored.
  */
 void resulted(String requestId,WorkspaceComposer.Outcome outcome,String failure,String modelId,Map<String,?> composerTiming,long nanos){
  Record r=find(requestId);if(r==null||r.resultNanos>=0||nanos<r.requestedNanos)return;
  r.resultNanos=nanos;r.outcome=outcome.name().toLowerCase(java.util.Locale.ROOT);r.failure=failure;
  r.modelId=modelId;r.model=isModel(modelId);
  if(composerTiming!=null)for(Map.Entry<String,?> e:composerTiming.entrySet())if(e.getValue() instanceof Number)r.composer.put(e.getKey(),((Number)e.getValue()).doubleValue());
 }

 /**
  * The first frame showing a committed layout was drawn. Only a committed request renders, only once, and
  * never before its result.
  */
 void rendered(String requestId,long frameNanos){
  Record r=find(requestId);
  if(r==null||r.renderedNanos>=0||!"committed".equals(r.outcome)||frameNanos<r.resultNanos)return;
  r.renderedNanos=frameNanos;
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
