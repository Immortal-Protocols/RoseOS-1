package dev.pixel.voice;
import android.content.*;
public class MediaButtonReceiver extends BroadcastReceiver {
 public void onReceive(Context context,Intent intent){if(!Intent.ACTION_MEDIA_BUTTON.equals(intent.getAction()))return;Intent forward=new Intent(context,VoiceService.class).setAction(Intent.ACTION_MEDIA_BUTTON);if(intent.getExtras()!=null)forward.putExtras(intent.getExtras());context.startForegroundService(forward);}
}
