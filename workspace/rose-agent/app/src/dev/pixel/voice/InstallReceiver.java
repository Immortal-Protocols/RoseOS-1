package dev.pixel.voice;
import android.content.*;import android.content.pm.PackageInstaller;import android.widget.Toast;
public class InstallReceiver extends BroadcastReceiver {
 @Override public void onReceive(Context c,Intent i){int status=i.getIntExtra(PackageInstaller.EXTRA_STATUS,PackageInstaller.STATUS_FAILURE);String message;
 if(status==PackageInstaller.STATUS_PENDING_USER_ACTION){Intent confirm=i.getParcelableExtra(Intent.EXTRA_INTENT);if(confirm!=null){confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);c.startActivity(confirm);}message="Confirm installation in Android.";}
 else if(status==PackageInstaller.STATUS_SUCCESS)message="Installation complete.";
 else message="Installation not completed: "+i.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE);
 c.getSharedPreferences("delivery",0).edit().putString("status",message).apply();Toast.makeText(c,message,Toast.LENGTH_LONG).show();
 }
}
