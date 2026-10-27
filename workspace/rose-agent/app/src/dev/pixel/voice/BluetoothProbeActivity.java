package dev.pixel.voice;
import android.app.Activity;
import android.os.*;
import android.bluetooth.*;
import android.bluetooth.le.*;
import android.widget.TextView;
import android.util.Log;
import java.util.*;

/** Temporary foreground diagnostic; never records microphone audio. */
public class BluetoothProbeActivity extends Activity {
 private BluetoothLeScanner scanner;private final Handler handler=new Handler();private boolean isolate;
 private final Map<String,Long> last=new HashMap<>();private TextView label;
 private final ScanCallback callback=new ScanCallback(){
  public void onScanResult(int type,ScanResult result){
   String address=result.getDevice().getAddress();long now=SystemClock.elapsedRealtime();
   if(now-last.getOrDefault(address,0L)<1000)return;last.put(address,now);
   ScanRecord record=result.getScanRecord();String name=record==null?null:record.getDeviceName();
   StringBuilder hex=new StringBuilder();if(record!=null)for(byte b:record.getBytes())hex.append(String.format("%02x",b));
   Log.i("PixelScan","advert t="+now+" addr="+address+" name="+name+" rssi="+result.getRssi()+" data="+hex);
   label.setText("Bluetooth unfold test\nScanning · microphone off\nReconnect attempts paused\nDevices observed: "+last.size()+"\nLeave this screen open during the test.");
  }
  public void onScanFailed(int error){Log.e("PixelScan","scan failed code="+error);label.setText("Scan failed: "+error);}
 };
 public void onCreate(Bundle state){super.onCreate(state);label=new TextView(this);label.setText("Starting Bluetooth unfold test");label.setTextSize(22);label.setPadding(40,100,40,40);setContentView(label);
  isolate=getIntent().getBooleanExtra("isolateVoice",false);
  if(isolate){
   label.setText("Bluetooth isolation test\nRose Agent service stopped\nGlasses remain paired\nClose this screen to restore Rose Agent.");
   stopService(new android.content.Intent(this,VoiceService.class));
   Log.i("PixelScan","VOICE ISOLATION START t="+System.currentTimeMillis());
   handler.postDelayed(()->finish(),180000);return;
  }
  GlassesReconnect.probeUntil=SystemClock.elapsedRealtime()+300000;
  if(VoiceService.instance!=null)VoiceService.instance.command("end");
  try{scanner=getSystemService(BluetoothManager.class).getAdapter().getBluetoothLeScanner();scanner.startScan(null,new ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(),callback);Log.i("PixelScan","SCAN START t="+SystemClock.elapsedRealtime());}catch(Exception e){Log.e("PixelScan","start failed",e);label.setText(e.toString());}
  handler.postDelayed(()->finish(),300000);
 }
 protected void onDestroy(){handler.removeCallbacksAndMessages(null);if(scanner!=null)scanner.stopScan(callback);GlassesReconnect.probeUntil=0;if(isolate){startForegroundService(new android.content.Intent(this,VoiceService.class));Log.i("PixelScan","VOICE ISOLATION END t="+System.currentTimeMillis());}else Log.i("PixelScan","SCAN STOP");super.onDestroy();}
}
