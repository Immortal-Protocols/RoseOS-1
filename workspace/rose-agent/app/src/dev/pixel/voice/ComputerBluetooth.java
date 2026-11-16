package dev.pixel.voice;

import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothHidDevice;
import android.bluetooth.BluetoothHidDeviceAppSdpSettings;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothProfile;
import android.content.Context;
import android.content.SharedPreferences;
import java.util.concurrent.Executor;
import java.util.concurrent.Executors;

/**
 * Computer Link (docs/COMPUTER-LINK.md): this phone as a Bluetooth keyboard and absolute pointer
 * (ComputerHid) for one paired computer. Registered only while the Computer Link screen exists; the
 * computer pairs with it from its own Bluetooth settings. Reports go only to the connected host.
 */
final class ComputerBluetooth {
 private final Context context;
 private final Runnable changed;
 private final Executor callbacks=Executors.newSingleThreadExecutor();
 private final SharedPreferences prefs;
 private BluetoothAdapter adapter;
 private volatile BluetoothHidDevice hid;
 private volatile BluetoothDevice host;
 private volatile boolean registered;
 private volatile String problem;

 ComputerBluetooth(Context context,Runnable changed){
  this.context=context.getApplicationContext();this.changed=changed;
  prefs=this.context.getSharedPreferences("computer-link",0);
 }

 void start(){
  try{
   BluetoothManager manager=context.getSystemService(BluetoothManager.class);
   adapter=manager==null?null:manager.getAdapter();
   if(adapter==null){problem="This phone has no Bluetooth";changed.run();return;}
   if(!adapter.isEnabled()){problem="Turn on Bluetooth";changed.run();return;}
   problem=null;
   adapter.getProfileProxy(context,new BluetoothProfile.ServiceListener(){
    public void onServiceConnected(int profile,BluetoothProfile proxy){hid=(BluetoothHidDevice)proxy;register();}
    public void onServiceDisconnected(int profile){hid=null;host=null;registered=false;changed.run();}
   },BluetoothProfile.HID_DEVICE);
  }catch(SecurityException e){problem="Allow Nearby devices for Rose Agent";changed.run();}
 }

 private void register(){
  BluetoothHidDevice h=hid;if(h==null)return;
  BluetoothHidDeviceAppSdpSettings sdp=new BluetoothHidDeviceAppSdpSettings("Rose Agent","Rose Computer Link","Rose",BluetoothHidDevice.SUBCLASS1_COMBO,ComputerHid.DESCRIPTOR);
  try{
   if(!h.registerApp(sdp,null,null,callbacks,callback)){problem="Bluetooth keyboard is in use by another app";changed.run();}
  }catch(SecurityException e){problem="Allow Nearby devices for Rose Agent";changed.run();}
 }

 private final BluetoothHidDevice.Callback callback=new BluetoothHidDevice.Callback(){
  @Override public void onAppStatusChanged(BluetoothDevice plugged,boolean nowRegistered){
   registered=nowRegistered;
   if(!nowRegistered)host=null;
   else if(host==null){
    // Reconnect to the computer this phone last served, so pairing is needed only once.
    BluetoothDevice target=plugged!=null?plugged:saved();
    if(target!=null)try{hid.connect(target);}catch(RuntimeException ignored){}
   }
   changed.run();
  }
  @Override public void onConnectionStateChanged(BluetoothDevice device,int state){
   if(state==BluetoothProfile.STATE_CONNECTED){host=device;prefs.edit().putString("host",device.getAddress()).apply();}
   else if(state==BluetoothProfile.STATE_DISCONNECTED&&device.equals(host))host=null;
   changed.run();
  }
  @Override public void onGetReport(BluetoothDevice device,byte type,byte id,int bufferSize){
   BluetoothHidDevice h=hid;if(h==null)return;
   try{h.replyReport(device,type,id,id==ComputerHid.POINTER_ID?new byte[7]:ComputerHid.keyboard(0));}catch(RuntimeException ignored){}
  }
  @Override public void onSetReport(BluetoothDevice device,byte type,byte id,byte[] data){
   // Keyboard LED state from the computer: acknowledged, not used.
   BluetoothHidDevice h=hid;if(h==null)return;
   try{h.reportError(device,BluetoothHidDevice.ERROR_RSP_SUCCESS);}catch(RuntimeException ignored){}
  }
 };

 private BluetoothDevice saved(){
  String address=prefs.getString("host",null);
  if(address==null||adapter==null||!BluetoothAdapter.checkBluetoothAddress(address))return null;
  return adapter.getRemoteDevice(address);
 }

 boolean connected(){return hid!=null&&host!=null;}

 String describe(){
  if(problem!=null)return problem;
  if(hid==null)return "Starting Bluetooth";
  if(!registered)return "Registering as a keyboard and mouse";
  BluetoothDevice h=host;
  if(h==null)return "Not connected. On the Mac: System Settings, Bluetooth, connect this phone";
  String name;try{name=h.getName();}catch(SecurityException e){name=null;}
  return "Connected to "+(name==null?"computer":name);
 }

 boolean send(int id,byte[] data){
  BluetoothHidDevice h=hid;BluetoothDevice d=host;
  if(h==null||d==null)return false;
  try{return h.sendReport(d,id,data);}catch(SecurityException e){return false;}
 }

 private volatile int lastX=-1,lastY=-1;

 boolean pointer(int buttons,int x,int y,int wheel,int pan){
  lastX=x;lastY=y;
  return send(ComputerHid.POINTER_ID,ComputerHid.pointer(buttons,x,y,wheel,pan));
 }

 /** Lift every key and button where the pointer already is. Safe to call at any time. */
 void releaseAll(){
  send(ComputerHid.KEYBOARD_ID,ComputerHid.keyboard(0));
  if(lastX>=0)send(ComputerHid.POINTER_ID,ComputerHid.pointer(0,lastX,lastY,0,0));
 }

 void stop(){
  BluetoothHidDevice h=hid;
  if(h!=null){
   releaseAll();
   try{h.unregisterApp();}catch(RuntimeException ignored){}
   if(adapter!=null)adapter.closeProfileProxy(BluetoothProfile.HID_DEVICE,h);
  }
  hid=null;host=null;registered=false;
 }
}
