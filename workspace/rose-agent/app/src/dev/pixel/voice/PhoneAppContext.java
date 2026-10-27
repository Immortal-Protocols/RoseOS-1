package dev.pixel.voice;

import android.app.KeyguardManager;
import android.content.Context;
import android.view.accessibility.AccessibilityNodeInfo;
import org.json.JSONObject;

/** Foreground identity plus optional state from our signature-verified native apps. */
final class PhoneAppContext {
 static JSONObject read(Context context) {
  JSONObject result=new JSONObject();
  try {
   result.put("observedAt",System.currentTimeMillis());
   boolean locked=((KeyguardManager)context.getSystemService(Context.KEYGUARD_SERVICE)).isKeyguardLocked();
   result.put("locked",locked);
   if(locked)return result.put("status","locked");
   PhoneAccessibility service=PhoneAccessibility.active;
   if(service==null)return result.put("status","unavailable");
   AccessibilityNodeInfo root=service.getRootInActiveWindow();
   if(root==null)return result.put("status","no_window");
   String pkg;
   try{pkg=root.getPackageName()==null?"":root.getPackageName().toString();}finally{root.recycle();}
   if(!pkg.matches("(?:[A-Za-z][A-Za-z0-9_]*\\.)+[A-Za-z0-9_]+"))return result.put("status","unavailable");
   String label=pkg;
   try{label=context.getPackageManager().getApplicationLabel(context.getPackageManager().getApplicationInfo(pkg,0)).toString();}catch(Exception ignored){}
   label=AppLabels.label(context.getPackageManager(),pkg,label).replaceAll("[\\p{Cntrl}]"," ");
   if(label.length()>80)label=label.substring(0,80);
   result.put("status","ok").put("foregroundApp",new JSONObject().put("packageName",pkg).put("label",label));
   JSONObject appState=AppStateSnapshot.read(context,pkg);if(appState!=null)result.put("appState",appState);
   return result;
  }catch(Exception ignored){try{result.put("status","unavailable");}catch(Exception unused){}return result;}
 }
}
