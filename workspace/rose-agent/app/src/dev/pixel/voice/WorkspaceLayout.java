package dev.pixel.voice;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Project 2: one layout of the workspace page left of Home, and the phone's own check of a composer result.
 *
 * The layout is Models' rose-layout-v1 (workspace/models/local-composer/contract/rose-layout-v1.md), with
 * OS Product's catalog cat-1 (workspace/rose-agent/docs/workspace-contract.md). The composer has already
 * validated its output against its aliases; the phone checks the result again against its own registry,
 * because nothing may reach Home unchecked. Failures use the contract's fixed tokens, never model text,
 * and any failure keeps the last layout: nothing from a rejected result is shown.
 *
 * Every id is one the phone resolved itself before the request. A result never carries a package, Intent,
 * URI, style or code, and it cannot decide whether an action runs at once or asks Rose first: the
 * registry's own action kind decides that. No Android types, so the rules run as plain JVM tests.
 */
final class WorkspaceLayout {
 static final String CONTRACT="rose-layout-v1",CATALOG="cat-1";
 static final int MAX_SECTIONS=6,MAX_ITEMS=8,MAX_TOTAL_ITEMS=20,LAYOUT_TITLE=40,SECTION_TITLE=30,MAX_REQUEST=500;
 /** rose-layout-v1's failure tokens: all a caller may show or log about a failure. */
 static final List<String> FAILURES=Collections.unmodifiableList(Arrays.asList("bad_request","catalog_mismatch",
   "stale_snapshot","empty_context","model_unavailable","timeout","cancelled","invalid_output","unknown_ref",
   "disallowed_ref","over_limit","duplicate_ref"));
 /** Snapshot kinds, as rose-layout-v1 names its lists. */
 static final String APPS="apps",NOTIFICATIONS="notifications",CONTENT="content",ACTIONS="actions";
 /** Action kinds. agent_task asks first (App Core's workspace task handoff v1); every other kind runs at once. */
 static final String AGENT_TASK="agent_task";

 /** One catalog component: the item kinds it may reference, its item bounds and how many actions it takes. */
 static final class Rule {
  final Set<String> items;final int min,max,actions;final boolean tasksAsItems;
  Rule(int min,int max,int actions,boolean tasksAsItems,String... items){this.items=new HashSet<>(Arrays.asList(items));this.min=min;this.max=max;this.actions=actions;this.tasksAsItems=tasksAsItems;}
 }
 /**
  * Catalog cat-1, OS Product's answer to rose-layout-v1's starting set, drawn from the approved HiFi r2
  * cards: grid (its "apps" card), carousel, list, card (a notification or content item with up to 2 of its
  * actions) and button (its "actions" card: a row of 1 to 4 actions). A Rose task appears only in a button
  * row or as a card action, never in a grid.
  */
 static final Map<String,Rule> CATALOG_RULES;
 static {
  Map<String,Rule> c=new LinkedHashMap<>();
  c.put("grid",new Rule(2,8,0,false,APPS,ACTIONS));
  c.put("carousel",new Rule(2,8,0,false,CONTENT,NOTIFICATIONS,APPS));
  c.put("list",new Rule(1,8,0,false,NOTIFICATIONS,CONTENT));
  c.put("card",new Rule(1,1,2,false,NOTIFICATIONS,CONTENT));
  c.put("button",new Rule(1,4,0,true,ACTIONS));
  CATALOG_RULES=Collections.unmodifiableMap(c);
 }

 /** What the phone resolved for one request: each id's kind, and each action's kind and target. */
 static final class Registry {
  final String snapshotVersion;
  private final Map<String,String> kinds=new HashMap<>(),actionKinds=new HashMap<>(),targets=new HashMap<>();
  Registry(String snapshotVersion){this.snapshotVersion=snapshotVersion;}
  Registry add(String kind,String id){kinds.put(id,kind);return this;}
  Registry action(String id,String actionKind,String targetId){kinds.put(id,ACTIONS);actionKinds.put(id,actionKind);targets.put(id,targetId);return this;}
  String kind(String id){return kinds.get(id);}
  String actionKind(String id){return actionKinds.get(id);}
  String target(String id){return targets.get(id);}
  boolean has(String id){return kinds.containsKey(id);}
 }

 /** One section. key identifies it on this phone (pins follow it); composers never choose keys. */
 static final class Section {
  final String key,component,title;final List<String> items,actions;
  Section(String key,String component,String title,List<String> items,List<String> actions){
   this.key=key;this.component=component;this.title=title;
   this.items=Collections.unmodifiableList(new ArrayList<>(items));this.actions=Collections.unmodifiableList(new ArrayList<>(actions));
  }
  /** The same content, whatever the key. */
  boolean sameContent(Section o){return component.equals(o.component)&&title.equals(o.title)&&items.equals(o.items)&&actions.equals(o.actions);}
 }

 final String snapshotVersion,title;final List<Section> sections;
 WorkspaceLayout(String snapshotVersion,String title,List<Section> sections){
  this.snapshotVersion=snapshotVersion;this.title=title;this.sections=Collections.unmodifiableList(new ArrayList<>(sections));
 }

