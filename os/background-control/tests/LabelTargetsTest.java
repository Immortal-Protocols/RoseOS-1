package dev.pixel.backgroundcontrol;
import java.util.*;
public class LabelTargetsTest {
 static LabelTargets.Node n(int id,int parent,boolean click,boolean secret,String text){return new LabelTargets.Node(id,parent,true,click,secret,text,"");}
 static void check(boolean b,String message){if(!b)throw new AssertionError(message);}
 public static void main(String[] args){
  Map<Integer,LabelTargets.Node> m=new LinkedHashMap<>();m.put(0,n(0,-1,false,false,""));m.put(1,n(1,0,true,false,""));m.put(2,n(2,1,false,false,""));m.put(3,n(3,2,false,false,"Start"));m.put(4,n(4,2,false,false,"Start"));
  check(LabelTargets.resolve(m,"Start").equals(Collections.singleton(1)),"Nested label must resolve/dedupe clickable ancestor");
  check(LabelTargets.disambiguate(LabelTargets.resolve(m,"Start"),1).size()==1,"Clickable parent nodeId must work");
  check(LabelTargets.disambiguate(LabelTargets.resolve(m,"Start"),3).isEmpty(),"Text child cannot masquerade as target");
  m.put(299,n(299,0,true,false,"End"));check(LabelTargets.resolve(m,"End").equals(Collections.singleton(299)),"Upper permitted id must work");
  m.put(5,n(5,0,true,true,"Secret"));check(LabelTargets.resolve(m,"Secret").isEmpty(),"Password must be excluded");
  System.out.println("5 label/target compatibility cases passed");
 }
}
