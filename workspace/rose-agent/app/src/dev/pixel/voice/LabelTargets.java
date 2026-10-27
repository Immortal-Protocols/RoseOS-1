package dev.pixel.voice;
import java.util.*;
/** Resolves visible labels through their actual tree ancestry, never screen proximity. */
final class LabelTargets {
 static final class Node {
  final int id,parent; final boolean enabled,clickable,secret; final String text,description;
  Node(int id,int parent,boolean enabled,boolean clickable,boolean secret,String text,String description){this.id=id;this.parent=parent;this.enabled=enabled;this.clickable=clickable;this.secret=secret;this.text=text;this.description=description;}
 }
 static int target(Map<Integer,Node> nodes,int id){
  for(int depth=0;depth<51;depth++){
   Node n=nodes.get(id);if(n==null||!n.enabled||n.secret)return -1;
   if(n.clickable)return n.id;id=n.parent;
  }return -1;
 }
 static Set<Integer> resolve(Map<Integer,Node> nodes,String label){
  Set<Integer> ids=new LinkedHashSet<>();
  for(Node n:nodes.values())if(label.equals(n.text)||label.equals(n.description)){
   int id=target(nodes,n.id);if(id>=0)ids.add(id);
  }return ids;
 }
 static Set<Integer> disambiguate(Set<Integer> targets,Integer requested){
  if(requested==null)return targets;
  return targets.contains(requested)?Collections.singleton(requested):Collections.emptySet();
 }
}
