package dev.pixel.voice;

import java.util.*;
import java.util.regex.*;

/** Only the requested package is returned. Preserve commas in Android's description. */
final class MediaSnapshot {
 final String status,description;final boolean playing;final int state;final double speed;
 MediaSnapshot(String status,String description,int state){this(status,description,state,Double.NaN);}
 MediaSnapshot(String status,String description,int state,double speed){this.status=status;this.description=description;this.state=state;this.playing=state==3;this.speed=speed;}
 static MediaSnapshot parse(String dump,String packageName){
  Pattern packages=Pattern.compile("(?m)^\\s*package=([^\\s]+)\\s*$");
  Matcher m=packages.matcher(dump);List<Integer> starts=new ArrayList<>(),ends=new ArrayList<>();List<String> names=new ArrayList<>();
  while(m.find()){starts.add(m.start());ends.add(m.end());names.add(m.group(1));}
  List<MediaSnapshot> found=new ArrayList<>();
  for(int i=0;i<starts.size();i++){
   if(!packageName.equals(names.get(i)))continue;
   String block=dump.substring(ends.get(i),i+1<starts.size()?starts.get(i+1):dump.length());
   if(!Pattern.compile("(?m)^\\s*active=true\\s*$").matcher(block).find())continue;
   Matcher state=Pattern.compile("state=PlaybackState \\{state=(?:[A-Z_]+\\()?([0-9]+)").matcher(block);
   Matcher meta=Pattern.compile("(?m)^\\s*metadata: size=\\d+, description=(.*)$").matcher(block);
   if(!state.find()||!meta.find())continue;
   String description=meta.group(1).trim();if(description.isEmpty()||description.equals("null"))continue;
   int value=Integer.parseInt(state.group(1));
   Matcher speed=Pattern.compile("speed=([0-9.]+)").matcher(block);
   found.add(new MediaSnapshot(value==2||value==3?"ok":"not_ready",description,value,speed.find()?Double.parseDouble(speed.group(1)):Double.NaN));
  }
  if(found.size()>1)return new MediaSnapshot("ambiguous","",-1);
  return found.isEmpty()?new MediaSnapshot("unavailable","",-1):found.get(0);
 }
}
