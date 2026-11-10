package dev.pixel.voice;

import java.util.HashSet;
import java.util.Set;

/** Privacy-minimal projection; no task keys or content cross into the global setting. */
final class TrayBackgroundActivity {
 private Set<String> active=new HashSet<>();
 private long completedUntil;
 String state(WorkspaceOverviewFeed.ActivitySnapshot s,long now){
  if(!s.running.isEmpty()){
   active=new HashSet<>(s.running);completedUntil=0;
   return "working:"+s.runningUntil;
  }
  if(!active.isEmpty()){
   completedUntil=s.reliable&&s.finished.containsAll(active)?s.finishedUntil:0;
   active.clear();
  }
  if(!s.reliable||s.finished.isEmpty())completedUntil=0;
  return completedUntil>=now&&completedUntil>0?"terminal:"+completedUntil:"still:0";
 }
}
