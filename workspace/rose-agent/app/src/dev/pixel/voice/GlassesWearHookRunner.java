package dev.pixel.voice;

import android.content.Context;
import android.content.Intent;
import android.os.SystemClock;
import org.json.JSONArray;
import org.json.JSONObject;

/** Runs the glasses-on hook (Nov 7 plan 1O) and reports honestly whether it can run at all.
 *
 * <p>One GlassesWearHook per process decides; this class only performs its decision. Only a wear_sensor source can
 * drive it: a Bluetooth/HFP connection, an audio route or the hinge/unfold advertisement is not "worn", and the hook
 * refuses them. Nothing calls {@link #onWearSignal} in this revision, because the repository has no wear source. The
 * real one is Meta DAT's Device.donState (DONNED/DOFFED), which only the external Glasses Camera app
 * (dev.pixel.glassescamera) links; relaying it here needs a coordinated change outside this repository and Meta
 * developer registration, which is a user/access decision. Until then {@link #status} says the signal is unavailable.
 *
 * <p>Actions: tray starts Rose Home (MainActivity as HOME), which brings Home and its tray forward; raising the OS
 * edge tray over another app needs an OS Product seam and is not attempted. app opens packageName through
 * PhoneControl's open_app, with its keyguard and launcher checks. The last fire is kept in memory only. */
final class GlassesWearHookRunner {
 private GlassesWearHookRunner() {}

 static final GlassesWearHook HOOK = new GlassesWearHook();
 static final String LIMITATION = "No glasses-worn signal is available at this revision. The real source is Meta DAT "
   + "Device.donState (DONNED/DOFFED), which is not in this repository: only the external Glasses Camera app "
   + "(dev.pixel.glassescamera) uses DAT. Relaying it needs a coordinated change outside this repository and Meta "
   + "developer registration (a user/access decision). Bluetooth connection, HFP audio and the hinge/unfold "
   + "advertisement are not treated as worn, so the hook does not fire.";

 private static boolean wearReported;
 private static JSONObject lastFire;
 private static String lastRefusal;

 static void onWearSignal(Context ctx, GlassesWearHook.Source source, int state) {
  GlassesWearHook.Decision d = HOOK.observe(source, state, SystemClock.elapsedRealtime(), CustomizationStore.read(ctx));
  synchronized (GlassesWearHookRunner.class) {
   if (GlassesWearHook.Decision.REFUSED.equals(d.kind)) { lastRefusal = d.reason; return; }
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
   boolean available; JSONObject fire; String refusal;
   synchronized (GlassesWearHookRunner.class) { available = wearReported; fire = lastFire; refusal = lastRefusal; }
   String name = HOOK.sourceName();
   JSONObject s = new JSONObject().put("signalAvailable", available)
     .put("source", available && name != null ? name : "none")
     .put("state", GlassesWearHook.stateName(HOOK.state())).put("fires", HOOK.fires())
     .put("lastFire", fire == null ? JSONObject.NULL : new JSONObject(fire.toString()));
   if (!available) s.put("limitation", LIMITATION);
   if (refusal != null) s.put("lastRefused", refusal);
   return s.put("notWear", new JSONArray().put("bluetooth_connection").put("hfp_audio").put("hinge_unfold_advertisement"));
  } catch (Exception e) {
   return new JSONObject();
  }
 }
}
