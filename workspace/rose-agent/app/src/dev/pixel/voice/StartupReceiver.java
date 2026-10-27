package dev.pixel.voice;
import android.content.*;
/** Best-effort startup; a denied background launch must not crash the app. */
public class StartupReceiver extends BroadcastReceiver {
 public void onReceive(Context context,Intent intent){
  try{context.startForegroundService(new Intent(context,VoiceService.class));}
  catch(IllegalStateException|SecurityException denied){
   android.util.Log.w("PixelStartup","Background voice startup denied; waiting for user activation");
  }
 }
}
