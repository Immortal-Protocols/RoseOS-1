package dev.pixel.voice;

import android.content.Context;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** App-command handler for the customization_* operations (Nov 7 plan 1O). It is meant to be reached from
 * SettingsAgentProvider.execute by the "customization_" prefix, so conversation and CLI share one signed path and one
 * catalog (assets/customization-agent-tools.json); that wiring is pending agreement with the provider's owner and is
 * not made here.
 *
 * <p>Arguments arrive as org.json and are converted to the plain types CustomizationConfig validates (Long, Double,
 * Boolean, String); nested objects, arrays and null are refused. Every failure is an exception, which the provider turns
 * into {"status":"error","reason":...}. A success reports "verified" only when the stored read-back equals the
 * requested value, says whether Home has drawn that revision yet, and carries the glasses-wear status, so a caller never
 * mistakes a saved value for a visible one, or a connection for a worn signal. */
final class CustomizationAgent {
 private CustomizationAgent() {}

 static final String PREFIX = "customization_", CATALOG_ASSET = "customization-agent-tools.json";
 static final String HOME_PENDING_NOTE = "Home applies changes when its customization listener is integrated (pending Home/App Product seam)";

 static JSONObject execute(Context ctx, String method, JSONObject params) throws Exception {
  if (method == null || !CustomizationConfig.OPERATIONS.contains(method)) throw new IllegalArgumentException("Unknown customization operation");
  Map<String, Object> args = arguments(params);
  CustomizationStore.Applied a = CustomizationStore.apply(ctx, method, args);
  String status = CustomizationConfig.STATE.equals(method) ? "ok" : a.verified() ? "verified" : "uncertain";
  return result(ctx, status, method, a.changed(), a.stored);
 }

 static JSONObject result(Context ctx, String status, String operation, boolean changed, CustomizationConfig stored) throws Exception {
  // Only an exact match counts: a mark above the stored revision (the config was reset) is not proof either.
  long applied = CustomizationStore.homeAppliedRevision(ctx);
  boolean drawn = applied == stored.revision;
  JSONObject home = new JSONObject().put("appliedRevision", applied == CustomizationStore.NEVER_APPLIED ? JSONObject.NULL : (Object) applied)
    .put("applied", drawn);
  if (!drawn) home.put("note", HOME_PENDING_NOTE);
  return new JSONObject().put("status", status).put("operation", operation).put("changed", changed)
    .put("customization", new JSONObject(stored.toJson())).put("home", home)
    .put("glassesWear", GlassesWearHookRunner.status(ctx));
 }

 /** org.json arguments as CustomizationConfig expects them; a null object is no arguments. */
 static Map<String, Object> arguments(JSONObject params) {
  Map<String, Object> out = new LinkedHashMap<>();
  if (params == null) return out;
  for (Iterator<String> keys = params.keys(); keys.hasNext(); ) {
   String k = keys.next();
   out.put(k, plain(params.opt(k)));
  }
  return out;
 }

 static Object plain(Object v) {
  if (v instanceof String || v instanceof Boolean) return v;
  if (v instanceof Integer || v instanceof Long || v instanceof Short || v instanceof Byte) return ((Number) v).longValue();
  if (v instanceof BigInteger && ((BigInteger) v).bitLength() < 64) return ((BigInteger) v).longValue();
  if (v instanceof BigDecimal) {
   BigDecimal d = (BigDecimal) v;
   try { return d.longValueExact(); } catch (ArithmeticException fraction) { return d.doubleValue(); }
  }
  if (v instanceof Double || v instanceof Float) return ((Number) v).doubleValue();
  throw new IllegalArgumentException("Unsupported argument type");
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
