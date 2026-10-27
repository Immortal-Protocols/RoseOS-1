package dev.pixel.voice;

import android.accessibilityservice.AccessibilityService;
import android.app.KeyguardManager;
import android.content.Context;
import android.graphics.Bitmap;
import android.os.Build;
import android.view.Display;
import android.view.accessibility.AccessibilityNodeInfo;
import java.io.ByteArrayOutputStream;
import java.util.concurrent.*;
import org.json.JSONObject;

/** One explicitly requested frame, never a screen recorder. */
final class ScreenCapture {
 static String visualFrameId;
 static long visualFrameTime;
 static int visualWidth,visualHeight;
 static synchronized String registerFrame(int width,int height){visualFrameId=java.util.UUID.randomUUID().toString();visualFrameTime=android.os.SystemClock.elapsedRealtime();visualWidth=width;visualHeight=height;return visualFrameId;}
 static synchronized int[] consumeFrame(String id){
  if(visualFrameId==null||!visualFrameId.equals(id)||android.os.SystemClock.elapsedRealtime()-visualFrameTime>30000)return null;
  visualFrameId=null;return new int[]{visualWidth,visualHeight};
 }
 static JSONObject capture(Context context)throws Exception {return capture(context,false);}
 static JSONObject capture(Context context,boolean visualOnly)throws Exception {
  return capture(context,visualOnly,true);
 }
 // Background capture must never replace a foreground agent's gesture target.
 static JSONObject captureForBookmark(Context context)throws Exception {return capture(context,false,false);}
 private static JSONObject capture(Context context,boolean visualOnly,boolean register)throws Exception {
  if(Build.VERSION.SDK_INT<30)return new JSONObject().put("status","unsupported");
  if(locked(context))return new JSONObject().put("status","locked");
  AccessibilityService service=PhoneAccessibility.active;
  if(service==null)return new JSONObject().put("status","accessibility_disabled");
  CompletableFuture<JSONObject> result=new CompletableFuture<>();
  service.takeScreenshot(Display.DEFAULT_DISPLAY,service.getMainExecutor(),new AccessibilityService.TakeScreenshotCallback(){
   public void onSuccess(AccessibilityService.ScreenshotResult shot){
    Bitmap original=null,copy=null,scaled=null;
    try {
     if(locked(context)){result.complete(new JSONObject().put("status","locked"));return;}
     original=Bitmap.wrapHardwareBuffer(shot.getHardwareBuffer(),shot.getColorSpace());
     if(original==null)throw new IllegalStateException("Screenshot unavailable");
     copy=original.copy(Bitmap.Config.ARGB_8888,false);
     float scale=Math.min(1f,1600f/Math.max(copy.getWidth(),copy.getHeight()));
     scaled=Bitmap.createScaledBitmap(copy,Math.max(1,Math.round(copy.getWidth()*scale)),Math.max(1,Math.round(copy.getHeight()*scale)),true);
     ByteArrayOutputStream bytes=new ByteArrayOutputStream();scaled.compress(Bitmap.CompressFormat.JPEG,75,bytes);
     if(bytes.size()>800000)throw new IllegalStateException("Screenshot exceeds size limit");
     JSONObject out=new JSONObject().put("status","ok").put("capturedAt",System.currentTimeMillis()).put("width",scaled.getWidth()).put("height",scaled.getHeight()).put("mimeType","image/jpeg").put("imageBase64",android.util.Base64.encodeToString(bytes.toByteArray(),android.util.Base64.NO_WRAP));
     out.put("displayWidth",copy.getWidth()).put("displayHeight",copy.getHeight());
     if(register)out.put("screenshotId",registerFrame(copy.getWidth(),copy.getHeight())).put("validForMs",30000);
     if(!visualOnly){AccessibilityNodeInfo root=service.getRootInActiveWindow();if(root!=null){out.put("packageName",String.valueOf(root.getPackageName()));root.recycle();}}
     result.complete(out);
    }catch(Exception e){result.completeExceptionally(e);}finally{if(scaled!=null&&scaled!=copy)scaled.recycle();if(copy!=null)copy.recycle();if(original!=null)original.recycle();shot.getHardwareBuffer().close();}
   }
   public void onFailure(int code){try{result.complete(new JSONObject().put("status",code==AccessibilityService.ERROR_TAKE_SCREENSHOT_SECURE_WINDOW?"secure_window":"capture_failed").put("errorCode",code));}catch(Exception e){result.completeExceptionally(e);}}
  });
  return result.get(4,TimeUnit.SECONDS);
 }
 static boolean locked(Context c){return ((KeyguardManager)c.getSystemService(Context.KEYGUARD_SERVICE)).isKeyguardLocked();}
}
