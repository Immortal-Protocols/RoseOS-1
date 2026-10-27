package dev.pixel.voice;
import java.util.*;

public class TextInputGuardTest {
 static TextInputGuard.Node node(int id,int parent,String type,String key,String text,boolean edit,boolean focus,int y){
  return new TextInputGuard.Node(id,parent,type,key,text,"",edit,true,false,true,focus,20,y,600,y+60);
 }
 static TextInputGuard.Node field(int id,int parent,String key,String text,boolean focus,int y){return node(id,parent,"EditText",key,text,true,focus,y);}
 static TextInputGuard.Node variant(TextInputGuard.Node n,boolean enabled,boolean secret,boolean visible,String description){
  return new TextInputGuard.Node(n.id,n.parent,n.type,n.viewId,n.text,description,n.editable,enabled,secret,visible,n.focused,n.l,n.t,n.r,n.b);
 }
 static TextInputGuard.Frame frame(String win,boolean truncated,TextInputGuard.Node... nodes){
  Map<Integer,TextInputGuard.Node> map=new LinkedHashMap<>();for(TextInputGuard.Node n:nodes)map.put(n.id,n);return new TextInputGuard.Frame(win,truncated,map);
 }
 static void check(boolean ok,String why){if(!ok)throw new AssertionError(why);}
 public static void main(String[] args){
  TextInputGuard.Node root=node(0,-1,"Layout","root","",false,false,0),input=field(5,0,"app:id/search","",true,100);
  TextInputGuard.Frame old=frame("app:7",false,root,input,node(6,0,"Label","status","Loading",false,false,300));
  TextInputGuard.Frame changed=frame("app:7",false,root,input,node(8,0,"Label","status","Ready",false,false,400));
  check(TextInputGuard.resolve(old,changed,5)==5,"unrelated label/removal/geometry changes must not block unchanged field");
  TextInputGuard.Node movedIndex=field(9,0,"app:id/search","",true,100);
  check(TextInputGuard.resolve(old,frame("app:7",false,root,movedIndex),5)==9,"resolve by unique view ID, not obsolete traversal index");
  TextInputGuard.Node initiallyUnfocused=field(5,0,"app:id/search","",false,100);
  check(TextInputGuard.resolve(frame("app:7",false,root,initiallyUnfocused),changed,5)==5,"focus acquisition allowed");
  check(TextInputGuard.resolve(old,frame("app:7",false,root,initiallyUnfocused),5)<0,"focus loss rejected");
  check(TextInputGuard.resolve(old,frame("app:8",false,root,input),5)<0,"different window rejected");
  check(TextInputGuard.resolve(old,frame("other:7",false,root,input),5)<0,"different package rejected");
  check(TextInputGuard.resolve(old,frame("app:7",true,root,input),5)<0,"truncated current rejected");
  check(TextInputGuard.resolve(frame("app:7",true,root,input),changed,5)<0,"truncated original rejected");
  check(TextInputGuard.resolve(old,frame("app:7",false,root,field(5,0,"app:id/search","user edit",true,100)),5)<0,"changed value rejected");
  check(TextInputGuard.resolve(old,frame("app:7",false,root,field(5,0,"app:id/destination","",true,100)),5)<0,"different field rejected");
  check(TextInputGuard.resolve(old,frame("app:7",false,root,field(5,0,"app:id/search","",true,110)),5)<0,"changed target bounds rejected");
  check(TextInputGuard.resolve(old,frame("app:7",false,root,input,movedIndex),5)<0,"ambiguous current identity rejected");
  check(TextInputGuard.resolve(frame("app:7",false,root,input,movedIndex),changed,5)<0,"ambiguous original identity rejected");
  check(TextInputGuard.resolve(old,frame("app:7",false,root,variant(input,false,false,true,"")),5)<0,"disabled field rejected");
  check(TextInputGuard.resolve(old,frame("app:7",false,root,variant(input,true,true,true,"")),5)<0,"password field rejected");
  check(TextInputGuard.resolve(old,frame("app:7",false,root,variant(input,true,false,false,"")),5)<0,"invisible field rejected");
  check(TextInputGuard.resolve(old,frame("app:7",false,root,variant(input,true,false,true,"New purpose")),5)<0,"changed description rejected");
  check(TextInputGuard.resolve(old,frame("app:7",false,node(0,-1,"OtherLayout","root","",false,false,0),input),5)<0,"changed ancestry rejected");
  check(TextInputGuard.resolve(old,frame("app:7",false,root,input,field(10,0,"app:id/other","",true,300)),5)<0,"another focused field rejected");
  TextInputGuard.Node noId=field(5,0,"","",true,100);
  check(TextInputGuard.resolve(frame("app:7",false,root,noId),frame("app:7",false,root,noId),5)<0,"anonymous field falls back to old checks");
  System.out.println("PASS: stable-field typing; changed/ambiguous input, focus loss, ancestry, password, window and incomplete frames rejected");
 }
}
