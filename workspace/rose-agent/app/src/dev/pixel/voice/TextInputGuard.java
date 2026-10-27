package dev.pixel.voice;

import java.util.*;

/** Resolves a text field across unrelated UI changes; never permits a coordinate action. */
final class TextInputGuard {
 static final class Node {
  final int id,parent,l,t,r,b;
  final String type,viewId,text,description;
  final boolean editable,enabled,password,visible,focused;
  Node(int id,int parent,String type,String viewId,String text,String description,
       boolean editable,boolean enabled,boolean password,boolean visible,boolean focused,
       int l,int t,int r,int b){
   this.id=id;this.parent=parent;this.type=type;this.viewId=viewId;this.text=text;this.description=description;
   this.editable=editable;this.enabled=enabled;this.password=password;this.visible=visible;this.focused=focused;
   this.l=l;this.t=t;this.r=r;this.b=b;
  }
  boolean eligible(){return editable&&enabled&&!password&&visible&&l<r&&t<b&&!viewId.isEmpty();}
  boolean key(Node n){return type.equals(n.type)&&viewId.equals(n.viewId);}
 }
 static final class Frame {
  final String window;final boolean truncated;final Map<Integer,Node> nodes;
  Frame(String window,boolean truncated,Map<Integer,Node> nodes){this.window=window;this.truncated=truncated;this.nodes=nodes;}
 }
 static Node unique(Frame frame,Node key){
  Node found=null;
  for(Node n:frame.nodes.values())if(n.key(key)){if(found!=null)return null;found=n;}
  return found;
 }
 static boolean ancestry(Frame before,Node a,Frame after,Node b){
  Set<Integer> oldSeen=new HashSet<>(),newSeen=new HashSet<>();
  while(a.parent>=0&&b.parent>=0){
   if(!oldSeen.add(a.parent)||!newSeen.add(b.parent))return false;
   a=before.nodes.get(a.parent);b=after.nodes.get(b.parent);
   if(a==null||b==null||!a.key(b))return false;
  }
  return a.parent==b.parent;
 }
 static int resolve(Frame before,Frame after,int requested){
  if(before==null||after==null||before.truncated||after.truncated||!before.window.equals(after.window))return -1;
  Node a=before.nodes.get(requested);
  if(a==null||!a.eligible()||unique(before,a)!=a)return -1;
  Node b=unique(after,a);
  if(b==null||!b.eligible()||!a.text.equals(b.text)||!a.description.equals(b.description)
     ||a.l!=b.l||a.t!=b.t||a.r!=b.r||a.b!=b.b||!ancestry(before,a,after,b))return -1;
  // Acquiring focus is expected. Losing it, or another editable field holding it, is not.
  if(a.focused&&!b.focused)return -1;
  for(Node n:after.nodes.values())if(n.editable&&n.focused&&n.id!=b.id)return -1;
  return b.id;
 }
}
