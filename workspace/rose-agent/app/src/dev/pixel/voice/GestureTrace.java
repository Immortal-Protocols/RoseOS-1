package dev.pixel.voice;

import java.util.*;
import java.util.function.*;

/** Passive, bounded gesture evidence. A completed gesture is NOT app success. */
final class GestureTrace {
 private final LongSupplier clock;
 private final Consumer<List<Map<String,Object>>> save;
 private final LinkedHashMap<String,Map<String,Object>> records=new LinkedHashMap<>();
 GestureTrace(LongSupplier clock,Consumer<List<Map<String,Object>>> save){this.clock=clock;this.save=save;}
 synchronized String begin(Map<String,Object> metadata){
  String id=UUID.randomUUID().toString();
  Map<String,Object> r=new LinkedHashMap<>(metadata);
  r.put("id",id);r.put("startedElapsedMs",clock.getAsLong());r.put("phase","dispatching");r.put("revision",0);
  records.put(id,r);while(records.size()>24)records.remove(records.keySet().iterator().next());
  persist();return id;
 }
 synchronized void event(String id,String phase,Map<String,Object> details){
  Map<String,Object> r=records.get(id);if(r==null)return;
  // A late queue acknowledgement must not overwrite a terminal callback.
  if("queued".equals(phase)&&!"dispatching".equals(r.get("phase")))return;
  r.putAll(details);r.put("phase",phase);r.put("elapsedMs",clock.getAsLong()-((Number)r.get("startedElapsedMs")).longValue());
  r.put("revision",((Number)r.get("revision")).intValue()+1);persist();
 }
 synchronized void observe(Map<String,Object> screen){
  long now=clock.getAsLong();boolean changed=false;
  for(Map<String,Object> r:records.values()){
   long after=now-((Number)r.get("startedElapsedMs")).longValue();
   if(after<0||after>5000)continue;
   @SuppressWarnings("unchecked") List<Map<String,Object>> observations=(List<Map<String,Object>>)r.get("observations");
   if(observations==null){observations=new ArrayList<>();r.put("observations",observations);}
   if(observations.size()>=8)continue;
   Map<String,Object> observation=new LinkedHashMap<>(screen);observation.put("afterMs",after);observations.add(observation);
   r.put("revision",((Number)r.get("revision")).intValue()+1);changed=true;
  }
  if(changed)persist();
 }
 synchronized List<Map<String,Object>> snapshot(){
  List<Map<String,Object>> out=new ArrayList<>();
  for(Map<String,Object> r:records.values()){
   Map<String,Object> copy=new LinkedHashMap<>(r);
   @SuppressWarnings("unchecked") List<Map<String,Object>> observations=(List<Map<String,Object>>)r.get("observations");
   if(observations!=null){List<Map<String,Object>> items=new ArrayList<>();for(Map<String,Object> item:observations)items.add(new LinkedHashMap<>(item));copy.put("observations",items);}
   out.add(copy);
  }
  return out;
 }
 private void persist(){try{save.accept(snapshot());}catch(Exception ignored){/* Diagnostics never fail a gesture. */}}
}
