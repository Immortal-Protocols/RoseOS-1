package dev.pixel.voice;

import android.bluetooth.*;
import android.content.*;
import android.media.AudioDeviceInfo;
import android.os.SystemClock;
import org.json.*;
import java.util.ArrayDeque;

/** Bounded state-only diagnostics. No audio, call content, or vendor arguments. */
final class GlassesTransportTrace {
 private final VoiceService service;
 private final ArrayDeque<JSONObject> events=new ArrayDeque<>();
 private BluetoothHeadset headset;private BluetoothA2dp media;
 private boolean closed;
 private final BluetoothProfile.ServiceListener listener=new BluetoothProfile.ServiceListener(){
  public void onServiceConnected(int profile,BluetoothProfile proxy){
   if(closed){service.getSystemService(BluetoothManager.class).getAdapter().closeProfileProxy(profile,proxy);return;}
   if(profile==BluetoothProfile.HEADSET)headset=(BluetoothHeadset)proxy;
   if(profile==BluetoothProfile.A2DP)media=(BluetoothA2dp)proxy;
  }
  public void onServiceDisconnected(int profile){if(profile==BluetoothProfile.HEADSET)headset=null;if(profile==BluetoothProfile.A2DP)media=null;}
 };
 private final BroadcastReceiver receiver=new BroadcastReceiver(){public void onReceive(Context c,Intent i){
  BluetoothDevice d=i.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE);
  if(d==null||!GlassesSetup.meta(d.getName()))return;
  String action=i.getAction();String kind=BluetoothHeadset.ACTION_AUDIO_STATE_CHANGED.equals(action)?"headset_audio":"media_playback";
  try{JSONObject e=new JSONObject().put("kind",kind).put("at",System.currentTimeMillis())
   .put("previous",i.getIntExtra(BluetoothProfile.EXTRA_PREVIOUS_STATE,-1)).put("state",i.getIntExtra(BluetoothProfile.EXTRA_STATE,-1));
   synchronized(events){while(events.size()>=32)events.removeFirst();events.addLast(e);}
  }catch(Exception ignored){}
 }};
 GlassesTransportTrace(VoiceService service){
  this.service=service;
  IntentFilter filter=new IntentFilter();filter.addAction(BluetoothHeadset.ACTION_AUDIO_STATE_CHANGED);filter.addAction(BluetoothA2dp.ACTION_PLAYING_STATE_CHANGED);
  service.registerReceiver(receiver,filter,Context.RECEIVER_EXPORTED);
  BluetoothAdapter adapter=service.getSystemService(BluetoothManager.class).getAdapter();
  adapter.getProfileProxy(service,listener,BluetoothProfile.HEADSET);adapter.getProfileProxy(service,listener,BluetoothProfile.A2DP);
 }
 JSONObject read()throws Exception {
  JSONObject r=new JSONObject().put("observedAt",System.currentTimeMillis());
  AudioDeviceInfo selected=service.bluetoothDevice();
  if(selected!=null){
   BluetoothDevice d=service.getSystemService(BluetoothManager.class).getAdapter().getRemoteDevice(selected.getAddress());
   if(headset!=null)r.put("headsetAudioConnected",headset.isAudioConnected(d));
   if(media!=null)r.put("mediaPlaying",media.isA2dpPlaying(d));
  }
  JSONArray timeline=new JSONArray();synchronized(events){for(JSONObject e:events)timeline.put(e);}return r.put("events",timeline);
 }
 void close(){closed=true;service.unregisterReceiver(receiver);BluetoothAdapter a=service.getSystemService(BluetoothManager.class).getAdapter();if(headset!=null)a.closeProfileProxy(BluetoothProfile.HEADSET,headset);if(media!=null)a.closeProfileProxy(BluetoothProfile.A2DP,media);}
}
