package dev.pixel.voice;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Project 2: one layout of the workspace page left of Home, and the strict check of a composer's reply
 * (contract workspace-v1, workspace/rose-agent/docs/workspace-contract.md).
 *
 * The composer only ever names short ids that the phone resolved itself before the request (apps,
 * actions, notifications). It never supplies a package, Intent, URI or code, and it cannot say whether an
 * action runs at once or asks Rose first: the phone's own registry decides that. A reply is one JSON
 * object; anything else, or anything out of bounds, is rejected whole and nothing is shown from it.
 * No Android types, so the rules run as plain JVM tests.
 */
final class WorkspaceLayout {
 static final String CONTRACT="workspace-v1";
 static final int MAX_CARDS=8,MAX_TITLE=40,MAX_LAYOUT_TITLE=32,MAX_TEXT=60,MAX_REQUEST=500;
 static final Pattern ID=Pattern.compile("[a-z][a-z0-9.]{0,15}");
 /** Card types, as the approved HiFi names them: apps, actions, carousel, list (optionally a checklist), notification. */
 static final String APPS="apps",ACTIONS="actions",CAROUSEL="carousel",LIST="list",NOTIFICATION="notification";

 /** The ids the phone resolved for one request: what a reply may refer to. */
 static final class Ids {
  final Set<String> apps,actions,notifications;
  Ids(Set<String> apps,Set<String> actions,Set<String> notifications){
   this.apps=Collections.unmodifiableSet(new HashSet<>(apps));
   this.actions=Collections.unmodifiableSet(new HashSet<>(actions));
   this.notifications=Collections.unmodifiableSet(new HashSet<>(notifications));
  }
  boolean known(String id){return apps.contains(id)||actions.contains(id)||notifications.contains(id);}
 }

 /** One card. key identifies it on this phone (pins follow it); the composer never chooses keys. */
 static final class Card {
  final String key,type,title;final List<String> items;final boolean check;
  Card(String key,String type,String title,List<String> items,boolean check){
   this.key=key;this.type=type;this.title=title;this.items=Collections.unmodifiableList(new ArrayList<>(items));this.check=check;
  }
  Card withKey(String newKey){return new Card(newKey,type,title,items,check);}
  /** The same content, whatever the key. */
  boolean sameContent(Card o){return type.equals(o.type)&&title.equals(o.title)&&items.equals(o.items)&&check==o.check;}
 }

 final String title;final List<Card> cards;
 WorkspaceLayout(String title,List<Card> cards){this.title=title;this.cards=Collections.unmodifiableList(new ArrayList<>(cards));}

 static final class Invalid extends Exception{final String reason;Invalid(String reason){super(reason);this.reason=reason;}}

 /** Keys for new cards come from the caller (WorkspaceState), so they never collide with kept cards. */
 interface Keys{String next();}

 /**
  * Checks one composer reply against the ids resolved for its request. The whole reply is rejected on any
  * malformed or out-of-bounds part. An id the phone no longer has is a stale reference, not malformed:
  * that item is dropped, a card left empty is dropped, and only a layout with no card left is rejected.
  */
 static WorkspaceLayout parse(String reply,Ids ids,Keys keys)throws Invalid{
  Object root;
  try{root=MiniJson.parse(reply==null?"":reply.trim());}catch(IllegalArgumentException e){throw new Invalid("malformed");}
  Map<String,Object> o=object(root,"reply");
  only(o,"rose_layout","title","cards");
  if(!CONTRACT.equals(o.get("rose_layout")))throw new Invalid("contract");
  String title=text(o.get("title"),MAX_LAYOUT_TITLE,"title");
  List<Object> raw=list(o.get("cards"),1,MAX_CARDS,"cards");
  List<Card> cards=new ArrayList<>();Set<String> used=new HashSet<>();
  for(int i=0;i<raw.size();i++){Card c=card(raw.get(i),ids,used,keys,"cards["+i+"]");if(c!=null)cards.add(c);}
  if(cards.isEmpty())throw new Invalid("nothing_available");
  return new WorkspaceLayout(title,cards);
 }

 private static Card card(Object raw,Ids ids,Set<String> used,Keys keys,String at)throws Invalid{
  Map<String,Object> c=object(raw,at);
  Object type=c.get("type");
  if(APPS.equals(type)){only(c,"type","title","items");return refs(c,APPS,ids.apps,1,8,ids,used,keys,at);}
  if(ACTIONS.equals(type)){only(c,"type","title","items");return refs(c,ACTIONS,ids.actions,1,4,ids,used,keys,at);}
  if(CAROUSEL.equals(type)){only(c,"type","title","items");return refs(c,CAROUSEL,union(ids.apps,ids.actions),2,10,ids,used,keys,at);}
  if(NOTIFICATION.equals(type)){only(c,"type","title","items");return refs(c,NOTIFICATION,ids.notifications,1,1,ids,used,keys,at);}
  if(LIST.equals(type)){
   only(c,"type","title","items","check");
   String title=text(c.get("title"),MAX_TITLE,at+".title");
   List<Object> items=list(c.get("items"),1,8,at+".items");List<String> out=new ArrayList<>();
   for(int i=0;i<items.size();i++)out.add(text(items.get(i),MAX_TEXT,at+".items["+i+"]"));
   Object check=c.get("check");
   if(check!=null&&!(check instanceof Boolean))throw new Invalid(at+".check");
   return new Card(keys.next(),LIST,title,out,Boolean.TRUE.equals(check));
  }
  throw new Invalid(at+".type");
 }

