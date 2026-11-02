package dev.pixel.voice;
import android.app.KeyguardManager;
import android.content.*;
import android.content.pm.*;
import org.json.*;
import java.util.*;

final class PhoneControl {
 static JSONObject execute(Context context,JSONObject command)throws Exception{
  PackageManager pm=context.getPackageManager();
  String action=command.getString("action");
  // Read facts only on demand. No navigation, speech, or blocking GPS lookup.
  if(action.equals("environment"))return PhoneEnvironment.get(context).snapshot().put("status","ok").put("observedAt",System.currentTimeMillis());
  if(action.equals("bluebubbles_open_chat")){
   if(((KeyguardManager)context.getSystemService(Context.KEYGUARD_SERVICE)).isKeyguardLocked())return new JSONObject().put("status","locked");
   String guid=command.getString("chatGuid");
   if(guid.length()>300||!guid.matches("(?:iMessage|SMS|any);[+-];[^\\p{Cntrl}]+"))throw new IllegalArgumentException("Invalid Messages conversation ID");
   // The official app accepts the same explicit chat intent used by its own
   // notifications. No text extra is supplied: existing drafts are preserved.
   Intent open=new Intent().setComponent(new ComponentName("com.bluebubbles.messaging","com.bluebubbles.messaging.MainActivity"))
    .setType("OpenChat").putExtra("chatGuid",guid).putExtra("bubble",false).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
   context.startActivity(open);
   return new JSONObject().put("status","launch_requested").put("packageName","com.bluebubbles.messaging");
  }
  if(action.equals("app_context"))return PhoneAppContext.read(context);
  if(action.equals("migration_backup_open")){context.startActivity(new Intent(context,MigrationBackupActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));return new JSONObject().put("status","opened");}
  if(action.equals("migration_private_backup_open")){context.startActivity(new Intent(context,MigrationBackupActivity.class).putExtra("privateOnly",true).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_CLEAR_TOP));return new JSONObject().put("status","opened");}
  if(action.equals("migration_backup_status")){JSONObject versions=new JSONObject();for(String pkg:Arrays.asList("dev.pixel.notes","dev.pixel.ideas","dev.pixel.daybook","dev.pixel.reminders","dev.pixel.books"))try{versions.put(pkg,pm.getPackageInfo(pkg,0).getLongVersionCode());}catch(Exception ignored){}return new JSONObject().put("status","ok").put("message",MigrationBackupActivity.status()).put("allFilesAccess",android.os.Environment.isExternalStorageManager()).put("appVersions",versions);}
  if(action.equals("app_command")){
   String pkg=command.getString("packageName"),authority=pkg+".agent";
   ProviderInfo provider=pm.resolveContentProvider(authority,0);
   if(provider==null||!pkg.equals(provider.packageName))return new JSONObject().put("status","unavailable").put("reason","App has no agent command provider");
   boolean sameSigner=pm.checkSignatures(context.getPackageName(),pkg)==PackageManager.SIGNATURE_MATCH;
   String[] certificates=null;
   if(!sameSigner){
    PackageInfo info=pm.getPackageInfo(pkg,PackageManager.GET_SIGNING_CERTIFICATES);
    android.content.pm.Signature[] signers=info.signingInfo==null?null:info.signingInfo.getApkContentsSigners();
    if(signers!=null){
     certificates=new String[signers.length];
     for(int i=0;i<signers.length;i++){
      byte[] digest=java.security.MessageDigest.getInstance("SHA-256").digest(signers[i].toByteArray());
      StringBuilder hex=new StringBuilder();for(byte b:digest)hex.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
      certificates[i]=hex.toString();
     }
    }
   }
   if(!AgentAppTrust.allows(pkg,sameSigner,certificates))return new JSONObject().put("status","unavailable").put("reason","App signing identity does not match");
   // The controller's read-only preview is for Rose Agent's own overview on this phone (#225), never a phone command.
   if(pkg.equals("dev.pixel.backgroundcontrol")&&("preview".equals(command.optString("operation"))||"current_apps".equals(command.optString("operation"))))return new JSONObject().put("status","unavailable").put("reason","Preview is local to Rose Agent");
   // Background sessions may act while the owner is away from the screen only when the owner
   // armed it and recently authenticated. The physical-screen guards below are unchanged.
   if(pkg.equals("dev.pixel.backgroundcontrol")&&LockedControl.gates(command.getString("operation"))&&LockedControl.ownerAway(context)){JSONObject refusal=LockedControl.refusal(context);if(refusal!=null)return refusal;}
   android.os.Bundle args=new android.os.Bundle();args.putString("params",command.optJSONObject("params")==null?"{}":command.getJSONObject("params").toString());args.putString("operationId",command.getString("id"));
   android.os.Bundle result=context.getContentResolver().call(android.net.Uri.parse("content://"+authority),command.getString("operation"),command.getString("id"),args);
   if(result==null||result.getString("result")==null)return new JSONObject().put("status","uncertain").put("reason","App did not return a result");
   JSONObject response=new JSONObject(result.getString("result"));
   if(pkg.equals("dev.pixel.backgroundcontrol"))AutoAgentPip.observe(context,command.getString("operation"),response);
   return response;
  }
  if(action.equals("voice_ducking")){
   VoiceService voice=VoiceService.instance;
   if(voice==null||voice.voiceDucking==null)return new JSONObject().put("status","unavailable");
   if(command.has("duckMode"))voice.voiceDucking.setMode(command.getString("duckMode"));
   if(command.has("reductionDb"))voice.voiceDucking.setReduction((float)command.getDouble("reductionDb"));
   if(command.has("duckEnabled"))voice.voiceDucking.setEnabled(command.getBoolean("duckEnabled"));
   if(command.optInt("testSeconds")>0)voice.voiceDucking.probe(command.getInt("testSeconds"));
   return voice.voiceDucking.status();
  }
  if(action.equals("visualization_set"))return VisualStore.set(context,command.opt("visual"));
  if(action.equals("visualization_clear"))return VisualStore.clear(context);
  if(action.equals("visualization_status"))return VisualStore.status(context);
  if(action.equals("screenshot"))return ScreenCapture.capture(context,command.optBoolean("visualOnly",false));
  if(action.equals("screen_gesture"))return ScreenGesture.execute(context,command);
  if(action.equals("diagnostics"))return PhoneDiagnostics.read(context);
  if(action.equals("control_status"))return new JSONObject().put("status","ok").put("versionCode",pm.getPackageInfo(context.getPackageName(),0).getLongVersionCode()).put("screenChangeRejectionEnabled",context.getSharedPreferences("screen-change-guard",0).getBoolean("enabled",true)).put("uiControlEnabled",PhoneAccessibility.active!=null).put("accessibility",AccessibilityHealth.read(context,command.optBoolean("details",false))).put("agentViewer",AutoAgentPip.status());
  if(action.equals("setup_ui_control")){
   if(PhoneAccessibility.active!=null||AccessibilityHealth.read(context,false).optBoolean("configuredEnabled"))return AccessibilityHealth.reconnect(context);
   if(((KeyguardManager)context.getSystemService(Context.KEYGUARD_SERVICE)).isKeyguardLocked())return new JSONObject().put("status","locked");
   Intent settings=new Intent("android.settings.ACCESSIBILITY_DETAILS_SETTINGS").putExtra(Intent.EXTRA_COMPONENT_NAME,new ComponentName(context,PhoneAccessibility.class)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
   try{context.startActivity(settings);}catch(ActivityNotFoundException | SecurityException e){context.startActivity(new Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));}
   return new JSONObject().put("status","setup_requested").put("message","Enable Rose Agent phone controls in Android Accessibility settings.");
  }
  if(Arrays.asList("inspect_ui","tap","long_press","tap_label","touch_label","touch_point","set_text","ime_enter","scroll","swipe","drag","home","back").contains(action)){
   if(PhoneAccessibility.active==null)return AccessibilityHealth.reconnect(context);
   return PhoneAccessibility.active.execute(command);
  }
  Intent query=new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
  List<ResolveInfo> apps=pm.queryIntentActivities(query,0);
  if(action.equals("list_apps")){
   JSONArray result=new JSONArray();Set<String> seen=new HashSet<>();
   for(ResolveInfo app:apps)if(seen.add(app.activityInfo.packageName))
    result.put(new JSONObject().put("label",AppLabels.label(pm,app.activityInfo.packageName,app.loadLabel(pm).toString())).put("packageName",app.activityInfo.packageName));
   return new JSONObject().put("status","ok").put("apps",result);
  }
  if(!action.equals("open_app"))throw new IllegalArgumentException("Unsupported phone action");
  String target=command.getString("packageName");ResolveInfo selected=null;
  for(ResolveInfo app:apps)if(app.activityInfo.packageName.equals(target)){selected=app;break;}
  if(selected==null)return new JSONObject().put("status","not_found").put("packageName",target);
  KeyguardManager lock=(KeyguardManager)context.getSystemService(Context.KEYGUARD_SERVICE);
  if(lock.isKeyguardLocked())return new JSONObject().put("status","locked").put("packageName",target);
  context.startActivity(new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
   .setComponent(new ComponentName(target,selected.activityInfo.name)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
   android.app.ActivityOptions.makeBasic().setLaunchDisplayId(android.view.Display.DEFAULT_DISPLAY).toBundle());
  return new JSONObject().put("status","launch_requested").put("packageName",target)
   .put("label",AppLabels.label(pm,target,selected.loadLabel(pm).toString()));
 }
}
