package dev.pixel.backgroundcontrol;
import android.content.*;
public final class StopReceiver extends BroadcastReceiver {
 // The owner's Stop from one session's notification stops that session only; an older notification without an id is
 // slot 0, as before (MultiSession.ownerStop).
 public void onReceive(Context c,Intent i){Controller.ownerStop(c,i.getStringExtra("sessionId"));}
}