 /** A failure, carrying one of FAILURES. */
 static final class Invalid extends Exception{
  final String failure;
  Invalid(String failure){super(failure);this.failure=FAILURES.contains(failure)?failure:"invalid_output";}
 }

 /** Keys for new sections come from the caller (WorkspaceState), so they never collide with kept ones. */
 interface Keys{String next();}

 /**
  * The phone's check of one composer result for requestId against the registry built for that request.
  * A failed result passes its fixed token through. Any malformed or out-of-catalog part rejects the whole
  * result, so nothing of it is shown.
  */
 @SuppressWarnings("unchecked")
 static WorkspaceLayout check(String result,String requestId,Registry registry,Keys keys)throws Invalid{
  Object root;
  try{root=MiniJson.parse(result==null?"":result);}catch(IllegalArgumentException e){throw new Invalid("invalid_output");}
  Map<String,Object> r=object(root);
  // The composer may add diagnostics (model, timing, context, runtimeStats); the renderer reads none of them.
  only(r,"contract","requestId","status","layout","failure","model","timing","context","runtimeStats");
  if(!CONTRACT.equals(r.get("contract"))||requestId==null||!requestId.equals(r.get("requestId")))throw new Invalid("bad_request");
  Object status=r.get("status");
  if("failed".equals(status)){
   Object f=r.get("failure");
   if(!(f instanceof String)||!FAILURES.contains(f)||r.containsKey("layout"))throw new Invalid("invalid_output");
   throw new Invalid((String)f);
  }
  if(!"ok".equals(status)||r.containsKey("failure"))throw new Invalid("invalid_output");
  Map<String,Object> l=object(r.get("layout"));
  only(l,"snapshotVersion","catalogVersion","title","sections");
  if(!CATALOG.equals(l.get("catalogVersion")))throw new Invalid("catalog_mismatch");
  if(registry.snapshotVersion==null||!registry.snapshotVersion.equals(l.get("snapshotVersion")))throw new Invalid("stale_snapshot");
  String title=title(l.get("title"),LAYOUT_TITLE);
  Object rawSections=l.get("sections");
  if(!(rawSections instanceof List)||((List<Object>)rawSections).isEmpty())throw new Invalid("invalid_output");
  List<Object> raw=(List<Object>)rawSections;
  if(raw.size()>MAX_SECTIONS)throw new Invalid("over_limit");
  List<Section> sections=new ArrayList<>();Set<String> seen=new HashSet<>();int total=0;
  for(Object o:raw){
   Map<String,Object> s=object(o);
   only(s,"component","title","items","actions");
   Object component=s.get("component");Rule rule=component instanceof String?CATALOG_RULES.get(component):null;
   if(rule==null)throw new Invalid("invalid_output");
   String sectionTitle=title(s.get("title"),SECTION_TITLE);
   List<String> items=ids(s.get("items")),actions=s.containsKey("actions")?ids(s.get("actions")):new ArrayList<String>();
   if(items.size()<rule.min)throw new Invalid("invalid_output");
   if(!actions.isEmpty()&&rule.actions==0)throw new Invalid("disallowed_ref");
   if(items.size()>Math.min(rule.max,MAX_ITEMS)||actions.size()>rule.actions)throw new Invalid("over_limit");
   for(String id:concat(items,actions)){
    if(!registry.has(id))throw new Invalid("unknown_ref");
    if(!seen.add(id))throw new Invalid("duplicate_ref");
   }
   for(String id:items){
    if(!rule.items.contains(registry.kind(id)))throw new Invalid("disallowed_ref");
    if(AGENT_TASK.equals(registry.actionKind(id))&&!rule.tasksAsItems)throw new Invalid("disallowed_ref");
   }
   // A card's actions are actions that target one of the section's own items.
   for(String id:actions)if(!ACTIONS.equals(registry.kind(id))||!items.contains(registry.target(id)))throw new Invalid("disallowed_ref");
   total+=items.size();
   if(total>MAX_TOTAL_ITEMS)throw new Invalid("over_limit");
   sections.add(new Section(keys.next(),(String)component,sectionTitle,items,actions));
  }
  return new WorkspaceLayout((String)l.get("snapshotVersion"),title,sections);
 }

 /**
  * The layout as it can be shown now. An item whose app was removed or whose notification went away since
  * the layout was made is left out, and so is a section left below its component's minimum; the saved
  * layout itself is kept unchanged, so nothing reshuffles. null if nothing is left to show.
  */
 WorkspaceLayout available(Set<String> present){
  List<Section> out=new ArrayList<>();
  for(Section s:sections){
   List<String> items=new ArrayList<>();for(String id:s.items)if(present.contains(id))items.add(id);
   List<String> actions=new ArrayList<>();for(String id:s.actions)if(present.contains(id))actions.add(id);
   Rule rule=CATALOG_RULES.get(s.component);
   if(rule!=null&&items.size()>=rule.min)out.add(new Section(s.key,s.component,s.title,items,actions));
  }
  return out.isEmpty()?null:new WorkspaceLayout(snapshotVersion,title,out);
 }

