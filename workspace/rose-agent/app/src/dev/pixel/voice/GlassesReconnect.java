package dev.pixel.voice;
import android.bluetooth.*;
import android.bluetooth.le.*;
import java.util.Collections;
import android.content.*;
import android.os.*;
import android.util.Log;

/** Reconnect only the user's associated glasses using Android 37's public connect API. */
final class GlassesReconnect {
 static volatile long probeUntil;
 static volatile String lastEvent="No connection event observed since service start";
 static final String ADDRESS="80:AA:1C:64:4D:35";
 private final VoiceService context;
 private final GlassesAutoStartPolicy autoStart;
 private boolean connectionMayStart;
 void saveAutoStart(){context.getSharedPreferences("glasses",0).edit().putBoolean("manualEnd",autoStart.suppressed()).apply();}
 void userEnded(){autoStart.userEnded();saveAutoStart();cancelVoiceStart();context.traceVoice("autostart_policy","manual_end");}
 void userStarted(){autoStart.userStarted();saveAutoStart();}
 boolean suppressed(){return autoStart.suppressed();}
 private BluetoothLeScanner scanner;
 private boolean scanning;
 private int observedHinge=-1;
 private String observedAddress;
 private long hingeSeenAt,lastConnectRequest;
 private final ScanCallback advertisements=new ScanCallback(){
  public void onScanResult(int type,ScanResult result){
   if(stopped||SystemClock.elapsedRealtime()<probeUntil||result.getScanRecord()==null)return;
   if(!approved(result.getDevice().getAddress()))return;
   int state=GlassesAdvertisement.hinge(result.getScanRecord().getManufacturerSpecificData(0x01ab));
   if(state<0)return;
   autoStart.hinge(state==1);saveAutoStart();
   String address=result.getDevice().getAddress();
   boolean changed=state!=observedHinge||!address.equalsIgnoreCase(observedAddress);observedAddress=address;observedHinge=state;hingeSeenAt=SystemClock.elapsedRealtime();
   if(changed)Log.i("PixelReconnect","advertisement hinge="+(state==1?"open":"closed"));
   if(state==1&&changed){handler.removeCallbacks(check);handler.post(check);}
  }
  public void onScanFailed(int error){scanning=false;Log.w("PixelReconnect","hinge scan failed="+error);}
 };
 private void scan(boolean enabled){
  if(!enabled){if(scanning&&scanner!=null)scanner.stopScan(advertisements);scanning=false;return;}
  if(scanning||context.checkSelfPermission("android.permission.BLUETOOTH_SCAN")!=0)return;
  try{scanner=context.getSystemService(BluetoothManager.class).getAdapter().getBluetoothLeScanner();
   if(scanner!=null){scanner.startScan(Collections.singletonList(new ScanFilter.Builder().setManufacturerData(0x01ab,new byte[]{(byte)0x80,0x0a,0x01,0x33}).build()),new ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(),advertisements);scanning=true;Log.i("PixelReconnect","listening for target unfold advertisements");}
  }catch(Exception e){Log.w("PixelReconnect","hinge scan unavailable",e);}
 }
 private boolean approved(String address){
  android.companion.CompanionDeviceManager cdm=context.getSystemService(android.companion.CompanionDeviceManager.class);
  for(android.companion.AssociationInfo info:cdm.getMyAssociations())if(info.getDeviceMacAddress()!=null&&address.equalsIgnoreCase(info.getDeviceMacAddress().toString()))return true;
  return false;
 }
 private boolean audioCapable(BluetoothDevice device){
  android.os.ParcelUuid[] uuids=device.getUuids();
  if(uuids!=null)for(android.os.ParcelUuid uuid:uuids)if("0000111e-0000-1000-8000-00805f9b34fb".equalsIgnoreCase(uuid.toString()))return true;
  return false;
 }
 private String pendingAddress;
 private boolean pendingVoice;long voiceDeadline;long fastReconnectUntil;
 private final BroadcastReceiver connectionReceiver=new BroadcastReceiver(){public void onReceive(Context c,Intent i){
  BluetoothDevice device=i.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE);
  if(device==null)return;
  boolean known=approved(device.getAddress());
  if(GlassesSetup.meta(device.getName()))lastEvent="address="+device.getAddress()+" state="+i.getIntExtra(BluetoothProfile.EXTRA_STATE,-1)+" approved="+known+" at="+System.currentTimeMillis();
  if(!known)return;
  context.traceVoice("bluetooth_connection","state="+i.getIntExtra(BluetoothProfile.EXTRA_STATE,-1));
  int state=i.getIntExtra(BluetoothProfile.EXTRA_STATE,-1);
  if(state==BluetoothProfile.STATE_CONNECTED){
   connectionMayStart=autoStart.connected(SystemClock.elapsedRealtime());saveAutoStart();
   context.traceVoice("autostart_policy",connectionMayStart?"connection_allowed":"brief_reconnect_suppressed");
   scan(false);observedHinge=-1;
   if(SystemClock.elapsedRealtime()<probeUntil){scan(false);return;}
   Log.i("PixelReconnect","target connected; waiting for glasses audio before starting voice");
   pendingAddress=device.getAddress();context.getSharedPreferences("glasses",0).edit().putString("lastAddress",pendingAddress).apply();
   if(audioCapable(device))context.getSharedPreferences("glasses",0).edit().putString("lastReconnectAddress",pendingAddress).apply();
   pendingVoice=connectionMayStart;voiceDeadline=SystemClock.elapsedRealtime()+15000;
   context.ignoreButtonsUntil=SystemClock.elapsedRealtime()+4000;
   handler.removeCallbacks(startVoice);handler.post(startVoice);
  }else if(state==BluetoothProfile.STATE_DISCONNECTING||state==BluetoothProfile.STATE_DISCONNECTED){
   autoStart.disconnected(SystemClock.elapsedRealtime());
   if(SystemClock.elapsedRealtime()>=probeUntil)scan(true);fastReconnectUntil=SystemClock.elapsedRealtime()+60000;handler.removeCallbacks(check);handler.postDelayed(check,5000);cancelVoiceStart();
   android.media.AudioDeviceInfo active=context.audio.getCommunicationDevice();
   if((active==null||device.getAddress().equalsIgnoreCase(active.getAddress()))&&GlassesDisconnectPolicy.shouldEndVoice(context.communicating,context.bluetoothRequested)){
    Log.i("PixelReconnect","target disconnected; ending glasses voice");context.command("disconnect","bluetooth_disconnect");
   }else Log.i("PixelReconnect","target disconnected; preserving phone voice or idle session");
  }
 }};
 private final Runnable startVoice=new Runnable(){public void run(){
  if(stopped||!pendingVoice||autoStart.suppressed())return;
  android.media.AudioDeviceInfo d=context.bluetoothDevice();
  if(d!=null&&pendingAddress.equalsIgnoreCase(d.getAddress())&&(d.getType()==7||d.getType()==26)){
   pendingVoice=false;Log.i("PixelReconnect","glasses device available; starting capture and verifying route");context.command("auto_talk","bluetooth_reconnect");
  }else if(SystemClock.elapsedRealtime()<voiceDeadline)handler.postDelayed(this,250);
  else{pendingVoice=false;Log.w("PixelReconnect","Audio route did not become ready; not opening phone mic");}
 }};
 void cancelVoiceStart(){pendingVoice=false;handler.removeCallbacks(startVoice);}
 private final Handler handler=new Handler(Looper.getMainLooper());
 private boolean stopped;
 GlassesReconnect(VoiceService context){this.context=context;this.autoStart=new GlassesAutoStartPolicy(context.getSharedPreferences("glasses",0).getBoolean("manualEnd",false));try{for(BluetoothDevice d:context.getSystemService(BluetoothManager.class).getAdapter().getBondedDevices())android.util.Log.i("PixelPair", "bonded name="+d.getName()+" address="+d.getAddress());}catch(SecurityException e){android.util.Log.w("PixelPair","Cannot inspect bonded devices",e);}}
 private final Runnable check=new Runnable(){public void run(){
  if(stopped)return;
  try {
   if(SystemClock.elapsedRealtime()<probeUntil){scan(false);return;}
   if(context.checkSelfPermission("android.permission.BLUETOOTH_CONNECT")!=android.content.pm.PackageManager.PERMISSION_GRANTED)return;
   BluetoothManager manager=context.getSystemService(BluetoothManager.class);
   BluetoothAdapter adapter=manager.getAdapter();
   if(adapter==null||!adapter.isEnabled()){scan(false);return;}
   // Do not connect arbitrary devices or attempt pairing. Association is configured at setup.
   android.content.SharedPreferences preferences=context.getSharedPreferences("glasses",0);
   java.util.List<String> candidates=new java.util.ArrayList<>();
   android.companion.CompanionDeviceManager cdm=context.getSystemService(android.companion.CompanionDeviceManager.class);
   for(android.companion.AssociationInfo info:cdm.getMyAssociations())if(info.getDeviceMacAddress()!=null)candidates.add(info.getDeviceMacAddress().toString());
   String address=GlassesReconnectTarget.select(preferences.getString("lastReconnectAddress",ADDRESS),ADDRESS,candidates);
   if(address==null)return;
   BluetoothDevice device=adapter.getRemoteDevice(address);
   if(device.getBondState()!=BluetoothDevice.BOND_BONDED)return;
   if(adapter.getProfileConnectionState(BluetoothProfile.HEADSET)==BluetoothProfile.STATE_CONNECTED){scan(false);return;}
   scan(true);
   if(adapter.getProfileConnectionState(BluetoothProfile.HEADSET)==BluetoothProfile.STATE_CONNECTING)return;
   long now=SystemClock.elapsedRealtime();
   if(address.equalsIgnoreCase(observedAddress)&&observedHinge==0&&now-hingeSeenAt<20000)return;
   if(now-lastConnectRequest<2000)return;
   lastConnectRequest=now;
   if(Build.VERSION.SDK_INT<37)return;
   // Public since API 37; reflection allows compiling with the existing API 35 SDK.
   Object result=BluetoothDevice.class.getMethod("connect").invoke(device);
   Log.i("PixelReconnect","connect requested result="+result);
  }catch(Exception e){Log.w("PixelReconnect","Reconnect failed",e);}
  finally{if(!stopped)handler.postDelayed(this,SystemClock.elapsedRealtime()<fastReconnectUntil?2000:10000);}
 }};
 void start(){context.registerReceiver(connectionReceiver,new IntentFilter(BluetoothHeadset.ACTION_CONNECTION_STATE_CHANGED),Context.RECEIVER_EXPORTED);handler.postDelayed(check,2000);}
 void stop(){stopped=true;scan(false);cancelVoiceStart();context.unregisterReceiver(connectionReceiver);handler.removeCallbacks(check);}
}
