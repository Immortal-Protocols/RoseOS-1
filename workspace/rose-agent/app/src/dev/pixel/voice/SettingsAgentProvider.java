package dev.pixel.voice;

import android.Manifest;
import android.content.*;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.hardware.camera2.*;
import android.net.Uri;
import android.os.*;
import android.provider.Settings;
import org.json.*;
import java.util.*;
import java.util.concurrent.*;

/** Signed app-command host for device settings and the YouTube adapter. */
public final class SettingsAgentProvider extends ContentProvider {
 private final ExecutorService worker=Executors.newSingleThreadExecutor();
 private final LinkedHashMap<String,Bundle> receipts=new LinkedHashMap<>();
 private final ConcurrentHashMap<String,Boolean> torchStates=new ConcurrentHashMap<>();
 private CameraManager cameras;
 private HandlerThread callbacks;
 private YouTubeAgent youtube;
 @Override public boolean onCreate(){
  youtube=new YouTubeAgent(getContext());
  cameras=(CameraManager)getContext().getSystemService(Context.CAMERA_SERVICE);
  callbacks=new HandlerThread("settings-torch-state");callbacks.start();
  cameras.registerTorchCallback(new CameraManager.TorchCallback(){
   @Override public void onTorchModeChanged(String id,boolean enabled){torchStates.put(id,enabled);}
   @Override public void onTorchModeUnavailable(String id){torchStates.remove(id);}
  },new Handler(callbacks.getLooper()));
  return true;
 }
 private String rearFlash()throws Exception{
  for(String id:cameras.getCameraIdList()){
   CameraCharacteristics c=cameras.getCameraCharacteristics(id);
   if(Boolean.TRUE.equals(c.get(CameraCharacteristics.FLASH_INFO_AVAILABLE))&&Integer.valueOf(CameraCharacteristics.LENS_FACING_BACK).equals(c.get(CameraCharacteristics.LENS_FACING)))return id;
  }
  throw new IllegalStateException("No rear flashlight available");
 }
 private JSONObject state()throws Exception{
  ContentResolver r=getContext().getContentResolver();
  Bundle activity=workspaceActivity();
  int raw=Settings.System.getInt(r,Settings.System.SCREEN_BRIGHTNESS,-1);
  JSONObject s=new JSONObject().put("status","ok")
   .put("canWriteSettings",Settings.System.canWrite(getContext()))
   .put("cameraPermission",getContext().checkSelfPermission(Manifest.permission.CAMERA)==PackageManager.PERMISSION_GRANTED)
   .put("brightnessMode",Settings.System.getInt(r,Settings.System.SCREEN_BRIGHTNESS_MODE,0)==1?"automatic":"manual")
   .put("brightnessRaw",raw).put("brightnessPercent",raw<0?JSONObject.NULL:Math.round(raw*10000.0/255.0)/100.0)
   .put("brightnessMeaning","Stored system brightness preference, not measured screen luminance; automatic mode may choose a different level")
   .put("autoRotate",Settings.System.getInt(r,Settings.System.ACCELEROMETER_ROTATION,0)==1)
   .put("airplaneMode",Settings.Global.getInt(r,Settings.Global.AIRPLANE_MODE_ON,0)==1)
   .put("airplaneModeDirectControl",false)
   .put("workspaceTaskAvailable",activity.getBoolean("available"))
   .put("workspaceTaskRunning",activity.getBoolean("running"));
  try{String id=rearFlash();Boolean on=torchStates.get(id);s.put("flashlight",on==null?JSONObject.NULL:on).put("flashlightAvailable",on!=null);}catch(Exception e){s.put("flashlight",JSONObject.NULL).put("flashlightAvailable",false);}
  return s;
 }
 private static Bundle workspaceActivity(){
  VoiceService s=VoiceService.instance;
  boolean available=s!=null&&s.taskStateAvailable&&(s.taskStateStreamConnected||SystemClock.elapsedRealtime()-s.taskStateReceivedAt<5000);
  boolean running=false;
  try{JSONObject task=s==null?null:new JSONObject(s.taskStateJson).optJSONObject("current");String state=task==null?"":task.optString("state");running=available&&(state.equals("running")||state.equals("checking")||state.equals("stopping"));}catch(Exception ignored){}
  Bundle b=new Bundle();b.putBoolean("available",available);b.putBoolean("running",running);return b;
 }
 private static void only(JSONObject p,String... allowed)throws Exception{
  HashSet<String> set=new HashSet<>(Arrays.asList(allowed));Iterator<String> it=p.keys();while(it.hasNext())if(!set.contains(it.next()))throw new IllegalArgumentException("Unknown setting argument");
 }
 private static boolean bool(JSONObject p,String key)throws Exception{Object v=p.get(key);if(!(v instanceof Boolean))throw new IllegalArgumentException("Expected boolean "+key);return (Boolean)v;}
 private JSONObject execute(String method,JSONObject p)throws Exception{
  if(method.equals("capabilities")){
   java.io.InputStream in=getContext().getAssets().open("settings-agent-tools.json");java.io.ByteArrayOutputStream out=new java.io.ByteArrayOutputStream();byte[] b=new byte[4096];int n;while((n=in.read(b))!=-1)out.write(b,0,n);in.close();
   JSONArray tools=new JSONArray(out.toString("UTF-8"));
   in=getContext().getAssets().open("youtube-agent-tools.json");out.reset();while((n=in.read(b))!=-1)out.write(b,0,n);in.close();JSONArray yt=new JSONArray(out.toString("UTF-8"));for(int i=0;i<yt.length();i++)tools.put(yt.get(i));
   JSONArray cz=CustomizationAgent.catalog(getContext());for(int i=0;i<cz.length();i++)tools.put(cz.get(i));
   return new JSONObject().put("status","ok").put("schemaVersion",1).put("versionCode",getContext().getPackageManager().getPackageInfo(getContext().getPackageName(),0).getLongVersionCode()).put("tools",tools);
  }
  if(method.startsWith("youtube_"))return youtube.execute(method,p);
  if(CustomizationAgent.handles(method))return CustomizationAgent.execute(getContext(),method,p);
  if(method.startsWith("install_approval_"))return new PlayInstallBridge(getContext()).execute(method,p);
  if(method.equals("get_state")){only(p);return state();}
  if(method.equals("request_access")){
   only(p,"access");String access=p.getString("access");if(!access.equals("camera")&&!access.equals("settings"))throw new IllegalArgumentException("Unknown access");
   getContext().startActivity(new Intent(getContext(),SettingsAccessActivity.class).putExtra("access",access).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
   return new JSONObject().put("status","permission_requested");
  }
  if(method.equals("brightness")){
   only(p,"percent","automatic");if(!p.has("percent")&&!p.has("automatic"))throw new IllegalArgumentException("Supply percent or automatic");
   if(!Settings.System.canWrite(getContext()))return state().put("status","permission_required").put("reason","Rose Agent needs Modify system settings access");
   boolean automatic=p.has("automatic")&&bool(p,"automatic");
   if(automatic&&p.has("percent"))throw new IllegalArgumentException("Choose automatic brightness or a manual percent");
   int raw=-1;
   if(p.has("percent")){Object v=p.get("percent");if(!(v instanceof Number))throw new IllegalArgumentException("Expected numeric percent");double percent=((Number)v).doubleValue();if(!Double.isFinite(percent)||percent<0||percent>100)throw new IllegalArgumentException("Percent must be between 0 and 100");raw=Math.max(1,(int)Math.round(percent*255.0/100));}
   ContentResolver r=getContext().getContentResolver();boolean ok=Settings.System.putInt(r,Settings.System.SCREEN_BRIGHTNESS_MODE,automatic?1:0);
   if(raw>=0)ok=Settings.System.putInt(r,Settings.System.SCREEN_BRIGHTNESS,raw)&&ok;
   JSONObject s=state();boolean verified=ok&&s.getString("brightnessMode").equals(automatic?"automatic":"manual")&&(raw<0||s.getInt("brightnessRaw")==raw);
   return s.put("status",verified?"verified":"uncertain");
  }
  if(method.equals("rotation")){
   only(p,"enabled");boolean enabled=bool(p,"enabled");if(!Settings.System.canWrite(getContext()))return state().put("status","permission_required").put("reason","Rose Agent needs Modify system settings access");
   boolean ok=Settings.System.putInt(getContext().getContentResolver(),Settings.System.ACCELEROMETER_ROTATION,enabled?1:0);JSONObject s=state();return s.put("status",ok&&s.getBoolean("autoRotate")==enabled?"verified":"uncertain");
  }
  if(method.equals("flashlight")){
   only(p,"enabled");boolean enabled=bool(p,"enabled");if(getContext().checkSelfPermission(Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED)return state().put("status","permission_required").put("reason","Camera access is required for flashlight control");
   String id=rearFlash();if(!Boolean.valueOf(enabled).equals(torchStates.get(id)))cameras.setTorchMode(id,enabled);
   long end=SystemClock.elapsedRealtime()+2000;while(!Boolean.valueOf(enabled).equals(torchStates.get(id))&&SystemClock.elapsedRealtime()<end)Thread.sleep(25);
   return state().put("status",Boolean.valueOf(enabled).equals(torchStates.get(id))?"verified":"uncertain");
  }
  if(method.equals("open_panel")){
   only(p,"panel");String panel=p.getString("panel"),action;
   switch(panel){case "airplane":action=Settings.ACTION_AIRPLANE_MODE_SETTINGS;break;case "internet":action=Settings.Panel.ACTION_INTERNET_CONNECTIVITY;break;case "wifi":action=Settings.ACTION_WIFI_SETTINGS;break;case "bluetooth":action=Settings.ACTION_BLUETOOTH_SETTINGS;break;case "display":action=Settings.ACTION_DISPLAY_SETTINGS;break;case "sound":action=Settings.ACTION_SOUND_SETTINGS;break;default:throw new IllegalArgumentException("Unknown system panel");}
   getContext().startActivity(new Intent(action).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
   long end=SystemClock.elapsedRealtime()+2500;while(SystemClock.elapsedRealtime()<end){PhoneAccessibility a=PhoneAccessibility.active;if(a!=null){JSONObject screen=a.execute(new JSONObject().put("action","inspect_ui"));if("com.android.settings".equals(screen.optString("packageName")))return state().put("status","verified").put("panelOpened",panel).put("settingChanged",false);}Thread.sleep(60);}
   return state().put("status","uncertain").put("reason","Settings panel foreground was not confirmed").put("settingChanged",false);
  }
  throw new IllegalArgumentException("Unknown settings operation");
 }
 @Override public Bundle call(String method,String arg,Bundle extras){
  if(getContext().getPackageManager().checkSignatures(android.os.Process.myUid(),Binder.getCallingUid())!=PackageManager.SIGNATURE_MATCH)throw new SecurityException("Caller signing identity does not match");
  if(method.equals("workspace_activity"))return workspaceActivity();
  try{return worker.submit(()->{
   String key=arg==null?null:method+":"+arg;if(key!=null&&receipts.containsKey(key))return new Bundle(receipts.get(key));
   JSONObject result;try{result=execute(method,new JSONObject(extras==null?"{}":extras.getString("params","{}")));}catch(Exception e){result=new JSONObject();try{result.put("status","error").put("reason",e.getMessage()==null?"Settings command failed":e.getMessage());}catch(Exception ignored){}}
   Bundle b=new Bundle();b.putString("result",result.toString());if(key!=null){receipts.put(key,b);if(receipts.size()>100)receipts.remove(receipts.keySet().iterator().next());}return b;
  }).get(method.startsWith("youtube_")?19:method.startsWith("install_approval_")?16:8,TimeUnit.SECONDS);}catch(Exception e){Bundle b=new Bundle();b.putString("result","{\"status\":\"uncertain\",\"reason\":\"Settings command outcome was not confirmed; read state before retrying\"}");return b;}
 }
 @Override public Cursor query(Uri u,String[] p,String s,String[] a,String o){return null;}
 @Override public String getType(Uri u){return null;}
 @Override public Uri insert(Uri u,ContentValues v){return null;}
 @Override public int delete(Uri u,String s,String[] a){return 0;}
 @Override public int update(Uri u,ContentValues v,String s,String[] a){return 0;}
}
