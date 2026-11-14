package dev.pixel.voice;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Project 2: the workspace's side of App Core's composer intent/result v1 (docs/APP-BUILDER.md, section 4, on
 * #782). Rose Agent is the composer provider. SettingsAgentProvider forwards the {@code workspace_compose}
 * operation here after its signature gate. Its receipts, keyed by the command id, answer a repeat without
 * composing again.
 *
 * Pure (no Android types): this file parses a request strictly (section 4.1), decides which kinds the phone can
 * honestly offer, and writes the section 4.2 result.
 */
final class WorkspaceAgent {
 private WorkspaceAgent() {}

 static final String OPERATION="workspace_compose";
 static final String PROMPT="prompt",ARRANGE="arrange",REGENERATE="regenerate",UNDO="undo";
 static final List<String> KINDS=Collections.unmodifiableList(Arrays.asList(PROMPT,ARRANGE,REGENERATE,UNDO));
 static final int MAX_REQUEST_ID=64;
 private static final Set<String> KEYS=new HashSet<>(Arrays.asList("schemaVersion","requestId","kind","text"));

 /** A refused request: reason is a short fixed token, never caller text. */
 static final class Refused extends Exception {
  final String reason;
  Refused(String reason){super(reason,null,false,false);this.reason=reason;}
 }

 /** One section 4.1 request. text is null except for prompt. */
 static final class Request {
  final String requestId,kind,text;
  Request(String requestId,String kind,String text){this.requestId=requestId;this.kind=kind;this.text=text;}
 }

 /**
  * The request in params, read strictly: only the four keys, schemaVersion 1, a requestId of 1 to 64
  * characters, a known kind, and text exactly when the kind is prompt (non-blank, at most 500). requestId is
  * returned in the refusal when it is readable, so the result can still echo it.
  */
 static Request parse(Map<String,Object> params)throws Refused{
  if(params==null)throw new Refused("bad_request");
  for(String k:params.keySet())if(!KEYS.contains(k))throw new Refused("unknown_field");
  if(!Long.valueOf(1).equals(params.get("schemaVersion")))throw new Refused("schema_version");
  Object id=params.get("requestId");
  if(!(id instanceof String)||((String)id).isEmpty()||((String)id).length()>MAX_REQUEST_ID)throw new Refused("request_id");
  Object kind=params.get("kind");
  if(!(kind instanceof String)||!KINDS.contains(kind))throw new Refused("kind");
  Object text=params.get("text");
  if(PROMPT.equals(kind)){
   if(!(text instanceof String))throw new Refused("text");
   String t=((String)text).trim();
   if(t.isEmpty()||t.codePointCount(0,t.length())>WorkspaceLayout.MAX_REQUEST)throw new Refused("text");
   return new Request((String)id,PROMPT,t);
  }
  if(text!=null&&text!=MiniJson.NULL)throw new Refused("text");
  return new Request((String)id,(String)kind,null);
 }

 /** The requestId in params if it is a usable one, for echoing in a refusal; otherwise null. */
 static String requestId(Map<String,Object> params){
  Object id=params==null?null:params.get("requestId");
  return id instanceof String&&!((String)id).isEmpty()&&((String)id).length()<=MAX_REQUEST_ID?(String)id:null;
 }

 /**
  * The kinds the phone can honestly honour. The page and a composer are always on the phone. prompt is offered
  * only with a real model: the starter ignores words, so Rose would otherwise claim a request it cannot follow.
  */
 static List<String> kinds(boolean model){
  List<String> k=new ArrayList<>(KINDS);if(!model)k.remove(PROMPT);return k;
 }

 /** True when the kind is one the phone offers right now (section 4.1: a kind it does not list is refused). */
 static boolean offered(String kind,boolean model){return kinds(model).contains(kind);}

 /**
  * The section 4.2 result. revision is required for shown and prepared; without one such a status is reported
  * as uncertain. reason is a fixed token or null.
  */
 static Map<String,Object> result(String requestId,String status,Long revision,String reason){
  if(("shown".equals(status)||"prepared".equals(status))&&(revision==null||revision<0)){status="uncertain";revision=null;}
  Map<String,Object> m=new LinkedHashMap<>();
  m.put("schemaVersion",1L);m.put("requestId",requestId);m.put("status",status);
  if(revision!=null)m.put("revision",revision);
  if(reason!=null)m.put("reason",reason);
  return m;
 }

 /**
  * A composition's result, mapped by WorkspaceComposer.routeStatus: model_unavailable reads as unavailable,
  * any other rose-layout-v1 failure as rejected with the token as reason, and a superseded request as
  * uncertain. onScreen says whether the page drew it.
  */
 static Map<String,Object> composed(String requestId,WorkspaceComposer.Outcome outcome,String failure,boolean onScreen,long revision){
  String status=WorkspaceComposer.routeStatus(outcome,failure,onScreen);
  boolean done="shown".equals(status)||"prepared".equals(status);
  String reason="rejected".equals(status)?(failure==null?"invalid_output":failure):"uncertain".equals(status)?"superseded":null;
  return result(requestId,status,done?revision:null,reason);
 }

 /** undo is the page's own: no composer call. Nothing to undo is a refusal, and nothing changes. */
 static Map<String,Object> undone(String requestId,boolean changed,boolean onScreen,long revision){
  return changed?result(requestId,onScreen?"shown":"prepared",revision,null):result(requestId,"rejected",null,"nothing_to_undo");
 }
}