 private static List<String> concat(List<String> a,List<String> b){List<String> c=new ArrayList<>(a);c.addAll(b);return c;}
 @SuppressWarnings("unchecked")
 private static Map<String,Object> object(Object v)throws Invalid{if(!(v instanceof Map))throw new Invalid("invalid_output");return (Map<String,Object>)v;}
 @SuppressWarnings("unchecked")
 private static List<String> ids(Object v)throws Invalid{
  if(!(v instanceof List))throw new Invalid("invalid_output");List<String> out=new ArrayList<>();
  for(Object o:(List<Object>)v){if(!(o instanceof String)||((String)o).isEmpty())throw new Invalid("invalid_output");out.add((String)o);}
  return out;
 }
 private static void only(Map<String,Object> o,String... allowed)throws Invalid{
  Set<String> ok=new HashSet<>(Arrays.asList(allowed));
  for(String k:o.keySet())if(!ok.contains(k))throw new Invalid("invalid_output");
 }
 /** Plain, printable title: control characters are invalid output, length beyond the bound is over_limit. */
 static String title(Object v,int max)throws Invalid{
  if(!(v instanceof String))throw new Invalid("invalid_output");String raw=(String)v;
  // Checked before trimming: trim() would silently strip control characters at either end.
  for(int i=0;i<raw.length();i++)if(raw.charAt(i)<0x20||raw.charAt(i)==0x7f)throw new Invalid("invalid_output");
  if(raw.trim().isEmpty())throw new Invalid("invalid_output");
  if(raw.codePointCount(0,raw.length())>max)throw new Invalid("over_limit");
  return raw;
 }

 // ---------- storage form (the phone's own) ----------

 Map<String,Object> toMap(){
  Map<String,Object> m=new LinkedHashMap<>();m.put("snapshotVersion",snapshotVersion);m.put("title",title);List<Object> ss=new ArrayList<>();
  for(Section s:sections){Map<String,Object> sm=new LinkedHashMap<>();sm.put("key",s.key);sm.put("component",s.component);sm.put("title",s.title);
   sm.put("items",new ArrayList<Object>(s.items));sm.put("actions",new ArrayList<Object>(s.actions));ss.add(sm);}
  m.put("sections",ss);return m;
 }

 /** A stored layout, read back strictly; null if it is not one (the store then treats it as absent). */
 @SuppressWarnings("unchecked")
 static WorkspaceLayout fromMap(Object raw){
  try{
   Map<String,Object> m=object(raw);only(m,"snapshotVersion","title","sections");
   Object version=m.get("snapshotVersion");if(!(version instanceof String))return null;
   String title=title(m.get("title"),LAYOUT_TITLE);
   Object ss=m.get("sections");if(!(ss instanceof List)||((List<Object>)ss).isEmpty()||((List<Object>)ss).size()>MAX_SECTIONS)return null;
   List<Section> out=new ArrayList<>();Set<String> keys=new HashSet<>();
   for(Object o:(List<Object>)ss){
    Map<String,Object> s=object(o);only(s,"key","component","title","items","actions");
    Object key=s.get("key"),component=s.get("component");
    if(!(key instanceof String)||!keys.add((String)key)||!CATALOG_RULES.containsKey(component))return null;
    List<String> items=ids(s.get("items")),actions=ids(s.get("actions"));
    if(items.isEmpty()||items.size()>MAX_ITEMS||actions.size()>2)return null;
    out.add(new Section((String)key,(String)component,title(s.get("title"),SECTION_TITLE),items,actions));
   }
   return new WorkspaceLayout((String)version,title,out);
  }catch(Invalid e){return null;}
 }

 /** JSON text for the store (the only JSON this class writes). */
 static String json(Object v){StringBuilder b=new StringBuilder();write(b,v);return b.toString();}
 @SuppressWarnings("unchecked")
 private static void write(StringBuilder b,Object v){
  if(v==null||v==MiniJson.NULL){b.append("null");return;}
  if(v instanceof String){quote(b,(String)v);return;}
  if(v instanceof Boolean||v instanceof Long||v instanceof Integer){b.append(v);return;}
  if(v instanceof Map){b.append('{');boolean first=true;for(Map.Entry<String,Object> e:((Map<String,Object>)v).entrySet()){if(!first)b.append(',');first=false;quote(b,e.getKey());b.append(':');write(b,e.getValue());}b.append('}');return;}
  if(v instanceof List){b.append('[');boolean first=true;for(Object o:(List<Object>)v){if(!first)b.append(',');first=false;write(b,o);}b.append(']');return;}
  throw new IllegalArgumentException("unsupported "+v.getClass());
 }
 private static void quote(StringBuilder b,String s){
  b.append('"');
  for(int i=0;i<s.length();i++){char c=s.charAt(i);
   if(c=='"'||c=='\\')b.append('\\').append(c);
   else if(c<0x20)b.append(String.format("\\u%04x",(int)c));
   else b.append(c);}
  b.append('"');
 }
}
