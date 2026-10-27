package dev.pixel.voice;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.SystemClock;

/** User presses Ask camera. Reuses the existing voice session and microphone owner. */
public final class CameraEntryActivity extends Activity {
 private boolean started;
 @Override public void onCreate(Bundle state){super.onCreate(state);}
 @Override public void onResume(){super.onResume();
  if(started)return;started=true;
  if(checkSelfPermission("android.permission.RECORD_AUDIO")!=PackageManager.PERMISSION_GRANTED){
   startActivity(new Intent(this,MainActivity.class));finish();return;
  }
  VoiceService current=VoiceService.instance;
  if(CameraEntryPolicy.shouldStart(current!=null,current!=null&&current.live,current!=null&&current.connected,current!=null&&current.communicating))
   startForegroundService(new Intent(this,VoiceService.class).setAction("talk"));
  else if(current.muted)
   android.widget.Toast.makeText(this,"Microphone is muted. Unmute to talk.",android.widget.Toast.LENGTH_SHORT).show();
  Handler handler=new Handler();long deadline=SystemClock.elapsedRealtime()+3000;
  handler.post(new Runnable(){public void run(){VoiceService service=VoiceService.instance;
   if((service!=null&&service.communicating)||SystemClock.elapsedRealtime()>deadline){finish();overridePendingTransition(0,0);}
   else handler.postDelayed(this,50);
  }});
 }
}
