package dev.pixel.voice;

import android.app.Activity;
import android.os.Bundle;
import android.content.Intent;
import android.net.Uri;
import android.provider.Settings;
import android.Manifest;
import android.content.pm.PackageManager;

/** Setup only; never starts voice or changes a setting on the user's behalf. */
public final class SettingsAccessActivity extends Activity {
 @Override public void onCreate(Bundle state) {
  super.onCreate(state);
  if("camera".equals(getIntent().getStringExtra("access"))) {
   if(checkSelfPermission(Manifest.permission.CAMERA)==PackageManager.PERMISSION_GRANTED)finish();
   else requestPermissions(new String[]{Manifest.permission.CAMERA},71);
  } else {
   startActivity(new Intent(Settings.ACTION_MANAGE_WRITE_SETTINGS,Uri.parse("package:"+getPackageName())));
   finish();
  }
 }
 @Override public void onRequestPermissionsResult(int request,String[] permissions,int[] results){super.onRequestPermissionsResult(request,permissions,results);finish();}
}
