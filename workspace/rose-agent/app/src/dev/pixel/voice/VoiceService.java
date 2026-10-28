package dev.pixel.voice;
import android.app.*;import android.os.*;import android.webkit.*;import android.media.*;import android.content.*;import android.content.pm.PackageManager;import android.view.*;import java.io.*;import java.net.*;import java.security.*;import java.security.cert.*;import javax.net.ssl.*;import org.json.*;import java.util.concurrent.*;
public class VoiceService extends Service {

 static volatile VoiceService instance;
 void setServerEndpoint(String e){if(!e.matches("https://[A-Za-z0-9.:-]+"))return;endpoint=e;getSharedPreferences("MainActivity",0).edit().putString("endpoint",e).apply();runOnUiThread(()->{if(phoneCommandStream!=null){phoneCommandStream.stop();phoneCommandStream=new PhoneCommandStream(VoiceService.this);phoneCommandStream.start();}});}

 volatile boolean typedSending=false;
 volatile String typedError="",typedLastSent="";
 volatile long typedFinished=0, conversationRevision=-1;
 volatile String taskModelLabel="Model connecting…";
 volatile String taskStateJson="{\"version\":1,\"current\":null}";
 volatile boolean taskStateAvailable=false,taskStateStreamConnected=false;
 volatile long taskStateReceivedAt=0;
 volatile String conversationJson="{\"messages\":[]}";
 synchronized boolean submitText(String text){
  text=text.trim();if(typedSending||text.isEmpty()||text.length()>12000)return false;
  if(!guardianName.isEmpty()){final String guardianText=text;runOnUiThread(()->{web.evaluateJavascript("window.sendGuardianTyped("+JSONObject.quote(guardianText)+")",null);typedLastSent=guardianText;typedFinished=System.currentTimeMillis();});return true;}
  final String requestText=text;typedSending=true;typedError="";
  requests.submit(()->{
   try{
    HttpsURLConnection c=(HttpsURLConnection)new URL(endpoint+"/task").openConnection();
    c.setSSLSocketFactory(ssl);c.setHostnameVerifier((h,session)->true);
    c.setConnectTimeout(6000);c.setReadTimeout(35000);c.setRequestMethod("POST");
    c.setRequestProperty("Content-Type","application/json");c.setDoOutput(true);
    try(OutputStream out=c.getOutputStream()){out.write(new JSONObject().put("text",requestText).put("appContext",PhoneAppContext.read(this)).toString().getBytes("UTF-8"));}
    int code=c.getResponseCode();String response=read(code<400?c.getInputStream():c.getErrorStream());c.disconnect();
    if(code>=400)throw new IOException(new JSONObject(response).optString("error","Request failed"));
    typedLastSent=requestText;
   }catch(Exception e){typedError="Could not confirm sending. Check task status before retrying."; }
   finally{typedFinished=System.currentTimeMillis();typedSending=false;}
  });
  return true;
 }

