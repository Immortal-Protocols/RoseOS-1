package dev.pixel.voice;

import android.content.Intent;
import android.os.*;
import org.json.JSONObject;
import java.util.function.Consumer;

/** One signature-protected local command. No UI search or blind retry. */
final class ClockControlRoutine {
 private final VoiceService service; private final JSONObject command; private final Consumer<JSONObject> complete;
 private final Handler handler=new Handler(Looper.getMainLooper()); private volatile boolean done;
 private final long started=SystemClock.elapsedRealtime();
 ClockControlRoutine(VoiceService service,JSONObject command,Consumer<JSONObject> complete){this.service=service;this.command=command;this.complete=complete;}
 boolean running(){return !done;}
 void start(){handler.post(()->{try{
  if(command.getLong("expires")<System.currentTimeMillis()){fail("Command expired");return;}
  ResultReceiver callback=new ResultReceiver(handler){@Override protected void onReceiveResult(int code,Bundle data){try{finish(new JSONObject(data.getString("result")));}catch(Exception e){fail("Invalid Clock reply");}}};
  Parcel p=Parcel.obtain();ResultReceiver portable;
  try{callback.writeToParcel(p,0);p.setDataPosition(0);portable=ResultReceiver.CREATOR.createFromParcel(p);}finally{p.recycle();}
  service.sendBroadcast(new Intent().setClassName("dev.pixel.clock","com.android.deskclock.ClockCommandReceiver")
    .addFlags(Intent.FLAG_INCLUDE_STOPPED_PACKAGES).putExtra("operationId",command.getString("id"))
    .putExtra("request",command.getJSONObject("params").toString()).putExtra("reply",portable));
  handler.postDelayed(()->fail("Clock did not confirm. Read state before requesting another action."),12000);
 }catch(Exception e){fail(e.getMessage());}});}
 void cancel(){fail("Clock connection closed; outcome unknown. Read state before another action.");}
 private void fail(String reason){try{finish(new JSONObject().put("status","uncertain").put("reason",reason));}catch(Exception ignored){}}
 private void finish(JSONObject result){if(done)return;done=true;handler.removeCallbacksAndMessages(null);
  String op=command.optJSONObject("params")==null?"":command.optJSONObject("params").optString("operation");
  if("verified".equals(result.optString("status"))&&!op.matches(".*_(get|list|search|time)"))try{
   service.startActivity(new Intent().setClassName("dev.pixel.clock","com.android.deskclock.DeskClock").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
  }catch(Exception ignored){}
  try{result.put("commandElapsedMs",SystemClock.elapsedRealtime()-started).put("modelCalls",0);}catch(Exception ignored){}
  complete.accept(result);
 }
}
