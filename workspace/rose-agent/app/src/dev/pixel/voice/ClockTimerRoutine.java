package dev.pixel.voice;

import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.ResultReceiver;
import android.os.Parcel;
import android.os.SystemClock;
import org.json.JSONObject;
import java.util.function.Consumer;

/** One local Clock command with an explicit, verified result; never retries a start. */
final class ClockTimerRoutine {
 private final VoiceService service; private final JSONObject command; private final Consumer<JSONObject> complete;
 private final Handler handler=new Handler(Looper.getMainLooper()); private boolean done; private final long started=SystemClock.elapsedRealtime();
 ClockTimerRoutine(VoiceService service,JSONObject command,Consumer<JSONObject> complete){this.service=service;this.command=command;this.complete=complete;}
 boolean running(){return !done;}
 void start(){handler.post(()->{try{
  int seconds=command.optInt("seconds"); if(seconds<1||seconds>86400)throw new IllegalArgumentException("Duration must be one second to one day");
  ResultReceiver callback=new ResultReceiver(handler){@Override protected void onReceiveResult(int code,Bundle data){try{finish(new JSONObject(data.getString("result")));}catch(Exception e){fail("Invalid Clock reply");}}};
  Parcel parcel=Parcel.obtain();ResultReceiver portable;
  try {callback.writeToParcel(parcel,0);parcel.setDataPosition(0);portable=ResultReceiver.CREATOR.createFromParcel(parcel);}finally{parcel.recycle();}
  Intent i=new Intent().setClassName("dev.pixel.clock","com.android.deskclock.TimerCommandReceiver").addFlags(Intent.FLAG_INCLUDE_STOPPED_PACKAGES)
    .putExtra("operationId",command.getString("id")).putExtra("seconds",seconds).putExtra("label",command.optString("label"))
    .putExtra("reply",portable);
  service.sendBroadcast(i);
  handler.postDelayed(()->fail("Clock did not confirm; inspect timers before requesting another"),12000);
 }catch(Exception e){fail(e.getMessage());}});}
 private void fail(String reason){try{finish(new JSONObject().put("status","uncertain").put("reason",reason));}catch(Exception ignored){}}
 private void finish(JSONObject result){if(done)return;done=true;
 if("verified_timer_started".equals(result.optString("status")))try{service.startActivity(new Intent().setClassName("dev.pixel.clock","com.android.deskclock.DeskClock").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).putExtra("com.android.deskclock.extra.TIMER_ID",result.optInt("timerId")));}catch(Exception ignored){}
handler.removeCallbacksAndMessages(null);try{result.put("commandElapsedMs",SystemClock.elapsedRealtime()-started);result.put("modelCalls",0);}catch(Exception ignored){}complete.accept(result);}
}
