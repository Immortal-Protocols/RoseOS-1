package dev.pixel.backgroundcontrol;

import java.util.List;
import java.util.Locale;

/** Whether this OS build lets this controller drive any app in its background workspace (Android's
 * config_pixelComputerControlPrototypeAgents, enforced by the platform's hasPrototypeControl), and what that
 * changes here. The platform decides; this only mirrors its pin so the controller can report its real
 * capability and follow the workspace into another app (an installer, a sign-in screen) only when Android
 * allows that too. Pure logic, no Android types. */
final class AnyAppControl {
 private AnyAppControl() {}

 /** The platform's refusal of a target this OS build does not allow (ComputerControl allowlist). */
 static final String REFUSED_TARGET = "Invalid target package for ComputerControl";

 /** True when one pin is exactly "packageName:certificate" for this package and one of its signer
  * SHA-256 digests (hex, case and ':' separators ignored). Anything malformed matches nothing. */
 static boolean pinned(String packageName, List<String> signerSha256, String[] pins) {
  if (packageName == null || packageName.isEmpty() || signerSha256 == null || pins == null) return false;
  for (String pin : pins) {
   if (pin == null) continue;
   int split = pin.indexOf(':');
   if (split <= 0 || !pin.substring(0, split).equals(packageName)) continue;
   String cert = hex(pin.substring(split + 1));
   if (cert == null) continue;
   for (String signer : signerSha256) if (cert.equals(hex(signer))) return true;
  }
  return false;
 }

 /** A node can be read and acted on when it belongs to the session's target app, or, when this build lets
  * the controller drive any app, to whatever app is in front in the session's own workspace. */
 static boolean actionable(String nodePackage, String target, boolean anyApp) {
  if (nodePackage == null || nodePackage.isEmpty()) return false;
  return anyApp || nodePackage.equals(target);
 }

 /** The session request failed because this OS build does not allow the target app. */
 static boolean targetRefused(String error) {
  return error != null && error.contains(REFUSED_TARGET);
 }

 private static String hex(String value) {
  if (value == null) return null;
  String h = value.replace(":", "").trim().toLowerCase(Locale.ROOT);
  return h.matches("[0-9a-f]{64}") ? h : null;
 }
}
