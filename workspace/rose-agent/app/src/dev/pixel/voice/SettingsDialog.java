package dev.pixel.voice;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import org.json.JSONObject;

/**
 * Rose Agent's settings: Pair with Mac, and About (RoseSettingsModel). Nothing else is a setting.
 * This class gathers the pairing state and version and routes Pair with Mac; SettingsScreen draws the
 * window-owned dialog. Opened from a long press on Home and from the conversation's Settings
 * (MainActivity.homeOptions).
 */
final class SettingsDialog {
 private SettingsDialog() {}

 /** The Pair with Mac state line for this phone, read without any network check. */
 static String pairingState(Context c) {
  LocalRoseConnection.Paired p = LocalRoseConnection.paired(c);
  if (p == null) return RoseSettingsModel.pairing(null, LocalRoseConnection.pairedFlag(c), null, null);
  String state = "", reason = "";
  try {
   // status() of a paired phone reads only this process's link state and saved pairing.
   JSONObject s = new JSONObject(LocalRoseConnection.status(c));
   state = s.optString("state");
   reason = s.optString("reason");
  } catch (Exception ignored) {
  }
  return RoseSettingsModel.pairing(p.serverName, false, state, reason);
 }

 static String version(Context c) {
  try {
   android.content.pm.PackageInfo info = c.getPackageManager().getPackageInfo(c.getPackageName(), 0);
   return RoseSettingsModel.version(info.versionName, info.getLongVersionCode());
  } catch (Exception unknown) {
   return RoseSettingsModel.version(null, 0);
  }
 }

 static AlertDialog show(Activity activity) {
  // The established QR pairing flow: pair, pair again or change Macs, unpair, or cancel there.
  return SettingsScreen.show(activity, pairingState(activity), version(activity),
    () -> activity.startActivity(new Intent(activity, LocalRosePairActivity.class)));
 }
}
