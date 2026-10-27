package dev.pixel.voice;
import org.json.*;
final class PhoneControl {
 static String query,snapshot,state,imeStatus;
 static int reads,imeCalls,playCalls,focusCalls,rejections;
 static boolean loseFocus,focused,initiallyUnfocused,stuckFocus,changedFocusField;static int focusRejections,touchCalls;
 static int selectionRejections;static String selectionStatus="action_accepted";
 static void reset(int stale,String status,boolean lose){query="old query";state="navigate";snapshot="";reads=imeCalls=playCalls=focusCalls=0;rejections=stale;imeStatus=status;loseFocus=lose;focused=!initiallyUnfocused;touchCalls=0;}
 static JSONObject result(String status){return new JSONObject().put("status",status);}
 static JSONObject execute(VoiceService ignored,JSONObject c){
  String action=c.getString("action");
  if(action.equals("open_app"))return result("launch_requested");
  if(action.equals("inspect_ui")){
   snapshot="snapshot-"+(++reads);
   JSONArray nodes=new JSONArray().put(new JSONObject().put("id",10).put("text",changedFocusField&&focusCalls>0?"unexpected query":query).put("class","android.widget.AutoCompleteTextView").put("editable",true).put("focused",focused).put("imeEnter",focused).put("clickTarget",10));
   if(state.equals("results"))nodes.put(new JSONObject().put("id",20).put("text","TOP RESULTS")).put(new JSONObject().put("id",30).put("text","Diamonds").put("viewId","com.apple.android.music:id/title").put("clickTarget",30)).put(new JSONObject().put("id",31).put("text","Song • Rihanna").put("clickTarget",30));
   if(state.equals("verify"))nodes.put(new JSONObject().put("id",40).put("description","playing Diamonds")).put(new JSONObject().put("id",41).put("description","Pause").put("clickTarget",41));
   return result("ok").put("snapshotId",snapshot).put("packageName",AppleMusicRoutine.PKG).put("truncated",false).put("nodes",nodes);
  }
  if(!snapshot.equals(c.optString("snapshotId")))throw new AssertionError("Reused an outdated snapshot");
  snapshot="invalidated";
  if(action.equals("set_text")){query=c.getString("text");state="submit";if(loseFocus)focused=false;return result("action_accepted");}
  if(action.equals("ime_enter")){
   if(!focused)throw new AssertionError("Submitted unfocused field");
   imeCalls++;
   if(rejections-->0)return result("stale_screen");
   if(imeStatus.equals("action_accepted")){state="results";focused=false;}
   return result(imeStatus);
  }
  if(action.equals("touch_label")){touchCalls++;if(changedFocusField)throw new AssertionError("Touched changed field");focused=true;return result("gesture_dispatched");}
  if(action.equals("tap_label")||action.equals("tap")){
   if(c.getInt("nodeId")==10){focusCalls++;if(focusRejections-->0)return result("stale_screen");if(!stuckFocus)focused=true;return result("action_failed");}
   if(!state.equals("results"))throw new AssertionError("Tapped before confirmed submission");
   if(selectionRejections-->0)return result("label_target_mismatch");
   playCalls++;if(selectionStatus.equals("action_accepted"))state="verify";return result(selectionStatus);
  }
  throw new AssertionError("Unexpected action "+action);
 }
}
