package dev.pixel.voice;
import java.util.*;
public class TargetSnapshotGuardTest {
 static TargetSnapshotGuard.Node n(int id,String meaning,int l,int t,int r,int b){return new TargetSnapshotGuard.Node(id,meaning,l,t,r,b);}
 static TargetSnapshotGuard.Frame f(String window,boolean truncated,TargetSnapshotGuard.Node... nodes){Map<Integer,TargetSnapshotGuard.Node> map=new LinkedHashMap<>();for(TargetSnapshotGuard.Node n:nodes)map.put(n.id,n);return new TargetSnapshotGuard.Frame(window,truncated,map);}
 static void check(boolean ok,String why){if(!ok)throw new AssertionError(why);}
 public static void main(String[] args){
  TargetSnapshotGuard.Node root=n(0,"root",0,0,1080,2404),button=n(22,"Reserve enabled",39,2208,520,2345),marker=n(33,"My Location",488,831,571,931),moved=n(33,"My Location",493,828,576,928);
  TargetSnapshotGuard.Frame before=f("bird:1",false,root,button,marker),after=f("bird:1",false,root,button,moved);
  check(TargetSnapshotGuard.allows(before,after,22,null,null,72),"unrelated moving map marker must not block Reserve");
  check(!TargetSnapshotGuard.allows(before,f("bird:2",false,root,button,moved),22,null,null,72),"new window");
  check(!TargetSnapshotGuard.allows(before,f("other:1",false,root,button,moved),22,null,null,72),"new app");
  check(!TargetSnapshotGuard.allows(before,f("bird:1",true,root,button,moved),22,null,null,72),"incomplete tree");
  check(!TargetSnapshotGuard.allows(before,f("bird:1",false,root,n(22,"Reserve enabled",40,2208,521,2345),moved),22,null,null,72),"moved target");
  check(!TargetSnapshotGuard.allows(before,f("bird:1",false,root,n(22,"Start enabled",39,2208,520,2345),moved),22,null,null,72),"relabeled target");
  check(!TargetSnapshotGuard.allows(before,f("bird:1",false,root,n(22,"Reserve disabled",39,2208,520,2345),moved),22,null,null,72),"disabled target");
  check(!TargetSnapshotGuard.allows(before,f("bird:1",false,root,button,moved,n(34,"dialog",0,0,1080,2404)),22,null,null,72),"new node/dialog");
  check(!TargetSnapshotGuard.allows(before,f("bird:1",false,root,button,n(33,"My Location",493,2150,576,2280)),22,null,null,72),"geometry entering target area");
  TargetSnapshotGuard.Node nearby=n(33,"nearby",530,2208,570,2345);
  check(!TargetSnapshotGuard.allows(f("bird:1",false,root,button,nearby),f("bird:1",false,root,button,n(33,"nearby",540,2208,580,2345)),22,null,null,72),"nearby geometry");
  // Point tap on a map surface tolerates remote motion, but not motion at the point.
  check(TargetSnapshotGuard.allows(before,after,0,501,658,72),"remote geometry outside point area");
  check(!TargetSnapshotGuard.allows(before,after,0,530,880,72),"marker under point changed");
  check(!TargetSnapshotGuard.allows(before,after,0,-1,658,72),"point outside surface");
  check(!TargetSnapshotGuard.allows(before,after,99,null,null,72),"missing target");
  System.out.println("PASS: moving map allowed; changed target, vicinity, meaning, window and incomplete trees rejected");
 }
}