 /** A card of references: every item must be an id; ids of the wrong kind are invalid, unknown ones are stale. */
 private static Card refs(Map<String,Object> c,String type,Set<String> allowed,int min,int max,Ids ids,Set<String> used,Keys keys,String at)throws Invalid{
  String title=text(c.get("title"),MAX_TITLE,at+".title");
  List<Object> items=list(c.get("items"),min,max,at+".items");List<String> kept=new ArrayList<>();
  for(int i=0;i<items.size();i++){
   Object v=items.get(i);
   if(!(v instanceof String)||!ID.matcher((String)v).matches())throw new Invalid(at+".items["+i+"]");
   String id=(String)v;
   if(!allowed.contains(id)){if(ids.known(id))throw new Invalid(at+".items["+i+"].kind");continue;}  // stale: drop
   if(!used.add(id))throw new Invalid(at+".items["+i+"].duplicate");
   kept.add(id);
  }
  if(kept.size()<Math.min(min,1)||kept.isEmpty())return null;
  if(CAROUSEL.equals(type)&&kept.size()<2)return null;
  return new Card(keys.next(),type,title,kept,false);
 }

 private static Set<String> union(Set<String> a,Set<String> b){Set<String> u=new HashSet<>(a);u.addAll(b);return u;}

 @SuppressWarnings("unchecked")
 private static Map<String,Object> object(Object v,String at)throws Invalid{if(!(v instanceof Map))throw new Invalid(at);return (Map<String,Object>)v;}
 @SuppressWarnings("unchecked")
 private static List<Object> list(Object v,int min,int max,String at)throws Invalid{
  if(!(v instanceof List))throw new Invalid(at);List<Object> l=(List<Object>)v;
  if(l.size()<min||l.size()>max)throw new Invalid(at+".count");return l;
 }
 private static void only(Map<String,Object> o,String... allowed)throws Invalid{
  Set<String> ok=new HashSet<>();Collections.addAll(ok,allowed);
  for(String k:o.keySet())if(!ok.contains(k))throw new Invalid("unknown_field."+k);
 }
 /** Plain, printable text: non-empty, bounded, no control characters (no markup is ever interpreted). */
 static String text(Object v,int max,String at)throws Invalid{
  if(!(v instanceof String))throw new Invalid(at);String raw=(String)v;
  // Checked before trimming: trim() would silently strip control characters at either end.
  for(int i=0;i<raw.length();i++){char ch=raw.charAt(i);if(Character.isISOControl(ch))throw new Invalid(at+".control");}
  String s=raw.trim();
  if(s.isEmpty()||s.codePointCount(0,s.length())>max)throw new Invalid(at+".length");
  return s;
 }

 // ---------- storage form (the phone's own, not the composer's) ----------

 Map<String,Object> toMap(){
  Map<String,Object> m=new LinkedHashMap<>();m.put("title",title);List<Object> cs=new ArrayList<>();
  for(Card c:cards){Map<String,Object> cm=new LinkedHashMap<>();cm.put("key",c.key);cm.put("type",c.type);cm.put("title",c.title);
   cm.put("items",new ArrayList<Object>(c.items));if(c.check)cm.put("check",Boolean.TRUE);cs.add(cm);}
  m.put("cards",cs);return m;
 }

 /** A stored layout, read back strictly; null if it is not one (the store then treats it as absent). */
 @SuppressWarnings("unchecked")
 static WorkspaceLayout fromMap(Object raw){
  try{
   Map<String,Object> m=object(raw,"layout");only(m,"title","cards");String title=text(m.get("title"),MAX_LAYOUT_TITLE,"title");
   List<Object> cs=list(m.get("cards"),1,MAX_CARDS,"cards");List<Card> out=new ArrayList<>();Set<String> keys=new HashSet<>();
   for(Object o:cs){Map<String,Object> c=object(o,"card");only(c,"key","type","title","items","check");Object key=c.get("key"),type=c.get("type");
    if(!(key instanceof String)||!keys.add((String)key))return null;
    if(!(APPS.equals(type)||ACTIONS.equals(type)||CAROUSEL.equals(type)||LIST.equals(type)||NOTIFICATION.equals(type)))return null;
    List<Object> items=list(c.get("items"),1,10,"items");List<String> strs=new ArrayList<>();
    for(Object it:items){if(!(it instanceof String))return null;strs.add((String)it);}
    out.add(new Card((String)key,(String)type,text(c.get("title"),MAX_TITLE,"title"),strs,Boolean.TRUE.equals(c.get("check"))));}
   return new WorkspaceLayout(title,out);
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
