package dev.pixel.backgroundcontrol;
import android.content.*;import android.content.pm.PackageManager;import android.database.Cursor;import android.net.Uri;import android.os.*;import org.json.*;
public final class AgentProvider extends ContentProvider {
 public boolean onCreate(){return true;}
 public Bundle call(String method,String arg,Bundle extras){
  if(getContext().getPackageManager().checkSignatures(android.os.Process.myUid(),Binder.getCallingUid())!=PackageManager.SIGNATURE_MATCH)throw new SecurityException("Caller signing identity does not match");
  JSONObject result;try{JSONObject p=new JSONObject(extras==null?"{}":extras.getString("params","{}"));switch(method){
   case "capabilities":{int limit=Controller.sessionLimit(getContext());result=new JSONObject().put("status","ok").put("schemaVersion",4).put("multiSession",limit>=2).put("maxSessions",limit).put("backgroundOnly",true).put("operations",new JSONArray(new String[]{"list_apps","start","get_state","command","stop"}));break;}
   case "list_apps":result=Controller.apps(getContext());break;
   case "start":result=Controller.route(getContext(),"start",p);break;
   case "get_state":result=Controller.route(getContext(),"get_state",p);break;
   case "preview":result=Controller.route(getContext(),"preview",p);break;
   case "current_apps":result=Controller.route(getContext(),"current_apps",p);break; // Rose Agent's own overview only (#156/#225); not in capabilities // Rose Agent's own overview only (#225); not in capabilities
   case "command":result=Controller.route(getContext(),"command",p);break;
   case "stop":result=Controller.route(getContext(),"stop",p);break;
   default:throw new IllegalArgumentException("Unknown background operation");
  }}catch(Throwable e){try{String message=Controller.root(e).toString();result=new JSONObject().put("status",message.contains("snapshot")||message.contains("Snapshot")||message.contains("stale")||message.contains("changed; inspect")?"stale_screen":"error").put("error",message).put("foregroundFallback",false);}catch(Exception impossible){throw new IllegalStateException(impossible);}}
  Bundle out=new Bundle();out.putString("result",result.toString());return out;
 }
 public Cursor query(Uri u,String[] p,String s,String[] a,String o){return null;}public String getType(Uri u){return null;}public Uri insert(Uri u,ContentValues v){throw new UnsupportedOperationException();}public int delete(Uri u,String s,String[] a){throw new UnsupportedOperationException();}public int update(Uri u,ContentValues v,String s,String[] a){throw new UnsupportedOperationException();}
}
