package dev.pixel.voice;

import android.accessibilityservice.GestureDescription;
import android.app.KeyguardManager;
import android.content.Context;
import android.graphics.Path;
import android.util.DisplayMetrics;
import android.view.WindowManager;
import org.json.*;

/** Screenshot-based physical gestures. Never reads accessibility nodes or bounds. */
final class ScreenGesture {
 static JSONObject execute(Context context,JSONObject command)throws Exception{
  if(((KeyguardManager)context.getSystemService(Context.KEYGUARD_SERVICE)).isKeyguardLocked())return new JSONObject().put("status","locked");
  PhoneAccessibility service=PhoneAccessibility.active;
  if(service==null)return new JSONObject().put("status","accessibility_disabled");
  int[] size=ScreenCapture.consumeFrame(command.getString("screenshotId"));
  if(size==null)return new JSONObject().put("status","stale_screenshot");
  DisplayMetrics display=new DisplayMetrics();((WindowManager)context.getSystemService(Context.WINDOW_SERVICE)).getDefaultDisplay().getRealMetrics(display);
  if(display.widthPixels!=size[0]||display.heightPixels!=size[1])return new JSONObject().put("status","display_changed");
  JSONArray points=command.getJSONArray("points");
  if(points.length()!=2&&points.length()!=4)throw new IllegalArgumentException("Expected a point or drag endpoints");
  for(int i=0;i<points.length();i++){double value=points.getDouble(i);if(!Double.isFinite(value)||value!=Math.rint(value)||value<0||value>=size[i%2])throw new IllegalArgumentException("Point outside captured display");}
  Path path=new Path();path.moveTo(points.getInt(0),points.getInt(1));
  int duration=70;
  if(points.length()==4){path.lineTo(points.getInt(2),points.getInt(3));duration=command.optInt("durationMs",350);if(duration<100||duration>2000)throw new IllegalArgumentException("Invalid gesture duration");}
  float[] tracePoints=new float[points.length()];for(int i=0;i<tracePoints.length;i++)tracePoints[i]=(float)points.getDouble(i);
  String traceId=service.beginGesture(command,null,null,null,tracePoints,duration);
  boolean queued=service.dispatchObservedGesture(new GestureDescription.Builder().addStroke(new GestureDescription.StrokeDescription(path,0,duration)).build(),traceId);
  return new JSONObject().put("status",queued?"gesture_dispatched":"action_failed").put("gestureId",traceId).put("targeting","screenshot_coordinates");
 }
}
