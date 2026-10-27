package dev.pixel.voice;
import java.util.*;
import org.json.*;

/** Rejection-only metadata. Never emits labels, field text, IDs from apps, or coordinates. */
final class ScreenRejectionDiagnostics {
 static final String[] FIELDS={"parentId","class","viewId","text","visible","description","password","clickable","editable","scrollable","focused","imeEnter","enabled","checked","selected","clickTarget"};
 static JSONObject compare(String reason,TargetSnapshotGuard.Frame before,TargetSnapshotGuard.Frame after,int target,long age)throws Exception{
  JSONObject result=new JSONObject().put("reason",reason).put("snapshotAgeMs",Math.max(-1,age)).put("targetNodeId",target);
  if(before==null||after==null)return result;
  result.put("oldNodeCount",before.nodes.size()).put("newNodeCount",after.nodes.size()).put("windowChanged",!before.window.equals(after.window)).put("truncatedBefore",before.truncated).put("truncatedAfter",after.truncated);
  int added=0,removed=0,changed=0,geometry=0;Map<String,Integer> counts=new LinkedHashMap<>();
  for(Integer id:after.nodes.keySet())if(!before.nodes.containsKey(id))added++;
  for(TargetSnapshotGuard.Node old:before.nodes.values()){
   TargetSnapshotGuard.Node now=after.nodes.get(old.id);if(now==null){removed++;continue;}
   if(!old.sameBounds(now))geometry++;
   if(!old.meaning.equals(now.meaning))changed++;
   JSONObject a=new JSONObject(old.meaning),b=new JSONObject(now.meaning);
   for(String field:FIELDS)if(!Objects.equals(a.opt(field),b.opt(field)))counts.put(field,counts.getOrDefault(field,0)+1);
  }
  JSONObject fields=new JSONObject();for(Map.Entry<String,Integer> e:counts.entrySet())fields.put(e.getKey(),e.getValue());
  result.put("addedNodeCount",added).put("removedNodeCount",removed).put("changedNodeCount",changed).put("boundsChangedNodeCount",geometry).put("changedFieldCounts",fields);
  TargetSnapshotGuard.Node a=before.nodes.get(target),b=after.nodes.get(target);result.put("targetPresentBefore",a!=null).put("targetPresentAfter",b!=null);
  if(a!=null&&b!=null){
   JSONArray changedFields=new JSONArray();JSONObject x=new JSONObject(a.meaning),y=new JSONObject(b.meaning);
   for(String field:FIELDS)if(!Objects.equals(x.opt(field),y.opt(field)))changedFields.put(field);
   result.put("targetChangedFields",changedFields).put("targetBoundsChanged",!a.sameBounds(b)).put("targetUnchanged",a.meaning.equals(b.meaning)&&a.sameBounds(b));
  }
  return result;
 }
}
