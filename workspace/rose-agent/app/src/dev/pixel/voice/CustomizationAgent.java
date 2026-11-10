package dev.pixel.voice;

import android.content.Context;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** App-command handler for the customization_* operations (Nov 7 plan 1O). SettingsAgentProvider.execute forwards the
 * "customization_" prefix here, after the provider's existing signature gate in call(), and its capabilities merge
 * appends {@link #catalog}, so conversation and CLI share one signed path and one catalog
 * (assets/customization-agent-tools.json).
 *
 * <p>Arguments arrive as org.json and are converted to the plain types CustomizationConfig validates (Long, Double,
 * Boolean, String); nested objects, arrays and null are refused. Every failure is an exception, which the provider turns
 * into {"status":"error","reason":...}. The result's fields are decided by CustomizationOutcome (host-tested): "verified"
 * means stored, Home's effective column count appears only once Home has drawn that revision, and the glasses-wear
 * status rides along, so a caller never mistakes a saved value for a visible one, or a connection for a worn signal. */
final class CustomizationAgent {
 private CustomizationAgent() {}

 static final String PREFIX = "customization_", CATALOG_ASSET = "customization-agent-tools.json";

 static JSONObject execute(Context ctx, String method, JSONObject params) throws Exception {
  if (method == null || !CustomizationConfig.OPERATIONS.contains(method)) throw new IllegalArgumentException("Unknown customization operation");
  Map<String, Object> args = arguments(params);
  CustomizationStore.Applied a = CustomizationStore.apply(ctx, method, args);
  String status = CustomizationOutcome.status(method, a.verified());
  return result(ctx, status, method, a.changed(), a.stored);
 }

 static JSONObject result(Context ctx, String status, String operation, boolean changed, CustomizationConfig stored) throws Exception {
  JSONObject home = new JSONObject(CustomizationConfig.json(CustomizationOutcome.home(stored.revision,
    CustomizationStore.homeAppliedRevision(ctx), CustomizationStore.homeAppliedColumns(ctx))));
  return new JSONObject().put("status", status).put("operation", operation).put("changed", changed)
    .put("customization", new JSONObject(stored.toJson())).put("home", home)
    .put("glassesWear", GlassesWearHookRunner.status(ctx))
    .put("system", new JSONObject(CustomizationConfig.json(CustomizationOutcome.system(stored.theme, AgentTrayService.shownTheme()))));
 }

 /** org.json arguments as CustomizationConfig expects them; a null object is no arguments. */
 static Map<String, Object> arguments(JSONObject params) {
  Map<String, Object> out = new LinkedHashMap<>();
  if (params == null) return out;
  for (Iterator<String> keys = params.keys(); keys.hasNext(); ) {
   String k = keys.next();
   out.put(k, CustomizationOutcome.plain(params.opt(k)));
  }
  return out;
 }

 /** The shared tool catalog, for the capabilities merge seam. */
 static JSONArray catalog(Context ctx) throws Exception {
  try (InputStream in = ctx.getAssets().open(CATALOG_ASSET)) {
   ByteArrayOutputStream b = new ByteArrayOutputStream();
   byte[] buf = new byte[8192];
   for (int n; (n = in.read(buf)) > 0; ) b.write(buf, 0, n);
   return new JSONArray(new String(b.toByteArray(), StandardCharsets.UTF_8));
  }
 }

 static boolean handles(String method) { return method != null && method.startsWith(PREFIX); }

 static List<String> operations() { return CustomizationConfig.OPERATIONS; }
}
