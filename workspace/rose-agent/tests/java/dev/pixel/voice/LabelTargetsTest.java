package dev.pixel.voice;
import java.util.*;
public class LabelTargetsTest {
 static void check(boolean ok){if(!ok)throw new AssertionError();}
 public static void main(String[] args){
  Map<Integer,LabelTargets.Node> n=new LinkedHashMap<>();
  n.put(0,new LabelTargets.Node(0,-1,true,true,false,"",""));
  n.put(1,new LabelTargets.Node(1,0,true,true,false,"",""));
  n.put(2,new LabelTargets.Node(2,1,true,false,false,"Install",""));
  n.put(3,new LabelTargets.Node(3,1,true,false,false,"","Install"));
  check(LabelTargets.resolve(n,"Install").equals(Collections.singleton(1)));
  n.put(4,new LabelTargets.Node(4,0,true,true,false,"Install",""));
  check(LabelTargets.resolve(n,"Install").size()==2);
  check(LabelTargets.disambiguate(LabelTargets.resolve(n,"Install"),1).equals(Collections.singleton(1)));
  check(LabelTargets.disambiguate(LabelTargets.resolve(n,"Install"),4).equals(Collections.singleton(4)));
  check(LabelTargets.disambiguate(LabelTargets.resolve(n,"Install"),2).isEmpty());
  check(LabelTargets.disambiguate(LabelTargets.resolve(n,"Install"),99).isEmpty());
  check(LabelTargets.disambiguate(LabelTargets.resolve(n,"Install"),null).size()==2);
  n.put(1,new LabelTargets.Node(1,0,false,true,false,"",""));
  check(LabelTargets.resolve(n,"Install").equals(Collections.singleton(4)));
  n.put(4,new LabelTargets.Node(4,0,true,true,true,"Install",""));
  check(LabelTargets.resolve(n,"Install").isEmpty());
  check(LabelTargets.resolve(n,"install").isEmpty());
  System.out.println("PASS: nested labels, duplicate label deduplication, ambiguity, disabled ancestors, password exclusion");
 }
}
