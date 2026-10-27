package dev.pixel.voice;
import java.util.List;
import java.util.Locale;
/** Discovery aliases without headset services must not replace the audio endpoint. */
final class GlassesReconnectTarget {
 static String select(String preferred,String fallback,List<String> approvedCandidates){
  for(String wanted:new String[]{preferred,fallback})if(wanted!=null)for(String candidate:approvedCandidates)
   if(wanted.equalsIgnoreCase(candidate))return candidate.toUpperCase(Locale.ROOT);
  return null;
 }
}
