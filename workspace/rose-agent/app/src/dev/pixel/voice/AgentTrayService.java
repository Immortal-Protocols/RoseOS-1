package dev.pixel.voice;

import android.app.Service;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.*;
import java.util.*;

/** Narrow, platform-SystemUI-only control surface. Never exposes conversation content. */
public final class AgentTrayService extends Service {
 private static volatile AgentTrayService instance;
 private long lastRegisterAt;
 @Override public void onCreate(){super.onCreate();instance=this;TrayReturn.INSTANCE.attach(returnPort);}
 /** 1C: the shell's return target and answers arrive on this link (TrayReturn); Rose sends only to that client. */
 private final TrayReturn.Port returnPort=new TrayReturn.Port(){
  public boolean send(Object client,int what,int request,long token){
   Messenger to=clients.get(client);
   if(to==null)return false;
   Message m=Message.obtain(null,what,request,0);
   if(what==TrayReturn.RETURN){Bundle b=new Bundle();b.putLong("token",token);m.setData(b);}
   try{to.send(m);return true;}catch(RemoteException e){main.post(()->removeClient((IBinder)client));return false;}
  }
  public void schedule(Runnable task,long delayMs){main.postDelayed(task,delayMs);}
 };
 static org.json.JSONObject diagnostics() throws org.json.JSONException {
  AgentTrayService service=instance;
  VoiceService voice=VoiceService.instance;
  org.json.JSONObject result=new org.json.JSONObject().put("serviceCreated",service!=null);
  if(service!=null)result.put("registeredClients",service.clients.size()).put("lastRegisterAt",service.lastRegisterAt);
  if(voice!=null)result.put("listening",voice.live).put("inputLevel",voice.trayInputLevel).put("outputLevel",voice.trayOutputLevel).put("levelAgeMs",voice.trayAudioObservedAt==0?-1:SystemClock.elapsedRealtime()-voice.trayAudioObservedAt);
  return result;
 }
 public static final int REGISTER=1, UNREGISTER=2, START=3, MUTE=4, UNMUTE=5, END=6, STATE=100;
 private final Handler main=new Handler(Looper.getMainLooper());
 private final Map<IBinder,Messenger> clients=new HashMap<>();
 private final Map<IBinder,IBinder.DeathRecipient> deaths=new HashMap<>();
 /** F08 (RoseChin): registered clients whose latest REGISTER declared the Rose chin reachable. */
 private final Set<IBinder> chinReachable=new HashSet<>();
 private final Messenger endpoint=new Messenger(new Handler(Looper.getMainLooper()) {
  @Override public void handleMessage(Message m){receive(m);}
 });
 private String lastState="";
 private final Runnable poll=new Runnable(){public void run(){publish(false);if(!clients.isEmpty())main.postDelayed(this,80);}};
 @Override public IBinder onBind(Intent intent){return endpoint.getBinder();}
 private boolean trusted(int uid){
  if(uid<0||!UserHandle.getUserHandleForUid(uid).equals(android.os.Process.myUserHandle()))return false;
  PackageManager pm=getPackageManager();
  if(checkPermission("android.permission.STATUS_BAR_SERVICE",-1,uid)!=PackageManager.PERMISSION_GRANTED)return false;
  String[] packages=pm.getPackagesForUid(uid);
  return packages!=null&&Arrays.asList(packages).contains("com.android.systemui")&&
    pm.checkSignatures("android","com.android.systemui")==PackageManager.SIGNATURE_MATCH;
 }
 private void receive(Message m){
  if(!trusted(m.sendingUid))return;
  if(m.what==REGISTER&&m.replyTo!=null){
   lastRegisterAt=SystemClock.elapsedRealtime();
   IBinder binder=m.replyTo.getBinder();
   if(!clients.containsKey(binder)){
    IBinder.DeathRecipient death=()->main.post(()->removeClient(binder));
    try{binder.linkToDeath(death,0);}catch(RemoteException e){return;}
    deaths.put(binder,death);clients.put(binder,m.replyTo);
   }
   if(RoseChin.declaresReachable(m.what,m.arg2))chinReachable.add(binder);else chinReachable.remove(binder);
   send(m.replyTo,state(this));
   main.removeCallbacks(poll);main.post(poll);return;
  }
  if(m.what==UNREGISTER&&m.replyTo!=null){removeClient(m.replyTo.getBinder());return;}
  // 1C: only a registered client's target counts; it names the client Rose's RETURN goes to.
  if(m.what==TrayReturn.TARGET){
   IBinder from=m.replyTo==null?null:m.replyTo.getBinder();
   if(from==null||!clients.containsKey(from))return;
   Bundle d=m.getData();
   TrayReturn.INSTANCE.target(from,d.getInt("protocolVersion"),d.getLong("token"),d.getString("package"),
     d.getInt("userId",-1),d.getString("label"),android.os.Process.myUid()/100000,getPackageName());
   return;
  }
  if(m.what==TrayReturn.RESULT){TrayReturn.INSTANCE.result(m.arg1,m.arg2);return;}
  // Rose's microphone-input mute (RoseMicInput): recorded, and applied to a live call; idle it starts nothing.
  if(m.what==MUTE||m.what==UNMUTE){RoseMicState.request(this,m.what==MUTE);publish(true);return;}
  String action=m.what==START?"talk":m.what==END?"end":null;
  if(action==null)return;
  VoiceService voice=VoiceService.instance;
  if(voice!=null)voice.command(action);
  else if(m.what==START){try{startForegroundService(new Intent(this,VoiceService.class).setAction(action));}catch(RuntimeException e){android.util.Log.w("AgentTray","Voice start unavailable",e);}}
  publish(true);
 }
 /** F08: true while a registered trusted client has declared the Rose chin reachable (RoseChin). */
 static boolean chinReachable(){AgentTrayService service=instance;return service!=null&&!service.chinReachable.isEmpty();}
 /** True while the platform SystemUI tray client is bound; the app-side glow then stays hidden. */
 static boolean systemTrayRegistered(){AgentTrayService service=instance;return service!=null&&!service.clients.isEmpty();}
 /** v1 state from the same ConversationState the conversation surface renders; no status-text inference. */
 static Bundle state(){return state(instance);}
 /** v1 state; "muted" is Rose's microphone-input mute as every surface shows it (RoseMicInput.shown). */
 static Bundle state(android.content.Context context){
  VoiceService v=VoiceService.instance;
  ConversationState c=v==null?ConversationState.of(false,false,false,false,false,null,null,null)
   :ConversationState.of(true,v.live,v.connected,v.muted,v.communicating,v.lastEndReason,v.lastStartError,v.backendReachable);
  Bundle b=new Bundle();b.putInt("protocolVersion",1);b.putBoolean("available",true);
  b.putBoolean("connected",c.trayConnected());b.putBoolean("listening",c.trayListening());
  b.putBoolean("muted",context==null?c.trayMuted():RoseMicInput.shown(c.active(),c.muted,RoseMicState.muted(context)));
  boolean fresh=v!=null&&SystemClock.elapsedRealtime()-v.trayAudioObservedAt<1200;
  b.putFloat("inputLevel",fresh&&c.trayListening()?v.trayInputLevel:0f);
  b.putFloat("outputLevel",fresh&&c.active()?v.trayOutputLevel:0f);
  b.putLong("audioObservedAt",v==null?0L:v.trayAudioObservedAt);
  b.putString("status",c.trayStatus(v!=null));
  return b;
 }
 private String signature(Bundle b){return b.getBoolean("connected")+":"+b.getBoolean("listening")+":"+b.getBoolean("muted")+":"+b.getString("status")+":"+Math.round(b.getFloat("inputLevel")*50)+":"+Math.round(b.getFloat("outputLevel")*50)+":"+(b.getLong("audioObservedAt")/400);}
 private void publish(boolean force){
  Bundle b=state(this);String next=signature(b);if(!force&&lastState.equals(next))return;lastState=next;
  for(Messenger client:new ArrayList<>(clients.values()))send(client,b);
 }
 private void send(Messenger client,Bundle data){
  Message response=Message.obtain(null,STATE);response.setData(new Bundle(data));
  try{client.send(response);}catch(RemoteException e){removeClient(client.getBinder());}
 }
 private void removeClient(IBinder binder){
  clients.remove(binder);chinReachable.remove(binder);TrayReturn.INSTANCE.lost(binder);IBinder.DeathRecipient death=deaths.remove(binder);
  if(death!=null)binder.unlinkToDeath(death,0);
  if(clients.isEmpty())main.removeCallbacks(poll);
 }
 @Override public void onDestroy(){if(instance==this)instance=null;TrayReturn.INSTANCE.lost(null);for(IBinder binder:new ArrayList<>(clients.keySet()))removeClient(binder);main.removeCallbacksAndMessages(null);super.onDestroy();}
}
