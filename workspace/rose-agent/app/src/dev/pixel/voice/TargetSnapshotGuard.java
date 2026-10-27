package dev.pixel.voice;

import java.util.*;

/** Allows unrelated geometry updates without accepting a changed target or scene. */
final class TargetSnapshotGuard {
 static final class Node {
  final int id,l,t,r,b; final String meaning;
  Node(int id,String meaning,int l,int t,int r,int b){this.id=id;this.meaning=meaning;this.l=l;this.t=t;this.r=r;this.b=b;}
  boolean overlaps(int left,int top,int right,int bottom){return l<right&&r>left&&t<bottom&&b>top;}
  boolean sameBounds(Node n){return l==n.l&&t==n.t&&r==n.r&&b==n.b;}
 }
 static final class Frame {
  final String window; final boolean truncated; final Map<Integer,Node> nodes;
  Frame(String window,boolean truncated,Map<Integer,Node> nodes){this.window=window;this.truncated=truncated;this.nodes=nodes;}
 }
 static boolean allows(Frame old,Frame now,int target,Integer x,Integer y,int margin){
  if(old==null||now==null||old.truncated||now.truncated||!old.window.equals(now.window)||!old.nodes.keySet().equals(now.nodes.keySet()))return false;
  Node a=old.nodes.get(target),b=now.nodes.get(target);
  if(a==null||b==null||!a.sameBounds(b))return false;
  int l=a.l-margin,t=a.t-margin,r=a.r+margin,bot=a.b+margin;
  if(x!=null&&y!=null){
   if(x<a.l||x>=a.r||y<a.t||y>=a.b)return false;
   l=x-margin;t=y-margin;r=x+margin+1;bot=y+margin+1;
  }
  for(Node before:old.nodes.values()){
   Node after=now.nodes.get(before.id);
   // Text, identity, hierarchy, enabled/selected state and actions stay strict.
   if(!before.meaning.equals(after.meaning))return false;
   if(!before.sameBounds(after)&&(before.overlaps(l,t,r,bot)||after.overlaps(l,t,r,bot)))return false;
  }
  return true;
 }
}
