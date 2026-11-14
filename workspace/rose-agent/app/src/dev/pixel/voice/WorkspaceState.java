package dev.pixel.voice;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Project 2: everything the workspace page keeps between visits and restarts, as the approved HiFi (r2)
 * keeps it: the layout, the request it answered, pins, the last 20 undo steps, the last 3 layouts as
 * recents, and what the last tap on each direct action did. Pure (no Android types): the page saves
 * toJson() after every change and restores with fromJson().
 *
 * Pins keep their position: when a new layout arrives, each pinned section stays at its index and only the
 * unpinned sections around it change. Nothing here rearranges by itself; only commit, undo, pin and reopen
 * change the layout, and each is the person's request.
 */
final class WorkspaceState {
 static final int UNDO=20,RECENT=3,ACTION_RESULTS=20;

 /** One saved arrangement: the layout, what was asked, and which of its sections were pinned. */
 static final class Entry {
  final WorkspaceLayout layout;final String request;final Set<String> pins;
  Entry(WorkspaceLayout layout,String request,Set<String> pins){this.layout=layout;this.request=request;this.pins=new LinkedHashSet<>(pins);}
 }

 private WorkspaceLayout layout;private String request;
 private final Set<String> pins=new LinkedHashSet<>();
 private final List<Entry> undo=new ArrayList<>(),recent=new ArrayList<>();
 private final Map<String,String> actionResults=new LinkedHashMap<>();
 private long nextKey=1;
 /** The store's revision (App Core section 4.2): it grows with every change to the layout or its pins, and never goes back, even on undo. */
 private long revision;
 long revision(){return revision;}

 WorkspaceLayout layout(){return layout;}
 String request(){return request;}
 boolean pinned(String key){return pins.contains(key);}
 Set<String> pins(){return new LinkedHashSet<>(pins);}
 boolean canUndo(){return !undo.isEmpty();}
 int undoDepth(){return undo.size();}
 List<Entry> recent(){return new ArrayList<>(recent);}
 String actionResult(String actionId){return actionResults.get(actionId);}

 /** Section keys are the phone's own and never repeat, so a pin can never attach to a different section. */
 WorkspaceLayout.Keys keys(){return () -> "k"+(nextKey++);}

 /**
  * A new layout from the composer replaces the current one, except that every pinned section keeps its
  * exact index (the HiFi's splice at min(index, length)). A new section with the same content as a pinned
  * one is dropped, so a pin never appears twice. The previous arrangement becomes one undo step, and the
  * result becomes the newest recent layout.
  */
 void commit(WorkspaceLayout incoming,String newRequest){
  List<WorkspaceLayout.Section> out=new ArrayList<>();
  List<int[]> keptAt=new ArrayList<>();List<WorkspaceLayout.Section> kept=new ArrayList<>();
  if(layout!=null)for(int i=0;i<layout.sections.size();i++){WorkspaceLayout.Section c=layout.sections.get(i);if(pins.contains(c.key)){keptAt.add(new int[]{i});kept.add(c);}}
  for(WorkspaceLayout.Section c:incoming.sections){
   boolean dup=false;for(WorkspaceLayout.Section p:kept)if(p.sameContent(c)){dup=true;break;}
   if(!dup)out.add(c);
  }
  for(int j=0;j<kept.size();j++)out.add(Math.min(keptAt.get(j)[0],out.size()),kept.get(j));
  while(out.size()>WorkspaceLayout.MAX_SECTIONS){
   // Over the bound only because pins were kept: drop unpinned sections from the end.
   for(int i=out.size()-1;i>=0;i--)if(!pins.contains(out.get(i).key)){out.remove(i);break;}
   if(out.size()>WorkspaceLayout.MAX_SECTIONS&&allPinned(out))break;
  }
  pushUndo();
  layout=new WorkspaceLayout(incoming.snapshotVersion,incoming.title,out);request=newRequest;revision++;
  remember();
 }

 private boolean allPinned(List<WorkspaceLayout.Section> sections){for(WorkspaceLayout.Section c:sections)if(!pins.contains(c.key))return false;return true;}

 /** Pin or unpin a section of the current layout; also an undo step, as in the HiFi. */
 boolean togglePin(String key){
  if(layout==null)return false;boolean present=false;
  for(WorkspaceLayout.Section c:layout.sections)if(c.key.equals(key)){present=true;break;}
  if(!present)return false;
  pushUndo();
  if(!pins.remove(key))pins.add(key);
  revision++;
  return true;
 }

 /** Back one step: the layout, its request and its pins exactly as they were. */
 boolean undo(){
  if(undo.isEmpty())return false;
  Entry e=undo.remove(undo.size()-1);
  layout=e.layout;request=e.request;pins.clear();pins.addAll(e.pins);
  revision++;
  return true;
 }

 /** Reopen a recent layout (also an undo step). Pins carry over only to sections that are in it. */
 boolean reopen(int index){
  if(index<0||index>=recent.size())return false;
  Entry e=recent.get(index);if(e.layout==layout)return false;
  pushUndo();
  layout=e.layout;request=e.request;revision++;
  Set<String> keysNow=new HashSet<>();for(WorkspaceLayout.Section c:layout.sections)keysNow.add(c.key);
  pins.retainAll(keysNow);
  remember();
  return true;
 }

