package dev.pixel.voice;
import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.content.Intent;
import android.content.pm.PackageManager;

/** Visible entry point allows user-initiated Sky launch to start microphone foreground work. */
public class SkyEntryActivity extends Activity {
 @Override public void onCreate(Bundle state){super.onCreate(state);}
 @Override public void onResume(){super.onResume();
  if(checkSelfPermission("android.permission.RECORD_AUDIO")!=PackageManager.PERMISSION_GRANTED){
   startActivity(new Intent(this,MainActivity.class));finish();return;
  }
  startForegroundService(new Intent(this,VoiceService.class).setAction("sky_open"));
  final Handler handler=new Handler();final long deadline=android.os.SystemClock.elapsedRealtime()+3000;
  handler.post(new Runnable(){public void run(){VoiceService service=VoiceService.instance;
   if((service!=null&&service.communicating)||android.os.SystemClock.elapsedRealtime()>deadline){finish();overridePendingTransition(0,0);}
   else handler.postDelayed(this,50);
  }});
 }
}
