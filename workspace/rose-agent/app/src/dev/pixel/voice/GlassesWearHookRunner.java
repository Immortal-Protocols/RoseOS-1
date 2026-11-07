package dev.pixel.voice;

import android.content.Context;
import android.content.Intent;
import android.os.SystemClock;
import org.json.JSONObject;

/** Runs the glasses-on hook (Nov 7 plan 1O) and reports which signal drives it.
 *
 * <p>One GlassesWearHook per process decides; this class only performs its decision. The signal is the owner's chosen
 * proxy (Nathan, 2026-11-07), reported by BluetoothAudioWear: any Bluetooth audio device connecting counts as glasses
 * on, and the last one disconnecting counts as removal. Status names it as that proxy, never as a worn sensor.
 *
 * <p>Actions:
 * <ul>
 * <li>tray starts Rose Home (MainActivity as HOME), which brings Home and its tray forward. Raising the OS edge tray
 * over another app needs an OS Product seam and is not attempted.</li>
 * <li>app opens packageName through PhoneControl's open_app, with its keyguard and launcher checks.</li>
 * </ul>
 * The last fire is kept in memory only. */
final class GlassesWearHookRunner {
 private GlassesWearHookRunner() {}

 static final GlassesWearHook HOOK = new GlassesWearHook();
 static final String BASIS = "Owner-chosen proxy (2026-11-07): any Bluetooth audio device connecting counts as glasses "
   + "on and the last one disconnecting as removal. It is not a worn sensor: other Bluetooth audio devices count too.";
 static final String LIMITATION = "No glasses signal has been reported in this process yet (Bluetooth audio watching "
   + "has not started).";

 private static boolean wearReported;
 private static JSONObject lastFire;
 private static String lastRefusal, lastInvalidWear;

 static void onWearSignal(Context ctx, GlassesWearHook.Source source, int state) {
  GlassesWearHook.Decision d = HOOK.observe(source, state, SystemClock.elapsedRealtime(), CustomizationStore.read(ctx));
  synchronized (GlassesWearHookRunner.class) {
   if (GlassesWearHook.Decision.REFUSED.equals(d.kind)) {
    // lastRefused is for turned-away non-wear sources; a wear sensor's bad state is reported apart.
    if (source != null && source.isWear()) lastInvalidWear = d.reason; else lastRefusal = d.reason;
    return;
   }
   if (source != null && source.isWear()) wearReported = true;
  }
  if (!d.fires()) return;
  String result;
  try {
   if (CustomizationConfig.ACTION_APP.equals(d.action))
    result = PhoneControl.execute(ctx, new JSONObject().put("action", "open_app").put("packageName", d.packageName)).optString("status", "unknown");
   else {
    ctx.startActivity(new Intent(ctx, MainActivity.class).setAction(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
      .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
    result = "launch_requested";
   }
  } catch (Exception e) { result = "error"; }
  try {
   JSONObject fire = new JSONObject().put("at", System.currentTimeMillis()).put("action", d.action)
     .put("packageName", d.packageName == null ? JSONObject.NULL : d.packageName).put("result", result);
   synchronized (GlassesWearHookRunner.class) { lastFire = fire; }
  } catch (Exception ignored) {}
 }

 static JSONObject status(Context ctx) {
  try {
   boolean available; JSONObject fire; String refusal, invalid;
   synchronized (GlassesWearHookRunner.class) { available = wearReported; fire = lastFire; refusal = lastRefusal; invalid = lastInvalidWear; }
   String name = HOOK.sourceName();
   JSONObject s = new JSONObject().put("signalAvailable", available)
     .put("source", available && name != null ? name : "none")
     .put("state", GlassesWearHook.stateName(HOOK.state())).put("fires", HOOK.fires())
     .put("lastFire", fire == null ? JSONObject.NULL : new JSONObject(fire.toString()));
   s.put("basis", BASIS);
   if (!available) s.put("limitation", LIMITATION);
   if (refusal != null) s.put("lastRefused", refusal);
   if (invalid != null) s.put("lastInvalidWearReport", invalid);
   return s;
  } catch (Exception e) {
   // Never drop the basis: the minimal form still names the signal for what it is.
   JSONObject s = new JSONObject();
   try { s.put("signalAvailable", false).put("basis", BASIS).put("limitation", LIMITATION); } catch (Exception ignored) {}
   return s;
  }
 }
}
