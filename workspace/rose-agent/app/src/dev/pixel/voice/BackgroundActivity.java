package dev.pixel.voice;

import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

/** Actual task lifecycle, not voice or the presence of an open workspace.
 * Pure monotonic-time model; drawing never extends a producer receipt's life. */
final class BackgroundActivity {
 static final long MAX_AGE_MS=3000, SETTLE_MS=240, PERIOD_MS=2400;
 enum Phase { STILL, WORKING, SETTLING }
 private Set<String> active=Collections.emptySet();
 private long began=-1,settleAt=-1;
 private float settleFrom;
 Phase phase=Phase.STILL;

 float frame(Set<String> running,Set<String> finished,boolean reliable,long now,boolean motion){
  Set<String> next=reliable?new HashSet<>(running):Collections.<String>emptySet();
  if(!next.isEmpty()){
   if(active.isEmpty())began=now;
   phase=Phase.WORKING;settleAt=-1;
  }else if(!active.isEmpty()){
   // Only observed completion settles. Lost connection, waiting, a replaced
   // scope, malformed input and expired evidence all go still immediately.
   if(reliable&&finished.containsAll(active)&&motion){
    settleFrom=workingOffset(now);settleAt=now;phase=Phase.SETTLING;
   }else{settleAt=-1;phase=Phase.STILL;}
  }else if(phase==Phase.SETTLING&&(now<settleAt||now-settleAt>=SETTLE_MS))phase=Phase.STILL;
  active=next;
  if(!motion){settleAt=-1;if(active.isEmpty())phase=Phase.STILL;return 0;}
  if(phase==Phase.WORKING)return workingOffset(now);
  if(phase==Phase.SETTLING){
   float remaining=1f-(float)(now-settleAt)/SETTLE_MS;
   return settleFrom*Math.max(0,Math.min(1,remaining));
  }
  return 0;
 }
 private float workingOffset(long now){
  // Sub-dp travel within the existing 64x28 glyph box. Hit targets do not move.
  if(began<0||now<began)return 0;
  return .6f*(float)Math.sin(2*Math.PI*((now-began)%PERIOD_MS)/PERIOD_MS);
 }
 static boolean fresh(long received,long now){return received>=0&&now>=received&&now-received<=MAX_AGE_MS;}
}
