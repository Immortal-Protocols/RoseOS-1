package dev.pixel.voice;

import android.app.Activity;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.companion.AssociationInfo;
import android.companion.AssociationRequest;
import android.companion.BluetoothDeviceFilter;
import android.companion.CompanionDeviceManager;
import android.content.Context;
import android.content.IntentSender;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import org.json.JSONArray;
import org.json.JSONObject;

/** Explicit registration of an already connected pair; never pairs arbitrary devices. */
final class GlassesSetup {
 static boolean meta(String name) {
  String n=name==null?"":name.toLowerCase(Locale.ROOT);
  return n.startsWith("rb meta")||n.startsWith("ray-ban meta")||n.startsWith("oakley meta");
 }
 static List<AudioDeviceInfo> connected(Context c) {
  List<AudioDeviceInfo> found=new ArrayList<>();
  for(AudioDeviceInfo d:c.getSystemService(AudioManager.class).getAvailableCommunicationDevices())
   if(VoiceService.isBluetooth(d)&&meta(d.getProductName().toString()))found.add(d);
  return found;
 }
 static JSONObject status(Context c)throws Exception {
  JSONArray associations=new JSONArray(),devices=new JSONArray();
  for(AssociationInfo a:c.getSystemService(CompanionDeviceManager.class).getMyAssociations())
   associations.put(new JSONObject().put("id",a.getId()).put("address",String.valueOf(a.getDeviceMacAddress())));
  for(AudioDeviceInfo d:connected(c))devices.put(new JSONObject().put("name",d.getProductName().toString()).put("address",d.getAddress()));
  return new JSONObject().put("associations",associations).put("connectedGlasses",devices)
   .put("lastEvent",GlassesReconnect.lastEvent);
 }
 static void begin(Activity activity) {
  try {
   List<AudioDeviceInfo> devices=connected(activity);
   if(devices.size()!=1){toast(activity,"Connect just the glasses you want to set up, then try again.");return;}
   AudioDeviceInfo selected=devices.get(0);String address=selected.getAddress();
   if(!BluetoothAdapter.checkBluetoothAddress(address)){toast(activity,"Glasses address is unavailable.");return;}
   BluetoothDevice device=activity.getSystemService(android.bluetooth.BluetoothManager.class).getAdapter().getRemoteDevice(address);
   if(device.getBondState()!=BluetoothDevice.BOND_BONDED){toast(activity,"Pair these glasses in Bluetooth settings first.");return;}
   CompanionDeviceManager manager=activity.getSystemService(CompanionDeviceManager.class);
   for(AssociationInfo info:manager.getMyAssociations())if(info.getDeviceMacAddress()!=null&&address.equalsIgnoreCase(info.getDeviceMacAddress().toString())){
    toast(activity,"These glasses are already registered for auto-start.");return;
   }
   AssociationRequest request=new AssociationRequest.Builder().addDeviceFilter(new BluetoothDeviceFilter.Builder().setAddress(address).build()).setSingleDevice(true).build();
   manager.associate(request,new CompanionDeviceManager.Callback(){
    @Override public void onAssociationPending(IntentSender sender){
     try{activity.startIntentSenderForResult(sender,81,null,0,0,0);}catch(Exception e){toast(activity,"Could not open glasses registration: "+e.getClass().getSimpleName());}
    }
    @Override public void onAssociationCreated(AssociationInfo info){
     if(info.getDeviceMacAddress()!=null&&address.equalsIgnoreCase(info.getDeviceMacAddress().toString())){
      activity.getSharedPreferences("glasses",0).edit().putString("lastAddress",address).putString("lastReconnectAddress",address).apply();
      toast(activity,"Glasses registered. Reconnect them to test auto-start.");
     }
    }
    @Override public void onFailure(CharSequence error){toast(activity,"Glasses registration did not finish: "+error);}
   },new Handler(Looper.getMainLooper()));
  }catch(Exception e){toast(activity,"Glasses setup unavailable: "+e.getClass().getSimpleName());}
 }
 static void toast(Activity a,String text){a.runOnUiThread(()->Toast.makeText(a,text,Toast.LENGTH_LONG).show());}
}
