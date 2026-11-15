package dev.pixel.voice;

import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import rose.models.composer.LocalInference;

/**
 * Project 2: the workspace's decision comes from the Rose server's model, not from Rose Agent (Nathan,
 * 2026-11-14). This client sends the phone's own rose-layout-v1 request (WorkspaceSnapshot.request) to App
 * Core's signed POST /workspace/v1/compose and returns the server's rose-layout-v1 result for the page to check.
 * The contract was agreed with App Core on 2026-11-14:
 * <ul>
 *   <li>The body is {schemaVersion: 1, requestId, request}, device-scoped. A repeat of the same (device,
 *   requestId, body) returns the first result.</li>
 *   <li>A different body for the same device/requestId is refused with 400.</li>
 *   <li>A 200 carries a rose-layout-v1 result, ok or failed. The other replies are 503 unavailable, 400 refused
 *   and 401 when unsigned.</li>
 * </ul>
 *
 * <ul>
 *   <li><b>The phone is still the gate.</b> A result is only passed on. WorkspaceComposer.finish and
 *   WorkspaceLayout.check judge it against the phone's own registry, so an unknown id is refused and a failure
 *   keeps the last layout.</li>
 *   <li><b>One attempt, read strictly.</b> There is no retry: the request has its own deadline. A 200 whose
 *   envelope is not rose-layout-v1 for this requestId is uncertain, never shown.</li>
 *   <li><b>Explicit opt-out.</b> With the route disabled, nothing is sent.</li>
 * </ul>
 * Pure (no Android types): the transport and signer are WorkspaceTaskClient's interfaces, and tests use fakes.
 */
final class WorkspaceDecisionClient {
 static final String PATH="/workspace/v1/compose";
 /** The most the phone sends; a bigger request is not sent. */
 static final int BODY_MAX=64*1024;

 enum Kind { DECIDED, UNAVAILABLE, REFUSED, NOT_AUTHORIZED, UNCERTAIN, NOT_SENT }

 static final class Outcome {
  final Kind kind;final String result,reason;
  Outcome(Kind kind,String result,String reason){this.kind=kind;this.result=result;this.reason=reason;}
 }

 private final WorkspaceTaskClient.Gate gate;private final WorkspaceTaskClient.Transport transport;private final WorkspaceTaskClient.Signer signer;
 WorkspaceDecisionClient(WorkspaceTaskClient.Gate gate,WorkspaceTaskClient.Transport transport,WorkspaceTaskClient.Signer signer){this.gate=gate;this.transport=transport;this.signer=signer;}

 /** The POST body, or null if requestJson is not a request for requestId. */
 static byte[] body(String requestId,String requestJson){
  try{
   Object request=MiniJson.parse(requestJson);
   if(!(request instanceof Map)||requestId==null||!requestId.equals(((Map<?,?>)request).get("requestId")))return null;
   Map<String,Object> m=new LinkedHashMap<>();m.put("schemaVersion",1L);m.put("requestId",requestId);m.put("request",request);
   return WorkspaceLayout.json(m).getBytes(StandardCharsets.UTF_8);
  }catch(RuntimeException e){return null;}
 }

 /** One decision, off the main thread. */
 Outcome decide(String requestId,String requestJson){
  return decide(requestId,requestJson,LocalInference.Cancellation.NEVER,null);
 }

 Outcome decide(String requestId,String requestJson,LocalInference.Cancellation cancellation){return decide(requestId,requestJson,cancellation,null);}

 /**
  * Superseded queued work is never sent, and an in-flight reply is discarded after cancellation. With abort,
  * a superseded request in flight is also stopped: WorkspaceAbort.fire() closes its socket, so the phone's
  * worker moves on at once and the server cancels the composition. The answer is then uncertain and is
  * discarded; there is no retry.
  */
 Outcome decide(String requestId,String requestJson,LocalInference.Cancellation cancellation,WorkspaceAbort abort){
  if(abort!=null&&abort.fired())return new Outcome(Kind.NOT_SENT,null,"cancelled");
  if(cancellation.isCancelled())return new Outcome(Kind.NOT_SENT,null,"cancelled");
  if(!gate.enabled())return new Outcome(Kind.NOT_SENT,null,"disabled");
  byte[] body=body(requestId,requestJson);
  if(body==null)return new Outcome(Kind.NOT_SENT,null,"bad_request");
  if(body.length>BODY_MAX)return new Outcome(Kind.NOT_SENT,null,"too_large");
  WorkspaceTaskClient.Response r;
  try{
   Map<String,String> headers=new LinkedHashMap<>(signer.headers("POST",PATH));headers.put("Content-Type","application/json");
   if(cancellation.isCancelled()||(abort!=null&&abort.fired()))return new Outcome(Kind.NOT_SENT,null,"cancelled");
   r=abort==null?transport.post(PATH,headers,body):transport.post(PATH,headers,body,abort);
  }catch(Exception e){return new Outcome(Kind.UNCERTAIN,null,abort!=null&&abort.fired()?"cancelled":"no_reply");}
  if(cancellation.isCancelled())return new Outcome(Kind.UNCERTAIN,null,"cancelled");
  return read(r,requestId);
 }

 /** The server's reply, read strictly. Only a rose-layout-v1 result for this requestId is DECIDED. */
 static Outcome read(WorkspaceTaskClient.Response r,String requestId){
  if(r==null)return new Outcome(Kind.UNCERTAIN,null,"no_reply");
  switch(r.status){
   case 200:
    try{
     Object o=MiniJson.parse(r.body);
     if(o instanceof Map){Map<?,?> m=(Map<?,?>)o;Object status=m.get("status");
      if(WorkspaceLayout.CONTRACT.equals(m.get("contract"))&&requestId.equals(m.get("requestId"))&&("ok".equals(status)||"failed".equals(status)))
       return new Outcome(Kind.DECIDED,r.body,null);}
    }catch(RuntimeException ignored){}
    return new Outcome(Kind.UNCERTAIN,null,"unreadable_reply");
   case 503:return new Outcome(Kind.UNAVAILABLE,null,"unavailable");
   case 400:return new Outcome(Kind.REFUSED,null,"refused");
   case 401:return new Outcome(Kind.NOT_AUTHORIZED,null,"not_authorized");
   default:return new Outcome(Kind.UNCERTAIN,null,"unexpected_reply");
  }
 }

 /**
  * The result text for the composer. It is the server's result when decided. Otherwise it is a rose-layout-v1
  * failure (model_unavailable), so the page keeps its layout, and App Core hears unavailable.
  */
 static String result(String requestId,Outcome o){
  return o!=null&&o.kind==Kind.DECIDED?o.result:WorkspaceEngine.failed(requestId,"model_unavailable");
 }
}