 /** The platform's answer to the last tap on a direct action (display only: the app holds the truth). */
 void actionResult(String actionId,String status){
  actionResults.remove(actionId);actionResults.put(actionId,status);
  while(actionResults.size()>ACTION_RESULTS)actionResults.remove(actionResults.keySet().iterator().next());
 }

 private void pushUndo(){
  if(layout==null)return;
  undo.add(new Entry(layout,request,pins));
  while(undo.size()>UNDO)undo.remove(0);
 }
 private void remember(){
  for(int i=recent.size()-1;i>=0;i--)if(recent.get(i).layout==layout||(recent.get(i).request!=null&&recent.get(i).request.equals(request)))recent.remove(i);
  recent.add(0,new Entry(layout,request,new HashSet<String>()));
  while(recent.size()>RECENT)recent.remove(recent.size()-1);
 }

 // ---------- storage ----------

 String toJson(){
  Map<String,Object> m=new LinkedHashMap<>();
  m.put("v",1L);m.put("next",nextKey);m.put("rev",revision);
  m.put("layout",layout==null?null:layout.toMap());m.put("request",request);m.put("pins",new ArrayList<Object>(pins));
  m.put("undo",entries(undo));m.put("recent",entries(recent));
  m.put("actions",new LinkedHashMap<String,Object>(actionResults));
  return WorkspaceLayout.json(m);
 }
 private static List<Object> entries(List<Entry> es){
  List<Object> out=new ArrayList<>();
  for(Entry e:es){Map<String,Object> m=new LinkedHashMap<>();m.put("layout",e.layout.toMap());m.put("request",e.request);m.put("pins",new ArrayList<Object>(e.pins));out.add(m);}
  return out;
 }

 /**
  * The saved state, read back strictly. Anything that is not a well-formed saved state gives an empty
  * state (a corrupt store can never break the page); a damaged undo or recent entry is skipped alone.
  */
 @SuppressWarnings("unchecked")
 static WorkspaceState fromJson(String saved){
  WorkspaceState s=new WorkspaceState();
  if(saved==null||saved.isEmpty())return s;
  try{
   Object root=MiniJson.parse(saved);if(!(root instanceof Map))return s;
   Map<String,Object> m=(Map<String,Object>)root;
   if(!Long.valueOf(1).equals(m.get("v")))return s;
   Object next=m.get("next");
   Object rev=m.get("rev");if(rev instanceof Long&&(Long)rev>=0)s.revision=(Long)rev;
   Object rawLayout=m.get("layout");
   if(rawLayout!=null&&rawLayout!=MiniJson.NULL){
    WorkspaceLayout l=WorkspaceLayout.fromMap(rawLayout);if(l==null)return new WorkspaceState();
    s.layout=l;s.request=string(m.get("request"));
    s.pins.addAll(strings(m.get("pins"),l));
   }
   for(Object o:list(m.get("undo"))){Entry e=entry(o);if(e!=null)s.undo.add(e);}
   while(s.undo.size()>UNDO)s.undo.remove(0);
   for(Object o:list(m.get("recent"))){Entry e=entry(o);if(e!=null)s.recent.add(e);}
   while(s.recent.size()>RECENT)s.recent.remove(s.recent.size()-1);
   Object actions=m.get("actions");
   if(actions instanceof Map)for(Map.Entry<String,Object> e:((Map<String,Object>)actions).entrySet())if(e.getValue() instanceof String)s.actionResult(e.getKey(),(String)e.getValue());
   // Keys keep counting above every key in use, even if "next" was damaged.
   long max=next instanceof Long?(Long)next:1;
   for(WorkspaceLayout l:s.allLayouts())for(WorkspaceLayout.Section c:l.sections){long k=keyNumber(c.key);if(k>=max)max=k+1;}
   s.nextKey=Math.max(1,max);
   return s;
  }catch(RuntimeException e){return new WorkspaceState();}
 }
 private List<WorkspaceLayout> allLayouts(){List<WorkspaceLayout> ls=new ArrayList<>();if(layout!=null)ls.add(layout);for(Entry e:undo)ls.add(e.layout);for(Entry e:recent)ls.add(e.layout);return ls;}
 private static long keyNumber(String key){try{return key.startsWith("k")?Long.parseLong(key.substring(1)):0;}catch(NumberFormatException e){return 0;}}
 @SuppressWarnings("unchecked")
 private static List<Object> list(Object v){return v instanceof List?(List<Object>)v:new ArrayList<Object>();}
 private static String string(Object v){return v instanceof String?(String)v:null;}
 /** Pins, kept only for sections that are in the layout. */
 private static Set<String> strings(Object v,WorkspaceLayout l){
  Set<String> keys=new HashSet<>();for(WorkspaceLayout.Section c:l.sections)keys.add(c.key);
  Set<String> out=new LinkedHashSet<>();for(Object o:list(v))if(o instanceof String&&keys.contains(o))out.add((String)o);return out;
 }
 @SuppressWarnings("unchecked")
 private static Entry entry(Object o){
  if(!(o instanceof Map))return null;Map<String,Object> m=(Map<String,Object>)o;
  WorkspaceLayout l=WorkspaceLayout.fromMap(m.get("layout"));if(l==null)return null;
  return new Entry(l,string(m.get("request")),strings(m.get("pins"),l));
 }
}
