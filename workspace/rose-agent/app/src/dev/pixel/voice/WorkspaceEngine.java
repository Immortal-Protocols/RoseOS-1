package dev.pixel.voice;

import java.util.LinkedHashMap;
import java.util.Map;
import rose.models.composer.Catalog;
import rose.models.composer.ComposeResult;
import rose.models.composer.Composer;
import rose.models.composer.Json;
import rose.models.composer.LocalInference;

/**
 * Project 2: one composer call, request text in and result text out. It runs Models' composer
 * (workspace/models/local-composer/java, catalog cat-1) over whatever LocalInference the phone has. Today that is
 * only WorkspaceStarter, which is not a model.
 *
 * Pure (no Android types). The page calls it off the main thread and hands the result to
 * WorkspaceComposer.finish, which checks it again with WorkspaceLayout.check. It never throws: anything
 * unexpected becomes a rose-layout-v1 failure.
 */
final class WorkspaceEngine {
 /** The page's own deadline for one call (cat-1's default). */
 static final long DEADLINE_MS=4000;

 private final Composer composer;
 private final String modelId;

 WorkspaceEngine(LocalInference inference){
  composer=new Composer(Catalog.cat1(),inference);
  String id=null;try{id=inference.info().get("id");}catch(RuntimeException ignored){}
  modelId=id;
 }

 /** The id the inference reports ("starter" for the starter layout); WorkspaceTiming labels by it. */
 String modelId(){return modelId;}

 /** The result for one rose-layout-v1 request. */
 String compose(String requestJson,LocalInference.Cancellation cancellation){
  String requestId=null;
  try{
   Object request=Json.parse(requestJson);
   if(request instanceof Map&&((Map<?,?>)request).get("requestId") instanceof String)requestId=(String)((Map<?,?>)request).get("requestId");
   ComposeResult r=composer.compose(request,cancellation==null?LocalInference.Cancellation.NEVER:cancellation);
   return r.toJson();
  }catch(Json.ParseError e){return failed(requestId,"bad_request");}
  catch(RuntimeException e){return failed(requestId,"model_unavailable");}
 }

 static String failed(String requestId,String failure){
  Map<String,Object> m=new LinkedHashMap<>();
  m.put("contract",WorkspaceLayout.CONTRACT);m.put("requestId",requestId);m.put("status","failed");m.put("failure",failure);
  return WorkspaceLayout.json(m);
 }
}