 volatile String status="Connecting…",task="",guardianName="",pendingCommand;
 volatile boolean connected=false,live=false,muted=false;
 // Typed voice facts from the web client (Bridge.voiceLifecycle/backendReachable), read by the conversation surface.
 volatile String lastEndReason="",lastStartError="";volatile Boolean backendReachable;
 volatile float level=0, trayInputLevel=0, trayOutputLevel=0;
 volatile long trayAudioObservedAt=0;
 GlassesReconnect glassesReconnect;
 StatusGlowOverlay statusGlow;
 GlassesTransportTrace glassesTransportTrace;
 VoiceLifecycleTrace voiceLifecycle;
 void traceVoice(String kind,String detail){if(voiceLifecycle!=null)voiceLifecycle.record(kind,detail);}
 boolean loaded=false;long ignoreButtonsUntil=0;
 final Handler main=new Handler(Looper.getMainLooper());
 private final Runnable appContextChanged=this::publishAppContext;
 private void publishAppContext(){if(loaded&&live&&web!=null)web.evaluateJavascript("window.refreshAppContext?.()",null);}
 void appWindowChanged(){main.removeCallbacks(appContextChanged);main.postDelayed(appContextChanged,100);}
 void runOnUiThread(Runnable r){main.post(r);}
 public IBinder onBind(Intent i){return null;}
 public int onStartCommand(Intent i,int flags,int id){if(i!=null&&"podcast_open".equals(i.getAction()))receivePodcast(i.getStringExtra("context"));if(i!=null&&Intent.ACTION_MEDIA_BUTTON.equals(i.getAction()))handleMediaButton(i);else if(i!=null&&i.getAction()!=null)command(i.getAction(),"intent");return START_STICKY;}
 void command(String c){command(c,"internal");}
 void command(String c,String origin){traceVoice("command",origin+":"+c);runOnUiThread(()->{
 if(glassesReconnect!=null){if("end".equals(c)&&!"capture_check".equals(origin))glassesReconnect.userEnded();else if("talk".equals(c)||"glasses".equals(c))glassesReconnect.userStarted();else if("auto_talk".equals(c)&&glassesReconnect.suppressed()){traceVoice("autostart_policy","late_start_suppressed");return;}}if("end".equals(c)){ignoreButtonsUntil=SystemClock.elapsedRealtime()+400;if(glassesReconnect!=null)glassesReconnect.cancelVoiceStart();}if(!loaded){pendingCommand=c;if(voicePageHeld&&("talk".equals(c)||"glasses".equals(c))){voicePageHeld=false;voiceRenderer.reset();web.loadUrl(VOICE_PAGE);}return;}String script="toggle_conversation_mode".equals(c)?"window.toggleConversationMode()":"toggle_task_widgets".equals(c)?"window.toggleTaskWidgets()":"toggle_decisions_routing".equals(c)?"window.toggleDecisionsRouting()":"guardian_back".equals(c)?"document.getElementById('guardianBack').click()":"podcast_open".equals(c)?"window.openPodcastCompanion()":"book_open".equals(c)?"window.openBookCompanion()":"sky_open".equals(c)?"window.openSkyGuide()":"auto_talk".equals(c)?"window.autoStartFromGlasses()":"glasses".equals(c)?"window.glassesTap()":"talk".equals(c)?"window.startFromGlasses()":"mute".equals(c)?"document.getElementById('mute').click()":"tray_mute".equals(c)?"window.setTrayMuted(true)":"tray_unmute".equals(c)?"window.setTrayMuted(false)":"end".equals(c)?"endConversation()":"disconnect".equals(c)?"cleanup()":"";if(!script.isEmpty())web.evaluateJavascript(script,null);});}
 void setResultCardMode(String mode){if(!java.util.Arrays.asList("html","native","text").contains(mode))return;runOnUiThread(()->{if(loaded)web.evaluateJavascript("window.setResultCardMode("+JSONObject.quote(mode)+")",null);});}
 void connectGuardian(String name){runOnUiThread(()->{if(loaded)web.evaluateJavascript("window.connectGuardian("+JSONObject.quote(name)+")",null);});}
 PendingIntent action(String a){return PendingIntent.getService(this,a.hashCode(),new Intent(this,VoiceService.class).setAction(a),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);}
 void notification(boolean micOn){shownAgentState="";agentNotification();}
 String shownAgentState="";
 void agentNotification(){
  String state=live&&connected?(muted?"Agent on · Microphone muted":"Agent on · Listening"):communicating?"Agent connecting · Not ready":"Agent off · Microphone off";
  if(state.equals(shownAgentState))return;shownAgentState=state;
  NotificationManager nm=getSystemService(NotificationManager.class);
  NotificationChannel channel=new NotificationChannel("agent_indicator","Agent status",NotificationManager.IMPORTANCE_DEFAULT);channel.setSound(null,null);channel.enableVibration(false);channel.setShowBadge(false);nm.createNotificationChannel(channel);
  int icon=live&&connected?(muted?R.drawable.agent_muted:R.drawable.agent_listening):communicating?R.drawable.agent_connecting:R.drawable.agent_off;
  Notification.Builder b=new Notification.Builder(this,"agent_indicator").setSmallIcon(icon).setContentTitle(state).setContentText("Rose Agent").setCategory(Notification.CATEGORY_STATUS).setOngoing(true).setOnlyAlertOnce(true).setShowWhen(false).setVisibility(Notification.VISIBILITY_PUBLIC).setContentIntent(PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE));
  if(live&&connected)b.addAction(new Notification.Action.Builder(null,muted?"Unmute":"Mute",action(muted?"tray_unmute":"tray_mute")).build());
  else b.addAction(new Notification.Action.Builder(null,"Talk",action("talk")).build());
  if(live||communicating)b.addAction(new Notification.Action.Builder(null,"End",action("end")).build());
  int type=android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE;if(communicating){type|=android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE;if(PhoneEnvironment.permitted(this))type|=android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION;}
  startForeground(41,b.build(),type);nm.cancel(42);
 }
 String pendingInstallId,pendingInstallLabel;
 android.media.session.MediaSession mediaSession;
 volatile String lastButton="None";final GlassesButtonGate buttonGate=new GlassesButtonGate();
 void recordButton(String event){lastButton=(lastButton+" | "+System.currentTimeMillis()+" "+event);if(lastButton.length()>1200)lastButton=lastButton.substring(lastButton.length()-1200);android.util.Log.i("PixelButton",lastButton);}
 void startFromGlasses(){if(SystemClock.elapsedRealtime()<ignoreButtonsUntil){recordButton("Ignored post-End audio transition");return;}android.util.Log.i("PixelTiming","glasses_button uptime="+SystemClock.elapsedRealtime());command("glasses");}
 boolean doubleTapMutes(){AudioDeviceInfo d=bluetoothDevice();return d!=null&&getSharedPreferences("glasses-controls",0).getBoolean(d.getProductName().toString()+"-double-tap-mute",false);}
 void nextButton(){if(doubleTapMutes()){recordButton("double tap toggles mute");startFromGlasses();}else command("end");}
 boolean handleMediaButton(Intent intent){android.view.KeyEvent event=intent.getParcelableExtra(Intent.EXTRA_KEY_EVENT);if(event==null)return false;int key=event.getKeyCode();recordButton(android.view.KeyEvent.keyCodeToString(key)+" action="+event.getAction()+" repeats="+event.getRepeatCount());if(key==KeyEvent.KEYCODE_MEDIA_NEXT){if(buttonGate.accept(key,event.getAction(),event.getRepeatCount(),event.getDownTime(),SystemClock.elapsedRealtime()))nextButton();return true;}if(key!=KeyEvent.KEYCODE_MEDIA_PLAY&&key!=KeyEvent.KEYCODE_MEDIA_PAUSE&&key!=KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE&&key!=KeyEvent.KEYCODE_HEADSETHOOK)return false;if(buttonGate.accept(key,event.getAction(),event.getRepeatCount(),event.getDownTime(),SystemClock.elapsedRealtime())){recordButton("accepted tap");startFromGlasses();}return true;}
 void activateMediaButtons(){if(mediaSession==null)return;mediaSession.setMetadata(new android.media.MediaMetadata.Builder().putString(android.media.MediaMetadata.METADATA_KEY_TITLE,"Rose Agent").build());mediaSession.setPlaybackState(new android.media.session.PlaybackState.Builder().setActions(android.media.session.PlaybackState.ACTION_PLAY|android.media.session.PlaybackState.ACTION_PAUSE|android.media.session.PlaybackState.ACTION_PLAY_PAUSE|android.media.session.PlaybackState.ACTION_SKIP_TO_NEXT).setState(android.media.session.PlaybackState.STATE_PLAYING,0,1,SystemClock.elapsedRealtime()).build());mediaSession.setActive(true);}
 void refreshGlassesButtons(){runOnUiThread(()->{if(mediaSession==null)return;mediaSession.setActive(false);activateMediaButtons();recordButton("media-button session refreshed");});}
 void setupMediaButtons(){mediaSession=new android.media.session.MediaSession(this,"PixelVoiceButtons");mediaSession.setFlags(android.media.session.MediaSession.FLAG_HANDLES_MEDIA_BUTTONS|android.media.session.MediaSession.FLAG_HANDLES_TRANSPORT_CONTROLS);mediaSession.setCallback(new android.media.session.MediaSession.Callback(){
 public boolean onMediaButtonEvent(Intent intent){return handleMediaButton(intent);}
 public void onPlay(){recordButton("PLAY");startFromGlasses();}public void onPause(){recordButton("PAUSE");startFromGlasses();}public void onSkipToNext(){recordButton("NEXT");if(buttonGate.accept(KeyEvent.KEYCODE_MEDIA_NEXT,0,0,0,SystemClock.elapsedRealtime()))nextButton();}public void onSkipToPrevious(){recordButton("PREVIOUS");}
 });mediaSession.setPlaybackState(new android.media.session.PlaybackState.Builder().setActions(android.media.session.PlaybackState.ACTION_PLAY|android.media.session.PlaybackState.ACTION_PAUSE|android.media.session.PlaybackState.ACTION_PLAY_PAUSE|android.media.session.PlaybackState.ACTION_SKIP_TO_NEXT|android.media.session.PlaybackState.ACTION_SKIP_TO_PREVIOUS).setState(android.media.session.PlaybackState.STATE_PLAYING,0,1).build());mediaSession.setMediaButtonReceiver(PendingIntent.getBroadcast(this,73,new Intent(Intent.ACTION_MEDIA_BUTTON).setComponent(new ComponentName(this,MediaButtonReceiver.class)),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_MUTABLE));activateMediaButtons();}

 VoiceDucking voiceDucking;
 volatile String outputBoostState="not_playing";
 boolean voiceBoostEnabled(){return getSharedPreferences("voice-output-boost",0).getBoolean("enabled",false);}
 void setVoiceBoostEnabled(boolean enabled){getSharedPreferences("voice-output-boost",0).edit().putBoolean("enabled",enabled).apply();if(web!=null)web.evaluateJavascript("window.updateVoiceBoost&&window.updateVoiceBoost("+enabled+")",null);}

 volatile boolean replySpeaking=false;
 void sendReplyActivity(boolean speaking){replySpeaking=speaking;if(voiceDucking!=null)voiceDucking.sample(speaking);Intent event=new Intent("dev.pixel.podcasts.VOICE_SPEAKING").setPackage("dev.pixel.podcasts").putExtra("speaking",speaking);sendBroadcast(event);}
 float conversationVolume=-1; long volumeGuardUntil=0;
 int volumeRestoreGeneration=0;
 boolean volumeRouteReady(){
  if(!communicating||audio==null||requestedRoute<0)return false;
  AudioDeviceInfo d=audio.getCommunicationDevice();
  return d!=null&&d.getId()==requestedRoute&&(!bluetoothRequested||isBluetooth(d));
 }
 boolean mayLearnVolume(){return volumeRouteReady()&&SystemClock.elapsedRealtime()>volumeGuardUntil;}
 void freezeVolume(){volumeRestoreGeneration++;volumeGuardUntil=Long.MAX_VALUE;}

 void restoreConversationVolume(){if(!volumeRouteReady()||conversationVolume<0)return;volumeGuardUntil=Math.max(volumeGuardUntil,SystemClock.elapsedRealtime()+350);for(int stream:new int[]{AudioManager.STREAM_VOICE_CALL,AudioManager.STREAM_MUSIC}){int value=Math.round(conversationVolume*audio.getStreamMaxVolume(stream));value=Math.max(audio.getStreamMinVolume(stream),value);if(stream==AudioManager.STREAM_MUSIC&&voiceDucking!=null&&voiceDucking.updateMediaBaseline(value))continue;if(audio.getStreamVolume(stream)!=value)audio.setStreamVolume(stream,value,0);}android.util.Log.i("PixelVolume","restored call="+audio.getStreamVolume(0)+" media="+audio.getStreamVolume(3));}
 final Runnable volumeMonitor=new Runnable(){public void run(){if(mayLearnVolume()){float value=(float)audio.getStreamVolume(0)/audio.getStreamMaxVolume(0);if(Math.abs(value-conversationVolume)>.02f){conversationVolume=value;getSharedPreferences("voice-volume-v2",0).edit().putFloat("level",value).apply();restoreConversationVolume();}}main.postDelayed(this,250);}};
 void scheduleVolumeRestore(){
  if(!volumeRouteReady())return;
  final int generation=++volumeRestoreGeneration;
  volumeGuardUntil=SystemClock.elapsedRealtime()+2500;
  for(int delay:new int[]{0,250,750,1500,2200})main.postDelayed(()->{
   if(generation==volumeRestoreGeneration&&volumeRouteReady())restoreConversationVolume();
  },delay);
 }
 String pendingPodcastContext;
 final java.util.Set<String> podcastUploads=ConcurrentHashMap.newKeySet();
 final java.util.Set<String> podcastUploaded=ConcurrentHashMap.newKeySet();
 volatile String podcastAudioKey="";
 volatile boolean podcastAudioEnabled=false;
 void uploadPodcastAudio(JSONObject d){
  String source=d.optString("audioUrl"),version=d.optString("audioVersion"),uri=d.optString("audioUri"),key=source+"|"+version;
  podcastAudioKey=key;podcastAudioEnabled=d.optBoolean("active")&&d.optBoolean("playing")&&d.optBoolean("foreground");
  if(!podcastAudioEnabled||!d.optBoolean("localAudio")||!"unavailable".equals(d.optString("transcriptStatus"))||ssl==null||podcastUploaded.contains(key)||!uri.startsWith("content://dev.pixel.podcasts.provider/")||!podcastUploads.add(key))return;
  requests.submit(()->{
   HttpsURLConnection connection=null;
   try{
    connection=(HttpsURLConnection)new URL(endpoint+"/podcast/audio?source="+URLEncoder.encode(source,"UTF-8")+"&version="+URLEncoder.encode(version,"UTF-8")).openConnection();
    connection.setSSLSocketFactory(ssl);connection.setHostnameVerifier((h,s)->true);connection.setConnectTimeout(6000);connection.setReadTimeout(35000);connection.setRequestMethod("POST");connection.setRequestProperty("Content-Type","application/octet-stream");connection.setChunkedStreamingMode(65536);connection.setDoOutput(true);
    try(InputStream in=getContentResolver().openInputStream(android.net.Uri.parse(uri));OutputStream out=connection.getOutputStream()){
     byte[] buffer=new byte[65536];int n;long total=0;
     while((n=in.read(buffer))!=-1){if(!podcastAudioEnabled||!key.equals(podcastAudioKey))throw new IOException("Podcast stopped");total+=n;if(total>300000000)throw new IOException("Podcast too large");out.write(buffer,0,n);}
    }
    int code=connection.getResponseCode();if(code!=200)throw new IOException("Audio transfer failed "+code);read(connection.getInputStream());podcastUploaded.add(key);android.util.Log.i("PixelPodcastBridge","local audio transferred");
   }catch(Exception e){android.util.Log.w("PixelPodcastBridge","Local podcast transfer stopped: "+e.getClass().getSimpleName());}
   finally{if(connection!=null)connection.disconnect();podcastUploads.remove(key);}
  });
 }
 void receivePodcast(String raw){
  if(raw==null||raw.length()>40000)return;
  try{JSONObject d=new JSONObject(raw);if(!"Pixel Podcasts".equals(d.optString("app"))||Math.abs(System.currentTimeMillis()-d.optLong("observedAt"))>15000)return;
   uploadPodcastAudio(d);pendingPodcastContext=d.toString();if(loaded)web.evaluateJavascript("window.receivePodcastContext("+pendingPodcastContext+")",result->{if(d.has("requestId"))android.util.Log.i("PixelPodcastBridge","accepted="+result+" request="+d.optString("requestId")+" playing="+d.optBoolean("playing")+" transcript="+d.optString("transcriptStatus")+" segments="+(d.optJSONArray("segments")==null?0:d.optJSONArray("segments").length())+" position="+d.optLong("positionMs"));});
  }catch(Exception ignored){}
 }
 final BroadcastReceiver podcastReceiver=new BroadcastReceiver(){public void onReceive(Context c,Intent i){receivePodcast(i.getStringExtra("context"));}};
 BookNarrator bookNarrator;
 final BroadcastReceiver bookReceiver=new BroadcastReceiver(){public void onReceive(Context c,Intent i){
  if(!loaded)return;String raw=i.getStringExtra("context");if(raw==null||raw.length()>30000)return;
  try{JSONObject d=new JSONObject(raw);if(!"Pixel Books".equals(d.optString("app"))||Math.abs(System.currentTimeMillis()-d.optLong("observedAt"))>15000)return;
   web.evaluateJavascript("window.receiveBookContext("+d.toString()+")",null);
  }catch(Exception ignored){}
 }};
 final BroadcastReceiver skyReceiver=new BroadcastReceiver(){public void onReceive(Context c,Intent i){
  if(!"dev.pixel.voice.SKY_CONTEXT".equals(i.getAction())||!loaded)return;
  String raw=i.getStringExtra("context");if(raw==null||raw.length()>12000)return;
  try{JSONObject data=new JSONObject(raw);if(Math.abs(System.currentTimeMillis()-data.optLong("observedAt"))>10000)return;
   web.evaluateJavascript("window.receiveSkyContext("+data.toString()+")",result->android.util.Log.i("PixelSky","context accepted="+result));
  }catch(Exception ignored){}
 }};
 final BroadcastReceiver volumeReceiver=new BroadcastReceiver(){public void onReceive(Context c,Intent i){if(!mayLearnVolume())return;int stream=i.getIntExtra("android.media.EXTRA_VOLUME_STREAM_TYPE",-1);if(stream!=AudioManager.STREAM_VOICE_CALL)return;int value=i.getIntExtra("android.media.EXTRA_VOLUME_STREAM_VALUE",-1);if(value<0)return;conversationVolume=(float)value/audio.getStreamMaxVolume(stream);getSharedPreferences("voice-volume-v2",0).edit().putFloat("level",conversationVolume).apply();restoreConversationVolume();}};
 PowerManager.WakeLock voiceLock;
 volatile boolean communicating=false;
 volatile int requestedRoute=-1; int routeGeneration=0;
 boolean bluetoothRequested=false;
 AudioDeviceInfo bluetoothDevice(){for(AudioDeviceInfo d:audio.getAvailableCommunicationDevices())if(isBluetooth(d))return d;return null;}
 static boolean isBluetooth(AudioDeviceInfo d){return d!=null&&(d.getType()==AudioDeviceInfo.TYPE_BLUETOOTH_SCO||d.getType()==AudioDeviceInfo.TYPE_BLE_HEADSET);}
 AudioDeviceCallback routeCallback=new AudioDeviceCallback(){public void onAudioDevicesAdded(AudioDeviceInfo[] d){if(communicating)routeAudio();}public void onAudioDevicesRemoved(AudioDeviceInfo[] d){if(communicating)routeAudio();}};
 AudioTrack recognitionMedia;
 void prepareRecognitionMedia(){
  audio.clearCommunicationDevice();audio.setMode(AudioManager.MODE_NORMAL);
  if(recognitionMedia!=null)return;
  try{recognitionMedia=new AudioTrack.Builder().setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build()).setAudioFormat(new AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT).setSampleRate(48000).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build()).setBufferSizeInBytes(9600).setTransferMode(AudioTrack.MODE_STATIC).build();recognitionMedia.write(new short[4800],0,4800);recognitionMedia.setLoopPoints(0,4800,-1);recognitionMedia.play();}
  catch(RuntimeException e){android.util.Log.w("PixelTiming","recognition media setup failed",e);if(recognitionMedia!=null){recognitionMedia.release();recognitionMedia=null;}}
 }
 android.bluetooth.BluetoothHeadset recognitionHeadset;
 android.bluetooth.BluetoothDevice recognitionDevice;
 volatile boolean recognitionOpening=false; boolean recognitionAttempted=false;volatile boolean recognitionAccepted=false;
 int recognitionGeneration=0;
 void closeHeadsetRecognition(){
  recognitionGeneration++;recognitionOpening=false;recognitionAttempted=false;recognitionAccepted=false;
  if(recognitionHeadset!=null){try{if(recognitionDevice!=null)recognitionHeadset.stopVoiceRecognition(recognitionDevice);}catch(RuntimeException e){android.util.Log.w("PixelTiming","headset recognition cleanup",e);}
   getSystemService(android.bluetooth.BluetoothManager.class).getAdapter().closeProfileProxy(android.bluetooth.BluetoothProfile.HEADSET,recognitionHeadset);}
  recognitionHeadset=null;recognitionDevice=null;
  if(recognitionMedia!=null){recognitionMedia.release();recognitionMedia=null;}
 }
 void openHeadsetRecognition(){
  recognitionOpening=true;final int generation=++recognitionGeneration;
  prepareRecognitionMedia();
  android.util.Log.i("PixelTiming","recognition initial_mode="+audio.getMode());
  android.bluetooth.BluetoothAdapter adapter=getSystemService(android.bluetooth.BluetoothManager.class).getAdapter();
  boolean requested=adapter!=null&&adapter.getProfileProxy(this,new android.bluetooth.BluetoothProfile.ServiceListener(){
   public void onServiceConnected(int profile,android.bluetooth.BluetoothProfile proxy){
    android.bluetooth.BluetoothHeadset headset=(android.bluetooth.BluetoothHeadset)proxy;
    if(generation!=recognitionGeneration||!communicating){adapter.closeProfileProxy(profile,proxy);return;}
    recognitionHeadset=headset;
    try{java.util.List<android.bluetooth.BluetoothDevice> devices=headset.getConnectedDevices();
     AudioDeviceInfo selected=bluetoothDevice();String selectedAddress=selected==null?null:selected.getAddress();
     for(android.bluetooth.BluetoothDevice device:devices)if(device.getAddress().equalsIgnoreCase(selectedAddress)){recognitionDevice=device;break;}
     // Some Android routes omit their address. Only fall back when there is no ambiguity.
     if(recognitionDevice==null&&devices.size()==1)recognitionDevice=devices.get(0);
     boolean accepted=recognitionDevice!=null&&headset.startVoiceRecognition(recognitionDevice);recognitionAccepted=accepted;
     android.util.Log.i("PixelTiming","headset_recognition accepted="+accepted);
     awaitHeadsetRecognition(generation,SystemClock.elapsedRealtime()+(accepted?2500:0));
    }catch(RuntimeException e){android.util.Log.w("PixelTiming","headset recognition unavailable",e);awaitHeadsetRecognition(generation,0);}
   }
   public void onServiceDisconnected(int profile){}
  },android.bluetooth.BluetoothProfile.HEADSET);
  if(!requested){recognitionOpening=false;recognitionAttempted=true;routeAudio();}
  else main.postDelayed(()->{if(generation==recognitionGeneration&&recognitionOpening)awaitHeadsetRecognition(generation,0);},3000);
 }
 void awaitHeadsetRecognition(int generation,long deadline){
  if(generation!=recognitionGeneration||!communicating||!recognitionOpening)return;
  boolean connected=false;try{connected=recognitionHeadset!=null&&recognitionDevice!=null&&recognitionHeadset.isAudioConnected(recognitionDevice);}catch(RuntimeException ignored){}
  if(!connected&&SystemClock.elapsedRealtime()<deadline){main.postDelayed(()->awaitHeadsetRecognition(generation,deadline),50);return;}
  recognitionOpening=false;recognitionAttempted=true;
  android.util.Log.i("PixelTiming","headset_recognition audio_connected="+connected);routeAudio();
 }
 void routeAudio(){
  if(audio==null)return;
  if(!communicating){closeHeadsetRecognition();boolean hadRoute=requestedRoute!=-1||bluetoothRequested;requestedRoute=-1;bluetoothRequested=false;if(hadRoute){routeGeneration++;audio.clearCommunicationDevice();audio.setMode(AudioManager.MODE_NORMAL);}return;}
  AudioDeviceInfo chosen=bluetoothDevice();
  if(chosen==null&&bluetoothRequested)return; // Disconnect handler ends the conversation; never silently switch its mic to the phone.
  if(chosen==null)for(AudioDeviceInfo d:audio.getAvailableCommunicationDevices()){int t=d.getType();if(t==AudioDeviceInfo.TYPE_WIRED_HEADSET||t==AudioDeviceInfo.TYPE_USB_HEADSET){chosen=d;break;}if(t==AudioDeviceInfo.TYPE_BUILTIN_SPEAKER)chosen=d;}
  if(chosen==null||requestedRoute==chosen.getId())return;
  if(recognitionOpening)return;
  if(!recognitionAttempted&&chosen.getType()==7&&GlassesReconnect.ADDRESS.equalsIgnoreCase(chosen.getAddress())){openHeadsetRecognition();return;}
  freezeVolume();
  if(requestedRoute==-1)audio.setMode(AudioManager.MODE_IN_COMMUNICATION);
  boolean ok=audio.setCommunicationDevice(chosen);
  if(ok){requestedRoute=chosen.getId();bluetoothRequested=isBluetooth(chosen);scheduleVolumeRestore();}
  android.util.Log.i("PixelTiming","route_request type="+chosen.getType()+" accepted="+ok+" uptime="+SystemClock.elapsedRealtime());
 }
 void awaitReadyCue(int generation,long deadline,Runnable cue){
  if(!communicating||generation!=routeGeneration)return;
  boolean inputReady=false;
  for(AudioRecordingConfiguration r:audio.getActiveRecordingConfigurations())if(!r.isClientSilenced()&&r.getAudioDevice()!=null&&(!bluetoothRequested||isBluetooth(r.getAudioDevice())))inputReady=true;
  boolean outputReady=!bluetoothRequested||isBluetooth(audio.getCommunicationDevice());
  if(inputReady&&outputReady){scheduleVolumeRestore();android.util.Log.i("PixelTiming","capture_route_verified bluetooth="+bluetoothRequested+" uptime="+SystemClock.elapsedRealtime());cue.run();}
  else if(SystemClock.elapsedRealtime()<deadline)main.postDelayed(()->awaitReadyCue(generation,deadline,cue),50);
  else{traceVoice("capture_check_failed","cue_timeout");android.util.Log.w("PixelTiming","capture route verification timed out; ending conversation");command("end","capture_check");}
 }



 volatile AppleMusicRoutine musicRoutine;
 volatile ClockTimerRoutine clockTimerRoutine;
 volatile ClockControlRoutine clockControlRoutine;
 volatile MediaControlRoutine mediaRoutine;
 PhoneCommandStream phoneCommandStream;
 DebugBridge debugBridge;
 ClipboardSync clipboardSync;
 WebView web; final CallbackOwner callbacks=new CallbackOwner(); ExecutorService requests=Executors.newCachedThreadPool(); String pin,endpoint; DeviceIdentity identity; SSLSocketFactory ssl; AudioManager audio;
 @Override public void onCreate(){super.onCreate();instance=this;if(StatusGlowOverlay.supported()){statusGlow=new StatusGlowOverlay(this);statusGlow.start();}voiceLifecycle=new VoiceLifecycleTrace(this);notification(false);

 try{JSONObject c=new JSONObject(read(getAssets().open("connection.json")));pin=c.getString("pin");endpoint=getSharedPreferences("MainActivity",0).getString("endpoint",c.getString("endpoint"));identity=new DeviceIdentity(this);ssl=identity.ssl(pin);}catch(Exception e){throw new RuntimeException(e);}
 registerReceiver(podcastReceiver,new IntentFilter("dev.pixel.voice.PODCAST_CONTEXT"),"dev.pixel.voice.permission.PODCAST_CONTEXT",null,Context.RECEIVER_EXPORTED);bookNarrator=new BookNarrator(this);registerReceiver(bookReceiver,new IntentFilter("dev.pixel.voice.BOOK_CONTEXT"),"dev.pixel.voice.permission.BOOK_CONTEXT",null,Context.RECEIVER_EXPORTED);audio=(AudioManager)getSystemService(AUDIO_SERVICE);voiceDucking=new VoiceDucking(this,main);conversationVolume=getSharedPreferences("voice-volume-v2",0).getFloat("level",-1f);registerReceiver(skyReceiver,new IntentFilter("dev.pixel.voice.SKY_CONTEXT"),"dev.pixel.voice.permission.SKY_CONTEXT",null,Context.RECEIVER_EXPORTED);registerReceiver(volumeReceiver,new IntentFilter("android.media.VOLUME_CHANGED_ACTION"),Context.RECEIVER_NOT_EXPORTED);main.post(volumeMonitor);audio.registerAudioDeviceCallback(routeCallback,new Handler());audio.addOnCommunicationDeviceChangedListener(getMainExecutor(),d->{if(communicating){freezeVolume();scheduleVolumeRestore();}if(!communicating)ignoreButtonsUntil=Math.max(ignoreButtonsUntil,SystemClock.elapsedRealtime()+750);android.util.Log.i("PixelTiming","route_ready type="+(d==null?0:d.getType())+" uptime="+SystemClock.elapsedRealtime());});routeAudio();setupMediaButtons();glassesTransportTrace=new GlassesTransportTrace(this);glassesReconnect=new GlassesReconnect(this);glassesReconnect.start();createVoiceWeb(true);clipboardSync=new ClipboardSync(this);clipboardSync.start();}

 static final String VOICE_PAGE="https://appassets.androidplatform.net/index.html";
 /** The voice page's WebView, its bridge and settings, exactly as at startup. loadPage=false leaves a fresh
  * blank view (after repeated renderer loss) until an explicit Speak reloads it. */
 void createVoiceWeb(boolean loadPage){web=new WebView(this);web.setBackgroundColor(0xfff5f5f3);web.getSettings().setJavaScriptEnabled(true);web.getSettings().setDomStorageEnabled(true);web.getSettings().setMediaPlaybackRequiresUserGesture(false);web.getSettings().setAllowFileAccess(false);web.getSettings().setAllowContentAccess(false);WebView.setWebContentsDebuggingEnabled(false);
 web.setWebViewClient(new WebViewClient(){@Override public boolean onRenderProcessGone(WebView v,RenderProcessGoneDetail d){voiceRendererGone(v,d.didCrash());return true;}public void onPageFinished(WebView v,String u){if(v!=web)return;loaded=true;if(pendingPodcastContext!=null)receivePodcast(pendingPodcastContext);if(pendingCommand!=null){String c=pendingCommand;pendingCommand=null;command(c);}}public WebResourceResponse shouldInterceptRequest(WebView v,WebResourceRequest r){if("https".equals(r.getUrl().getScheme())&&("api.elevenlabs.io".equals(r.getUrl().getHost())||"livekit.rtc.elevenlabs.io".equals(r.getUrl().getHost())))return null;if(!"appassets.androidplatform.net".equals(r.getUrl().getHost()))return new WebResourceResponse("text/plain","utf-8",403,"Blocked",null,new ByteArrayInputStream(new byte[0]));String p=r.getUrl().getPath();if(p.equals("/"))p="/index.html";if(!p.equals("/index.html")&&!p.equals("/eleven-client.js")&&!p.equals("/voice-timing.js")&&!p.equals("/voice-commands.js")&&!p.equals("/model-command-speech.js")&&!p.equals("/phone-app-context.js")&&!p.equals("/voice-providers.js")&&!p.equals("/luna-first.js")&&!p.equals("/guardian-voice.js")&&!p.equals("/output-boost.js")&&!p.equals("/messages-tool.js")&&!p.equals("/media-tool.js")&&!p.equals("/clock-tool.js")&&!p.equals("/apple-music-voice.js")&&!p.equals("/book-companion.js")&&!p.equals("/podcast-companion.js")&&!p.equals("/phone-command-delivery.js")&&!p.equals("/task-status-transport.js"))return new WebResourceResponse("text/plain","utf-8",404,"Not found",null,new ByteArrayInputStream(new byte[0]));try{return new WebResourceResponse(p.endsWith(".js")?"application/javascript":"text/html","utf-8",getAssets().open(p.substring(1)));}catch(Exception e){return null;}}public boolean shouldOverrideUrlLoading(WebView v,WebResourceRequest r){return !"appassets.androidplatform.net".equals(r.getUrl().getHost());}});
 web.setWebChromeClient(new WebChromeClient(){public void onPermissionRequest(PermissionRequest r){runOnUiThread(()->{if(r.getOrigin().toString().equals("https://appassets.androidplatform.net/")&&checkSelfPermission("android.permission.RECORD_AUDIO")==PackageManager.PERMISSION_GRANTED)r.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});else r.deny();});}public boolean onConsoleMessage(ConsoleMessage c){android.util.Log.i("PixelVoice",c.message());return true;}});
 web.addJavascriptInterface(new Bridge(callbacks.open()),"Native");if(loadPage)web.loadUrl(VOICE_PAGE);}
 final RendererRecovery voiceRenderer=new RendererRecovery(3,60000);boolean voicePageHeld;
 /** The voice page's renderer is gone, so the call it carried is gone too. The dead view is destroyed and
  * never reused; state shows the call ended with a typed reason; nothing pending is replayed; tasks are not
  * touched. The backend session lease cannot be released from here (the page held its id) and expires on
  * its own. A fresh page is loaded unless renderer loss repeats, then it waits for an explicit Speak. */
 void voiceRendererGone(WebView dead,boolean crashed){
  traceVoice("renderer_gone",crashed?"crash":"killed");
  if(dead!=web){dead.destroy();return;}
  loaded=false;pendingCommand=null;
  live=false;connected=false;muted=false;lastEndReason="renderer_gone";
  taskStateAvailable=false;taskStateStreamConnected=false;
  new Bridge().audio(false);
  dead.destroy();
  voicePageHeld=!voiceRenderer.allow(SystemClock.elapsedRealtime());
  createVoiceWeb(!voicePageHeld);
  agentNotification();
 }
 static String read(InputStream in)throws IOException{ByteArrayOutputStream b=new ByteArrayOutputStream();byte[] buf=new byte[8192];int n;while((n=in.read(buf))!=-1)b.write(buf,0,n);in.close();return b.toString("UTF-8");}static String hex(byte[] a){StringBuilder s=new StringBuilder();for(byte b:a)s.append(String.format("%02x",b));return s.toString();}
 public void deliveryStatus(String message){getSharedPreferences("delivery",0).edit().putString("status",message).apply();}
 final java.util.concurrent.atomic.AtomicBoolean installing=new java.util.concurrent.atomic.AtomicBoolean(false);
 /** A voice session is active or connecting; installing Rose Agent would replace this app and end it. */
 boolean voiceBusy(){return live||connected||communicating;}
 /** This app's installed versionCode, or -1 when it can't be read. */
 long selfVersion(){try{return getPackageManager().getPackageInfo(getPackageName(),0).getLongVersionCode();}catch(Exception e){return -1;}}
 /** Explicit "Update Rose Agent" install of the offered record. Admission is decided here against the service's
  * own voice state and the shared installing flag, so only one download/install runs at a time. */
 SelfUpdate.Admission installSelf(DirectUpdate.Offer offer){
  long installed=selfVersion();
  SelfUpdate.Admission a=SelfUpdate.admit(offer==null?-1:offer.versionCode,installed,voiceBusy(),installing.get(),getPackageManager().canRequestPackageInstalls());
  if(a!=SelfUpdate.Admission.START)return a;
  if(!installing.compareAndSet(false,true))return SelfUpdate.Admission.IN_PROGRESS;
  recordSelfUpdate(offer.versionCode,"downloading");
  requests.submit(()->{try{Delivery.installSelf(VoiceService.this,offer);}finally{installing.set(false);}});
  return a;
 }
 /** The last explicit self-update attempt: its target version and state (see SelfUpdate.attemptText). */
 void recordSelfUpdate(long target,String state){getSharedPreferences("self-update",0).edit().putLong("target",target).putString("state",state).apply();}
 ToneGenerator readyTone;
 /** Each voice view gets its own Bridge, bound to that view's generation: its asynchronous callbacks reach
  * the page only while that view is still the current one (CallbackOwner). */
 class Bridge {
 final int generation;
 Bridge(){this(callbacks.current());}
 Bridge(int generation){this.generation=generation;}
 @JavascriptInterface public void voiceLifecycle(String kind,String detail){
  if(!kind.matches("cleanup|provider_disconnect|start_timeout|start_error|user_end|handoff"))return;
  if(detail==null||!detail.matches("[a-zA-Z0-9_:-]{0,100}"))detail="other";
  traceVoice(kind,detail);
  // Typed end/failure facts for the conversation surface; display status text is never parsed.
  if(kind.equals("cleanup"))lastEndReason=detail;else if(kind.equals("start_error"))lastStartError=detail;
 }
 /** The voice client's own /health result; null until first checked. */
 @JavascriptInterface public void backendReachable(boolean reachable){backendReachable=reachable;}

 @JavascriptInterface public void taskState(String raw){try{if(raw==null||raw.length()>80000)return;JSONObject data=new JSONObject(raw);if(data.optInt("version")!=1)return;taskStateJson=raw;taskStateReceivedAt=SystemClock.elapsedRealtime();taskStateAvailable=true;}catch(Exception ignored){taskStateAvailable=false;}}
 @JavascriptInterface public void taskStateUnavailable(){taskStateAvailable=false;}
 @JavascriptInterface public void taskStateStream(boolean connected){taskStateStreamConnected=connected;if(!connected)taskStateAvailable=false;}
 @JavascriptInterface public void taskModelState(String raw){try{JSONObject data=new JSONObject(raw);taskModelLabel=data.optString("label","Model unavailable");}catch(Exception ignored){taskModelLabel="Model unavailable";}}
 @JavascriptInterface public void resultCardMode(String mode){if(java.util.Arrays.asList("html","native","text").contains(mode))getSharedPreferences("task-card",0).edit().putString("mode",mode).putBoolean("rich",!mode.equals("text")).apply();}
 @JavascriptInterface public void taskWidgetSetting(boolean enabled){getSharedPreferences("task-card",0).edit().putBoolean("rich",enabled).apply();}
 @JavascriptInterface public void decisionsRoutingState(boolean enabled){getSharedPreferences("decisions-routing",0).edit().putBoolean("enabled",enabled).apply();}
 @JavascriptInterface public void guardianNotice(String message){runOnUiThread(()->android.widget.Toast.makeText(VoiceService.this,message,android.widget.Toast.LENGTH_LONG).show());}

 @JavascriptInterface public void conversationModeState(boolean active){getSharedPreferences("conversation-mode",0).edit().putBoolean("active",active).apply();}
 @JavascriptInterface public void conversation(String raw){try{if(raw.length()>400000)return;JSONObject data=new JSONObject(raw);if(data.optJSONArray("messages")==null)return;conversationJson=raw;conversationRevision=data.optLong("revision");}catch(Exception ignored){}}
 @JavascriptInterface public void podcastTranscript(String raw){try{if(raw.length()>80000)return;JSONObject d=new JSONObject(raw);if(!d.optString("id").matches("[0-9]+")||d.optJSONArray("segments")==null)return;d.put("observedAt",System.currentTimeMillis());sendBroadcast(new Intent("dev.pixel.podcasts.TRANSCRIPT_CONTEXT").setPackage("dev.pixel.podcasts").putExtra("transcript",d.toString()),"dev.pixel.voice.permission.PODCAST_CONTEXT");}catch(Exception ignored){}}
 @JavascriptInterface public void podcastCommand(String raw){try{if(raw.length()>1500)return;JSONObject d=new JSONObject(raw);String action=d.optString("action");if(!action.matches("play|resume|pause|get_state|get_transcript"))return;Intent i=new Intent("dev.pixel.podcasts.VOICE_COMMAND").setPackage("dev.pixel.podcasts").putExtra("command",raw);sendBroadcast(i,"dev.pixel.voice.permission.PODCAST_CONTEXT");}catch(Exception ignored){}}

 @JavascriptInterface public void readerState(String raw){try{JSONObject d=new JSONObject(raw);if(raw.length()>10000)return;getSharedPreferences("book-state",0).edit().putString("state",raw).apply();Intent i=new Intent("dev.pixel.books.VOICE_STATE").setPackage("dev.pixel.books").putExtra("state",d.toString());sendBroadcast(i,"dev.pixel.voice.permission.BOOK_CONTEXT");}catch(Exception ignored){}}
 @JavascriptInterface public void readerIllustration(String raw){if(raw.length()>650000)return;try{JSONObject d=new JSONObject(raw);if(d.has("image")){byte[] bytes=android.util.Base64.decode(d.getString("image"),android.util.Base64.DEFAULT);d.put("imageUri",BookIllustrationProvider.store(VoiceService.this,bytes).toString());d.remove("image");}String metadata=d.toString();if(metadata.length()>20000)throw new IOException("Illustration metadata too large");Intent i=new Intent("dev.pixel.books.VOICE_STATE").setPackage("dev.pixel.books").putExtra("illustration",metadata);sendBroadcast(i,"dev.pixel.voice.permission.BOOK_CONTEXT");}catch(Exception e){android.util.Log.w("PixelBooks","Illustration transfer failed",e);}}
 @JavascriptInterface public void readerPlay(String audio){if(audio.length()>12000000)return;runOnUiThread(()->bookNarrator.play(audio));}
 @JavascriptInterface public void readerPause(){runOnUiThread(()->bookNarrator.pause());}
 @JavascriptInterface public void readerStop(){runOnUiThread(()->bookNarrator.stop());}
 @JavascriptInterface public void readerResume(){runOnUiThread(()->{if(!bookNarrator.resume())callbacks.run(generation,()->web.evaluateJavascript("window.readerAudioEvent('missing')",null));});}

 @JavascriptInterface public String phoneCommand(String raw){
  try{
   JSONObject c=new JSONObject(raw);String id=c.getString("id");
   if(!id.matches("[a-f0-9-]{36}"))throw new IllegalArgumentException("Invalid command id");
   SharedPreferences saved=getSharedPreferences("phone-command-results",0);
   String prior=saved.getString(id,null);if(prior!=null)return prior;
   if(c.getLong("expires")<System.currentTimeMillis())return new JSONObject().put("status","expired").toString();
   if("debug_bridge_connect".equals(c.optString("action"))){
    synchronized(VoiceService.this){if(debugBridge==null)debugBridge=new DebugBridge(VoiceService.this);JSONObject result=debugBridge.connect(c);savePhoneResult(saved,id,result);return result.toString();}
   }
   if("clock_control".equals(c.optString("action"))){
    synchronized(VoiceService.this){
     String existing=saved.getString(id,null);if(existing!=null)return existing;
     if((clockControlRoutine!=null&&clockControlRoutine.running())||(clockTimerRoutine!=null&&clockTimerRoutine.running())||(musicRoutine!=null&&musicRoutine.running())||(mediaRoutine!=null&&mediaRoutine.running()))return new JSONObject().put("status","routine_busy").toString();
     JSONObject running=new JSONObject().put("status","running");savePhoneResult(saved,id,running);
     clockControlRoutine=new ClockControlRoutine(VoiceService.this,c,result->savePhoneResult(saved,id,result));clockControlRoutine.start();return running.toString();
    }
   }
   if(clockControlRoutine!=null&&clockControlRoutine.running()&&!java.util.Arrays.asList("inspect_ui","app_context","environment","diagnostics","screenshot","control_status","list_apps").contains(c.optString("action")))return new JSONObject().put("status","routine_busy").toString();
   if("media_control".equals(c.optString("action"))){
    synchronized(VoiceService.this){
     String existing=saved.getString(id,null);if(existing!=null)return existing;
     if((mediaRoutine!=null&&mediaRoutine.running())||(musicRoutine!=null&&musicRoutine.running())||(clockTimerRoutine!=null&&clockTimerRoutine.running()))return new JSONObject().put("status","routine_busy").toString();
     JSONObject running=new JSONObject().put("status","running");savePhoneResult(saved,id,running);
     mediaRoutine=new MediaControlRoutine(VoiceService.this,c,result->savePhoneResult(saved,id,result));mediaRoutine.start();return running.toString();
    }
   }
   if(mediaRoutine!=null&&mediaRoutine.running()&&!java.util.Arrays.asList("inspect_ui","app_context","environment","diagnostics","screenshot","control_status","list_apps").contains(c.optString("action")))return new JSONObject().put("status","routine_busy").toString();
   if("clock_timer_start".equals(c.optString("action"))){
    synchronized(VoiceService.this){
     if((clockTimerRoutine!=null&&clockTimerRoutine.running())||(musicRoutine!=null&&musicRoutine.running()))return new JSONObject().put("status","routine_busy").toString();
     JSONObject running=new JSONObject().put("status","running").put("action","clock_timer_start");savePhoneResult(saved,id,running);
     clockTimerRoutine=new ClockTimerRoutine(VoiceService.this,c,result->savePhoneResult(saved,id,result));clockTimerRoutine.start();return running.toString();
    }
   }
   if("apple_music_run".equals(c.optString("action"))){
    synchronized(VoiceService.this){
     String existing=saved.getString(id,null);if(existing!=null)return existing;
     if((musicRoutine!=null&&musicRoutine.running())||(clockTimerRoutine!=null&&clockTimerRoutine.running()))return new JSONObject().put("status","routine_busy").toString();
     JSONObject running=new JSONObject().put("status","running");savePhoneResult(saved,id,running);
     musicRoutine=new AppleMusicRoutine(VoiceService.this,c,result->savePhoneResult(saved,id,result));musicRoutine.start();return running.toString();
    }
   }
   if(musicRoutine!=null&&musicRoutine.running()&&!java.util.Arrays.asList("inspect_ui","app_context","environment","diagnostics","screenshot","control_status","list_apps").contains(c.optString("action")))return new JSONObject().put("status","routine_busy").toString();
   if("app_command".equals(c.optString("action"))){
    synchronized(VoiceService.this){
     String existing=saved.getString(id,null);if(existing!=null)return existing;
     savePhoneResult(saved,id,new JSONObject().put("status","running"));
     // Providers can wait for external app state. Never block our UI thread or
     // the five-second UI-action future while those operations complete.
     requests.execute(()->{
      JSONObject result;
      try{result=c.optLong("expires")<System.currentTimeMillis()?new JSONObject().put("status","expired"):PhoneControl.execute(VoiceService.this,c);}
      catch(Exception error){result=new JSONObject();try{result.put("status","uncertain").put("reason","App command result was not confirmed; inspect before retrying");}catch(Exception ignored){}}
      savePhoneResult(saved,id,result);
     });
     return new JSONObject().put("status","running").toString();
    }
   }
   if("diagnostics".equals(c.optString("action"))||"screenshot".equals(c.optString("action"))){JSONObject result=PhoneControl.execute(VoiceService.this,c);savePhoneResult(saved,id,result);return result.toString();}
   // Claim the id with a durable running receipt before scheduling: a re-query never posts a second action.
   PhoneCommandGate gate=new PhoneCommandGate(new PhoneCommandGate.Receipts(){public String get(String k){return saved.getString(k,null);}public void put(String k,String v){try{savePhoneResult(saved,k,new JSONObject(v));}catch(Exception e){throw new IllegalStateException(e);}}},VoiceService.this);
   String owned=gate.claim(id);if(owned!=null)return owned;
   java.util.concurrent.CompletableFuture<String> task=new java.util.concurrent.CompletableFuture<>();
   main.post(()->{
    JSONObject result;
    try{result=c.getLong("expires")<System.currentTimeMillis()?new JSONObject().put("status","expired"):PhoneControl.execute(VoiceService.this,c);}
    catch(Exception e){result=new JSONObject();try{result.put("status","error").put("message",e.getClass().getSimpleName()+": "+e.getMessage());}catch(Exception ignored){}}
    // Persist the action result before observing: a lost reply must never cause a second tap.
    savePhoneResult(saved,id,result);
    final JSONObject completed=result;
    if("tap_label".equals(c.optString("action"))&&"action_accepted".equals(result.optString("status"))){
     main.postDelayed(()->{
      try{completed.put("observation",PhoneControl.execute(VoiceService.this,new JSONObject().put("action","inspect_ui")));}
      catch(Exception e){try{completed.put("observation",new JSONObject().put("status","error").put("message",e.toString()));}catch(Exception ignored){}}
      savePhoneResult(saved,id,completed);task.complete(completed.toString());
     },150);
    }else if(("open_app".equals(c.optString("action"))||"bluebubbles_open_chat".equals(c.optString("action")))&&"launch_requested".equals(result.optString("status"))){
     main.postDelayed(()->observeOpenedApp(task,saved,id,completed,completed.optString("packageName"),SystemClock.elapsedRealtime()+3000),150);
    }else task.complete(result.toString());
   });
   // A wait that runs out returns the current receipt (running until the action saves its result).
   return gate.await(task,id,5000);
  }catch(Exception e){return "{\"status\":\"error\",\"message\":"+JSONObject.quote(e.toString())+"}";}
 }

 private void observeOpenedApp(java.util.concurrent.CompletableFuture<String> task,SharedPreferences saved,String id,JSONObject result,String target,long deadline){
  try{
   JSONObject observation=PhoneControl.execute(VoiceService.this,new JSONObject().put("action","inspect_ui"));
   boolean matched="ok".equals(observation.optString("status"))&&target.equals(observation.optString("packageName"));
   String status=observation.optString("status");
   if(!matched&&!status.equals("locked")&&!status.equals("accessibility_disabled")&&SystemClock.elapsedRealtime()<deadline){
    main.postDelayed(()->observeOpenedApp(task,saved,id,result,target,deadline),150);return;
   }
   result.put("observation",observation).put("foregroundVerified",matched);
   result.put("status",matched?"opened":status.equals("locked")?"locked":"launch_unverified");
  }catch(Exception e){try{result.put("status","launch_unverified").put("message",e.toString());}catch(Exception ignored){}}
  savePhoneResult(saved,id,result);task.complete(result.toString());
 }
 private void savePhoneResult(SharedPreferences saved,String id,JSONObject result){
  SharedPreferences.Editor edit=saved.edit();if(saved.getAll().size()>100)edit.clear();edit.putString(id,result.toString()).commit();
 }

 @JavascriptInterface public void readyChime(){playSessionCue("start");}
 @JavascriptInterface public void sessionCue(String kind){if(!kind.equals("mute")&&!kind.equals("unmute")&&!kind.equals("end"))return;if(kind.equals("end"))main.postDelayed(()->playSessionCue(kind),400);else playSessionCue(kind);}
 void playSessionCue(String kind){runOnUiThread(()->{if(!communicating&&!kind.equals("end"))return;if(bluetoothRequested&&!isBluetooth(audio.getCommunicationDevice())){requestedRoute=-1;routeAudio();}Runnable play=()->{try{int rate=48000,frames=12000;short[] cue=new short[frames];for(int n=0;n<frames;n++){double t=(double)n/rate;double local=t<.11?t:t-.13;double envelope=local>=0&&local<.11?Math.min(1,Math.min(local/.012,(.11-local)/.02)):0;double low=kind.equals("mute")?440:kind.equals("end")?330:kind.equals("unmute")?850:740;double high=kind.equals("mute")?330:kind.equals("end")?220:kind.equals("unmute")?1150:990;double frequency=t<.12?low:high;cue[n]=(short)(Math.sin(2*Math.PI*frequency*t)*envelope*6000);}final AudioTrack track=new AudioTrack.Builder().setAudioAttributes(new AudioAttributes.Builder().setUsage(kind.equals("end")?AudioAttributes.USAGE_MEDIA:AudioAttributes.USAGE_VOICE_COMMUNICATION).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()).setAudioFormat(new AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT).setSampleRate(rate).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build()).setBufferSizeInBytes(frames*2).setTransferMode(AudioTrack.MODE_STATIC).build();AudioDeviceInfo device=audio.getCommunicationDevice();if(device!=null)track.setPreferredDevice(device);track.write(cue,0,cue.length);track.play();main.postDelayed(()->{AudioDeviceInfo routed=track.getRoutedDevice();android.util.Log.i("PixelTiming","ready_chime routed_type="+(routed==null?0:routed.getType())+" uptime="+SystemClock.elapsedRealtime());},50);main.postDelayed(()->{track.stop();track.release();},500);}catch(RuntimeException e){android.util.Log.w("PixelVoice","Session chime unavailable",e);}};if(kind.equals("end"))play.run();else awaitReadyCue(routeGeneration,SystemClock.elapsedRealtime()+8000,play);});}


 @JavascriptInterface public void timing(String stage,double elapsed){android.util.Log.i("PixelTiming",stage+" elapsed_ms="+Math.round(elapsed)+" uptime="+SystemClock.elapsedRealtime());}

 @JavascriptInterface public void state(String json){try{JSONObject o=new JSONObject(json);status=o.optString("status");guardianName=o.optString("guardianName");task=o.optString("task");connected=o.optBoolean("connected");live=o.optBoolean("ready");muted=o.optBoolean("muted");level=(float)o.optDouble("level");if(!live||!connected){trayInputLevel=0;trayOutputLevel=0;}else if(muted)trayInputLevel=0;runOnUiThread(()->agentNotification());}catch(Exception ignored){}}

 @JavascriptInterface public void glassesMediaAction(String action){if(!"play".equals(action)&&!"pause".equals(action)&&!"next".equals(action))return;runOnUiThread(()->{recordButton("Web player "+action);if("next".equals(action)){if(buttonGate.accept(KeyEvent.KEYCODE_MEDIA_NEXT,0,0,0,SystemClock.elapsedRealtime()))nextButton();}else startFromGlasses();});}
 @JavascriptInterface public String lastButton(){return lastButton;}
 @JavascriptInterface public boolean cameraMicrophoneSuspended(){return CameraMicrophoneProvider.suspended(VoiceService.this);}
 @JavascriptInterface public void cameraMicrophoneResult(String token,String result){CameraMicrophoneProvider.complete(token,result);}
 @JavascriptInterface public String appContext(){return PhoneAppContext.read(VoiceService.this).toString();}
 /** Rose Agent updates itself only from the explicit menu check, never from the automatic /apps loop. */
 @JavascriptInterface public boolean isSelf(String packageName){return getPackageName().equals(packageName);}
 @JavascriptInterface public boolean shouldOffer(String packageName,long version){if(!packageName.equals(getPackageName()))return true;try{return version>getPackageManager().getPackageInfo(getPackageName(),0).getLongVersionCode();}catch(Exception e){return false;}}
 @JavascriptInterface public String installStatus(){return getSharedPreferences("delivery",0).getString("status","");}
 @JavascriptInterface public void installApp(String id,String label){if(!id.matches("[a-f0-9]{64}")||!installing.compareAndSet(false,true))return;requests.submit(()->{try{Delivery.install(VoiceService.this,id,label,endpoint,ssl);}finally{installing.set(false);}});}

 @JavascriptInterface public void startPhonePush(){runOnUiThread(()->{if(phoneCommandStream==null){phoneCommandStream=new PhoneCommandStream(VoiceService.this);phoneCommandStream.start();}});}
 @JavascriptInterface public String endpoint(){return endpoint;}
 @JavascriptInterface public void setEndpoint(String e){VoiceService.this.setServerEndpoint(e);}
 @JavascriptInterface public void trayAudio(float input,float output){trayInputLevel=Float.isFinite(input)?Math.max(0,Math.min(1,input)):0;trayOutputLevel=Float.isFinite(output)?Math.max(0,Math.min(1,output)):0;trayAudioObservedAt=SystemClock.elapsedRealtime();}
 @JavascriptInterface public void assistantSpeaking(boolean speaking){runOnUiThread(()->sendReplyActivity(communicating&&speaking));}
 @JavascriptInterface public void audio(boolean on){runOnUiThread(()->{freezeVolume();communicating=on;if(!on){trayInputLevel=0;trayOutputLevel=0;trayAudioObservedAt=0;sendReplyActivity(false);if(voiceDucking!=null)voiceDucking.release();bookNarrator.stop();}if(on){activateMediaButtons();if(conversationVolume<0){conversationVolume=(float)audio.getStreamVolume(0)/audio.getStreamMaxVolume(0);getSharedPreferences("voice-volume-v2",0).edit().putFloat("level",conversationVolume).apply();}scheduleVolumeRestore();if(voiceLock==null){voiceLock=((PowerManager)getSystemService(POWER_SERVICE)).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"PixelVoice:conversation");voiceLock.setReferenceCounted(false);}voiceLock.acquire();}else if(voiceLock!=null&&voiceLock.isHeld())voiceLock.release();notification(on);agentNotification();PhoneEnvironment.get(VoiceService.this).setActive(on);routeAudio();});}
 private long installedVersion(){try{return getPackageManager().getPackageInfo(getPackageName(),0).getLongVersionCode();}catch(Exception e){return -1;}}
 @JavascriptInterface public boolean audioPrepared(){return communicating&&!recognitionOpening&&requestedRoute>=0;}
 @JavascriptInterface public boolean voiceBoostEnabled(){return VoiceService.this.voiceBoostEnabled();}
  @JavascriptInterface public void voiceBoostState(String state){outputBoostState=state;}
  @JavascriptInterface public String audioRoute(){if(Build.VERSION.SDK_INT>=31){AudioDeviceInfo d=audio.getCommunicationDevice();return (d==null?"No voice device":d.getProductName().toString()+" (type "+d.getType()+")")+" · version "+installedVersion()+" · saved "+Math.round(conversationVolume*100)+"% · call "+audio.getStreamVolume(0)+"/"+audio.getStreamMaxVolume(0)+" · settling "+!mayLearnVolume();}return "System audio";}
 @JavascriptInterface public void request(String id,String method,String route,String body){if(!route.startsWith("/")||route.contains(".."))return;requests.submit(()->{int status=0;String result;try{URL url=new URL(endpoint+route);HttpsURLConnection c=(HttpsURLConnection)url.openConnection();c.setSSLSocketFactory(ssl);c.setHostnameVerifier((h,s)->true);c.setConnectTimeout(6000);c.setReadTimeout(35000);c.setRequestMethod(method);if(method.equals("POST")){c.setDoOutput(true);c.setRequestProperty("Content-Type","application/json");try(OutputStream o=c.getOutputStream()){o.write(body.getBytes("UTF-8"));}}status=c.getResponseCode();result=read(status<400?c.getInputStream():c.getErrorStream());c.disconnect();}catch(Exception e){result="{\"error\":"+JSONObject.quote(e.getClass().getSimpleName()+": "+e.getMessage())+"}";}final String js="window.nativeResponse("+JSONObject.quote(id)+","+status+","+JSONObject.quote(result)+")";runOnUiThread(()->callbacks.run(generation,()->web.evaluateJavascript(js,null)));});}
 }
 @Override public void onDestroy(){if(statusGlow!=null)statusGlow.stop();if(debugBridge!=null)debugBridge.close();PhoneEnvironment.get(this).setActive(false);if(glassesTransportTrace!=null)glassesTransportTrace.close();if(clipboardSync!=null)clipboardSync.stop();if(phoneCommandStream!=null)phoneCommandStream.stop();if(clockControlRoutine!=null)clockControlRoutine.cancel();if(mediaRoutine!=null)mediaRoutine.cancel();if(musicRoutine!=null)musicRoutine.cancel();if(voiceDucking!=null)voiceDucking.release();bookNarrator.stop();unregisterReceiver(podcastReceiver);unregisterReceiver(bookReceiver);sendReplyActivity(false);if(glassesReconnect!=null)glassesReconnect.stop();instance=null;main.removeCallbacks(appContextChanged);main.removeCallbacks(volumeMonitor);unregisterReceiver(volumeReceiver);unregisterReceiver(skyReceiver);if(readyTone!=null){readyTone.release();readyTone=null;}if(voiceLock!=null&&voiceLock.isHeld())voiceLock.release();communicating=false;if(mediaSession!=null)mediaSession.release();audio.unregisterAudioDeviceCallback(routeCallback);routeAudio();callbacks.close();web.destroy();requests.shutdownNow();super.onDestroy();}
}
