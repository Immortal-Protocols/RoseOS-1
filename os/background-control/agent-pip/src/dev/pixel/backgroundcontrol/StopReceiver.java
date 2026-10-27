package dev.pixel.backgroundcontrol;
import android.content.*;
public final class StopReceiver extends BroadcastReceiver {
 public void onReceive(Context c,Intent i){Controller.stop(c,StopLedger.USER);}
}
