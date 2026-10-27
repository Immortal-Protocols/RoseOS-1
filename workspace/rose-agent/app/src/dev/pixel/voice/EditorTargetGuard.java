package dev.pixel.voice;
/** Re-resolve the same observed editor, never an ordinal reused by a loading list. */
final class EditorTargetGuard {
 static boolean eligible(TextInputGuard.Node n){return n!=null&&n.editable&&n.enabled&&!n.password&&n.visible&&n.l<n.r&&n.t<n.b;}
 static boolean same(TextInputGuard.Node a,TextInputGuard.Node b){return eligible(b)&&a.type.equals(b.type)&&a.viewId.equals(b.viewId)&&a.text.equals(b.text)&&a.description.equals(b.description)&&a.l==b.l&&a.t==b.t&&a.r==b.r&&a.b==b.b;}
 static int resolve(TextInputGuard.Frame before,TextInputGuard.Frame after,int requested){
  if(before==null||after==null||before.truncated||after.truncated||!before.window.equals(after.window))return -1;
  TextInputGuard.Node a=before.nodes.get(requested);if(!eligible(a))return -1;
  int oldMatches=0;for(TextInputGuard.Node n:before.nodes.values())if(same(a,n))oldMatches++;if(oldMatches!=1)return -1;
  TextInputGuard.Node b=null;for(TextInputGuard.Node n:after.nodes.values())if(same(a,n)){if(b!=null)return -1;b=n;}
  if(b==null||!TextInputGuard.ancestry(before,a,after,b)||a.focused&&!b.focused)return -1;
  for(TextInputGuard.Node n:after.nodes.values())if(n.editable&&n.focused&&n.id!=b.id)return -1;
  return b.id;
 }
}
